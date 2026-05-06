"""In-memory coordination channel for cross-machine dev sessions.

Each authenticated user has an inbox keyed by their username. Senders post
messages addressed to a recipient; receivers drain their own inbox.

State is process-local and lost on server restart — durable record of
the conversation lives in `.coord/*.md` (git). This channel is for the
fast, low-latency turn between two interactive dev sessions.

`drain()` supports a long-poll mode: if `wait_ms > 0` and the inbox is
empty, the call blocks until a message arrives or the deadline elapses.
This lets clients stay event-driven instead of polling on a timer.
"""

import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from threading import Condition
from typing import Any

INBOX_MAX = 1000
BODY_MAX_BYTES = 64 * 1024

_inboxes: dict[str, deque[dict[str, Any]]] = defaultdict(
    lambda: deque(maxlen=INBOX_MAX)
)
# Single Condition guards the inbox dict and signals long-poll waiters
# when a new message lands. notify_all wakes everyone; recipients re-check
# their own inbox before returning.
_cond = Condition()


def post(*, to: str, sender: str, body: str) -> dict[str, Any]:
    if len(body.encode("utf-8")) > BODY_MAX_BYTES:
        raise ValueError(f"body exceeds {BODY_MAX_BYTES} bytes")
    msg = {
        "sender": sender,
        "to": to,
        "body": body,
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    with _cond:
        _inboxes[to].append(msg)
        _cond.notify_all()
    return msg


def drain(user: str, peek: bool = False, wait_ms: int = 0) -> list[dict[str, Any]]:
    """Return messages for ``user``. If ``peek`` is False (default) the
    inbox is cleared after read. If ``wait_ms`` > 0 and the inbox is
    empty, block up to that many ms waiting for a message to arrive.
    """
    deadline = time.monotonic() + (wait_ms / 1000.0) if wait_ms > 0 else None
    with _cond:
        while not _inboxes[user]:
            if deadline is None:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            _cond.wait(timeout=remaining)
        msgs = list(_inboxes[user])
        if not peek:
            _inboxes[user].clear()
    return msgs


def reset() -> None:
    with _cond:
        _inboxes.clear()
