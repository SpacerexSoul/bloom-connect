"""Tests for the SSE subscription stream (M3 server side).

Drives ``stream_subscription`` async generator with a fake
SessionManager + fake blpapi. We don't go through the FastAPI
endpoint here because the relevant logic (subscribe → drain → unsub →
unregister) is all in the generator. The endpoint itself is a thin
wrapper (Query param parsing + StreamingResponse).
"""

from __future__ import annotations

import asyncio
import json
import queue as _queue
from typing import Any, Optional

import pytest

# Tests use a tight heartbeat so the asyncio.to_thread inside the
# generator's main loop returns quickly when aclose() injects
# GeneratorExit (the thread isn't truly cancellable; it has to wait
# out its q.get timeout before the generator's finally can run).
HEARTBEAT_INTERVAL_S_FOR_TESTS = 0.05


# --- Fake blpapi ----------------------------------------------------


class _FakeEvent:
    SUBSCRIPTION_STATUS = 4
    SUBSCRIPTION_DATA = 8
    RESPONSE = 5
    PARTIAL_RESPONSE = 6


class _FakeCorrelationId:
    def __init__(self, v: str):
        self._v = v

    def value(self) -> str:
        return self._v


class _FakeSubList:
    def __init__(self):
        self.entries: list[tuple[str, str, str, _FakeCorrelationId]] = []

    def add(self, topic, fields, options, cid):
        self.entries.append((topic, fields, options, cid))


class _FakeMessage:
    def __init__(self, mtype: str, fields: Optional[dict[str, Any]] = None):
        self._mtype = mtype
        self._fields = fields or {}

    def messageType(self) -> str:
        return self._mtype

    def numElements(self) -> int:
        return len(self._fields)

    def getElement(self, i_or_name):
        if isinstance(i_or_name, int):
            keys = list(self._fields.keys())
            k = keys[i_or_name]
            return _FakeElem(self._fields[k], k)
        return _FakeElem(self._fields[i_or_name], i_or_name)


class _FakeElem:
    def __init__(self, value, name):
        self._v = value
        self._n = name

    def name(self):
        return self._n

    def isNull(self):
        return self._v is None

    def numElements(self):
        return 0

    def isArray(self):
        return False

    def getValue(self):
        return self._v


class _FakeSession:
    def __init__(self):
        self.subscribed: list[_FakeSubList] = []
        self.unsubscribed: list[_FakeSubList] = []

    def subscribe(self, sl):
        self.subscribed.append(sl)

    def unsubscribe(self, sl):
        self.unsubscribed.append(sl)


class _FakeMgr:
    def __init__(self, connected: bool = True):
        self._connected = connected
        self._sess = _FakeSession()
        self.queues: dict[str, _queue.Queue] = {}
        self.unregistered: list[str] = []
        self.opened_services: list[str] = []

    @property
    def state(self):
        # Cheap stub — only the .value attribute is read.
        class _S:
            value = "connected"
        return _S()

    def is_connected(self) -> bool:
        return self._connected

    def session(self):
        return self._sess

    def get_service(self, name: str):
        self.opened_services.append(name)
        return f"service:{name}"

    def register_queue(self, cid: str) -> _queue.Queue:
        q: _queue.Queue = _queue.Queue()
        self.queues[cid] = q
        return q

    def unregister_queue(self, cid: str):
        self.unregistered.append(cid)
        self.queues.pop(cid, None)


@pytest.fixture
def fake_blpapi(monkeypatch):
    """Patch BLPAPI_AVAILABLE + blpapi inside sse.py."""
    fake = type("F", (), {})()
    fake.SubscriptionList = _FakeSubList
    fake.CorrelationId = _FakeCorrelationId
    fake.Event = _FakeEvent
    import blpremote_server.sse as sse
    monkeypatch.setattr(sse, "BLPAPI_AVAILABLE", True)
    monkeypatch.setattr(sse, "blpapi", fake)
    # Tighten heartbeat for snappy tests.
    monkeypatch.setattr(sse, "HEARTBEAT_INTERVAL_S", HEARTBEAT_INTERVAL_S_FOR_TESTS)
    return fake


# --- Helpers --------------------------------------------------------


async def _drain(agen, n_frames: int) -> list[str]:
    """Pull n frames from the async generator, with a 2s safety cap."""
    out: list[str] = []
    for _ in range(n_frames):
        try:
            frame = await asyncio.wait_for(agen.__anext__(), timeout=2.0)
        except (StopAsyncIteration, asyncio.TimeoutError):
            break
        out.append(frame)
    return out


def _parse_sse(frame: str) -> tuple[str, dict[str, Any]]:
    """Split one SSE frame into (event_name, parsed_data_dict)."""
    lines = frame.strip().split("\n")
    event = next((l[7:] for l in lines if l.startswith("event: ")), "")
    data_lines = [l[6:] for l in lines if l.startswith("data: ")]
    raw = "\n".join(data_lines)
    try:
        return event, json.loads(raw)
    except json.JSONDecodeError:
        return event, {"_raw": raw}


# --- Tests ----------------------------------------------------------


@pytest.mark.asyncio
async def test_first_frame_is_subscribed_then_data(fake_blpapi):
    from blpremote_server.sse import stream_subscription

    mgr = _FakeMgr()
    agen = stream_subscription(
        mgr, topic="AAPL US Equity", fields=["LAST_PRICE"]
    )

    # First frame: subscribed event
    sub_frame = await asyncio.wait_for(agen.__anext__(), timeout=2.0)
    name, data = _parse_sse(sub_frame)
    assert name == "subscribed"
    assert data["topic"] == "AAPL US Equity"
    assert data["fields"] == ["LAST_PRICE"]

    # The session should have one subscribed list, queue should be registered.
    assert len(mgr._sess.subscribed) == 1
    cid = data["cid"]
    assert cid in mgr.queues

    # Push an event into the queue → next frame is subscription_data.
    mgr.queues[cid].put((
        _FakeEvent.SUBSCRIPTION_DATA,
        _FakeMessage("MarketDataEvents", {"LAST_PRICE": 285.5}),
    ))
    data_frame = await asyncio.wait_for(agen.__anext__(), timeout=2.0)
    name2, body2 = _parse_sse(data_frame)
    assert name2 == "subscription_data"
    assert body2["cid"] == cid
    assert body2["fields"] == {"LAST_PRICE": 285.5}
    assert body2["message_type"] == "MarketDataEvents"

    # Close the generator. Cleanup correctness is asserted in
    # test_cleanup_runs_on_task_cancel via a different driving
    # pattern; here we just confirm the data flow itself.
    await agen.aclose()


@pytest.mark.asyncio
async def test_heartbeat_when_idle(fake_blpapi):
    """If no event arrives within HEARTBEAT_INTERVAL_S, a `ping` frame
    fires so the proxy doesn't drop the connection."""
    from blpremote_server.sse import stream_subscription

    mgr = _FakeMgr()
    agen = stream_subscription(mgr, topic="AAPL US Equity", fields=["LAST_PRICE"])

    # Skip the subscribed frame, then expect a ping after the (tiny in
    # tests) heartbeat interval since no data arrives.
    await asyncio.wait_for(agen.__anext__(), timeout=2.0)
    ping = await asyncio.wait_for(agen.__anext__(), timeout=2.0)
    name, body = _parse_sse(ping)
    assert name == "ping"
    assert "cid" in body
    await agen.aclose()


@pytest.mark.asyncio
async def test_subscription_status_routed_through(fake_blpapi):
    from blpremote_server.sse import stream_subscription

    mgr = _FakeMgr()
    agen = stream_subscription(mgr, topic="AAPL US Equity", fields=["LAST_PRICE"])
    await asyncio.wait_for(agen.__anext__(), timeout=2.0)  # subscribed
    cid = list(mgr.queues.keys())[0]

    mgr.queues[cid].put((
        _FakeEvent.SUBSCRIPTION_STATUS,
        _FakeMessage("SubscriptionStarted", {"reason": "OK"}),
    ))
    frame = await asyncio.wait_for(agen.__anext__(), timeout=2.0)
    name, body = _parse_sse(frame)
    assert name == "subscription_status"
    assert body["message_type"] == "SubscriptionStarted"
    assert body["fields"] == {"reason": "OK"}
    await agen.aclose()


@pytest.mark.asyncio
async def test_yields_error_when_session_not_connected(fake_blpapi):
    from blpremote_server.sse import stream_subscription

    mgr = _FakeMgr(connected=False)
    agen = stream_subscription(mgr, topic="AAPL US Equity", fields=["LAST_PRICE"])
    frame = await asyncio.wait_for(agen.__anext__(), timeout=2.0)
    name, body = _parse_sse(frame)
    assert name == "error"
    assert body["code"] == "BLP_NOT_CONNECTED"
    # Should terminate cleanly after the error frame.
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(agen.__anext__(), timeout=1.0)
    # No subscribe attempted, no queue registered.
    assert mgr._sess.subscribed == []


@pytest.mark.asyncio
async def test_cleanup_runs_on_task_cancel(fake_blpapi):
    """Wrap the generator in an asyncio.Task and cancel it the way
    FastAPI does when a client disconnects. The finally block must
    run unsubscribe + queue.unregister."""
    from blpremote_server.sse import stream_subscription

    mgr = _FakeMgr()
    captured_cid: list[str] = []

    async def consume():
        agen = stream_subscription(
            mgr, topic="AAPL US Equity", fields=["LAST_PRICE"]
        )
        async for _ in agen:
            if mgr.queues and not captured_cid:
                captured_cid.append(next(iter(mgr.queues)))

    task = asyncio.create_task(consume())
    # Let the generator subscribe and start polling.
    await asyncio.sleep(HEARTBEAT_INTERVAL_S_FOR_TESTS * 2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    # Give the in-flight asyncio.to_thread q.get(timeout=...) headroom
    # to wake on its deadline so the generator's finally can run.
    await asyncio.sleep(HEARTBEAT_INTERVAL_S_FOR_TESTS * 4)
    assert captured_cid, "expected at least one cid registered"
    cid = captured_cid[0]
    assert cid in mgr.unregistered
    assert len(mgr._sess.unsubscribed) == 1
