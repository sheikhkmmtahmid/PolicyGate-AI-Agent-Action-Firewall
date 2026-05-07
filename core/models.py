from pydantic import BaseModel, field_validator
from typing import Optional
from datetime import datetime


class GateRequest(BaseModel):
    tool_name: str
    params: dict
    context: Optional[dict] = {}

    @field_validator("tool_name")
    @classmethod
    def tool_name_must_not_be_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("tool_name cannot be empty")
        return v.strip()

    @field_validator("params")
    @classmethod
    def params_must_be_dict(cls, v):
        if v is None:
            raise ValueError("params must be a dict, not null")
        return v


class GateResponse(BaseModel):
    allowed: bool
    reason: str
    result: Optional[dict] = None


class AuditEntry(BaseModel):
    timestamp: datetime
    tool_name: str
    params: dict
    allowed: bool
    reason: str
    agent_id: Optional[str] = None
