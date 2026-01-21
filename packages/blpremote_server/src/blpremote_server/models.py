"""Pydantic models for server request/response handling."""

import uuid
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field


# Operation types for the IR (same as client)
class StartSessionOp(BaseModel):
    op: Literal["start_session"] = "start_session"


class OpenServiceOp(BaseModel):
    op: Literal["open_service"] = "open_service"
    service: str


class CreateRequestOp(BaseModel):
    op: Literal["create_request"] = "create_request"
    service: str
    request: str
    id: str


class AppendOp(BaseModel):
    op: Literal["append"] = "append"
    id: str
    path: str
    value: Union[str, int, float, bool]


class SetOp(BaseModel):
    op: Literal["set"] = "set"
    id: str
    path: str
    value: Union[str, int, float, bool]


class SendRequestOp(BaseModel):
    op: Literal["send_request"] = "send_request"
    id: str
    correlation_id: str


class CollectResponseOp(BaseModel):
    op: Literal["collect_refdata_response"] = "collect_refdata_response"
    correlation_id: str
    timeout_ms: int = 10000


Op = Union[
    StartSessionOp,
    OpenServiceOp,
    CreateRequestOp,
    AppendOp,
    SetOp,
    SendRequestOp,
    CollectResponseOp,
]


class PlanLimits(BaseModel):
    max_securities: int = 200
    max_fields: int = 200
    max_timeout_ms: int = 30000


class AuthToken(BaseModel):
    token: str


class ExecutionPlan(BaseModel):
    protocol_version: str = "1.0"
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    auth: AuthToken
    ops: list[Op] = Field(default_factory=list)
    limits: PlanLimits = Field(default_factory=PlanLimits)


class ErrorDetail(BaseModel):
    code: str
    message: str
    security: Optional[str] = None
    field: Optional[str] = None


class ExecutionResult(BaseModel):
    request_id: str
    status: Literal["ok", "error", "partial"]
    data: dict[str, Any] = Field(default_factory=dict)
    errors: list[ErrorDetail] = Field(default_factory=list)
    server_timing_ms: Optional[int] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    expires_in: int
    token_type: str = "Bearer"


class HealthResponse(BaseModel):
    status: str = "healthy"
    bloomberg_connected: bool = False


class VersionResponse(BaseModel):
    version: str
    protocol_version: str = "1.0"
