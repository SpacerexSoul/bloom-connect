"""Pydantic models for execution plans and responses."""

import uuid
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field


# Operation types for the IR
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
    """Generic response collector. Routes to the same server handler that
    historically lived behind ``collect_refdata_response``; the rename
    reflects that the handler already worked for HistoricalDataResponse
    too. Introduced in protocol_version 1.1."""

    op: Literal["collect_response"] = "collect_response"
    correlation_id: str
    timeout_ms: int = 10000


class CollectRefdataResponseOp(BaseModel):
    """Deprecated alias of :class:`CollectResponseOp`. Accepted for
    protocol_version 1.1; retired in 1.2. Server emits an
    ``IR_DEPRECATED_OP`` notice in :attr:`ExecutionResult.warnings`
    when this op is used."""

    op: Literal["collect_refdata_response"] = "collect_refdata_response"
    correlation_id: str
    timeout_ms: int = 10000


# Union of all operation types
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
    """Limits for execution plan validation."""

    max_securities: int = 200
    max_fields: int = 200
    max_timeout_ms: int = 30000


class AuthToken(BaseModel):
    """Authentication token for requests."""

    token: str


class ExecutionPlan(BaseModel):
    """Complete execution plan to send to the server.

    ``protocol_version`` defaults to ``"1.1"`` (M2). The server still
    accepts ``"1.0"`` for callers pinned to the older shape.
    """

    protocol_version: str = "1.1"
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    auth: AuthToken
    ops: list[Op] = Field(default_factory=list)
    limits: PlanLimits = Field(default_factory=PlanLimits)


class ErrorDetail(BaseModel):
    """Details about an error in the response."""

    code: str
    message: str
    security: Optional[str] = None
    field: Optional[str] = None


class ExecutionResult(BaseModel):
    """Response from the server after plan execution.

    ``warnings`` carries advisory items that didn't materially affect
    the result — deprecated-op notices, unverified-service flags,
    schema fallback messages. Distinct from ``errors``, which mean
    some part of the request failed or returned partial data.
    Status calculation is unaffected by warnings.
    """

    request_id: str
    status: Literal["ok", "error", "partial"]
    data: dict[str, Any] = Field(default_factory=dict)
    errors: list[ErrorDetail] = Field(default_factory=list)
    warnings: list[ErrorDetail] = Field(default_factory=list)
    server_timing_ms: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        """Return data as a dictionary."""
        return self.data

    def to_dataframe(self):
        """Convert data to a pandas DataFrame (requires pandas)."""
        try:
            import pandas as pd

            if not self.data:
                return pd.DataFrame()
            return pd.DataFrame.from_dict(self.data, orient="index")
        except ImportError:
            raise ImportError(
                "pandas is required for to_dataframe(). Install with: pip install pandas"
            )


class LoginRequest(BaseModel):
    """Login request payload."""

    username: str
    password: str


class LoginResponse(BaseModel):
    """Login response with token."""

    token: str
    expires_in: int  # seconds until expiration
    token_type: str = "Bearer"
