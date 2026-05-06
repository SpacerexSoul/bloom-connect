"""Bloomberg API executor — runs against a long-lived async session.

Per-request flow:
  1. StartSessionOp / OpenServiceOp are no-ops (the SessionManager
     handles startup at server boot and ensures services are open).
  2. CreateRequestOp / AppendOp / SetOp build a blpapi.Request locally
     to this execute() call (no shared mutable executor state).
  3. SendRequestOp registers a per-correlation-id queue with the
     SessionManager, then submits the request.
  4. CollectResponseOp blocks on that queue, draining (event_type, msg)
     pairs until a RESPONSE event arrives or the timeout elapses.

The session is shared across concurrent requests; per-cid queues
isolate them. ``_cleanup_per_call`` only unregisters this call's
queues — it never stops the session.
"""

from __future__ import annotations

import queue as _queue
import time
from typing import Any, Optional

from blpremote_server.exceptions import SessionError, TimeoutError
from blpremote_server.models import (
    AppendOp,
    CollectResponseOp,
    CreateRequestOp,
    ErrorDetail,
    ExecutionPlan,
    ExecutionResult,
    OpenServiceOp,
    SendRequestOp,
    SetOp,
    StartSessionOp,
)
from blpremote_server.session_manager import (
    BLPAPI_AVAILABLE,
    SessionManager,
    blpapi,
    get_manager,
)


class BloombergExecutor:
    """Stateless executor — all per-call state lives in execute()."""

    def __init__(self, session_manager: Optional[SessionManager] = None):
        self._mgr = session_manager  # if None, resolved lazily per call

    def _manager(self) -> SessionManager:
        return self._mgr if self._mgr is not None else get_manager()

    def execute(self, plan: ExecutionPlan) -> ExecutionResult:
        if not BLPAPI_AVAILABLE:
            return self._mock_execute(plan)

        start_time = time.time()
        errors: list[ErrorDetail] = []
        data: dict[str, Any] = {}

        # Per-call state — never shared across concurrent execute() calls.
        requests: dict[str, Any] = {}
        registered_cids: list[str] = []

        mgr = self._manager()

        try:
            for op in plan.ops:
                if isinstance(op, StartSessionOp):
                    # Session is owned by SessionManager; start at app boot.
                    # Op kept for client-side IR backwards compat.
                    if not mgr.is_connected():
                        raise SessionError(
                            f"bloomberg session not available (state={mgr.state.value})"
                        )
                elif isinstance(op, OpenServiceOp):
                    mgr.get_service(op.service)
                elif isinstance(op, CreateRequestOp):
                    service = mgr.get_service(op.service)
                    requests[op.id] = service.createRequest(op.request)
                elif isinstance(op, AppendOp):
                    self._append(requests, op.id, op.path, op.value)
                elif isinstance(op, SetOp):
                    self._set(requests, op.id, op.path, op.value)
                elif isinstance(op, SendRequestOp):
                    self._send_request(
                        mgr, requests, op.id, op.correlation_id, registered_cids
                    )
                elif isinstance(op, CollectResponseOp):
                    result_data, result_errors = self._collect_response(
                        mgr, op.correlation_id, op.timeout_ms
                    )
                    self._merge_data(data, result_data)
                    errors.extend(result_errors)

        except SessionError as e:
            errors.append(ErrorDetail(code=e.code, message=e.message))
        except TimeoutError as e:
            errors.append(ErrorDetail(code=e.code, message=e.message))
        except Exception as e:
            errors.append(ErrorDetail(code="EXECUTION_FAILED", message=str(e)))
        finally:
            for cid in registered_cids:
                mgr.unregister_queue(cid)

        elapsed_ms = int((time.time() - start_time) * 1000)
        status = "ok" if not errors else ("partial" if data else "error")

        return ExecutionResult(
            request_id=plan.request_id,
            status=status,
            data=data,
            errors=errors,
            server_timing_ms=elapsed_ms,
        )

    # --- Op helpers ---------------------------------------------------

    @staticmethod
    def _append(
        requests: dict[str, Any], request_id: str, path: str, value: Any
    ) -> None:
        request = requests.get(request_id)
        if request is None:
            raise SessionError(f"request not found: {request_id}")
        request.getElement(path).appendValue(value)

    @staticmethod
    def _set(
        requests: dict[str, Any], request_id: str, path: str, value: Any
    ) -> None:
        request = requests.get(request_id)
        if request is None:
            raise SessionError(f"request not found: {request_id}")
        request.set(path, value)

    @staticmethod
    def _send_request(
        mgr: SessionManager,
        requests: dict[str, Any],
        request_id: str,
        correlation_id: str,
        registered_cids: list[str],
    ) -> None:
        request = requests.get(request_id)
        if request is None:
            raise SessionError(f"request not found: {request_id}")
        # Register queue BEFORE sending so we don't race the response.
        mgr.register_queue(correlation_id)
        registered_cids.append(correlation_id)
        cid = blpapi.CorrelationId(correlation_id)
        mgr.session().sendRequest(request, correlationId=cid)

    @staticmethod
    def _collect_response(
        mgr: SessionManager, correlation_id: str, timeout_ms: int
    ) -> tuple[dict[str, Any], list[ErrorDetail]]:
        from blpremote_server.executor.normalize import extract_security_data

        q = mgr.register_queue(correlation_id)  # idempotent: replaces if exists
        # The send path already registered; this is just retrieving the same
        # queue. Either way, ``mgr._queues[cid]`` is set.

        data: dict[str, Any] = {}
        errors: list[ErrorDetail] = []
        deadline = time.time() + (timeout_ms / 1000.0)

        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError(
                    f"timeout waiting for response: {correlation_id}"
                )
            try:
                event_type, msg = q.get(timeout=remaining)
            except _queue.Empty:
                raise TimeoutError(
                    f"timeout waiting for response: {correlation_id}"
                )

            if msg.hasElement("responseError"):
                err_elem = msg.getElement("responseError")
                errors.append(
                    ErrorDetail(
                        code="BLP_RESPONSE_ERROR",
                        message=err_elem.getElementAsString("message"),
                    )
                )
            else:
                msg_data = extract_security_data(msg)
                BloombergExecutor._merge_data(data, msg_data)

                if msg.hasElement("securityData"):
                    sec_data = msg.getElement("securityData")
                    if sec_data.isArray():
                        for i in range(sec_data.numValues()):
                            BloombergExecutor._check_security_errors(
                                sec_data.getValueAsElement(i), errors
                            )
                    else:
                        BloombergExecutor._check_security_errors(sec_data, errors)

            # RESPONSE marks end of stream for this correlation id.
            if event_type == blpapi.Event.RESPONSE:
                break

        return data, errors

    @staticmethod
    def _merge_data(
        data: dict[str, Any], incoming: dict[str, dict[str, Any]]
    ) -> None:
        for sec, fields in incoming.items():
            if sec in data:
                for field, value in fields.items():
                    existing = data[sec].get(field)
                    if isinstance(existing, list) and isinstance(value, list):
                        existing.extend(value)
                    else:
                        data[sec][field] = value
            else:
                data[sec] = fields

    @staticmethod
    def _check_security_errors(
        sec_element: Any, errors: list[ErrorDetail]
    ) -> None:
        try:
            sec_name = (
                sec_element.getElementAsString("security")
                if sec_element.hasElement("security")
                else "unknown"
            )
            if sec_element.hasElement("securityError"):
                err = sec_element.getElement("securityError")
                errors.append(
                    ErrorDetail(
                        code="BLP_SECURITY_ERROR",
                        message=err.getElementAsString("message"),
                        security=sec_name,
                    )
                )
            if sec_element.hasElement("fieldExceptions"):
                field_exc = sec_element.getElement("fieldExceptions")
                for j in range(field_exc.numValues()):
                    fe = field_exc.getValueAsElement(j)
                    field_id = fe.getElementAsString("fieldId")
                    err_info = fe.getElement("errorInfo")
                    errors.append(
                        ErrorDetail(
                            code="BLP_FIELD_ERROR",
                            message=err_info.getElementAsString("message"),
                            security=sec_name,
                            field=field_id,
                        )
                    )
        except Exception:
            pass

    # --- Mock path (no blpapi installed) ------------------------------

    @staticmethod
    def _mock_execute(plan: ExecutionPlan) -> ExecutionResult:
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


# Module-level executor binds to the singleton SessionManager lazily.
_executor = BloombergExecutor()


def execute_plan(plan: ExecutionPlan) -> ExecutionResult:
    """Execute a plan using the process-wide executor + SessionManager."""
    return _executor.execute(plan)
