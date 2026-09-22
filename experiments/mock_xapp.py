import os
import threading
import time
from typing import Any, Dict

from fastapi import FastAPI

from common.schemas import AgentCard


NAME = os.getenv("MOCK_XAPP_NAME", "Mock xApp")
CAPABILITY = os.getenv("MOCK_XAPP_CAPABILITY", "mock_control")
ACTION = os.getenv("MOCK_XAPP_ACTION", "mock_action")

app = FastAPI(title=NAME)
lock = threading.Lock()
executions: list[Dict[str, Any]] = []


@app.get("/card", response_model=AgentCard)
def card() -> AgentCard:
    return AgentCard(
        name=NAME,
        role="xApp",
        version="0.1",
        capabilities=[CAPABILITY],
        endpoints={"execute_action": "/a2a/action/execute"},
        constraints={"supported_action": ACTION, "experiment_only": True},
    )


@app.post("/a2a/action/execute")
def execute_action(event: Dict[str, Any]) -> Dict[str, Any]:
    action = event.get("action") or event.get("payload", {}).get("action")
    if action != ACTION:
        return {
            "ok": False,
            "control_success": False,
            "reason": f"unsupported action: {action}",
        }
    record = {
        "action": action,
        "policy_id": event.get("policy_id"),
        "trace_id": event.get("trace_id"),
        "completed_at_ms": int(time.time() * 1000),
    }
    with lock:
        executions.append(record)
    return {"ok": True, "control_success": True, **record}


@app.get("/demo/status")
def status() -> Dict[str, Any]:
    with lock:
        history = list(executions)
    return {"name": NAME, "capability": CAPABILITY, "executions": history}


@app.post("/demo/reset")
def reset() -> Dict[str, bool]:
    with lock:
        executions.clear()
    return {"ok": True}
