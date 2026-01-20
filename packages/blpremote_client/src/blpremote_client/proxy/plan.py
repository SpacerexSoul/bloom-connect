"""Execution plan builder for accumulating IR operations."""

from typing import Optional, Union
from blpremote_client.models import (
    ExecutionPlan,
    AuthToken,
    Op,
    StartSessionOp,
    OpenServiceOp,
    CreateRequestOp,
    AppendOp,
    SetOp,
    SendRequestOp,
    CollectResponseOp,
    PlanLimits,
)


class PlanBuilder:
    """Builder for accumulating operations into an execution plan."""

    def __init__(self, token: Optional[str] = None):
        self._ops: list[Op] = []
        self._token = token
        self._request_counter = 0
        self._correlation_counter = 0
        self._limits = PlanLimits()

    def start_session(self) -> "PlanBuilder":
        """Add a start_session operation."""
        self._ops.append(StartSessionOp())
        return self

    def open_service(self, service: str) -> "PlanBuilder":
        """Add an open_service operation."""
        self._ops.append(OpenServiceOp(service=service))
        return self

    def create_request(self, service: str, request_type: str) -> str:
        """Add a create_request operation and return the request ID."""
        self._request_counter += 1
        req_id = f"req{self._request_counter}"
        self._ops.append(
            CreateRequestOp(service=service, request=request_type, id=req_id)
        )
        return req_id

    def append(self, request_id: str, path: str, value: Union[str, int, float, bool]) -> "PlanBuilder":
        """Add an append operation."""
        self._ops.append(AppendOp(id=request_id, path=path, value=value))
        return self

    def set(self, request_id: str, path: str, value: Union[str, int, float, bool]) -> "PlanBuilder":
        """Add a set operation."""
        self._ops.append(SetOp(id=request_id, path=path, value=value))
        return self

    def send_request(self, request_id: str) -> str:
        """Add a send_request operation and return the correlation ID."""
        self._correlation_counter += 1
        cid = f"cid{self._correlation_counter}"
        self._ops.append(SendRequestOp(id=request_id, correlation_id=cid))
        return cid

    def collect_response(self, correlation_id: str, timeout_ms: int = 10000) -> "PlanBuilder":
        """Add a collect_response operation."""
        self._ops.append(
            CollectResponseOp(correlation_id=correlation_id, timeout_ms=timeout_ms)
        )
        return self

    def set_limits(
        self,
        max_securities: Optional[int] = None,
        max_fields: Optional[int] = None,
        max_timeout_ms: Optional[int] = None,
    ) -> "PlanBuilder":
        """Set execution limits."""
        if max_securities is not None:
            self._limits.max_securities = max_securities
        if max_fields is not None:
            self._limits.max_fields = max_fields
        if max_timeout_ms is not None:
            self._limits.max_timeout_ms = max_timeout_ms
        return self

    def set_token(self, token: str) -> "PlanBuilder":
        """Set the authentication token."""
        self._token = token
        return self

    def build(self) -> ExecutionPlan:
        """Build the execution plan."""
        if not self._token:
            raise ValueError("Token must be set before building the plan")
        return ExecutionPlan(
            auth=AuthToken(token=self._token),
            ops=self._ops,
            limits=self._limits,
        )

    def clear(self) -> "PlanBuilder":
        """Clear all operations."""
        self._ops.clear()
        self._request_counter = 0
        self._correlation_counter = 0
        return self
