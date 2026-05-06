"""SSE streaming for Bloomberg subscriptions.

Wraps the M1 subscription PoC into an HTTP endpoint per M3:
  - GET /v1/subscribe?topic=...&fields=...
  - Bearer auth.
  - One subscription per connection (M3 baseline; multi-topic is a
    follow-up).
  - Each blpapi SUBSCRIPTION_DATA / SUBSCRIPTION_STATUS event for the
    cid is serialised as one SSE message:
        event: subscription_data | subscription_status
        data: {"cid": ..., "message_type": ..., "fields": {...}}
  - Heartbeat (`event: ping`) every ~25s during idle to keep proxies
    (ngrok, nginx) from dropping the connection.
  - Clean unsubscribe + queue.unregister on disconnect or any error.

The session is the long-lived async session held by SessionManager;
the per-cid queue dispatch from session_manager._on_event is what
delivers events here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, AsyncIterator, Optional

from blpremote_server.session_manager import (
    BLPAPI_AVAILABLE,
    SessionManager,
    blpapi,
)

logger = logging.getLogger(__name__)

# Time the queue.get() blocks (in seconds) before yielding a heartbeat
# instead. Keep < 30s so most proxy idle-timeouts don't trip.
HEARTBEAT_INTERVAL_S = 25.0


def _serialise_message(msg: Any) -> dict[str, Any]:
    """Best-effort JSON-friendly view of a blpapi.Message.

    For SUBSCRIPTION_DATA messages this is the field tick payload
    (LAST_PRICE etc.). For SUBSCRIPTION_STATUS messages this is the
    status name + any reason elements. We don't reuse the M2
    normalisers here because subscription messages are flat enough
    that walking each top-level element into a dict suffices.
    """
    out: dict[str, Any] = {}
    try:
        out["message_type"] = str(msg.messageType())
    except Exception:
        out["message_type"] = "unknown"
    try:
        n = msg.numElements()
    except Exception:
        n = 0
    fields: dict[str, Any] = {}
    for i in range(n):
        try:
            elem = msg.getElement(i)
            name = str(elem.name())
            fields[name] = _value_of(elem)
        except Exception:
            continue
    if fields:
        out["fields"] = fields
    return out


def _value_of(elem: Any) -> Any:
    """Pull a JSON-serialisable value out of an Element (best-effort)."""
    try:
        if elem.isNull():
            return None
    except Exception:
        pass
    try:
        if elem.numElements() > 0:
            return {
                str(elem.getElement(i).name()): _value_of(elem.getElement(i))
                for i in range(elem.numElements())
            }
    except Exception:
        pass
    try:
        if elem.isArray():
            return [_value_of(elem.getValueAsElement(i)) for i in range(elem.numValues())]
    except Exception:
        pass
    try:
        return elem.getValue()
    except Exception:
        try:
            return str(elem.getValue())
        except Exception:
            return None


def _format_sse(event: str, data: Any) -> str:
    """Render one SSE message frame."""
    payload = data if isinstance(data, str) else json.dumps(data, default=str)
    # Each line of payload is prefixed with `data: ` per SSE spec.
    payload_lines = "\n".join(f"data: {line}" for line in payload.split("\n"))
    return f"event: {event}\n{payload_lines}\n\n"


def _event_type_name(event_type_int: int) -> str:
    """Map blpapi.Event integer to a stable SSE event name."""
    if not BLPAPI_AVAILABLE:
        return f"event_type_{event_type_int}"
    names = {
        getattr(blpapi.Event, "SUBSCRIPTION_DATA", -1): "subscription_data",
        getattr(blpapi.Event, "SUBSCRIPTION_STATUS", -1): "subscription_status",
        getattr(blpapi.Event, "RESPONSE", -1): "response",
        getattr(blpapi.Event, "PARTIAL_RESPONSE", -1): "partial_response",
    }
    return names.get(event_type_int, f"event_type_{event_type_int}")


async def stream_subscription(
    mgr: SessionManager,
    topic: str,
    fields: list[str],
    options: str = "",
    *,
    cid_prefix: str = "sse",
) -> AsyncIterator[str]:
    """Async generator yielding SSE-framed strings for a single subscription.

    Caller must wrap in StreamingResponse(..., media_type='text/event-stream').
    Cleans up subscription + queue on any exit (cancellation, exception,
    normal return).
    """
    if not BLPAPI_AVAILABLE:
        yield _format_sse("error", {"code": "BLP_UNAVAILABLE",
                                    "message": "blpapi not available"})
        return
    if not mgr.is_connected():
        yield _format_sse("error", {"code": "BLP_NOT_CONNECTED",
                                    "message": f"session state={mgr.state.value}"})
        return

    cid = f"{cid_prefix}-{uuid.uuid4().hex[:12]}"
    fields_str = ",".join(fields)
    # Open //blp/mktdata if not already open.
    try:
        mgr.get_service("//blp/mktdata")
    except Exception as exc:
        yield _format_sse(
            "error",
            {"code": "BLP_SERVICE_OPEN_FAILED",
             "message": f"failed to open //blp/mktdata: {exc}"},
        )
        return

    q = mgr.register_queue(cid)
    sub_list = blpapi.SubscriptionList()
    sub_list.add(topic, fields_str, options, blpapi.CorrelationId(cid))

    try:
        mgr.session().subscribe(sub_list)
    except Exception as exc:
        mgr.unregister_queue(cid)
        yield _format_sse(
            "error",
            {"code": "BLP_SUBSCRIBE_FAILED", "message": str(exc)},
        )
        return

    # Confirm subscription AFTER the blpapi call succeeds so clients
    # that act on the "subscribed" event know the request actually
    # made it to BBG (the SubscriptionStarted status event will
    # follow shortly).
    yield _format_sse(
        "subscribed",
        {"cid": cid, "topic": topic, "fields": fields, "options": options},
    )

    try:
        while True:
            try:
                event_type, msg = await asyncio.to_thread(
                    q.get, True, HEARTBEAT_INTERVAL_S
                )
            except Exception:
                # queue.Empty after the heartbeat interval — emit a
                # ping to keep the connection alive.
                yield _format_sse("ping", {"cid": cid})
                continue
            payload = _serialise_message(msg)
            payload["cid"] = cid
            yield _format_sse(_event_type_name(event_type), payload)
    except asyncio.CancelledError:
        # Client disconnected — fall through to cleanup.
        raise
    finally:
        try:
            mgr.session().unsubscribe(sub_list)
        except Exception:
            logger.warning("unsubscribe failed for cid=%s", cid, exc_info=True)
        mgr.unregister_queue(cid)
        logger.info("sse stream closed cid=%s topic=%s", cid, topic)
