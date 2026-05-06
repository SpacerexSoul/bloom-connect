"""Tests for SessionManager — the long-lived blpapi session wrapper.

Uses a fake blpapi module so the tests run without Bloomberg Terminal.
The fake exposes hooks to (a) control session.start() outcomes, (b)
fire status events back into the handler, (c) inspect what services
were opened.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

import pytest

from blpremote_server.session_manager import SessionManager, SessionState


# --- Fake blpapi -----------------------------------------------------


class _Event:
    SESSION_STATUS = 1
    SERVICE_STATUS = 2
    RESPONSE = 3
    PARTIAL_RESPONSE = 4
    SUBSCRIPTION_DATA = 5


class _FakeCorrelationId:
    def __init__(self, value: str):
        self._v = value

    def value(self) -> str:
        return self._v


class _FakeMessage:
    def __init__(self, mtype: str, cids: Optional[list[_FakeCorrelationId]] = None):
        self._mtype = mtype
        self._cids = cids or []

    def messageType(self) -> str:
        return self._mtype

    def correlationIds(self) -> list[_FakeCorrelationId]:
        return self._cids


class _FakeEventObj:
    def __init__(self, etype: int, msgs: list[_FakeMessage]):
        self._etype = etype
        self._msgs = msgs

    def eventType(self) -> int:
        return self._etype

    def __iter__(self):
        return iter(self._msgs)


class _FakeSession:
    """Fake blpapi.Session — start/stop/openService and event firing."""

    def __init__(self, opts: Any, handler: Callable[[Any, Any], None]):
        self._opts = opts
        self._handler = handler
        self.start_outcomes: list[bool] = [True]
        self.opened_services: list[str] = []
        self.stopped = False

    # The next start() pops from start_outcomes (defaults to True).
    def start(self) -> bool:
        outcome = self.start_outcomes.pop(0) if self.start_outcomes else True
        return outcome

    def stop(self) -> None:
        self.stopped = True

    def openService(self, name: str) -> bool:
        self.opened_services.append(name)
        return True

    def getService(self, name: str) -> str:
        return f"service::{name}"

    # Test hook: deliver an event into the handler.
    def fire(self, event: _FakeEventObj) -> None:
        self._handler(event, self)


class _FakeSessionOptions:
    def __init__(self):
        self.host = None
        self.port = None

    def setServerHost(self, host: str) -> None:
        self.host = host

    def setServerPort(self, port: int) -> None:
        self.port = port


class _FakeBlpapi:
    """Drop-in replacement for the blpapi module."""

    Event = _Event
    CorrelationId = _FakeCorrelationId

    def __init__(self):
        self.SessionOptions = _FakeSessionOptions
        self.sessions: list[_FakeSession] = []

        outer = self

        class _SessionFactory:
            def __call__(self_inner, opts, handler):
                s = _FakeSession(opts, handler)
                outer.sessions.append(s)
                return s

        self.Session = _SessionFactory()


# --- Fixtures --------------------------------------------------------


@pytest.fixture
def fake_blpapi() -> _FakeBlpapi:
    return _FakeBlpapi()


@pytest.fixture
def fast_backoff(monkeypatch):
    """Shrink reconnect backoff so timing-sensitive tests run quickly."""
    monkeypatch.setattr(SessionManager, "INITIAL_BACKOFF_S", 0.01)
    monkeypatch.setattr(SessionManager, "MAX_BACKOFF_S", 0.05)
    monkeypatch.setattr(SessionManager, "MAX_RECONNECT_ATTEMPTS", 3)


# --- Tests -----------------------------------------------------------


def test_start_opens_session_and_services(fake_blpapi):
    mgr = SessionManager(
        host="h", port=1, services=["//blp/refdata", "//blp/news"],
        blpapi_module=fake_blpapi,
    )
    mgr.start()
    assert mgr.state == SessionState.CONNECTED
    assert mgr.is_connected()
    assert mgr.reconnect_attempts == 0

    sess = fake_blpapi.sessions[0]
    assert sorted(sess.opened_services) == ["//blp/news", "//blp/refdata"]
    assert sess._opts.host == "h" and sess._opts.port == 1


def test_start_failure_transitions_to_failed(fake_blpapi):
    mgr = SessionManager(blpapi_module=fake_blpapi)

    # Force the next session.start() to fail.
    original_factory = fake_blpapi.Session

    def fail_once(opts, handler):
        s = original_factory(opts, handler)
        s.start_outcomes = [False]
        return s

    fake_blpapi.Session = fail_once

    with pytest.raises(Exception):
        mgr.start()
    assert mgr.state == SessionState.FAILED


def test_session_lost_event_triggers_reconnect(fake_blpapi, fast_backoff):
    mgr = SessionManager(
        services=["//blp/refdata"], blpapi_module=fake_blpapi
    )
    mgr.start()
    first_session = fake_blpapi.sessions[0]
    assert mgr.is_connected()

    # Fire SessionTerminated into the handler.
    first_session.fire(
        _FakeEventObj(
            _Event.SESSION_STATUS, [_FakeMessage("SessionTerminated")]
        )
    )

    # Wait for reconnect to land.
    deadline = time.time() + 2.0
    while time.time() < deadline and not mgr.is_connected():
        time.sleep(0.01)

    assert mgr.is_connected(), f"expected reconnect, state={mgr.state}"
    assert mgr.reconnect_attempts >= 1
    assert mgr.last_reconnect_ts is not None
    # New session was created and old one was stopped.
    assert len(fake_blpapi.sessions) >= 2
    assert first_session.stopped is True
    # Services were re-opened on the new session.
    assert "//blp/refdata" in fake_blpapi.sessions[-1].opened_services


def test_reconnect_gives_up_after_max_attempts(fake_blpapi, fast_backoff):
    mgr = SessionManager(blpapi_module=fake_blpapi)
    mgr.start()
    first = fake_blpapi.sessions[0]

    # Make every subsequent session.start() fail.
    base_factory = fake_blpapi.Session

    def always_fail(opts, handler):
        s = base_factory(opts, handler)
        s.start_outcomes = [False]
        return s

    fake_blpapi.Session = always_fail

    first.fire(
        _FakeEventObj(
            _Event.SESSION_STATUS, [_FakeMessage("SessionTerminated")]
        )
    )

    deadline = time.time() + 3.0
    while time.time() < deadline and mgr.state != SessionState.FAILED:
        time.sleep(0.01)
    assert mgr.state == SessionState.FAILED


def test_event_dispatch_routes_by_correlation_id(fake_blpapi):
    mgr = SessionManager(blpapi_module=fake_blpapi)
    mgr.start()
    sess = fake_blpapi.sessions[0]

    q = mgr.register_queue("cid-1")
    msg = _FakeMessage("ResponseMsg", cids=[_FakeCorrelationId("cid-1")])
    sess.fire(_FakeEventObj(_Event.RESPONSE, [msg]))

    etype, dispatched = q.get(timeout=1.0)
    assert etype == _Event.RESPONSE
    assert dispatched is msg


def test_unknown_correlation_id_is_dropped(fake_blpapi):
    mgr = SessionManager(blpapi_module=fake_blpapi)
    mgr.start()
    sess = fake_blpapi.sessions[0]

    q = mgr.register_queue("cid-A")
    other = _FakeMessage("ResponseMsg", cids=[_FakeCorrelationId("cid-B")])
    sess.fire(_FakeEventObj(_Event.RESPONSE, [other]))

    assert q.empty()


def test_status_events_are_not_dispatched_to_queues(fake_blpapi):
    mgr = SessionManager(blpapi_module=fake_blpapi)
    mgr.start()
    sess = fake_blpapi.sessions[0]

    q = mgr.register_queue("any")
    sess.fire(
        _FakeEventObj(
            _Event.SESSION_STATUS,
            [_FakeMessage("SessionStarted", cids=[_FakeCorrelationId("any")])],
        )
    )
    assert q.empty()


def test_get_service_opens_lazily(fake_blpapi):
    mgr = SessionManager(blpapi_module=fake_blpapi)  # no pre-opened
    mgr.start()
    sess = fake_blpapi.sessions[0]
    assert sess.opened_services == []

    svc = mgr.get_service("//blp/refdata")
    assert svc == "service:://blp/refdata"
    assert sess.opened_services == ["//blp/refdata"]
    # Second call is cached.
    mgr.get_service("//blp/refdata")
    assert sess.opened_services == ["//blp/refdata"]


def test_stop_is_idempotent(fake_blpapi):
    mgr = SessionManager(blpapi_module=fake_blpapi)
    mgr.start()
    mgr.stop()
    assert mgr.state == SessionState.STOPPED
    # Second stop is a no-op.
    mgr.stop()
    assert mgr.state == SessionState.STOPPED


def test_no_blpapi_module_raises(monkeypatch):
    mgr = SessionManager(blpapi_module=None)
    with pytest.raises(Exception):
        mgr.start()
    assert mgr.state == SessionState.FAILED
