"""Per-user token-bucket rate limiter.

In-memory, single-process. Each user gets their own bucket sized by
``settings.rate_limit_burst`` and refilled at
``settings.rate_limit_per_minute / 60`` tokens per second. ``acquire``
returns True if a token was available and consumed; False if the
caller should be throttled (the HTTP layer turns False into 429).

Why a token bucket and not a sliding window?
- Bursts get absorbed cleanly: a fan-out of N parallel calls all
  succeed if N ≤ burst, no false-positive throttles on legitimate
  parallelism.
- Refills are continuous, not bucketed: no edge-of-window jitter
  where a caller blocked at :59 gets unthrottled at :00.
- O(1) per call, O(users) memory. We're a single-team server; this
  scales fine for years.

Settings:
    rate_limit_per_minute — max sustained req/min/user (0 = disabled)
    rate_limit_burst      — bucket size, max parallel allowance
                            (0 = disabled)

Either field at 0 disables limiting entirely. Defaults are generous
(300/min, 30 burst).
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from blpremote_server.config import settings


class TokenBucket:
    def __init__(self, *, capacity: int, refill_per_second: float):
        if capacity <= 0 or refill_per_second <= 0:
            raise ValueError("capacity and refill_per_second must be positive")
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, *, now: Optional[float] = None) -> bool:
        """Try to take one token. Returns True on success, False if
        the bucket is empty (caller should be throttled)."""
        now = now if now is not None else time.monotonic()
        with self._lock:
            elapsed = now - self._last_refill
            if elapsed > 0:
                self._tokens = min(
                    self.capacity, self._tokens + elapsed * self.refill_per_second,
                )
                self._last_refill = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False

    def peek(self) -> float:
        """Read current token count without consuming. For tests / debug."""
        with self._lock:
            return self._tokens


class _PerUserLimiter:
    """Lazily creates a TokenBucket per user, lazily reads settings.

    Settings are read every check so a runtime config change (e.g.
    test monkeypatch) takes effect without restarting the server.
    """

    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def _disabled(self) -> bool:
        return (
            settings.rate_limit_per_minute <= 0
            or settings.rate_limit_burst <= 0
        )

    def _bucket_for(self, user: str) -> TokenBucket:
        with self._lock:
            bucket = self._buckets.get(user)
            wanted_capacity = settings.rate_limit_burst
            wanted_refill = settings.rate_limit_per_minute / 60.0
            # Recreate the bucket if settings changed since last bucket build.
            if (
                bucket is None
                or bucket.capacity != wanted_capacity
                or abs(bucket.refill_per_second - wanted_refill) > 1e-9
            ):
                bucket = TokenBucket(
                    capacity=wanted_capacity,
                    refill_per_second=wanted_refill,
                )
                self._buckets[user] = bucket
            return bucket

    def check(self, user: str) -> bool:
        """Returns True if the request is allowed, False if throttled."""
        if self._disabled():
            return True
        return self._bucket_for(user).acquire()

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


# Module singleton.
_limiter = _PerUserLimiter()


def check_rate_limit(user: str) -> bool:
    """True = allowed; False = should 429."""
    return _limiter.check(user)


def reset_for_tests() -> None:
    _limiter.reset()
