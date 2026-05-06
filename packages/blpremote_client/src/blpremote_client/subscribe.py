"""Client-side subscription to the M3 SSE endpoint.

Streams Bloomberg market-data events from the server's
``GET /v1/subscribe`` endpoint and yields parsed Python dicts.

Wire format (per SSE spec, set by the server in ``sse.py``):

    event: <name>
    data: <json>
    <blank line>

Event names the server emits: ``subscribed``, ``subscription_status``,
``subscription_data``, ``response``, ``ping``, ``error``.

Usage:

    >>> for frame in subscribe(host, "AAPL US Equity", ["LAST_PRICE"]):
    ...     if frame["event"] == "subscription_data":
    ...         px = frame["data"]["fields"].get("LAST_PRICE")
    ...         if px is not None:
    ...             print("AAPL last:", px)

Quiet by default — pings are filtered. Pass ``with_pings=True`` to see
the heartbeat frames if you want connection-health observability.
"""

from __future__ import annotations

import json
from typing import Any, Iterator, Optional
from urllib.parse import urlencode

import httpx

from blpremote_client.exceptions import (
    AuthenticationError,
    ConnectionError as RemoteConnectionError,
)
from blpremote_client.host import RemoteHost


# Server emits a heartbeat at ~25s when idle. Allow ~30s read timeout to
# tolerate that without spuriously thinking the stream died.
_READ_TIMEOUT_S = 30.0


def _parse_sse_stream(line_iter: Iterator[str]) -> Iterator[dict[str, Any]]:
    """Parse a stream of SSE-formatted lines into ``{event, data}`` dicts.

    Per SSE spec we accumulate ``event:`` and ``data:`` lines until a
    blank line dispatches the frame. Ignores comments (``: ...``) and
    unknown fields. ``data:`` is always JSON-parsed in our protocol;
    invalid JSON is preserved as the raw string so the caller can
    decide how to handle it.
    """
    event_name: Optional[str] = None
    data_parts: list[str] = []
    for raw in line_iter:
        line = raw.rstrip("\r\n")
        if line == "":
            # Dispatch: only yield if we actually saw fields.
            if event_name is not None or data_parts:
                payload_str = "\n".join(data_parts)
                try:
                    parsed: Any = json.loads(payload_str) if payload_str else None
                except json.JSONDecodeError:
                    parsed = payload_str  # fall back to raw, caller decides
                yield {"event": event_name or "message", "data": parsed}
                event_name = None
                data_parts = []
            continue
        if line.startswith(":"):
            continue  # SSE comment / keepalive
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            # SSE allows the leading space after the colon; strip it.
            chunk = line[len("data:"):]
            if chunk.startswith(" "):
                chunk = chunk[1:]
            data_parts.append(chunk)
        # Other field types (id:, retry:) are ignored — server doesn't
        # emit them today and we don't need them yet.


def subscribe(
    host: RemoteHost,
    topic: str,
    fields: list[str],
    *,
    options: Optional[dict[str, str]] = None,
    with_pings: bool = False,
    with_status: bool = True,
    timeout_s: float = _READ_TIMEOUT_S,
) -> Iterator[dict[str, Any]]:
    """Subscribe to a Bloomberg topic and yield parsed SSE frames.

    Args:
        host:         Connected RemoteHost.
        topic:        Bloomberg subscription topic (e.g.
                      ``"AAPL US Equity"``). The server URL-encodes it.
        fields:       Real-time field names — ``LAST_PRICE``, ``BID``,
                      ``ASK``, ``VOLUME``, etc.
        options:      Optional ``key=value`` BBG subscription options
                      (e.g. ``{"interval": "1.0"}``). Joined into the
                      ``options`` query param the server expects.
        with_pings:   Yield heartbeat ``ping`` events. Off by default —
                      most callers just want market data.
        with_status:  Yield ``subscription_status`` events (SubscriptionStarted,
                      SubscriptionStreamsActivated, SubscriptionTerminated).
                      Default on so callers see lifecycle.
        timeout_s:    Per-read timeout in seconds. Server pings every
                      ~25s so 30s is a safe floor.

    Yields:
        ``{"event": str, "data": dict}`` per SSE frame from the server.

    Raises:
        AuthenticationError:   Token expired or invalid (401).
        RemoteConnectionError: Network drop or server unavailable.

    Cleanup: closing the iterator (``break``, exception, or normal
    exit of a ``for`` loop) closes the HTTP connection, which triggers
    the server-side ``finally`` that calls ``session.unsubscribe()``
    and unregisters the queue. No explicit unsubscribe call needed.
    """
    token = host._get_token()
    params: dict[str, str] = {
        "topic": topic,
        "fields": ",".join(fields),
    }
    if options:
        params["options"] = ",".join(f"{k}={v}" for k, v in options.items())
    url = f"{host.host}/v1/subscribe?{urlencode(params)}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "text/event-stream",
    }

    # Stream with no overall timeout — only per-read. The connection is
    # long-lived by design.
    with httpx.Client(timeout=httpx.Timeout(connect=10.0, read=timeout_s, write=10.0, pool=10.0)) as client:
        try:
            with client.stream("GET", url, headers=headers) as response:
                if response.status_code == 401:
                    host._token_manager.clear_token()
                    raise AuthenticationError("Token expired or invalid")
                if response.status_code != 200:
                    body = b""
                    try:
                        body = response.read()
                    except httpx.HTTPError:
                        pass
                    raise RemoteConnectionError(
                        f"Subscribe failed: HTTP {response.status_code} "
                        f"{body[:200].decode(errors='replace')}"
                    )
                for frame in _parse_sse_stream(response.iter_lines()):
                    name = frame["event"]
                    if name == "ping" and not with_pings:
                        continue
                    if name == "subscription_status" and not with_status:
                        continue
                    yield frame
        except httpx.ConnectError as e:
            raise RemoteConnectionError(
                f"Cannot connect to {host.host}", host=host.host
            ) from e
        except httpx.ReadTimeout as e:
            # Server sends ping every ~25s, so a true read timeout means
            # the connection is silently dead from the client's POV. Surface.
            raise RemoteConnectionError(
                f"Subscription stream went quiet for >{timeout_s}s — "
                "server may have died or the network silently dropped"
            ) from e
        except httpx.RemoteProtocolError as e:
            raise RemoteConnectionError(f"Stream protocol error: {e}") from e
