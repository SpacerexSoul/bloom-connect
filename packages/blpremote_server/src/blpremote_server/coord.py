"""In-memory coordination channel for cross-machine dev sessions.

Each authenticated user has an inbox keyed by their username. Senders post
messages addressed to a recipient; receivers drain their own inbox.

State is process-local and lost on server restart — durable record of
the conversation lives in `.coord/*.md` (git). This channel is for the
fast, low-latency turn between two interactive dev sessions.
"""

from collections import defaultdict, deque
from datetime import datetime, timezone
from threading import Lock
from typing import Any

INBOX_MAX = 1000
BODY_MAX_BYTES = 64 * 1024

_inboxes: dict[str, deque[dict[str, Any]]] = defaultdict(
    lambda: deque(maxlen=INBOX_MAX)
)
_lock = Lock()


def post(*, to: str, sender: str, body: str) -> dict[str, Any]:
    if len(body.encode("utf-8")) > BODY_MAX_BYTES:
        raise ValueError(f"body exceeds {BODY_MAX_BYTES} bytes")
    msg = {
        "sender": sender,
        "to": to,
        "body": body,
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    with _lock:
        _inboxes[to].append(msg)
    return msg


def drain(user: str, peek: bool = False) -> list[dict[str, Any]]:
    with _lock:
        msgs = list(_inboxes[user])
        if not peek:
            _inboxes[user].clear()
    return msgs


def reset() -> None:
    with _lock:
        _inboxes.clear()
