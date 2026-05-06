"""Per-service schema cache.

Per M2 IR contract §2.4 / §5.1:
  - Singleton, populated lazily (or pre-warmed at boot via the
    ``BLPREMOTE_SCHEMA_PREWARM`` env flag).
  - Source of truth: ``blpapi.Service.numOperations()`` /
    ``getOperation(i)``. The wire ``SchemaRequest`` path that
    `_normalize_schema` covers is rarely fired in practice; the
    canonical introspection is in-process via the Service object
    held by the SessionManager.
  - Refreshed on session reconnect (services are re-opened then;
    cache invalidates so the next ``get`` re-warms).
  - Read API returns ``(schema_dict, etag)`` so the HTTP layer
    can short-circuit If-None-Match.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from typing import Any, Optional

from blpremote_server.session_manager import SessionManager

logger = logging.getLogger(__name__)


class SchemaCache:
    """Caches per-service schema dicts; ETag derived from content."""

    def __init__(self, mgr: SessionManager):
        self._mgr = mgr
        self._cache: dict[str, dict[str, Any]] = {}
        self._etag: dict[str, str] = {}
        self._lock = threading.RLock()

    # ---- Public API -----------------------------------------------

    def get(self, service_name: str) -> Optional[tuple[dict[str, Any], str]]:
        """Return (schema_dict, etag) if cached, else None."""
        with self._lock:
            d = self._cache.get(service_name)
            if d is None:
                return None
            return d, self._etag[service_name]

    def warm(self, service_name: str) -> tuple[dict[str, Any], str]:
        """Force-populate the cache for the given service. Returns
        (schema_dict, etag)."""
        svc = self._mgr.get_service(service_name)
        info = self._serialize_service(svc, service_name)
        etag = self._compute_etag(info)
        with self._lock:
            self._cache[service_name] = info
            self._etag[service_name] = etag
        logger.info(
            "schema cache: warmed %s (%d operations, etag=%s)",
            service_name,
            len(info.get("operations", [])),
            etag,
        )
        return info, etag

    def get_or_warm(self, service_name: str) -> tuple[dict[str, Any], str]:
        cached = self.get(service_name)
        if cached is not None:
            return cached
        return self.warm(service_name)

    def invalidate(self, service_name: Optional[str] = None) -> None:
        """Drop one entry (or the whole cache if None). Wired into
        SessionManager reconnect."""
        with self._lock:
            if service_name is None:
                self._cache.clear()
                self._etag.clear()
            else:
                self._cache.pop(service_name, None)
                self._etag.pop(service_name, None)

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._cache.keys())

    # ---- Internals ------------------------------------------------

    @staticmethod
    def _serialize_service(svc: Any, name: str) -> dict[str, Any]:
        """Walk a blpapi.Service into a JSON-serialisable dict.

        For M2 (d) the shape is intentionally shallow — operation
        names + their request/response element-name lists. M8 (LLM
        query builder) can extend this with full element type info
        without changing the cache contract.
        """
        operations: list[dict[str, Any]] = []
        try:
            num = svc.numOperations()
        except Exception:
            num = 0
        for i in range(num):
            try:
                op = svc.getOperation(i)
                op_name = str(op.name())
                operations.append(
                    {
                        "name": op_name,
                        "request_elements": _describe_definition(
                            getattr(op, "requestDefinition", lambda: None)()
                        ),
                        "response_elements": _describe_response_definitions(op),
                    }
                )
            except Exception as e:
                operations.append({"name": f"_unknown_{i}", "error": str(e)})
        return {
            "service": name,
            "operations": operations,
        }

    @staticmethod
    def _compute_etag(info: dict[str, Any]) -> str:
        canonical = json.dumps(info, sort_keys=True, default=str).encode()
        return hashlib.sha256(canonical).hexdigest()[:16]


def _describe_definition(definition: Any) -> Optional[list[str]]:
    """Top-level element names of a SchemaTypeDefinition (or None)."""
    if definition is None:
        return None
    try:
        type_def = definition.typeDefinition()
    except Exception:
        type_def = definition
    out: list[str] = []
    try:
        for i in range(type_def.numElementDefinitions()):
            elem_def = type_def.getElementDefinition(i)
            out.append(str(elem_def.name()))
    except Exception:
        return None
    return out


def _describe_response_definitions(op: Any) -> list[dict[str, Any]]:
    """blpapi Operation may have multiple response definitions
    (e.g. PARTIAL_RESPONSE + RESPONSE)."""
    out: list[dict[str, Any]] = []
    try:
        n = op.numResponseDefinitions()
    except Exception:
        return out
    for i in range(n):
        try:
            rd = op.getResponseDefinition(i)
            out.append(
                {
                    "name": str(rd.name()),
                    "elements": _describe_definition(rd),
                }
            )
        except Exception:
            pass
    return out


# --- Module-level singleton -----------------------------------------

_cache: Optional[SchemaCache] = None
_cache_lock = threading.Lock()


def get_schema_cache() -> Optional[SchemaCache]:
    """Return the process-wide SchemaCache, or None if not yet initialised."""
    with _cache_lock:
        return _cache


def set_schema_cache(c: Optional[SchemaCache]) -> None:
    """App lifespan sets this once on boot; tests reset to None."""
    global _cache
    with _cache_lock:
        _cache = c
