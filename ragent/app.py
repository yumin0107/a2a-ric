import os
import time
import uuid
import httpx
from fastapi import FastAPI
from common.schemas import AgentCard, FeedbackReport
from common.llm_openai import chat_json

app = FastAPI(title="rAgent (LLM)")

XAGENT_URL = os.getenv("XAGENT_URL", "http://localhost:8002")
FEEDBACK_HISTORY = []

SYSTEM_RAGENT = (
    "You are an rApp rAgent for O-RAN. "
    "Your job is to create high-level policies for Near-RT control. "
    "You must output ONLY valid JSON."
)

POLICY_SCHEMA_HINT = """{
  "policy_id": "string",
  "objective": "string",
  "constraints": {"max_action_rate_per_min": 6, "cooldown_sec": 10},
  "rule": {"type":"threshold","metric":"prb_usage","gt":0.70,"action":"suggest_prb_boost"}
}"""


def fallback_policy():
    return {
        "policy_id": str(uuid.uuid4())[:8],
        "objective": "mitigate_congestion",
        "constraints": {"max_action_rate_per_min": 6, "cooldown_sec": 10},
        "rule": {
            "type": "threshold",
            "metric": "prb_usage",
            "gt": 0.70,
            "action": "suggest_prb_boost",
        },
    }


@app.get("/card", response_model=AgentCard)
def card():
    return AgentCard(
        name="rAgent",
        role="rAgent",
        capabilities=["policy_generation_llm", "long_term_objective"],
        endpoints={
            "propose_policy": "/a2a/policy/propose",
            "receive_feedback": "/a2a/feedback/report",
            "run_demo": "/demo/run_once",
        },
        constraints={"output_format": "json_only"},
    )


@app.post("/a2a/feedback/report")
def receive_feedback(report: FeedbackReport):
    item = report.model_dump()
    FEEDBACK_HISTORY.append(item)
    del FEEDBACK_HISTORY[:-100]
    print(f"[rAgent] feedback: {item}")
    return {"ok": True}


@app.get("/demo/status")
def status():
    return {"feedback": FEEDBACK_HISTORY}


@app.post("/demo/reset")
def reset():
    FEEDBACK_HISTORY.clear()
    return {"ok": True}


@app.post("/demo/run_once")
async def run_once(request: dict | None = None):
    trace_id = str(uuid.uuid4())
    request = request or {}

    # 1) LLM policy 생성
    llm_err = None
    if isinstance(request.get("policy"), dict):
        policy = dict(request["policy"])
        policy.setdefault("policy_id", str(uuid.uuid4())[:8])
    else:
        operator_intent = request.get(
            "operator_intent",
            "Mitigate congestion when PRB usage exceeds 0.70 by suggesting a PRB boost.",
        )
        try:
            policy = await chat_json(
                system=SYSTEM_RAGENT,
                user=(
                    f"Operator intent: {operator_intent}\n"
                    "Create ONE policy. Use metric prb_usage (0~1), a threshold rule, "
                    "and one of these actions: suggest_prb_boost, report_kpi, optimize_slice.\n"
                ),
                schema_hint=POLICY_SCHEMA_HINT,
                temperature=0.0,
                max_tokens=256,
            )
            if "policy_id" not in policy:
                policy["policy_id"] = str(uuid.uuid4())[:8]
        except Exception as e:
            policy = fallback_policy()
            llm_err = str(e)

    constraints = policy.setdefault("constraints", {})
    if "use_llm" in request:
        constraints["use_llm"] = bool(request["use_llm"])

    envelope = {
        "msg_type": "ProposePolicy",
        "sender": "rAgent",
        "receiver": "xAgent",
        "trace_id": trace_id,
        "payload": {"policy": policy},
    }

    # 2) xAgent로 전송
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{XAGENT_URL}/a2a/policy/propose", json=envelope)
            # xAgent가 에러 반환하면 여기서 예외 내지 말고 내용 보여주기
            if r.status_code >= 400:
                return {
                    "ok": False,
                    "trace_id": trace_id,
                    "stage": "post_to_xagent",
                    "xagent_status": r.status_code,
                    "xagent_body": r.text,
                    "xagent_url": XAGENT_URL,
                }
            xagent_body = r.json()
            if not xagent_body.get("ok", False):
                return {
                    "ok": False,
                    "trace_id": trace_id,
                    "stage": "xagent_rejected_policy",
                    "xagent_body": xagent_body,
                    "xagent_url": XAGENT_URL,
                }
    except Exception as e:
        return {
            "ok": False,
            "trace_id": trace_id,
            "stage": "httpx_post_to_xagent_failed",
            "err": str(e),
            "xagent_url": XAGENT_URL,
        }

    return {
        "ok": True,
        "trace_id": trace_id,
        "policy": policy,
        "llm_fallback": llm_err is not None,
        "llm_error": llm_err,
        "xagent_url": XAGENT_URL,
    }
