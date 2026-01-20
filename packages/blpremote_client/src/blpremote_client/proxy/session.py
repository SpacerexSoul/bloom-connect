"""Bloomberg-like Session proxy that builds and executes IR plans."""

from typing import Any, Optional
import uuid

from blpremote_client.host import RemoteHost
from blpremote_client.models import (
    ExecutionPlan,
    ExecutionResult,
    AuthToken,
    PlanLimits,
)
from blpremote_client.proxy.service import Service
from blpremote_client.proxy.request import Request


class SessionOptions:
    """Configuration options for a Session."""

    def __init__(self):
        self._server_host = "localhost"
        self._server_port = 8194

    def setServerHost(self, host: str) -> None:
        """Set the Bloomberg server host (not used for remote execution)."""
        self._server_host = host

    def setServerPort(self, port: int) -> None:
        """Set the Bloomberg server port (not used for remote execution)."""
        self._server_port = port


class Session:
    """
    Bloomberg-like Session proxy.

    Mimics the blpapi.Session interface but builds an execution plan
    that is sent to a remote Windows host for actual Bloomberg execution.

    Example:
        >>> opts = SessionOptions()
        >>> session = Session(opts, remote_host="http://192.168.1.100:8000",
        ...                   username="krishna", password="***")
        >>> session.start()
        >>> session.openService("//blp/refdata")
        >>> svc = session.getService("//blp/refdata")
        >>> req = svc.createRequest("ReferenceDataRequest")
        >>> req.getElement("securities").appendValue("IBM US Equity")
        >>> req.getElement("fields").appendValue("PX_LAST")
        >>> cid = session.sendRequest(req)
        >>> result = session.collectResponse(cid)
        >>> print(result.to_dict())
    """

    def __init__(
        self,
        options: SessionOptions,
        remote_host: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self._options = options
        self._remote = RemoteHost(remote_host, username, password, timeout)
        self._started = False
        self._services: dict[str, Service] = {}
        self._ops: list[dict[str, Any]] = []
        self._pending_requests: dict[str, Request] = {}
        self._correlation_counter = 0
        self._limits = PlanLimits()

    def start(self) -> bool:
        """
        Start the session.

        This records a start_session operation in the plan.
        Returns True to match Bloomberg API.
        """
        self._ops.append({"op": "start_session"})
        self._started = True
        return True

    def stop(self) -> None:
        """Stop the session and clear state."""
        self._started = False
        self._services.clear()
        self._ops.clear()
        self._pending_requests.clear()

    def openService(self, service_name: str) -> bool:
        """
        Open a Bloomberg service.

        This records an open_service operation in the plan.
        Returns True to match Bloomberg API.
        """
        if not self._started:
            raise RuntimeError("Session not started. Call start() first.")

        self._ops.append({"op": "open_service", "service": service_name})
        self._services[service_name] = Service(self, service_name)
        return True

    def getService(self, service_name: str) -> Service:
        """Get a previously opened service."""
        if service_name not in self._services:
            raise ValueError(f"Service {service_name} not opened. Call openService() first.")
        return self._services[service_name]

    def sendRequest(
        self,
        request: Request,
        correlation_id: Optional[str] = None,
    ) -> str:
        """
        Queue a request for sending.

        Returns the correlation ID that can be used to collect the response.
        """
        if correlation_id is None:
            self._correlation_counter += 1
            correlation_id = f"cid{self._correlation_counter}"

        # Add all the request's accumulated operations
        for op in request._get_operations():
            if op["type"] == "append":
                self._ops.append({
                    "op": "append",
                    "id": request._get_id(),
                    "path": op["path"],
                    "value": op["value"],
                })
            elif op["type"] == "set":
                self._ops.append({
                    "op": "set",
                    "id": request._get_id(),
                    "path": op["path"],
                    "value": op["value"],
                })

        # Add send_request operation
        self._ops.append({
            "op": "send_request",
            "id": request._get_id(),
            "correlation_id": correlation_id,
        })

        self._pending_requests[correlation_id] = request
        return correlation_id

    def collectResponse(
        self,
        correlation_id: str,
        timeout_ms: int = 10000,
    ) -> ExecutionResult:
        """
        Collect the response for a pending request.

        This builds and executes the complete plan on the remote host.
        """
        if correlation_id not in self._pending_requests:
            raise ValueError(f"No pending request with correlation_id: {correlation_id}")

        # Add collect_response operation
        self._ops.append({
            "op": "collect_refdata_response",
            "correlation_id": correlation_id,
            "timeout_ms": timeout_ms,
        })

        # Build and execute the plan
        token = self._remote._get_token()
        plan = ExecutionPlan(
            auth=AuthToken(token=token),
            ops=self._ops,  # type: ignore
            limits=self._limits,
        )

        result = self._remote.execute(plan)

        # Clear the pending request and ops
        del self._pending_requests[correlation_id]
        self._ops.clear()

        # Re-add start_session and open_service for next request
        self._ops.append({"op": "start_session"})
        for service_name in self._services:
            self._ops.append({"op": "open_service", "service": service_name})

        return result

    def _add_op(self, op: dict[str, Any]) -> None:
        """Internal: add an operation to the plan."""
        self._ops.append(op)

    def setLimits(
        self,
        max_securities: Optional[int] = None,
        max_fields: Optional[int] = None,
    ) -> None:
        """Set execution limits."""
        if max_securities is not None:
            self._limits.max_securities = max_securities
        if max_fields is not None:
            self._limits.max_fields = max_fields

    def close(self) -> None:
        """Close the session and underlying connection."""
        self.stop()
        self._remote.close()

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, *args) -> None:
        self.close()
