from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    api_key: str
    name: str = "default-agent"


class GateRequest(BaseModel):
    api_key: str
    tool_name: str
    params: Dict[str, Any] = Field(default_factory=dict)
    policies: Dict[str, Any] = Field(default_factory=dict)


class GateResponse(BaseModel):
    decision: Literal["allow", "block", "require_approval"]
    status: Literal["released", "blocked", "pending_approval"]
    reason: Optional[str] = None
    action_id: str


class RuleValueUpdate(BaseModel):
    value: float


class ApprovalDecision(BaseModel):
    decision: Literal["approved", "denied"]
