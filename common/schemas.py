from pydantic import BaseModel, Field
from typing import Any, Dict, List, Literal, Optional

Role = Literal["rAgent", "xAgent", "xApp"]


class AgentCard(BaseModel):
    name: str
    role: Role
    version: str = "0.1"
    capabilities: List[str] = Field(default_factory=list)
    intents: List[str] = Field(default_factory=list)
    endpoints: Dict[str, str] = Field(default_factory=dict)
    constraints: Dict[str, Any] = Field(default_factory=dict)


class Policy(BaseModel):
    policy_id: str
    objective: str
    constraints: Dict[str, Any] = Field(default_factory=dict)
    # 예: {"type":"threshold", "metric":"prb_usage", "gt":0.7, "action":"mitigate_congestion"}
    rule: Dict[str, Any]


class A2AEnvelope(BaseModel):
    msg_type: str
    sender: str
    receiver: str
    trace_id: str
    payload: Dict[str, Any]


class StateReport(BaseModel):
    source: str
    metrics: Dict[str, float]
    timestamp_ms: int


class FeedbackReport(BaseModel):
    policy_id: str
    status: Literal["accepted", "rejected", "running", "done", "failed"]
    details: Dict[str, Any] = Field(default_factory=dict)
    timestamp_ms: int
