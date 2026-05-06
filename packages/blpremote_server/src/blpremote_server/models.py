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
    # M2 r2: generic op replacing the misnamed `collect_refdata_response`.
    # Routes to the same executor handler — the rename reflects that the
    # handler already worked for HistoricalDataResponse too.
    op: Literal["collect_response"] = "collect_response"
    correlation_id: str
    timeout_ms: int = 10000


class CollectRefdataResponseOp(BaseModel):
    # Deprecated alias kept for protocol_version="1.1". Retired in 1.2.
    # Executor emits IR_DEPRECATED_OP into ExecutionResult.warnings when
    # this is used. Same handler as CollectResponseOp — only the wire
    # literal differs.
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
    CollectRefdataResponseOp,
]


class PlanLimits(BaseModel):
    max_securities: int = 200
    max_fields: int = 200
    max_timeout_ms: int = 30000


class AuthToken(BaseModel):
    token: str


class ExecutionPlan(BaseModel):
    # M2 r2: bumped default to "1.1". Server accepts "1.0" and "1.1";
    # "1.2" will retire the deprecated `collect_refdata_response` alias.
    protocol_version: str = "1.1"
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
    # M2 r2: advisory items that didn't materially affect the result —
    # deprecated-op notices, unverified-service flags, schema fallbacks.
    # Status calc is unchanged: warnings never affect status.
    warnings: list[ErrorDetail] = Field(default_factory=list)
    server_timing_ms: Optional[int] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    expires_in: int
    token_type: str = "Bearer"


class HealthResponse(BaseModel):
    # "healthy" — session connected and serving requests
    # "degraded" — session lost or reconnecting; requests will fail until restored
    # "unavailable" — blpapi not installed or session never started
    status: str = "healthy"
    bloomberg_connected: bool = False
    # Detailed session view (None when blpapi isn't installed):
    session_state: Optional[str] = None
    last_reconnect_ts: Optional[float] = None
    reconnect_attempts: int = 0


class VersionResponse(BaseModel):
    version: str
    protocol_version: str = "1.0"


class CoordSendRequest(BaseModel):
    to: str = Field(min_length=1, max_length=64)
    body: str


class CoordMessage(BaseModel):
    sender: str
    to: str
    body: str
    ts: str


class CoordInboxResponse(BaseModel):
    user: str
    messages: list[CoordMessage]


class CoordSendResponse(BaseModel):
    ok: bool = True
    ts: str
