"""Bloomberg API executor - runs on Windows with real blpapi."""

import time
from typing import Any, Optional

from blpremote_server.config import settings
from blpremote_server.exceptions import SessionError, TimeoutError, SecurityError, FieldError
from blpremote_server.models import (
    ExecutionPlan,
    ExecutionResult,
    ErrorDetail,
    StartSessionOp,
    OpenServiceOp,
    CreateRequestOp,
    AppendOp,
    SetOp,
    SendRequestOp,
    CollectResponseOp,
)

# Try to import blpapi - only available on Windows with Bloomberg
try:
    import blpapi
    BLPAPI_AVAILABLE = True
except ImportError:
    BLPAPI_AVAILABLE = False
    blpapi = None


class BloombergExecutor:
    """Executes validated IR plans using the real Bloomberg API."""

    def __init__(self):
        self._session: Optional["blpapi.Session"] = None
        self._services: dict[str, Any] = {}
        self._requests: dict[str, Any] = {}
        self._pending: dict[str, Any] = {}

    def execute(self, plan: ExecutionPlan) -> ExecutionResult:
        """Execute a validated plan and return results."""
        if not BLPAPI_AVAILABLE:
            return self._mock_execute(plan)

        start_time = time.time()
        errors: list[ErrorDetail] = []
        data: dict[str, Any] = {}

        try:
            for op in plan.ops:
                if isinstance(op, StartSessionOp):
                    self._start_session()
                elif isinstance(op, OpenServiceOp):
                    self._open_service(op.service)
                elif isinstance(op, CreateRequestOp):
                    self._create_request(op.id, op.service, op.request)
                elif isinstance(op, AppendOp):
                    self._append(op.id, op.path, op.value)
                elif isinstance(op, SetOp):
                    self._set(op.id, op.path, op.value)
                elif isinstance(op, SendRequestOp):
                    self._send_request(op.id, op.correlation_id)
                elif isinstance(op, CollectResponseOp):
                    result_data, result_errors = self._collect_response(
                        op.correlation_id, op.timeout_ms
                    )
                    data.update(result_data)
                    errors.extend(result_errors)

        except SessionError as e:
            errors.append(ErrorDetail(code=e.code, message=e.message))
        except TimeoutError as e:
            errors.append(ErrorDetail(code=e.code, message=e.message))
        except Exception as e:
            errors.append(ErrorDetail(code="EXECUTION_FAILED", message=str(e)))
        finally:
            self._cleanup()

        elapsed_ms = int((time.time() - start_time) * 1000)
        status = "ok" if not errors else ("partial" if data else "error")

        return ExecutionResult(
            request_id=plan.request_id,
            status=status,
            data=data,
            errors=errors,
            server_timing_ms=elapsed_ms,
        )

    def _start_session(self) -> None:
        """Start a Bloomberg session."""
        if self._session is not None:
            return

        session_options = blpapi.SessionOptions()
        session_options.setServerHost(settings.bloomberg_server_host)
        session_options.setServerPort(settings.bloomberg_server_port)

        self._session = blpapi.Session(session_options)
        if not self._session.start():
            raise SessionError("Failed to start Bloomberg session")

    def _open_service(self, service_name: str) -> None:
        """Open a Bloomberg service."""
        if service_name in self._services:
            return

        if not self._session.openService(service_name):
            raise SessionError(f"Failed to open service: {service_name}")

        self._services[service_name] = self._session.getService(service_name)

    def _create_request(self, request_id: str, service_name: str, request_type: str) -> None:
        """Create a Bloomberg request."""
        service = self._services.get(service_name)
        if not service:
            raise SessionError(f"Service not opened: {service_name}")

        request = service.createRequest(request_type)
        self._requests[request_id] = request

    def _append(self, request_id: str, path: str, value: Any) -> None:
        """Append a value to a request element."""
        request = self._requests.get(request_id)
        if not request:
            raise SessionError(f"Request not found: {request_id}")

        request.getElement(path).appendValue(value)

    def _set(self, request_id: str, path: str, value: Any) -> None:
        """Set a value on a request element."""
        request = self._requests.get(request_id)
        if not request:
            raise SessionError(f"Request not found: {request_id}")

        request.set(path, value)

    def _send_request(self, request_id: str, correlation_id: str) -> None:
        """Send a request."""
        request = self._requests.get(request_id)
        if not request:
            raise SessionError(f"Request not found: {request_id}")

        cid = blpapi.CorrelationId(correlation_id)
        self._session.sendRequest(request, correlationId=cid)
        self._pending[correlation_id] = cid

    def _collect_response(
        self, correlation_id: str, timeout_ms: int
    ) -> tuple[dict[str, Any], list[ErrorDetail]]:
        """Collect response for a request."""
        from blpremote_server.executor.normalize import extract_security_data

        data: dict[str, Any] = {}
        errors: list[ErrorDetail] = []
        end_time = time.time() + (timeout_ms / 1000.0)

        while True:
            remaining = end_time - time.time()
            if remaining <= 0:
                raise TimeoutError(f"Timeout waiting for response: {correlation_id}")

            event = self._session.nextEvent(int(remaining * 1000))

            for msg in event:
                # Check for errors in the message
                if msg.hasElement("responseError"):
                    error_elem = msg.getElement("responseError")
                    errors.append(ErrorDetail(
                        code="BLP_RESPONSE_ERROR",
                        message=error_elem.getElementAsString("message"),
                    ))
                    continue

                # Extract security data (handles both Reference and Historical)
                msg_data = extract_security_data(msg)
                
                # Merge data - for historical data, same security can come in multiple messages
                for sec, fields in msg_data.items():
                    if sec in data:
                        # Merge field data (extend lists for historical data)
                        for field, value in fields.items():
                            if field in data[sec] and isinstance(value, list) and isinstance(data[sec][field], list):
                                data[sec][field].extend(value)
                            else:
                                data[sec][field] = value
                    else:
                        data[sec] = fields

                # Check for per-security errors (ReferenceDataResponse style)
                if msg.hasElement("securityData"):
                    security_data = msg.getElement("securityData")
                    
                    # Handle array (ReferenceDataResponse) vs single element (HistoricalDataResponse)
                    if security_data.isArray():
                        for i in range(security_data.numValues()):
                            sec = security_data.getValueAsElement(i)
                            self._check_security_errors(sec, errors)
                    else:
                        # Single security (HistoricalDataResponse)
                        self._check_security_errors(security_data, errors)

            if event.eventType() == blpapi.Event.RESPONSE:
                break

        return data, errors

    def _check_security_errors(self, sec_element: Any, errors: list[ErrorDetail]) -> None:
        """Check a security element for errors."""
        try:
            sec_name = sec_element.getElementAsString("security") if sec_element.hasElement("security") else "unknown"
            
            if sec_element.hasElement("securityError"):
                err = sec_element.getElement("securityError")
                errors.append(ErrorDetail(
                    code="BLP_SECURITY_ERROR",
                    message=err.getElementAsString("message"),
                    security=sec_name,
                ))
            
            if sec_element.hasElement("fieldExceptions"):
                field_exc = sec_element.getElement("fieldExceptions")
                for j in range(field_exc.numValues()):
                    fe = field_exc.getValueAsElement(j)
                    field_id = fe.getElementAsString("fieldId")
                    err_info = fe.getElement("errorInfo")
                    errors.append(ErrorDetail(
                        code="BLP_FIELD_ERROR",
                        message=err_info.getElementAsString("message"),
                        security=sec_name,
                        field=field_id,
                    ))
        except Exception:
            pass

    def _cleanup(self) -> None:
        """Clean up session resources."""
        if self._session:
            try:
                self._session.stop()
            except Exception:
                pass
            self._session = None
        self._services.clear()
        self._requests.clear()
        self._pending.clear()

    def _mock_execute(self, plan: ExecutionPlan) -> ExecutionResult:
        """
        Mock execution for development/testing without Bloomberg.

        Returns dummy data to allow testing the full flow.
        """
        start_time = time.time()
        data: dict[str, Any] = {}
        securities: list[str] = []
        fields: list[str] = []

        for op in plan.ops:
            if isinstance(op, AppendOp):
                if op.path == "securities":
                    securities.append(str(op.value))
                elif op.path == "fields":
                    fields.append(str(op.value))

        # Generate mock data
        for security in securities:
            data[security] = {}
            for field in fields:
                if field == "PX_LAST":
                    data[security][field] = 123.45
                elif field == "NAME":
                    data[security][field] = f"Mock Company ({security})"
                elif field == "VOLUME":
                    data[security][field] = 1000000
                else:
                    data[security][field] = f"mock_{field}"

        elapsed_ms = int((time.time() - start_time) * 1000)

        return ExecutionResult(
            request_id=plan.request_id,
            status="ok",
            data=data,
            errors=[],
            server_timing_ms=elapsed_ms,
        )


# Global executor instance
executor = BloombergExecutor()


def execute_plan(plan: ExecutionPlan) -> ExecutionResult:
    """Execute a plan using the global executor."""
    return executor.execute(plan)
