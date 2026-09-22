import argparse
import csv
import json
import math
import os
import platform
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable

import httpx


RAGENT_URL = os.getenv("RAGENT_URL", "http://127.0.0.1:8001").rstrip("/")
XAGENT_URL = os.getenv("XAGENT_URL", "http://127.0.0.1:8002").rstrip("/")
XAPP_URL = os.getenv("XAPP_URL", "http://127.0.0.1:8003").rstrip("/")


def percentile(values: Iterable[float], quantile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return math.nan
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def describe(values: Iterable[float]) -> Dict[str, float | int]:
    data = [float(value) for value in values]
    if not data:
        return {"n": 0, "mean": math.nan, "median": math.nan, "p95": math.nan, "stdev": math.nan}
    return {
        "n": len(data),
        "mean": statistics.mean(data),
        "median": statistics.median(data),
        "p95": percentile(data, 0.95),
        "stdev": statistics.pstdev(data),
        "min": min(data),
        "max": max(data),
    }


def write_csv(path: Path, rows: list[Dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=list(rows[0].keys()), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def wait_for_metric_samples(client: httpx.Client, count: int, timeout_sec: float = 15) -> list[Dict[str, Any]]:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        response = client.get(f"{XAPP_URL}/demo/metrics")
        response.raise_for_status()
        samples = response.json()["samples"]
        if len(samples) >= count:
            return samples[-count:]
        time.sleep(0.05)
    raise TimeoutError(f"only received fewer than {count} E2 metric samples")


def timed_post(client: httpx.Client, url: str, payload: Dict[str, Any] | None = None) -> tuple[float, httpx.Response]:
    started = time.perf_counter_ns()
    response = client.post(url, json=payload)
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000.0
    response.raise_for_status()
    return elapsed_ms, response


def run_a2a_once(client: httpx.Client, trace_id: str) -> tuple[float, float, Dict[str, Any]]:
    client.post(f"{XAGENT_URL}/demo/reset").raise_for_status()
    client.post(f"{RAGENT_URL}/demo/reset").raise_for_status()

    policy_ms, policy_response = timed_post(client, f"{RAGENT_URL}/demo/run_once")
    if not policy_response.json().get("ok"):
        raise RuntimeError(f"policy delivery failed: {policy_response.text}")

    state_envelope = {
        "msg_type": "StateReport",
        "sender": "experiment-driver",
        "receiver": "xAgent",
        "trace_id": trace_id,
        "payload": {
            "source": "controlled_experiment",
            "metrics": {"prb_usage": 0.9, "e2_latency_us": 0.0, "ue_count": 1.0},
            "timestamp_ms": int(time.time() * 1000),
        },
    }
    a2a_ms, a2a_response = timed_post(
        client, f"{XAGENT_URL}/a2a/state/report", state_envelope
    )
    triggered = a2a_response.json().get("triggered", [])
    action_result = triggered[0].get("result", {}) if triggered else {}
    return policy_ms, a2a_ms, action_result


def run(output_dir: Path, trials: int, kpi_samples: int, routing_iterations: int) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    latency_rows: list[Dict[str, Any]] = []
    kpi_rows: list[Dict[str, Any]] = []
    routing_rows: list[Dict[str, Any]] = []

    with httpx.Client(timeout=10) as client:
        # Closed-loop emulator experiment: observe native MAC indications before
        # and after one real E2 control transaction.
        client.post(f"{XAPP_URL}/demo/reset_metrics").raise_for_status()
        pre_samples = wait_for_metric_samples(client, kpi_samples)
        for index, sample in enumerate(pre_samples, 1):
            kpi_rows.append({"phase": "before_control", "sample": index, **sample})

        _, control_response = timed_post(
            client,
            f"{XAPP_URL}/a2a/action/execute",
            {"action": "suggest_prb_boost", "source": "experiment"},
        )
        if not control_response.json().get("control_success"):
            raise RuntimeError(f"closed-loop control failed: {control_response.text}")

        client.post(f"{XAPP_URL}/demo/reset_metrics").raise_for_status()
        post_samples = wait_for_metric_samples(client, kpi_samples)
        for index, sample in enumerate(post_samples, 1):
            kpi_rows.append({"phase": "after_control", "sample": index, **sample})

        # Exclude three cold-start runs from the 30 measured repetitions.
        for warmup in range(1, 4):
            timed_post(
                client,
                f"{XAPP_URL}/a2a/action/execute",
                {"action": "suggest_prb_boost", "source": "warmup"},
            )
            run_a2a_once(client, f"warmup-{warmup}")

        # Interleave the direct REST baseline and the A2A path to reduce time-order bias.
        for trial in range(1, trials + 1):
            baseline_ms, baseline_response = timed_post(
                client,
                f"{XAPP_URL}/a2a/action/execute",
                {"action": "suggest_prb_boost", "source": "direct_rest_baseline"},
            )
            baseline_result = baseline_response.json()
            latency_rows.append(
                {
                    "path": "direct_rest",
                    "trial": trial,
                    "policy_delivery_ms": "",
                    "end_to_end_ms": round(baseline_ms, 6),
                    "full_cycle_ms": round(baseline_ms, 6),
                    "e2_control_internal_ms": baseline_result.get("latency_ms", ""),
                    "success": bool(baseline_result.get("control_success")),
                }
            )

            policy_ms, a2a_ms, action_result = run_a2a_once(
                client, f"experiment-{trial}"
            )
            latency_rows.append(
                {
                    "path": "a2a",
                    "trial": trial,
                    "policy_delivery_ms": round(policy_ms, 6),
                    "end_to_end_ms": round(a2a_ms, 6),
                    "full_cycle_ms": round(policy_ms + a2a_ms, 6),
                    "e2_control_internal_ms": action_result.get("latency_ms", ""),
                    "success": bool(action_result.get("control_success")),
                }
            )

        for card_count in (1, 10, 50):
            response = client.get(
                f"{XAGENT_URL}/demo/benchmark_routing",
                params={"card_count": card_count, "iterations": routing_iterations},
            )
            response.raise_for_status()
            routing_rows.append(response.json())

        health = client.get(f"{XAPP_URL}/healthz").json()

    direct_rows = [row for row in latency_rows if row["path"] == "direct_rest"]
    a2a_rows = [row for row in latency_rows if row["path"] == "a2a"]
    pre_usage = [row["prb_usage"] for row in kpi_rows if row["phase"] == "before_control"]
    post_usage = [row["prb_usage"] for row in kpi_rows if row["phase"] == "after_control"]
    e2_latency = [row["e2_latency_us"] for row in kpi_rows]

    direct_stats = describe(row["end_to_end_ms"] for row in direct_rows)
    a2a_stats = describe(row["end_to_end_ms"] for row in a2a_rows)
    policy_stats = describe(row["policy_delivery_ms"] for row in a2a_rows)
    full_cycle_stats = describe(row["full_cycle_ms"] for row in a2a_rows)
    internal_stats = describe(
        row["e2_control_internal_ms"]
        for row in a2a_rows
        if row["e2_control_internal_ms"] != ""
    )
    pre_stats = describe(pre_usage)
    post_stats = describe(post_usage)
    reduction_pct = (
        (pre_stats["mean"] - post_stats["mean"]) / pre_stats["mean"] * 100
        if pre_stats["mean"]
        else math.nan
    )
    direct_success = sum(bool(row["success"]) for row in direct_rows) / len(direct_rows) * 100
    a2a_success = sum(bool(row["success"]) for row in a2a_rows) / len(a2a_rows) * 100

    summary: Dict[str, Any] = {
        "metadata": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "trials_per_path": trials,
            "warmup_runs_excluded": 3,
            "kpi_samples_per_phase": kpi_samples,
            "testbed": "FlexRIC nearRT-RIC + built-in gNB emulator",
            "control_scope": "custom MAC action 42 with synthetic emulator mitigation",
        },
        "latency_ms": {
            "direct_rest": direct_stats,
            "a2a_state_to_control": a2a_stats,
            "a2a_policy_delivery": policy_stats,
            "a2a_policy_to_control_full_cycle": full_cycle_stats,
            "e2_control_internal": internal_stats,
            "a2a_overhead_mean_ms": a2a_stats["mean"] - direct_stats["mean"],
        },
        "success_rate_pct": {"direct_rest": direct_success, "a2a": a2a_success},
        "e2_indication_latency_us": describe(e2_latency),
        "closed_loop_prb_usage": {
            "before": pre_stats,
            "after": post_stats,
            "mean_reduction_pct": reduction_pct,
        },
        "routing_scalability": routing_rows,
        "final_health": health,
    }

    write_csv(output_dir / "latency_trials.csv", latency_rows)
    write_csv(output_dir / "kpi_effect.csv", kpi_rows)
    write_csv(output_dir / "routing_scalability.csv", routing_rows)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    report = f"""# FlexRIC A2A 30회 실험 결과

실험 환경: FlexRIC nearRT-RIC + 내장 gNB 에뮬레이터 1대
반복 횟수: 경로별 {trials}회, 제어 전후 KPI 각 {kpi_samples}개

## 지연시간 및 성공률

| 항목 | 평균 | 중앙값 | p95 | 표준편차 | 성공률 |
|---|---:|---:|---:|---:|---:|
| 직접 REST → E2 control | {direct_stats['mean']:.3f} ms | {direct_stats['median']:.3f} ms | {direct_stats['p95']:.3f} ms | {direct_stats['stdev']:.3f} ms | {direct_success:.1f}% |
| A2A 상태판단 → E2 control | {a2a_stats['mean']:.3f} ms | {a2a_stats['median']:.3f} ms | {a2a_stats['p95']:.3f} ms | {a2a_stats['stdev']:.3f} ms | {a2a_success:.1f}% |
| rAgent 정책 전달 | {policy_stats['mean']:.3f} ms | {policy_stats['median']:.3f} ms | {policy_stats['p95']:.3f} ms | {policy_stats['stdev']:.3f} ms | {a2a_success:.1f}% |
| rAgent 정책 생성 → E2 control 전체 | {full_cycle_stats['mean']:.3f} ms | {full_cycle_stats['median']:.3f} ms | {full_cycle_stats['p95']:.3f} ms | {full_cycle_stats['stdev']:.3f} ms | {a2a_success:.1f}% |
| FlexRIC 내부 E2 control | {internal_stats['mean']:.3f} ms | {internal_stats['median']:.3f} ms | {internal_stats['p95']:.3f} ms | {internal_stats['stdev']:.3f} ms | {a2a_success:.1f}% |

A2A 경로의 직접 REST 대비 평균 추가 지연은 **{summary['latency_ms']['a2a_overhead_mean_ms']:.3f} ms**이다.

## E2 indication 및 폐루프 효과

- E2 indication 지연: 평균 {summary['e2_indication_latency_us']['mean']:.1f} μs, p95 {summary['e2_indication_latency_us']['p95']:.1f} μs
- 제어 전 PRB 사용률 평균: {pre_stats['mean']:.4f}
- 제어 후 PRB 사용률 평균: {post_stats['mean']:.4f}
- 에뮬레이터 기반 평균 감소율: **{reduction_pct:.1f}%**

## Agent Card 라우팅 확장성

| Agent Card 수 | 평균 탐색시간 | p95 |
|---:|---:|---:|
"""
    for row in routing_rows:
        report += f"| {row['card_count']} | {row['mean_us']:.3f} μs | {row['p95_us']:.3f} μs |\n"
    report += """
## 해석 범위

- 지연시간과 성공률은 동일 호스트에서 실행된 프로토타입 수치이다.
- PRB 감소는 실제 무선 스케줄러가 아니라 control action 42에 반응하도록 만든 FlexRIC 에뮬레이터의 합성 효과이다.
- Agent Card 확장성은 실제 xApp 프로세스 수가 아닌 registry 내 카드 선형 탐색 비용이다.
"""
    (output_dir / "REPORT_RESULTS.md").write_text(report, encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=30)
    parser.add_argument("--kpi-samples", type=int, default=30)
    parser.add_argument("--routing-iterations", type=int, default=5000)
    args = parser.parse_args()
    summary = run(args.output_dir, args.trials, args.kpi_samples, args.routing_iterations)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
