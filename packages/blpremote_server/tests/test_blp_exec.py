"""Tests for the executor — uses a fake SessionManager + fake blpapi.

The executor must:
  - treat StartSessionOp / OpenServiceOp as guards over the long-lived
    session (raise if not connected; idempotent service open)
  - register a queue per correlation id BEFORE sending the request
  - collect (event_type, msg) pairs from the queue until a RESPONSE
    event arrives (or timeout)
  - never hold mutable per-request state on the executor instance
    (concurrent execute() calls must not collide)
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

import pytest


# --- Fake blpapi --------------------------------------------------------


class _Event:
    SESSION_STATUS = 1
    SERVICE_STATUS = 2
    RESPONSE = 3
    PARTIAL_RESPONSE = 4


class _FakeCorrelationId:
    def __init__(self, value: str):
        self._v = value

    def value(self) -> str:
        return self._v


class _FakeElement:
    def __init__(
        self,
        value: Any = None,
        children: Optional[dict[str, "_FakeElement"]] = None,
        is_array: bool = False,
        items: Optional[list["_FakeElement"]] = None,
    ):
        self._value = value
        self._children = children or {}
        self._is_array = is_array
        self._items = items or []
        self.appended: list[Any] = []

    def hasElement(self, name: str) -> bool:
        return name in self._children

    def getElement(self, name: str) -> "_FakeElement":
        return self._children[name]

    def getElementAsString(self, name: str) -> str:
        return str(self._children[name]._value)

    def appendValue(self, value: Any) -> None:
        self.appended.append(value)

    def isArray(self) -> bool:
        return self._is_array

    def numValues(self) -> int:
        return len(self._items)

    def getValueAsElement(self, i: int) -> "_FakeElement":
        return self._items[i]


class _FakeRequest:
    def __init__(self):
        self.elements: dict[str, _FakeElement] = {
            "securities": _FakeElement(),
            "fields": _FakeElement(),
        }
        self.set_calls: list[tuple[str, Any]] = []

    def getElement(self, name: str) -> _FakeElement:
        if name not in self.elements:
            self.elements[name] = _FakeElement()
        return self.elements[name]

    def set(self, path: str, value: Any) -> None:
        self.set_calls.append((path, value))


class _FakeService:
    def __init__(self, name: str):
        self.name = name
        self.created: list[_FakeRequest] = []

    def createRequest(self, _name: str) -> _FakeRequest:
        r = _FakeRequest()
        self.created.append(r)
        return r


class _FakeMessage:
    def __init__(
        self,
        cids: list[_FakeCorrelationId],
        elements: Optional[dict[str, _FakeElement]] = None,
    ):
        self._cids = cids
        self._elements = elements or {}

    def correlationIds(self) -> list[_FakeCorrelationId]:
        return self._cids

    def hasElement(self, name: str) -> bool:
        return name in self._elements

    def getElement(self, name: str) -> _FakeElement:
        return self._elements[name]


class _FakeSession:
    def __init__(self, opts, handler):
        self._handler = handler
        self.start_outcomes = [True]
        self.opened_services: list[str] = []
        self._services: dict[str, _FakeService] = {}
        self.sent_requests: list[tuple[Any, _FakeCorrelationId]] = []

    def start(self) -> bool:
        return self.start_outcomes.pop(0) if self.start_outcomes else True

    def stop(self) -> None:
        pass

    def openService(self, name: str) -> bool:
        self.opened_services.append(name)
        self._services[name] = _FakeService(name)
        return True

    def getService(self, name: str) -> _FakeService:
        return self._services[name]

    def sendRequest(self, request, correlationId) -> None:
        self.sent_requests.append((request, correlationId))

    def fire(self, event):
        self._handler(event, self)


class _FakeSessionOptions:
    def setServerHost(self, _h):
        pass

    def setServerPort(self, _p):
        pass


class _FakeBlpapi:
    Event = _Event
    CorrelationId = _FakeCorrelationId
    SessionOptions = _FakeSessionOptions

    def __init__(self):
        self.sessions: list[_FakeSession] = []

        outer = self

        def factory(opts, handler):
            s = _FakeSession(opts, handler)
            outer.sessions.append(s)
            return s

        self.Session = factory


# --- Fixtures ------------------------------------------------------------


@pytest.fixture
def fake_blpapi(monkeypatch):
    """Patch blpapi everywhere session_manager + blp_exec look for it."""
    fake = _FakeBlpapi()
    import blpremote_server.session_manager as sm

    monkeypatch.setattr(sm, "blpapi", fake)
    monkeypatch.setattr(sm, "BLPAPI_AVAILABLE", True)

    import blpremote_server.executor.blp_exec as be

    monkeypatch.setattr(be, "blpapi", fake)
    monkeypatch.setattr(be, "BLPAPI_AVAILABLE", True)

    yield fake


@pytest.fixture
def started_manager(fake_blpapi, monkeypatch):
    from blpremote_server.session_manager import SessionManager, set_manager

    mgr = SessionManager(
        services=["//blp/refdata"], blpapi_module=fake_blpapi
    )
    mgr.start()
    set_manager(mgr)
    yield mgr
    set_manager(None)


# --- Tests ---------------------------------------------------------------


def _build_simple_plan():
    from blpremote_server.models import (
        AppendOp,
        AuthToken,
        CollectResponseOp,
        CreateRequestOp,
        ExecutionPlan,
        OpenServiceOp,
        SendRequestOp,
        StartSessionOp,
    )

    return ExecutionPlan(
        auth=AuthToken(token="t"),
        ops=[
            StartSessionOp(),
            OpenServiceOp(service="//blp/refdata"),
            CreateRequestOp(
                service="//blp/refdata",
                request="ReferenceDataRequest",
                id="req1",
            ),
            AppendOp(id="req1", path="securities", value="AAPL US Equity"),
            AppendOp(id="req1", path="fields", value="PX_LAST"),
            SendRequestOp(id="req1", correlation_id="cid-aapl"),
            CollectResponseOp(correlation_id="cid-aapl", timeout_ms=2000),
        ],
    )


def test_executor_uses_long_lived_session(started_manager, fake_blpapi):
    """One execute() call should send via the singleton session and
    collect the response from the dispatched queue."""
    from blpremote_server.executor import execute_plan

    plan = _build_simple_plan()

    sess = fake_blpapi.sessions[0]

    def fire_response_after_send():
        # Wait for the request to be sent, then fire a synthetic
        # RESPONSE event with the matching correlation id.
        deadline = time.time() + 2.0
        while time.time() < deadline and not sess.sent_requests:
            time.sleep(0.005)
        msg = _FakeMessage(
            cids=[_FakeCorrelationId("cid-aapl")],
            elements={},
        )
        sess.fire(
            type("E", (), {
                "eventType": lambda self: _Event.RESPONSE,
                "__iter__": lambda self: iter([msg]),
            })()
        )

    t = threading.Thread(target=fire_response_after_send, daemon=True)
    t.start()

    result = execute_plan(plan)
    t.join(timeout=2.0)

    assert result.status == "ok"
    assert result.errors == []
    # Exactly one session was used (the long-lived one).
    assert len(fake_blpapi.sessions) == 1
    # The request was sent through that session.
    assert len(sess.sent_requests) == 1


def test_executor_raises_when_session_not_connected(fake_blpapi, monkeypatch):
    """If the session manager isn't connected, StartSessionOp surfaces
    a clear error instead of silently trying to send."""
    from blpremote_server.executor import execute_plan
    from blpremote_server.session_manager import (
        SessionManager,
        SessionState,
        set_manager,
    )

    mgr = SessionManager(blpapi_module=fake_blpapi)
    # Don't call start() — leaves it UNINITIALIZED.
    set_manager(mgr)
    try:
        plan = _build_simple_plan()
        result = execute_plan(plan)
        assert result.status == "error"
        assert any(
            "BLP_SESSION_FAIL" == e.code or "session not available" in e.message.lower()
            for e in result.errors
        )
    finally:
        set_manager(None)


def test_executor_collect_times_out(started_manager, fake_blpapi):
    """If no RESPONSE event arrives, collect raises TimeoutError which
    surfaces as a BLP_TIMEOUT error in the result."""
    from blpremote_server.executor import execute_plan

    plan = _build_simple_plan()
    # Override the Collect op timeout to something tiny.
    plan.ops[-1].timeout_ms = 100

    result = execute_plan(plan)
    assert result.status == "error"
    assert any(e.code == "BLP_TIMEOUT" for e in result.errors)


def test_concurrent_execute_calls_dont_collide(started_manager, fake_blpapi):
    """Two execute() calls with overlapping op IDs must succeed because
    request state lives on the call frame, not the executor."""
    from blpremote_server.executor import execute_plan
    from blpremote_server.models import (
        AppendOp,
        AuthToken,
        CollectResponseOp,
        CreateRequestOp,
        ExecutionPlan,
        OpenServiceOp,
        SendRequestOp,
        StartSessionOp,
    )

    sess = fake_blpapi.sessions[0]

    def make_plan(cid: str):
        return ExecutionPlan(
            auth=AuthToken(token="t"),
            ops=[
                StartSessionOp(),
                OpenServiceOp(service="//blp/refdata"),
                CreateRequestOp(
                    service="//blp/refdata",
                    request="ReferenceDataRequest",
                    id="req1",  # SAME id in both plans on purpose
                ),
                AppendOp(id="req1", path="securities", value=f"X-{cid}"),
                SendRequestOp(id="req1", correlation_id=cid),
                CollectResponseOp(correlation_id=cid, timeout_ms=2000),
            ],
        )

    def fire_for(cid: str):
        deadline = time.time() + 2.0
        target = None
        while time.time() < deadline:
            for req, c in sess.sent_requests:
                if c.value() == cid:
                    target = req
                    break
            if target is not None:
                break
            time.sleep(0.005)
        msg = _FakeMessage(cids=[_FakeCorrelationId(cid)], elements={})
        sess.fire(
            type("E", (), {
                "eventType": lambda self: _Event.RESPONSE,
                "__iter__": lambda self: iter([msg]),
            })()
        )

    results = {}

    def run(cid: str):
        results[cid] = execute_plan(make_plan(cid))

    threads = [
        threading.Thread(target=run, args=("cid-A",), daemon=True),
        threading.Thread(target=run, args=("cid-B",), daemon=True),
    ]
    fire_threads = [
        threading.Thread(target=fire_for, args=("cid-A",), daemon=True),
        threading.Thread(target=fire_for, args=("cid-B",), daemon=True),
    ]
    for t in threads:
        t.start()
    for t in fire_threads:
        t.start()
    for t in threads + fire_threads:
        t.join(timeout=3.0)

    assert results["cid-A"].status == "ok"
    assert results["cid-B"].status == "ok"
    assert len(sess.sent_requests) == 2
