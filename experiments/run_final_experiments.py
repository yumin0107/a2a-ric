import argparse
import csv
import json
import math
import os
import platform
import statistics
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable

import httpx


RAGENT_URL = os.getenv("RAGENT_URL", "http://127.0.0.1:8001").rstrip("/")
XAGENT_URL = os.getenv("XAGENT_URL", "http://127.0.0.1:8002").rstrip("/")
XAPP_URL = os.getenv("XAPP_URL", "http://127.0.0.1:8003").rstrip("/")
THROUGHPUT_XAPP_URL = os.getenv(
    "THROUGHPUT_XAPP_URL", "http://127.0.0.1:8101"
).rstrip("/")
ENERGY_XAPP_URL = os.getenv("ENERGY_XAPP_URL", "http://127.0.0.1:8102").rstrip("/")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://127.0.0.1:8000/v1").rstrip("/")


def percentile(values: Iterable[float], quantile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return math.nan
    position = (len(ordered) - 1) * quantile
    lower, upper = math.floor(position), math.ceil(position)
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
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def timed_post(
    client: httpx.Client, url: str, payload: Dict[str, Any] | None = None
) -> tuple[float, httpx.Response]:
    started = time.perf_counter_ns()
    response = client.post(url, json=payload)
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000.0
    response.raise_for_status()
    return elapsed_ms, response


def wait_for_samples(
    client: httpx.Client, count: int, timeout_sec: float = 15
) -> list[Dict[str, Any]]:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        response = client.get(f"{XAPP_URL}/demo/metrics")
        response.raise_for_status()
        samples = response.json()["samples"]
        if len(samples) >= count:
            return samples[-count:]
        time.sleep(0.05)
    raise TimeoutError(f"fewer than {count} E2 indication samples arrived")


def policy_envelope(policy: Dict[str, Any], trace_id: str) -> Dict[str, Any]:
    return {
        "msg_type": "ProposePolicy",
        "sender": "experiment-driver",
        "receiver": "xAgent",
        "trace_id": trace_id,
        "payload": {"policy": policy},
    }


def metrics_envelope(
    metrics: Dict[str, float],
    trace_id: str,
    conflict_mode: str = "priority_mutex",
) -> Dict[str, Any]:
    envelope = {
        "msg_type": "StateReport",
        "sender": "experiment-driver",
        "receiver": "xAgent",
        "trace_id": trace_id,
        "payload": {
            "source": "controlled_experiment",
            "metrics": metrics,
            "timestamp_ms": int(time.time() * 1000),
            "report_started_ns": time.perf_counter_ns(),
            "conflict_mode": conflict_mode,
        },
    }
    return envelope


def state_envelope(prb_usage: float, trace_id: str) -> Dict[str, Any]:
    return metrics_envelope(
        {"prb_usage": prb_usage, "e2_latency_us": 0.0, "ue_count": 2.0},
        trace_id,
    )


def semantic_cases() -> list[Dict[str, Any]]:
    definitions = [
        (
            "e2_mac_control",
            "suggest_prb_boost",
            "prb_usage",
            0.7,
            [
                "Relieve radio congestion",
                "Increase resources under high PRB load",
                "Control MAC load when the cell is busy",
                "Mitigate an overloaded cell",
                "Act on excessive PRB occupancy",
                "Reduce congestion through a PRB control action",
                "Boost MAC capacity after radio saturation",
                "Apply radio control for a congested sector",
                "Recover capacity when PRBs are overused",
                "Execute congestion relief at the MAC layer",
            ],
        ),
        (
            "kpi_reporting",
            "report_kpi",
            "e2_latency_us",
            500.0,
            [
                "Report latency KPI anomalies",
                "Observe transport delay",
                "Monitor E2 latency",
                "Generate a KPI report for high delay",
                "Track latency threshold violations",
                "Notify the operator of delayed indications",
                "Publish an alarm when E2 delay rises",
                "Watch indication latency without changing resources",
                "Create an observability report for transport delay",
                "Measure and report a slow E2 path",
            ],
        ),
        (
            "slice_optimization",
            "optimize_slice",
            "ue_count",
            3.0,
            [
                "Optimize slice allocation for many users",
                "Rebalance slice resources",
                "Adjust the network slice under load",
                "Improve slice capacity allocation",
                "Resize a slice when UE count rises",
                "Optimize slice resources for user demand",
                "Reallocate capacity among network slices",
                "Tune slice quotas for a growing UE population",
                "Change slice allocation under subscriber load",
                "Balance logical slice capacity for demand",
            ],
        ),
    ]
    cases: list[Dict[str, Any]] = []
    index = 0
    for capability, action, metric, threshold, objectives in definitions:
        for objective in objectives:
            index += 1
            should_trigger = index % 2 == 0
            value = threshold * (1.2 if should_trigger else 0.8)
            cases.append(
                {
                    "case_id": index,
                    "case_family": "registered_action",
                    "expected_capability": capability,
                    "expected_trigger": should_trigger,
                    "policy": {
                        "policy_id": f"semantic-{index}",
                        "objective": objective,
                        "constraints": {},
                        "rule": {
                            "type": "threshold",
                            "metric": metric,
                            "gt": threshold,
                            "action": action,
                        },
                    },
                    "metrics": {metric: value},
                }
            )

    open_vocabulary_definitions = [
        (
            "e2_mac_control",
            "prb_usage",
            0.7,
            [
                ("stabilize_air_interface", "Restore cell capacity during scheduler saturation"),
                ("relieve_scheduler_pressure", "Protect users when the sector becomes overloaded"),
                ("recover_air_capacity", "Respond to excessive channel occupancy"),
                ("ease_sector_contention", "Alleviate contention in a busy cell"),
                ("stabilize_scheduler", "Stabilize scheduling under sudden demand"),
                ("prevent_packet_blocking", "Reduce packet blocking caused by cell saturation"),
                ("handle_air_saturation", "Recover service when the air interface is heavily occupied"),
                ("rebalance_scheduler", "Improve service during scheduler overload"),
                ("protect_busy_sector", "Act when channel occupancy becomes excessive"),
                ("restore_radio_service", "Prevent degradation in a saturated sector"),
            ],
        ),
        (
            "kpi_reporting",
            "e2_latency_us",
            500.0,
            [
                ("emit_delay_alarm", "Alert operators to abnormal E2 delay"),
                ("inspect_transport_timing", "Detect unusual indication delivery time"),
                ("summarize_timing_anomaly", "Summarize slow message arrival patterns"),
                ("flag_stale_indications", "Identify stale RIC indications"),
                ("audit_e2_timing", "Audit whether E2 messages arrive late"),
                ("detect_path_slowness", "Detect a slow indication path"),
                ("notify_delay_breach", "Notify operators of excessive delivery time"),
                ("classify_timing_health", "Assess indication timing health"),
                ("track_message_age", "Quantify age of arriving E2 messages"),
                ("emit_timing_digest", "Produce a summary of indication delays"),
            ],
        ),
        (
            "slice_optimization",
            "ue_count",
            3.0,
            [
                ("adjust_tenant_quota", "Change capacity shares among service tenants"),
                ("rebalance_tenant_share", "Balance logical network shares across tenants"),
                ("protect_tenant_isolation", "Preserve isolation among service tenants under load"),
                ("resize_service_partition", "Resize a service partition as subscriber demand grows"),
                ("shift_tenant_capacity", "Move capacity between premium and best-effort tenants"),
                ("tune_logical_network", "Tune a logical network for changing demand"),
                ("adapt_service_quota", "Adapt service quotas to subscriber population"),
                ("rebalance_virtual_partition", "Balance capacity across virtual network partitions"),
                ("adjust_tenant_entitlement", "Change tenant entitlements when demand rises"),
                ("optimize_service_partition", "Improve capacity distribution among tenant partitions"),
            ],
        ),
    ]
    for capability, metric, threshold, cases_for_capability in open_vocabulary_definitions:
        for action, objective in cases_for_capability:
            index += 1
            should_trigger = index % 2 == 0
            value = threshold * (1.2 if should_trigger else 0.8)
            cases.append(
                {
                    "case_id": index,
                    "case_family": "open_vocabulary_holdout",
                    "expected_capability": capability,
                    "expected_trigger": should_trigger,
                    "policy": {
                        "policy_id": f"semantic-{index}",
                        "objective": objective,
                        "constraints": {},
                        "rule": {
                            "type": "threshold",
                            "metric": metric,
                            "gt": threshold,
                            "action": action,
                        },
                    },
                    "metrics": {metric: value},
                }
            )
    return cases


def run_semantic_evaluation(client: httpx.Client) -> list[Dict[str, Any]]:
    cards = [
        {
            "name": "MAC control xApp",
            "capabilities": ["e2_mac_control"],
            "_base_url": "http://mock-control",
        },
        {
            "name": "KPI monitor xApp",
            "capabilities": ["kpi_reporting"],
            "_base_url": "http://mock-monitor",
        },
        {
            "name": "Slice optimizer xApp",
            "capabilities": ["slice_optimization"],
            "_base_url": "http://mock-slice",
        },
    ]
    rows: list[Dict[str, Any]] = []
    for mode, routing_mode in (
        ("rule_only", "rule_only"),
        ("llm_rule", "llm_always"),
        ("adaptive_hybrid", "llm_on_rule_miss"),
    ):
        for case in semantic_cases():
            _, response = timed_post(
                client,
                f"{XAGENT_URL}/demo/evaluate_decision",
                {
                    "policy": case["policy"],
                    "metrics": case["metrics"],
                    "cards": cards,
                    "routing_mode": routing_mode,
                },
            )
            result = response.json()
            actual_capability = result["route"].get("required_capability")
            raw_capability = result["route"].get("llm_raw_capability", actual_capability)
            actual_trigger = bool(result["decision"].get("triggered"))
            rows.append(
                {
                    "mode": mode,
                    "case_id": case["case_id"],
                    "case_family": case["case_family"],
                    "expected_capability": case["expected_capability"],
                    "actual_capability": actual_capability,
                    "route_correct": actual_capability == case["expected_capability"],
                    "raw_capability": raw_capability,
                    "raw_route_correct": raw_capability == case["expected_capability"],
                    "expected_trigger": case["expected_trigger"],
                    "actual_trigger": actual_trigger,
                    "trigger_correct": actual_trigger == case["expected_trigger"],
                    "latency_ms": result["latency_ms"],
                    "route_source": result["route"].get("source"),
                    "decision_source": result["decision"].get("source"),
                    "fallback": bool(
                        result["route"].get("llm_error")
                        or result["decision"].get("llm_error")
                    ),
                    "guard_applied": bool(result["route"].get("guard_applied", False)),
                }
            )
    return rows


def run_latency_comparison(client: httpx.Client, trials: int) -> list[Dict[str, Any]]:
    rows: list[Dict[str, Any]] = []
    for mode in ("direct_rest", "rule_only_a2a", "llm_rule_a2a"):
        for trial in range(1, trials + 1):
            if mode == "direct_rest":
                elapsed, response = timed_post(
                    client,
                    f"{XAPP_URL}/a2a/action/execute",
                    {"action": "suggest_prb_boost", "source": "direct_rest"},
                )
                body = response.json()
                rows.append(
                    {
                        "mode": mode,
                        "trial": trial,
                        "policy_delivery_ms": 0.0,
                        "state_to_control_ms": elapsed,
                        "success": bool(body.get("control_success")),
                        "fallback": False,
                    }
                )
                continue

            client.post(f"{XAGENT_URL}/demo/reset").raise_for_status()
            client.post(f"{RAGENT_URL}/demo/reset").raise_for_status()
            use_llm = mode == "llm_rule_a2a"
            policy = {
                "policy_id": f"latency-{mode}-{trial}",
                "objective": "Mitigate congestion with MAC PRB control",
                "constraints": {"use_llm": use_llm},
                "rule": {
                    "type": "threshold",
                    "metric": "prb_usage",
                    "gt": 0.7,
                    "action": "suggest_prb_boost",
                },
            }
            policy_ms, policy_response = timed_post(
                client,
                f"{XAGENT_URL}/a2a/policy/propose",
                policy_envelope(policy, str(uuid.uuid4())),
            )
            policy_body = policy_response.json()
            state_ms, state_response = timed_post(
                client,
                f"{XAGENT_URL}/a2a/state/report",
                state_envelope(0.9, str(uuid.uuid4())),
            )
            triggered = state_response.json().get("triggered", [])
            success = bool(triggered and triggered[0].get("result", {}).get("control_success"))
            rows.append(
                {
                    "mode": mode,
                    "trial": trial,
                    "policy_delivery_ms": policy_ms,
                    "state_to_control_ms": state_ms,
                    "success": success,
                    "fallback": policy_body.get("route", {}).get("source") == "fallback"
                    or bool(triggered and triggered[0].get("decision_source") == "fallback"),
                }
            )
    return rows


def run_ragent_generation(client: httpx.Client, trials: int = 5) -> list[Dict[str, Any]]:
    rows: list[Dict[str, Any]] = []
    for trial in range(1, trials + 1):
        client.post(f"{XAGENT_URL}/demo/reset").raise_for_status()
        elapsed, response = timed_post(
            client,
            f"{RAGENT_URL}/demo/run_once",
            {
                "operator_intent": "Mitigate congestion when PRB usage exceeds 0.70 using MAC control.",
                "use_llm": True,
            },
        )
        body = response.json()
        rows.append(
            {
                "trial": trial,
                "latency_ms": elapsed,
                "success": bool(body.get("ok")),
                "fallback": bool(body.get("llm_fallback")),
                "policy_id": body.get("policy", {}).get("policy_id"),
            }
        )
    return rows


def run_scalability(client: httpx.Client, repetitions: int = 30) -> list[Dict[str, Any]]:
    rows: list[Dict[str, Any]] = []
    for card_count in (1, 10, 50, 100):
        for repetition in range(1, repetitions + 1):
            started = time.perf_counter_ns()
            response = client.get(
                f"{XAGENT_URL}/demo/benchmark_routing",
                params={"card_count": card_count, "iterations": 1000},
            )
            request_ms = (time.perf_counter_ns() - started) / 1_000_000.0
            response.raise_for_status()
            body = response.json()
            rows.append(
                {
                    "card_count": card_count,
                    "repetition": repetition,
                    "request_ms": request_ms,
                    "lookup_mean_us": body["mean_us"],
                    "lookup_p95_us": body["p95_us"],
                    "selected_correctly": body["selected"] == f"http://mock-{card_count - 1}",
                }
            )
    return rows


def run_conflict_and_constraint_tests(client: httpx.Client) -> list[Dict[str, Any]]:
    rows: list[Dict[str, Any]] = []
    for index in range(1, 21):
        high_is_boost = index % 2 == 0
        boost_priority = 10 if high_is_boost else 1
        throttle_priority = 1 if high_is_boost else 10
        expected = "boost" if high_is_boost else "throttle"
        _, response = timed_post(
            client,
            f"{XAGENT_URL}/demo/evaluate_conflicts",
            {
                "events": [
                    {
                        "policy_id": "boost",
                        "action": "suggest_prb_boost",
                        "constraints": {"priority": boost_priority},
                    },
                    {
                        "policy_id": "throttle",
                        "action": "throttle_prb",
                        "constraints": {"priority": throttle_priority},
                    },
                ]
            },
        )
        body = response.json()
        actual = body["selected"][0]["policy_id"]
        rows.append(
            {
                "category": "conflict",
                "case_id": index,
                "expected": expected,
                "actual": actual,
                "correct": actual == expected and len(body["suppressed"]) == 1,
            }
        )

    constraint_cases = [
        ("cooldown_block", {"cooldown_sec": 10}, 2, [], False, "cooldown"),
        ("cooldown_allow", {"cooldown_sec": 10}, 11, [], True, None),
        ("rate_block", {"max_action_rate_per_min": 3}, None, [5, 10, 20], False, "rate_limit"),
        ("rate_allow", {"max_action_rate_per_min": 3}, None, [5, 10], True, None),
    ]
    for repeat in range(1, 6):
        for name, constraints, last_age, prior_ages, expected_allowed, expected_reason in constraint_cases:
            _, response = timed_post(
                client,
                f"{XAGENT_URL}/demo/evaluate_constraints",
                {
                    "constraints": constraints,
                    "last_action_age_sec": last_age,
                    "prior_ages_sec": prior_ages,
                },
            )
            body = response.json()
            rows.append(
                {
                    "category": "constraint",
                    "case_id": f"{name}-{repeat}",
                    "expected": expected_reason or "allowed",
                    "actual": body.get("reason") or "allowed",
                    "correct": body.get("allowed") == expected_allowed
                    and body.get("reason") == expected_reason,
                }
            )
    return rows


def run_integrated_multi_xapp_conflicts(
    client: httpx.Client, trials: int = 20
) -> list[Dict[str, Any]]:
    """Compare no arbitration vs priority/mutex rules using real xApp processes."""
    rows: list[Dict[str, Any]] = []
    for trial in range(1, trials + 1):
        throughput_wins = trial % 2 == 1
        expected = "throughput" if throughput_wins else "energy"
        for condition, conflict_mode in (
            ("before_no_arbitration", "disabled_baseline"),
            ("after_priority_mutex", "priority_mutex"),
        ):
            client.post(f"{XAGENT_URL}/demo/reset").raise_for_status()
            client.post(f"{THROUGHPUT_XAPP_URL}/demo/reset").raise_for_status()
            client.post(f"{ENERGY_XAPP_URL}/demo/reset").raise_for_status()
            definitions = [
                (
                    "throughput",
                    "Increase throughput under high radio load",
                    "boost_throughput",
                    10 if throughput_wins else 1,
                ),
                (
                    "energy",
                    "Reduce energy use under high radio load",
                    "reduce_energy",
                    1 if throughput_wins else 10,
                ),
            ]
            routes: Dict[str, str | None] = {}
            accepted = True
            for name, objective, action, priority in definitions:
                policy = {
                    "policy_id": f"integrated-{condition}-{name}-{trial}",
                    "objective": objective,
                    "constraints": {
                        "use_llm": False,
                        "priority": priority,
                        "mutex_group": "radio_resource",
                    },
                    "rule": {
                        "type": "threshold",
                        "metric": "radio_load",
                        "gt": 0.7,
                        "action": action,
                    },
                }
                _, response = timed_post(
                    client,
                    f"{XAGENT_URL}/a2a/policy/propose",
                    policy_envelope(policy, str(uuid.uuid4())),
                )
                body = response.json()
                accepted = accepted and bool(body.get("ok"))
                routes[name] = body.get("route", {}).get("xapp_url")

            elapsed_ms, response = timed_post(
                client,
                f"{XAGENT_URL}/a2a/state/report",
                metrics_envelope(
                    {"radio_load": 0.9},
                    str(uuid.uuid4()),
                    conflict_mode=conflict_mode,
                ),
            )
            body = response.json()
            triggered = body.get("triggered", [])
            suppressed = body.get("suppressed", [])
            throughput_calls = len(
                client.get(f"{THROUGHPUT_XAPP_URL}/demo/status").json()["executions"]
            )
            energy_calls = len(
                client.get(f"{ENERGY_XAPP_URL}/demo/status").json()["executions"]
            )
            collision = throughput_calls > 0 and energy_calls > 0
            loser_executed = energy_calls > 0 if throughput_wins else throughput_calls > 0
            expected_calls = (1, 0) if throughput_wins else (0, 1)
            correct_exclusive = (
                accepted
                and routes["throughput"] == THROUGHPUT_XAPP_URL
                and routes["energy"] == ENERGY_XAPP_URL
                and (throughput_calls, energy_calls) == expected_calls
                and len(triggered) == 1
                and all(item.get("status") == "done" for item in triggered)
            )
            actual = (
                "both"
                if collision
                else "throughput"
                if throughput_calls
                else "energy"
                if energy_calls
                else "none"
            )
            rows.append(
                {
                    "condition": condition,
                    "policy_type": "rule_threshold_plus_priority_mutex",
                    "llm_enabled": False,
                    "trial": trial,
                    "expected_winner": expected,
                    "actual_execution": actual,
                    "throughput_priority": 10 if throughput_wins else 1,
                    "energy_priority": 1 if throughput_wins else 10,
                    "throughput_route": routes["throughput"],
                    "energy_route": routes["energy"],
                    "throughput_calls": throughput_calls,
                    "energy_calls": energy_calls,
                    "actions_per_state": throughput_calls + energy_calls,
                    "triggered_count": len(triggered),
                    "suppressed_count": len(suppressed),
                    "collision": collision,
                    "loser_executed": loser_executed,
                    "correct_exclusive_winner": correct_exclusive,
                    "latency_ms": elapsed_ms,
                    "correct": correct_exclusive,
                }
            )
    return rows


def window_mean(samples: list[Dict[str, Any]], metric: str) -> float:
    usable = [sample for sample in samples if float(sample.get("ue_count", 0)) > 0]
    selected = usable or samples
    return statistics.mean(float(sample[metric]) for sample in selected)


def run_effect_episodes(
    client: httpx.Client, episodes: int, samples_per_window: int
) -> list[Dict[str, Any]]:
    rows: list[Dict[str, Any]] = []
    client.post(f"{XAPP_URL}/demo/auto_report", json={"enabled": False}).raise_for_status()

    client.post(f"{XAPP_URL}/demo/reset_metrics").raise_for_status()
    baseline = wait_for_samples(client, episodes * samples_per_window * 2, timeout_sec=60)
    for episode in range(episodes):
        offset = episode * samples_per_window * 2
        before = baseline[offset : offset + samples_per_window]
        after = baseline[offset + samples_per_window : offset + samples_per_window * 2]
        rows.append(
            {
                "condition": "no_control",
                "episode": episode + 1,
                "before_mean": window_mean(before, "prb_usage"),
                "after_mean": window_mean(after, "prb_usage"),
                "throughput_before": window_mean(before, "throughput_proxy"),
                "throughput_after": window_mean(after, "throughput_proxy"),
                "queue_delay_before_ms": window_mean(before, "queue_delay_proxy_ms"),
                "queue_delay_after_ms": window_mean(after, "queue_delay_proxy_ms"),
                "retransmission_before": window_mean(before, "retransmission_proxy"),
                "retransmission_after": window_mean(after, "retransmission_proxy"),
            }
        )

    for episode in range(1, episodes + 1):
        time.sleep(5.2)
        client.post(f"{XAPP_URL}/demo/reset_metrics").raise_for_status()
        before = wait_for_samples(client, samples_per_window)
        _, response = timed_post(
            client,
            f"{XAPP_URL}/a2a/action/execute",
            {"action": "suggest_prb_boost", "source": "effect_episode"},
        )
        control_success = bool(response.json().get("control_success"))
        client.post(f"{XAPP_URL}/demo/reset_metrics").raise_for_status()
        after = wait_for_samples(client, samples_per_window)
        rows.append(
            {
                "condition": "control",
                "episode": episode,
                "before_mean": window_mean(before, "prb_usage"),
                "after_mean": window_mean(after, "prb_usage"),
                "throughput_before": window_mean(before, "throughput_proxy"),
                "throughput_after": window_mean(after, "throughput_proxy"),
                "queue_delay_before_ms": window_mean(before, "queue_delay_proxy_ms"),
                "queue_delay_after_ms": window_mean(after, "queue_delay_proxy_ms"),
                "retransmission_before": window_mean(before, "retransmission_proxy"),
                "retransmission_after": window_mean(after, "retransmission_proxy"),
                "control_success": control_success,
            }
        )
    for row in rows:
        row["change"] = row["after_mean"] - row["before_mean"]
        row["reduction_pct"] = (
            (row["before_mean"] - row["after_mean"]) / row["before_mean"] * 100
            if row["before_mean"]
            else math.nan
        )
        row["throughput_change_pct"] = (
            (row["throughput_after"] - row["throughput_before"])
            / row["throughput_before"]
            * 100
            if row["throughput_before"]
            else math.nan
        )
        row["queue_delay_reduction_pct"] = (
            (row["queue_delay_before_ms"] - row["queue_delay_after_ms"])
            / row["queue_delay_before_ms"]
            * 100
            if row["queue_delay_before_ms"]
            else math.nan
        )
        row["retransmission_reduction_pct"] = (
            (row["retransmission_before"] - row["retransmission_after"])
            / row["retransmission_before"]
            * 100
            if row["retransmission_before"]
            else math.nan
        )
    return rows


def run_automatic_closed_loop(client: httpx.Client, target_actions: int) -> list[Dict[str, Any]]:
    client.post(f"{XAPP_URL}/demo/auto_report", json={"enabled": False}).raise_for_status()
    client.post(f"{XAGENT_URL}/demo/reset").raise_for_status()
    client.post(f"{RAGENT_URL}/demo/reset").raise_for_status()
    policy = {
        "policy_id": "automatic-e2-loop",
        "objective": "Exercise the complete automatic E2 closed loop",
        "constraints": {"use_llm": False, "cooldown_sec": 0.05},
        "rule": {
            "type": "threshold",
            "metric": "prb_usage",
            "gt": -0.1,
            "action": "suggest_prb_boost",
        },
    }
    _, response = timed_post(
        client,
        f"{XAGENT_URL}/a2a/policy/propose",
        policy_envelope(policy, "automatic-policy"),
    )
    if not response.json().get("ok"):
        raise RuntimeError(f"automatic-loop policy rejected: {response.text}")
    client.post(f"{XAPP_URL}/demo/auto_report", json={"enabled": True}).raise_for_status()
    deadline = time.monotonic() + 60
    actions: list[Dict[str, Any]] = []
    while time.monotonic() < deadline:
        actions = client.get(f"{XAGENT_URL}/demo/status").json().get("actions", [])
        if len(actions) >= target_actions:
            break
        time.sleep(0.1)
    client.post(f"{XAPP_URL}/demo/auto_report", json={"enabled": False}).raise_for_status()
    if len(actions) < target_actions:
        raise TimeoutError(f"only {len(actions)} automatic actions completed")
    return [
        {
            "sequence": index,
            "trace_id": action.get("trace_id"),
            "source": action.get("source"),
            "state_to_done_ms": action.get("state_to_done_ms"),
            "control_success": action.get("status") == "done",
            "feedback_status": action.get("status"),
        }
        for index, action in enumerate(actions[-target_actions:], 1)
    ]


def summarize(
    semantic: list[Dict[str, Any]],
    latency: list[Dict[str, Any]],
    generation: list[Dict[str, Any]],
    scaling: list[Dict[str, Any]],
    conflicts: list[Dict[str, Any]],
    integrated_conflicts: list[Dict[str, Any]],
    effects: list[Dict[str, Any]],
    closed_loop: list[Dict[str, Any]],
    model: str,
) -> Dict[str, Any]:
    semantic_summary: Dict[str, Any] = {}

    def summarize_semantic_subset(subset: list[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "n": len(subset),
            "routing_accuracy_pct": sum(row["route_correct"] for row in subset)
            / len(subset)
            * 100,
            "raw_routing_accuracy_pct": sum(
                row["raw_route_correct"] for row in subset
            )
            / len(subset)
            * 100,
            "trigger_accuracy_pct": sum(row["trigger_correct"] for row in subset)
            / len(subset)
            * 100,
            "fallback_rate_pct": sum(row["fallback"] for row in subset)
            / len(subset)
            * 100,
            "guard_rate_pct": sum(row["guard_applied"] for row in subset)
            / len(subset)
            * 100,
            "llm_route_rate_pct": sum(row["route_source"] == "llm" for row in subset)
            / len(subset)
            * 100,
            "latency_ms": describe(row["latency_ms"] for row in subset),
        }

    for mode in ("rule_only", "llm_rule", "adaptive_hybrid"):
        subset = [row for row in semantic if row["mode"] == mode]
        semantic_summary[mode] = summarize_semantic_subset(subset)
    semantic_summary["by_family"] = {}
    for family in ("registered_action", "open_vocabulary_holdout"):
        semantic_summary["by_family"][family] = {}
        for mode in ("rule_only", "llm_rule", "adaptive_hybrid"):
            subset = [
                row
                for row in semantic
                if row["mode"] == mode and row["case_family"] == family
            ]
            semantic_summary["by_family"][family][mode] = summarize_semantic_subset(
                subset
            )

    latency_summary: Dict[str, Any] = {}
    for mode in ("direct_rest", "rule_only_a2a", "llm_rule_a2a"):
        subset = [row for row in latency if row["mode"] == mode]
        latency_summary[mode] = {
            "state_to_control_ms": describe(row["state_to_control_ms"] for row in subset),
            "policy_delivery_ms": describe(row["policy_delivery_ms"] for row in subset),
            "success_rate_pct": sum(row["success"] for row in subset) / len(subset) * 100,
            "fallback_rate_pct": sum(row["fallback"] for row in subset) / len(subset) * 100,
        }

    scaling_summary = []
    for count in (1, 10, 50, 100):
        subset = [row for row in scaling if row["card_count"] == count]
        scaling_summary.append(
            {
                "card_count": count,
                "request_ms": describe(row["request_ms"] for row in subset),
                "lookup_mean_us": statistics.mean(row["lookup_mean_us"] for row in subset),
                "selection_accuracy_pct": sum(row["selected_correctly"] for row in subset) / len(subset) * 100,
            }
        )

    effect_summary: Dict[str, Any] = {}
    for condition in ("no_control", "control"):
        subset = [row for row in effects if row["condition"] == condition]
        effect_summary[condition] = {
            "episodes": len(subset),
            "before": describe(row["before_mean"] for row in subset),
            "after": describe(row["after_mean"] for row in subset),
            "change": describe(row["change"] for row in subset),
            "mean_reduction_pct": statistics.mean(row["reduction_pct"] for row in subset),
            "throughput_before": describe(row["throughput_before"] for row in subset),
            "throughput_after": describe(row["throughput_after"] for row in subset),
            "mean_throughput_change_pct": statistics.mean(
                row["throughput_change_pct"] for row in subset
            ),
            "queue_delay_before_ms": describe(
                row["queue_delay_before_ms"] for row in subset
            ),
            "queue_delay_after_ms": describe(
                row["queue_delay_after_ms"] for row in subset
            ),
            "mean_queue_delay_reduction_pct": statistics.mean(
                row["queue_delay_reduction_pct"] for row in subset
            ),
            "retransmission_before": describe(
                row["retransmission_before"] for row in subset
            ),
            "retransmission_after": describe(
                row["retransmission_after"] for row in subset
            ),
            "mean_retransmission_reduction_pct": statistics.mean(
                row["retransmission_reduction_pct"] for row in subset
            ),
        }

    integrated_summary: Dict[str, Any] = {
        "policy": {
            "trigger": "deterministic threshold: radio_load > 0.7",
            "arbitration": "deterministic mutex_group + numeric priority",
            "llm_enabled": False,
            "priorities": "10 (winner) vs 1 (loser), alternating xApp",
        }
    }
    for condition in ("before_no_arbitration", "after_priority_mutex"):
        subset = [
            row for row in integrated_conflicts if row["condition"] == condition
        ]
        integrated_summary[condition] = {
            "n": len(subset),
            "collision_rate_pct": sum(row["collision"] for row in subset)
            / len(subset)
            * 100,
            "loser_execution_rate_pct": sum(row["loser_executed"] for row in subset)
            / len(subset)
            * 100,
            "correct_exclusive_winner_pct": sum(
                row["correct_exclusive_winner"] for row in subset
            )
            / len(subset)
            * 100,
            "mean_actions_per_state": statistics.mean(
                row["actions_per_state"] for row in subset
            ),
            "mean_suppressed_per_state": statistics.mean(
                row["suppressed_count"] for row in subset
            ),
            "latency_ms": describe(row["latency_ms"] for row in subset),
        }
    before_conflict = integrated_summary["before_no_arbitration"]
    after_conflict = integrated_summary["after_priority_mutex"]
    integrated_summary["improvement"] = {
        "collision_rate_reduction_pp": before_conflict["collision_rate_pct"]
        - after_conflict["collision_rate_pct"],
        "loser_execution_reduction_pp": before_conflict[
            "loser_execution_rate_pct"
        ]
        - after_conflict["loser_execution_rate_pct"],
        "correct_winner_gain_pp": after_conflict["correct_exclusive_winner_pct"]
        - before_conflict["correct_exclusive_winner_pct"],
        "action_count_reduction_pct": (
            before_conflict["mean_actions_per_state"]
            - after_conflict["mean_actions_per_state"]
        )
        / before_conflict["mean_actions_per_state"]
        * 100,
    }

    return {
        "metadata": {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "llm_model": model,
            "testbed": "FlexRIC nearRT-RIC + built-in gNB emulator",
        },
        "automatic_closed_loop": {
            "n": len(closed_loop),
            "success_rate_pct": sum(row["control_success"] for row in closed_loop) / len(closed_loop) * 100,
            "state_to_done_ms": describe(row["state_to_done_ms"] for row in closed_loop),
        },
        "semantic_decision": semantic_summary,
        "latency": latency_summary,
        "ragent_llm_policy_generation": {
            "n": len(generation),
            "success_rate_pct": sum(row["success"] for row in generation) / len(generation) * 100,
            "fallback_rate_pct": sum(row["fallback"] for row in generation) / len(generation) * 100,
            "latency_ms": describe(row["latency_ms"] for row in generation),
        },
        "routing_scalability": scaling_summary,
        "safety": {
            "n": len(conflicts),
            "accuracy_pct": sum(row["correct"] for row in conflicts) / len(conflicts) * 100,
        },
        "integrated_multi_xapp_conflict": integrated_summary,
        "closed_loop_effect": effect_summary,
    }


def write_report(path: Path, summary: Dict[str, Any]) -> None:
    automatic = summary["automatic_closed_loop"]
    semantic = summary["semantic_decision"]
    latency = summary["latency"]
    generation = summary["ragent_llm_policy_generation"]
    effect = summary["closed_loop_effect"]
    integrated = summary["integrated_multi_xapp_conflict"]
    lines = [
        "# 최종보고서용 최소 실험 결과",
        "",
        f"- LLM: `{summary['metadata']['llm_model']}`",
        f"- 테스트베드: {summary['metadata']['testbed']}",
        "",
        "## 1. 실제 자동 폐루프",
        "",
        "| 반복 | 성공률 | 평균 지연 | p95 |",
        "|---:|---:|---:|---:|",
        f"| {automatic['n']} | {automatic['success_rate_pct']:.1f}% | {automatic['state_to_done_ms']['mean']:.3f} ms | {automatic['state_to_done_ms']['p95']:.3f} ms |",
        "",
        "## 2. 의미 기반 라우팅·트리거",
        "",
        "| 시험군 | 방식 | 라우팅 정확도 | 트리거 정확도 | 평균 지연 |",
        "|---|---|---:|---:|---:|",
    ]
    for family, family_label in (
        ("registered_action", "등록 action"),
        ("open_vocabulary_holdout", "미등록 표현 hold-out"),
    ):
        for mode, mode_label in (
            ("rule_only", "Rule-only"),
            ("llm_rule", "LLM always"),
            ("adaptive_hybrid", "Adaptive hybrid"),
        ):
            item = semantic["by_family"][family][mode]
            lines.append(
                f"| {family_label} | {mode_label} | {item['routing_accuracy_pct']:.1f}% | {item['trigger_accuracy_pct']:.1f}% | {item['latency_ms']['mean']:.3f} ms |"
            )
    registered_rule = semantic["by_family"]["registered_action"]["rule_only"]
    registered_llm = semantic["by_family"]["registered_action"]["llm_rule"]
    holdout_rule = semantic["by_family"]["open_vocabulary_holdout"]["rule_only"]
    holdout_llm = semantic["by_family"]["open_vocabulary_holdout"]["llm_rule"]
    llm_overall = semantic["llm_rule"]
    adaptive_overall = semantic["adaptive_hybrid"]
    lines += [
        "",
        f"등록 action에서는 LLM의 정확도 이득이 {registered_llm['routing_accuracy_pct'] - registered_rule['routing_accuracy_pct']:+.1f}%p인 반면 평균 지연은 {registered_llm['latency_ms']['mean'] - registered_rule['latency_ms']['mean']:+.3f} ms 증가했다. 미등록 표현 hold-out에서는 정확도가 {holdout_rule['routing_accuracy_pct']:.1f}%에서 {holdout_llm['routing_accuracy_pct']:.1f}%로 **{holdout_llm['routing_accuracy_pct'] - holdout_rule['routing_accuracy_pct']:+.1f}%p** 개선됐고, 평균 지연은 {holdout_llm['latency_ms']['mean'] - holdout_rule['latency_ms']['mean']:+.3f} ms 증가했다.",
        f"Adaptive hybrid는 rule lookup 성공 시 LLM을 생략하고 실패 시에만 호출한다. 전체 60건에서 항상 LLM은 정확도 {llm_overall['routing_accuracy_pct']:.1f}%/평균 {llm_overall['latency_ms']['mean']:.3f} ms, Adaptive는 {adaptive_overall['routing_accuracy_pct']:.1f}%/{adaptive_overall['latency_ms']['mean']:.3f} ms였다. 즉 LLM 호출을 60회에서 30회로 줄여 평균 지연을 {(llm_overall['latency_ms']['mean'] - adaptive_overall['latency_ms']['mean']) / llm_overall['latency_ms']['mean'] * 100:.1f}% 줄이는 대신 정확도가 {adaptive_overall['routing_accuracy_pct'] - llm_overall['routing_accuracy_pct']:+.1f}%p 변했다.",
        "",
        "## 3. 실행 경로 지연시간",
        "",
        "| 방식 | 평균 | p95 | 성공률 |",
        "|---|---:|---:|---:|",
    ]
    for mode, label in (
        ("direct_rest", "Direct REST"),
        ("rule_only_a2a", "Rule-only A2A"),
        ("llm_rule_a2a", "LLM+Rule A2A"),
    ):
        item = latency[mode]
        lines.append(
            f"| {label} | {item['state_to_control_ms']['mean']:.3f} ms | {item['state_to_control_ms']['p95']:.3f} ms | {item['success_rate_pct']:.1f}% |"
        )
    lines += [
        "",
        f"rAgent LLM 정책 생성·전달은 {generation['n']}회 중 성공률 {generation['success_rate_pct']:.1f}%, fallback {generation['fallback_rate_pct']:.1f}%, 평균 {generation['latency_ms']['mean']:.3f} ms였다.",
        "",
        "## 4. Agent Card 확장성",
        "",
        "| 카드 수 | API 평균 지연 | 내부 탐색 평균 | 선택 정확도 |",
        "|---:|---:|---:|---:|",
    ]
    for item in summary["routing_scalability"]:
        lines.append(
            f"| {item['card_count']} | {item['request_ms']['mean']:.3f} ms | {item['lookup_mean_us']:.3f} μs | {item['selection_accuracy_pct']:.1f}% |"
        )
    lines += [
        "",
        "## 5. 다중 xApp 충돌·제약조건",
        "",
        f"우선순위 충돌, cooldown, rate-limit 총 {summary['safety']['n']}개 사례의 판정 정확도는 **{summary['safety']['accuracy_pct']:.1f}%**였다.",
        "",
        "충돌 실험에서는 LLM을 사용하지 않고, 두 정책 모두 `radio_load > 0.7`에서 trigger되도록 했다. 동일 `radio_resource` mutex group에서 priority 10 정책을 priority 1보다 먼저 선택하는 deterministic rule-based arbiter를 사용했다.",
        "",
        "| 지표 | 완화 전: arbiter 없음 | 완화 후: priority+mutex | 개선 |",
        "|---|---:|---:|---:|",
        f"| 충돌 발생률 | {integrated['before_no_arbitration']['collision_rate_pct']:.1f}% | {integrated['after_priority_mutex']['collision_rate_pct']:.1f}% | -{integrated['improvement']['collision_rate_reduction_pp']:.1f}%p |",
        f"| 하위 우선순위 xApp 오실행률 | {integrated['before_no_arbitration']['loser_execution_rate_pct']:.1f}% | {integrated['after_priority_mutex']['loser_execution_rate_pct']:.1f}% | -{integrated['improvement']['loser_execution_reduction_pp']:.1f}%p |",
        f"| 올바른 단일 승자율 | {integrated['before_no_arbitration']['correct_exclusive_winner_pct']:.1f}% | {integrated['after_priority_mutex']['correct_exclusive_winner_pct']:.1f}% | +{integrated['improvement']['correct_winner_gain_pp']:.1f}%p |",
        f"| 상태당 실행 action 수 | {integrated['before_no_arbitration']['mean_actions_per_state']:.2f} | {integrated['after_priority_mutex']['mean_actions_per_state']:.2f} | -{integrated['improvement']['action_count_reduction_pct']:.1f}% |",
        f"| 상태→실행 완료 평균 지연 | {integrated['before_no_arbitration']['latency_ms']['mean']:.3f} ms | {integrated['after_priority_mutex']['latency_ms']['mean']:.3f} ms | {integrated['after_priority_mutex']['latency_ms']['mean'] - integrated['before_no_arbitration']['latency_ms']['mean']:+.3f} ms |",
        "",
        "완화 후 지연 감소는 arbiter 연산 자체의 가속 효과가 아니라, 충돌한 두 xApp을 순차 실행하던 경로에서 하위 우선순위 xApp 실행과 feedback 1회를 제거한 결과다.",
        "",
        "## 6. 에뮬레이터 QoS 대리지표 제어 효과",
        "",
        "| 조건 | episode | PRB 전→후 | 처리량 전→후 | 큐 지연(ms) 전→후 | 재전송 전→후 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for condition, label in (("no_control", "제어 없음"), ("control", "E2 control")):
        item = effect[condition]
        lines.append(
            f"| {label} | {item['episodes']} | {item['before']['mean']:.4f}→{item['after']['mean']:.4f} | {item['throughput_before']['mean']:.4f}→{item['throughput_after']['mean']:.4f} | {item['queue_delay_before_ms']['mean']:.2f}→{item['queue_delay_after_ms']['mean']:.2f} | {item['retransmission_before']['mean']:.4f}→{item['retransmission_after']['mean']:.4f} |"
        )
    control_effect = effect["control"]
    lines += [
        "",
        f"E2 control episode별 평균 변화율은 처리량 **+{control_effect['mean_throughput_change_pct']:.1f}%**, 큐 지연 **-{control_effect['mean_queue_delay_reduction_pct']:.1f}%**, 재전송 **-{control_effect['mean_retransmission_reduction_pct']:.1f}%**였다.",
        "",
        "## 해석 범위",
        "",
        "- 동일 호스트에서 수행한 프로토타입 실험이다.",
        "- Rule-only baseline은 고정 action→capability table과 제한된 keyword 규칙이다. 의미 라우팅은 등록 action 30건과 table에서 제외한 미등록 action/intent 표현 hold-out 30건으로 분리했다. LLM은 action·objective·metric 전체 문맥을 사용하므로 hold-out 결과는 수동 rule coverage 밖의 일반화 성능을 뜻하며, LLM의 보편적 우월성을 뜻하지 않는다.",
        "- Direct REST는 정책 판단과 feedback을 생략한 지연 하한 기준으로, 동일 기능 간 우열 비교가 아니라 agent orchestration의 추가 비용을 보여준다.",
        "- PRB·처리량·큐 지연·재전송 변화는 custom MAC action 42에 반응하도록 패치한 gNB 에뮬레이터의 합성 대리지표다. 실제 UE/트래픽의 Mbps·종단 지연 QoS를 측정한 결과는 아니다.",
        "- 의미 판단은 단순·균형화된 총 60개 사례의 소규모 검증이며, 확장성 실험은 mock Agent Card registry를 사용해 실제 xApp 프로세스 100개를 실행한 결과가 아니다.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> Dict[str, Any]:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120) as client:
        model_response = client.get(f"{LLM_BASE_URL}/models")
        model_response.raise_for_status()
        model = model_response.json()["data"][0]["id"]
        for url in (f"{RAGENT_URL}/card", f"{XAGENT_URL}/card", f"{XAPP_URL}/healthz"):
            client.get(url).raise_for_status()

        effects = run_effect_episodes(client, args.effect_episodes, args.effect_samples)
        semantic = run_semantic_evaluation(client)
        latency = run_latency_comparison(client, args.latency_trials)
        generation = run_ragent_generation(client, args.generation_trials)
        scaling = run_scalability(client, args.scaling_repetitions)
        conflicts = run_conflict_and_constraint_tests(client)
        integrated_conflicts = run_integrated_multi_xapp_conflicts(client)
        closed_loop = run_automatic_closed_loop(client, args.closed_loop_actions)

    datasets = {
        "semantic_cases.csv": semantic,
        "latency_modes.csv": latency,
        "ragent_generation.csv": generation,
        "routing_scalability.csv": scaling,
        "conflict_constraints.csv": conflicts,
        "integrated_multi_xapp_conflicts.csv": integrated_conflicts,
        "prb_effect_episodes.csv": effects,
        "automatic_closed_loop.csv": closed_loop,
    }
    for name, rows in datasets.items():
        write_csv(args.output_dir / name, rows)
    summary = summarize(
        semantic,
        latency,
        generation,
        scaling,
        conflicts,
        integrated_conflicts,
        effects,
        closed_loop,
        model,
    )
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_report(args.output_dir / "FINAL_EXPERIMENT_RESULTS.md", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--latency-trials", type=int, default=15)
    parser.add_argument("--generation-trials", type=int, default=5)
    parser.add_argument("--scaling-repetitions", type=int, default=30)
    parser.add_argument("--effect-episodes", type=int, default=10)
    parser.add_argument("--effect-samples", type=int, default=10)
    parser.add_argument("--closed-loop-actions", type=int, default=30)
    args = parser.parse_args()
    print(json.dumps(run(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
