"""LRU + TTL cache for executed plans, keyed by IR hash.

Sits in front of :func:`executor.execute_plan` to short-circuit any
plan whose canonical IR hash matches a recent successful execution.
The hash function is the same one the M4(A) audit uses
(:func:`audit.hash_plan`) so a cache hit also produces a matching
``ir_hash`` in the audit log.

Cacheability rules — only ``status='ok'`` results enter the cache:

- ``ok`` is cacheable. Identical IR hash + same data inside the TTL.
- ``partial`` (some data, some errors) is NOT cached. The errors
  could be transient (BBG field misclassification, session bounce);
  serving the same cached error for 5s would mask a recovery.
- ``error`` (no data, only errors) is NOT cached. Same reasoning.

TTL default is 5s — long enough to deduplicate burst calls (e.g. a
strategy fetching the same field for ten symbols in parallel) but
short enough that next-tick prices feel fresh. Callers who want
longer caching should use absolute timestamps in their IR so the
hash stays identical, and bump
``BLPREMOTE_REQUEST_CACHE_TTL_S``.

Settings:
    request_cache_max_entries — LRU bound (default 1024)
    request_cache_ttl_s       — entry TTL in seconds (default 5.0,
                                 0 disables the cache entirely)
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Optional

from blpremote_server.audit import hash_plan
from blpremote_server.config import settings
from blpremote_server.models import ExecutionPlan, ExecutionResult


class RequestCache:
    def __init__(self, *, max_entries: int, ttl_s: float):
        if max_entries <= 0:
            raise ValueError("max_entries must be > 0")
        self._max_entries = max_entries
        self._ttl_s = ttl_s
        self._cache: "OrderedDict[str, tuple[ExecutionResult, float]]" = OrderedDict()
        self._lock = threading.Lock()

    @property
    def ttl_s(self) -> float:
        return self._ttl_s

    @property
    def max_entries(self) -> int:
        return self._max_entries

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)

    def get(self, plan: ExecutionPlan) -> Optional[ExecutionResult]:
        """Return a cached result for an identical plan, else None.

        Bumps the LRU position on hit. Drops stale entries inline so
        a long-quiet cache cleans itself.
        """
        key = hash_plan(plan)
        now = time.monotonic()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            result, expiry = entry
            if now > expiry:
                del self._cache[key]
                return None
            self._cache.move_to_end(key)
            return result

    def put(self, plan: ExecutionPlan, result: ExecutionResult) -> None:
        """Store a result, only if status='ok'. Evicts LRU when full."""
        if self._ttl_s <= 0:
            return
        if result.status != "ok":
            return
        key = hash_plan(plan)
        expiry = time.monotonic() + self._ttl_s
        with self._lock:
            self._cache[key] = (result, expiry)
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)

    def reset(self) -> None:
        with self._lock:
            self._cache.clear()


# --- Module-level singleton -------------------------------------------

_cache: Optional[RequestCache] = None
_cache_lock = threading.Lock()


def get_request_cache() -> Optional[RequestCache]:
    """Lazily build the cache from current settings.

    Returns ``None`` if the cache is disabled
    (``settings.request_cache_ttl_s <= 0``). Callers should treat
    ``None`` as "skip cache, execute directly".
    """
    global _cache
    with _cache_lock:
        if settings.request_cache_ttl_s <= 0:
            return None
        if _cache is None:
            _cache = RequestCache(
                max_entries=settings.request_cache_max_entries,
                ttl_s=settings.request_cache_ttl_s,
            )
        return _cache


def reset_for_tests() -> None:
    """Force the singleton to rebuild from current settings on next get."""
    global _cache
    with _cache_lock:
        _cache = None
