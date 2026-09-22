import os
import statistics
import time
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException

from common.llm_openai import chat_json
from common.schemas import A2AEnvelope, AgentCard, FeedbackReport, Policy, StateReport


app = FastAPI(title="xAgent (LLM Router)")

RAGENT_URL = os.getenv("RAGENT_URL", "http://ragent:8001").rstrip("/")
XAPP_MONITOR_URL = os.getenv("XAPP_MONITOR_URL", "http://xapp_monitor:8003").rstrip("/")
XAPP_URLS = [
    value.strip().rstrip("/")
    for value in os.getenv("XAPP_URLS", XAPP_MONITOR_URL).split(",")
    if value.strip()
]

POLICIES: Dict[str, Dict[str, Any]] = {}
POLICY_ROUTE: Dict[str, Dict[str, Any]] = {}
LAST_ACTION_TS: Dict[str, float] = {}
ACTION_TIMESTAMPS: Dict[str, deque[float]] = defaultdict(deque)
ACTION_HISTORY: List[Dict[str, Any]] = []
SUPPRESSION_HISTORY: List[Dict[str, Any]] = []

SYSTEM_XAGENT_ROUTE = (
    "You are an xAgent for Near-RT RIC. Select exactly one capability from the "
    "provided allowed capability names. Output ONLY valid JSON."
)
ROUTE_SCHEMA_HINT = """{
  "policy_id": "string",
  "required_capability": "one exact allowed capability string",
  "explanation": "short string"
}"""
SYSTEM_XAGENT_TRIGGER = (
    "You are an xAgent. Evaluate the numeric threshold rule against the metrics. "
    "Do not invent missing values. Output ONLY valid JSON."
)
TRIGGER_SCHEMA_HINT = """{
  "triggered": true,
  "reason": "short string"
}"""


@app.get("/card", response_model=AgentCard)
def card() -> AgentCard:
    return AgentCard(
        name="xAgent",
        role="xAgent",
        capabilities=[
            "policy_interpretation_llm",
            "routing_llm",
            "hybrid_trigger_decision",
            "policy_conflict_resolution",
        ],
        endpoints={
            "receive_policy": "/a2a/policy/propose",
            "receive_state": "/a2a/state/report",
            "discover": "/demo/discover",
        },
        constraints={"output_format": "json_only"},
    )


async def _discover_xapps() -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=5) as client:
        for base_url in XAPP_URLS:
            try:
                response = await client.get(f"{base_url}/card")
                response.raise_for_status()
                discovered = response.json()
                discovered["_base_url"] = base_url
                cards.append(discovered)
            except httpx.HTTPError as exc:
                print(f"[xAgent] xApp discovery failed for {base_url}: {exc}")
    return cards


def _select_xapp_url(cards: List[Dict[str, Any]], capability: str) -> Optional[str]:
    for candidate in cards:
        if capability in candidate.get("capabilities", []):
            value = candidate.get("_base_url")
            return str(value) if value else None
    return None


def _heuristic_capability(policy: Dict[str, Any]) -> str:
    action = str(policy.get("rule", {}).get("action", "")).lower()
    objective = str(policy.get("objective", "")).lower()
    action_map = {
        "suggest_prb_boost": "e2_mac_control",
        "throttle_prb": "e2_mac_control",
        "report_kpi": "kpi_reporting",
        "optimize_slice": "slice_optimization",
        "boost_throughput": "throughput_control",
        "reduce_energy": "energy_saving",
    }
    if action in action_map:
        return action_map[action]
    text = f"{action} {objective}"
    if any(token in text for token in ("slice", "allocation", "resource")):
        return "slice_optimization"
    if any(token in text for token in ("monitor", "report", "observe", "kpi")):
        return "kpi_reporting"
    if any(token in text for token in ("prb", "congestion", "boost", "throttle", "control")):
        return "e2_mac_control"
    return ""


def _use_llm_for_route(
    policy: Dict[str, Any], cards: List[Dict[str, Any]], routing_mode: str
) -> bool:
    if routing_mode == "rule_only":
        return False
    if routing_mode == "llm_always":
        return True
    if routing_mode == "llm_on_rule_miss":
        allowed = {
            str(capability)
            for candidate in cards
            for capability in candidate.get("capabilities", [])
        }
        return _heuristic_capability(policy) not in allowed
    raise ValueError(f"invalid routing_mode: {routing_mode}")


async def _route_policy(
    policy: Dict[str, Any], cards: List[Dict[str, Any]], use_llm: bool
) -> Dict[str, Any]:
    pid = str(policy["policy_id"])
    allowed = sorted(
        {
            str(capability)
            for candidate in cards
            for capability in candidate.get("capabilities", [])
        }
    )
    route: Dict[str, Any]
    llm_error: Optional[str] = None
    if use_llm and allowed:
        try:
            route = await chat_json(
                system=SYSTEM_XAGENT_ROUTE,
                user=(
                    f"Policy: {policy}\n"
                    f"Allowed capabilities: {allowed}\n"
                    "Capability meanings:\n"
                    "- e2_mac_control: changes the physical-radio/MAC scheduler to relieve a "
                    "busy or saturated cell, air interface, channel, or sector.\n"
                    "- kpi_reporting: observes, detects, audits, summarizes, alerts, or measures "
                    "timing/telemetry without changing radio or tenant capacity.\n"
                    "- slice_optimization: changes tenant quotas, entitlements, service partitions, "
                    "or logical/virtual network capacity shares.\n"
                    "Examples: 'alert on unusual indication timing' -> kpi_reporting; "
                    "'rebalance capacity among service tenants' -> slice_optimization; "
                    "'relieve a saturated cell scheduler' -> e2_mac_control.\n"
                    "Apply this decision order:\n"
                    "1. Tenant/quota/entitlement/logical-network/service-partition semantics -> "
                    "slice_optimization. The word capacity alone does not imply a slice.\n"
                    "2. Detect/audit/alert/summarize/measure message timing with no network change -> "
                    "kpi_reporting.\n"
                    "3. Scheduler/cell/sector/air-interface/channel/contention/packet-blocking or "
                    "radio saturation semantics -> e2_mac_control, even when phrased as prevent, "
                    "stabilize, protect, recover, or improve.\n"
                    "Use the rule metric as strong policy context: prb_usage is physical-radio/MAC "
                    "occupancy -> e2_mac_control; e2_latency_us is indication timing telemetry -> "
                    "kpi_reporting; ue_count together with tenant/partition/quota semantics -> "
                    "slice_optimization.\n"
                    "Use both objective and action semantics. Do not default to e2_mac_control. "
                    "Choose exactly one allowed capability."
                ),
                schema_hint=ROUTE_SCHEMA_HINT,
                temperature=0.0,
                max_tokens=128,
            )
            required = str(route.get("required_capability", ""))
            if required not in allowed:
                raise ValueError(f"LLM selected unsupported capability: {required}")
            route["llm_raw_capability"] = required
            guarded = _heuristic_capability(policy)
            if guarded in allowed and guarded != required:
                route["required_capability"] = guarded
                route["guard_applied"] = True
                route["explanation"] = (
                    f"LLM selected {required}; deterministic action guard selected {guarded}"
                )
            else:
                route["guard_applied"] = False
            route["source"] = "llm"
        except Exception as exc:
            llm_error = str(exc)
            required = _heuristic_capability(policy)
            route = {
                "required_capability": required,
                "explanation": "deterministic fallback after LLM failure",
                "source": "fallback",
                "guard_applied": False,
            }
    else:
        required = _heuristic_capability(policy)
        route = {
            "required_capability": required,
            "explanation": "deterministic action/objective mapping",
            "source": "rule",
            "guard_applied": False,
        }

    route["policy_id"] = pid
    route["xapp_url"] = _select_xapp_url(cards, route.get("required_capability", ""))
    route["llm_error"] = llm_error
    return route


def _rule_triggered(policy: Dict[str, Any], metrics: Dict[str, float]) -> bool:
    rule = policy.get("rule", {})
    if rule.get("type") != "threshold":
        return False
    metric = rule.get("metric")
    if metric not in metrics:
        return False
    value = float(metrics[metric])
    if "gt" in rule:
        return value > float(rule["gt"])
    if "lt" in rule:
        return value < float(rule["lt"])
    return False


async def _trigger_decision(
    policy: Dict[str, Any], metrics: Dict[str, float], use_llm: bool
) -> Dict[str, Any]:
    rule_yes = _rule_triggered(policy, metrics)
    if not use_llm:
        return {
            "triggered": rule_yes,
            "rule_triggered": rule_yes,
            "llm_triggered": None,
            "source": "rule",
            "reason": "deterministic threshold evaluation",
            "llm_error": None,
        }

    try:
        result = await chat_json(
            system=SYSTEM_XAGENT_TRIGGER,
            user=f"Policy rule: {policy.get('rule')}\nMetrics: {metrics}\nEvaluate it.",
            schema_hint=TRIGGER_SCHEMA_HINT,
            temperature=0.0,
            max_tokens=96,
        )
        llm_yes = bool(result.get("triggered", False))
        return {
            "triggered": rule_yes and llm_yes,
            "rule_triggered": rule_yes,
            "llm_triggered": llm_yes,
            "source": "hybrid",
            "reason": str(result.get("reason", "")),
            "llm_error": None,
        }
    except Exception as exc:
        return {
            "triggered": rule_yes,
            "rule_triggered": rule_yes,
            "llm_triggered": None,
            "source": "fallback",
            "reason": "rule fallback after LLM failure",
            "llm_error": str(exc),
        }


def _action_group(event: Dict[str, Any]) -> Optional[str]:
    explicit = event.get("constraints", {}).get("mutex_group")
    if explicit:
        return str(explicit)
    action = str(event.get("action", ""))
    if action in {"suggest_prb_boost", "throttle_prb"}:
        return "prb_control"
    if action in {"expand_slice", "shrink_slice", "optimize_slice"}:
        return "slice_control"
    if action in {"boost_throughput", "reduce_energy"}:
        return "radio_resource"
    return None


def _resolve_conflicts(
    events: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    independent: List[Dict[str, Any]] = []
    for event in events:
        group = _action_group(event)
        if group is None:
            independent.append(event)
        else:
            grouped[group].append(event)

    selected = list(independent)
    suppressed: List[Dict[str, Any]] = []
    for group, candidates in grouped.items():
        ordered = sorted(
            candidates,
            key=lambda item: (
                -int(item.get("constraints", {}).get("priority", 0)),
                str(item.get("policy_id", "")),
            ),
        )
        winner = ordered[0]
        selected.append(winner)
        for loser in ordered[1:]:
            suppressed.append(
                {
                    **loser,
                    "status": "suppressed",
                    "suppression_reason": f"conflict:{group}:winner={winner['policy_id']}",
                }
            )
    return selected, suppressed


def _constraint_block_reason(
    policy_id: str,
    constraints: Dict[str, Any],
    now: float,
    *,
    last_action_ts: Optional[float] = None,
    prior_timestamps: Optional[List[float]] = None,
) -> Optional[str]:
    last = LAST_ACTION_TS.get(policy_id, 0.0) if last_action_ts is None else last_action_ts
    cooldown = float(constraints.get("cooldown_sec", 0))
    if cooldown > 0 and last > 0 and now - last < cooldown:
        return "cooldown"

    limit = int(constraints.get("max_action_rate_per_min", 0))
    if prior_timestamps is None:
        history = ACTION_TIMESTAMPS[policy_id]
        while history and now - history[0] >= 60:
            history.popleft()
        count = len(history)
    else:
        count = sum(1 for timestamp in prior_timestamps if now - timestamp < 60)
    if limit > 0 and count >= limit:
        return "rate_limit"
    return None


async def _send_feedback_to_ragent(
    policy_id: str, status: str, details: Dict[str, Any]
) -> None:
    report = FeedbackReport(
        policy_id=policy_id,
        status=status,
        details=details,
        timestamp_ms=int(time.time() * 1000),
    )
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            response = await client.post(
                f"{RAGENT_URL}/a2a/feedback/report", json=report.model_dump()
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            print(f"[xAgent] feedback delivery failed: {exc}")


async def _execute_xapp_action(event: Dict[str, Any]) -> Dict[str, Any]:
    xapp_url = event.get("route", {}).get("xapp_url")
    if not xapp_url:
        raise RuntimeError("no xApp matched the required capability")
    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.post(f"{xapp_url}/a2a/action/execute", json=event)
        response.raise_for_status()
        return response.json()


@app.get("/demo/discover")
async def discover() -> Dict[str, Any]:
    return {"xapps": await _discover_xapps()}


@app.post("/a2a/policy/propose")
async def receive_policy(envelope: A2AEnvelope) -> Dict[str, Any]:
    trace_id = envelope.trace_id
    raw_policy = envelope.payload.get("policy")
    try:
        policy = Policy.model_validate(raw_policy).model_dump()
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid policy: {exc}") from exc

    pid = policy["policy_id"]
    cards = await _discover_xapps()
    constraints = policy.get("constraints", {})
    routing_mode = constraints.get("routing_mode")
    if routing_mode is None:
        routing_mode = "llm_always" if constraints.get("use_llm", True) else "rule_only"
    try:
        use_llm = _use_llm_for_route(policy, cards, str(routing_mode))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    route = await _route_policy(policy, cards, use_llm)
    route["routing_mode"] = routing_mode
    if not route.get("xapp_url"):
        await _send_feedback_to_ragent(
            pid,
            "rejected",
            {"trace_id": trace_id, "reason": "no_matching_capability", "route": route},
        )
        return {
            "ok": False,
            "trace_id": trace_id,
            "reason": "no_matching_capability",
            "route": route,
        }

    POLICIES[pid] = policy
    POLICY_ROUTE[pid] = route
    await _send_feedback_to_ragent(
        pid, "accepted", {"trace_id": trace_id, "route": route}
    )
    return {"ok": True, "trace_id": trace_id, "stored": pid, "route": route}


@app.post("/a2a/state/report")
async def receive_state(envelope: A2AEnvelope) -> Dict[str, Any]:
    trace_id = envelope.trace_id
    try:
        report = StateReport.model_validate(envelope.payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid state report: {exc}") from exc
    metrics = report.metrics
    now = time.time()
    candidates: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []

    for pid, policy in list(POLICIES.items()):
        constraints = policy.get("constraints", {})
        block_reason = _constraint_block_reason(pid, constraints, now)
        if block_reason:
            blocked.append({"policy_id": pid, "reason": block_reason})
            continue
        routing_mode = constraints.get("routing_mode")
        if routing_mode == "llm_on_rule_miss":
            use_llm = POLICY_ROUTE.get(pid, {}).get("source") == "llm"
        elif routing_mode == "rule_only":
            use_llm = False
        elif routing_mode == "llm_always":
            use_llm = True
        else:
            use_llm = bool(constraints.get("use_llm", True))
        decision = await _trigger_decision(policy, metrics, use_llm)
        if not decision["triggered"]:
            continue
        candidates.append(
            {
                "policy_id": pid,
                "action": policy.get("rule", {}).get("action"),
                "route": POLICY_ROUTE.get(pid, {}),
                "metrics": metrics,
                "source": report.source,
                "ts_ms": report.timestamp_ms,
                "report_started_ns": envelope.payload.get("report_started_ns"),
                "trace_id": trace_id,
                "reason": decision.get("reason", ""),
                "decision_source": decision.get("source"),
                "constraints": constraints,
            }
        )

    # `disabled_baseline` is used only by the experiment driver to quantify
    # behavior before arbitration. The production/default path always applies
    # deterministic priority + mutex-group conflict resolution.
    conflict_mode = str(
        envelope.payload.get("conflict_mode", "priority_mutex")
    )
    if conflict_mode == "disabled_baseline":
        selected, suppressed = list(candidates), []
    elif conflict_mode == "priority_mutex":
        selected, suppressed = _resolve_conflicts(candidates)
    else:
        raise HTTPException(status_code=422, detail=f"invalid conflict_mode: {conflict_mode}")
    SUPPRESSION_HISTORY.extend(suppressed)
    del SUPPRESSION_HISTORY[:-100]
    completed: List[Dict[str, Any]] = []

    for event in selected:
        pid = event["policy_id"]
        LAST_ACTION_TS[pid] = now
        ACTION_TIMESTAMPS[pid].append(now)
        await _send_feedback_to_ragent(pid, "running", event)
        try:
            result = await _execute_xapp_action(event)
            status = "done" if result.get("control_success") else "failed"
        except Exception as exc:
            result = {"ok": False, "control_success": False, "error": str(exc)}
            status = "failed"
        started_ns = event.get("report_started_ns")
        state_to_done_ms = None
        if isinstance(started_ns, int):
            state_to_done_ms = (time.perf_counter_ns() - started_ns) / 1_000_000.0
        action_record = {
            **event,
            "result": result,
            "status": status,
            "state_to_done_ms": state_to_done_ms,
        }
        completed.append(action_record)
        ACTION_HISTORY.append(action_record)
        del ACTION_HISTORY[:-100]
        await _send_feedback_to_ragent(pid, status, action_record)

    return {
        "ok": True,
        "trace_id": trace_id,
        "triggered": completed,
        "suppressed": suppressed,
        "blocked": blocked,
        "conflict_mode": conflict_mode,
    }


@app.get("/demo/status")
def status() -> Dict[str, Any]:
    return {
        "policies": POLICIES,
        "routes": POLICY_ROUTE,
        "actions": ACTION_HISTORY,
        "suppressions": SUPPRESSION_HISTORY,
    }


@app.post("/demo/reset")
def reset() -> Dict[str, bool]:
    POLICIES.clear()
    POLICY_ROUTE.clear()
    LAST_ACTION_TS.clear()
    ACTION_TIMESTAMPS.clear()
    ACTION_HISTORY.clear()
    SUPPRESSION_HISTORY.clear()
    return {"ok": True}


@app.get("/demo/benchmark_routing")
def benchmark_routing(card_count: int = 1, iterations: int = 1000) -> Dict[str, Any]:
    card_count = max(1, min(card_count, 10000))
    iterations = max(1, min(iterations, 100000))
    cards = [
        {
            "name": f"mock-xapp-{index}",
            "capabilities": ["unrelated_capability"],
            "_base_url": f"http://mock-{index}",
        }
        for index in range(card_count)
    ]
    cards[-1]["capabilities"] = ["e2_mac_control"]
    samples_us: List[float] = []
    selected: Optional[str] = None
    for _ in range(iterations):
        started_ns = time.perf_counter_ns()
        selected = _select_xapp_url(cards, "e2_mac_control")
        samples_us.append((time.perf_counter_ns() - started_ns) / 1000.0)
    ordered = sorted(samples_us)
    return {
        "card_count": card_count,
        "iterations": iterations,
        "selected": selected,
        "mean_us": statistics.mean(samples_us),
        "median_us": statistics.median(samples_us),
        "p95_us": ordered[int(0.95 * (len(ordered) - 1))],
        "stdev_us": statistics.pstdev(samples_us),
    }


@app.post("/demo/evaluate_decision")
async def evaluate_decision(request: Dict[str, Any]) -> Dict[str, Any]:
    policy = Policy.model_validate(request.get("policy")).model_dump()
    cards = list(request.get("cards") or [])
    metrics = {str(key): float(value) for key, value in request.get("metrics", {}).items()}
    routing_mode = request.get("routing_mode")
    if routing_mode is None:
        routing_mode = "llm_always" if request.get("use_llm", False) else "rule_only"
    use_llm = _use_llm_for_route(policy, cards, str(routing_mode))
    started = time.perf_counter_ns()
    route = await _route_policy(policy, cards, use_llm)
    decision = await _trigger_decision(policy, metrics, use_llm)
    return {
        "route": {**route, "routing_mode": routing_mode},
        "decision": decision,
        "latency_ms": (time.perf_counter_ns() - started) / 1_000_000.0,
    }


@app.post("/demo/evaluate_conflicts")
def evaluate_conflicts(request: Dict[str, Any]) -> Dict[str, Any]:
    events = list(request.get("events") or [])
    selected, suppressed = _resolve_conflicts(events)
    return {"selected": selected, "suppressed": suppressed}


@app.post("/demo/evaluate_constraints")
def evaluate_constraints(request: Dict[str, Any]) -> Dict[str, Any]:
    now = 1000.0
    prior_ages = [float(value) for value in request.get("prior_ages_sec", [])]
    prior = [now - age for age in prior_ages]
    last_age = request.get("last_action_age_sec")
    last = None if last_age is None else now - float(last_age)
    reason = _constraint_block_reason(
        "evaluation",
        dict(request.get("constraints") or {}),
        now,
        last_action_ts=last,
        prior_timestamps=prior,
    )
    return {"allowed": reason is None, "reason": reason}
