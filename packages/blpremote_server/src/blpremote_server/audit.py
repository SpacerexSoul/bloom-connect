"""Per-execution audit log.

Writes one JSONL entry per ``/v1/execute`` call with enough information
to reconstruct what happened without leaking the full IR by default.
Entries land on the dedicated ``audit`` logger (configured in
``logging_setup.py``) and, optionally, tee to a file path set by
``settings.audit_log_path``.

Entry shape::

    {"ts": "2026-05-06T18:30:01.234Z",
     "user": "mac",
     "request_id": "f7c3...",
     "summary": "ReferenceDataRequest 2sec×3fld",
     "ir_hash": "abc123def4567890",
     "result_hash": "fedcba9876543210",
     "elapsed_ms": 327,
     "status": "ok",
     "errors_count": 0,
     "warnings_count": 0}

Optional fields (added when the relevant condition is true):

- ``ir`` — full IR ops list, gated by
  ``settings.audit_include_raw_ir`` (env ``BLPREMOTE_AUDIT_INCLUDE_RAW_IR``).
- ``error_codes`` / ``warning_codes`` — list of distinct codes when
  the entry has any errors or warnings, so grep-by-code works without
  needing the raw payload.

Endpoints excluded from audit (per M2 contract §5.1 / §6 design):

- ``/health``, ``/version`` — metadata, no business operation.
- ``/v1/schema/...`` — read-only schema cache lookup.
- ``/v1/subscribe`` — has its own per-session audit line at
  connection close; per-frame would 100x the volume.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from typing import Any, Optional

from blpremote_server.config import settings
from blpremote_server.models import (
    AppendOp,
    CreateRequestOp,
    ExecutionPlan,
    ExecutionResult,
    SetOp,
)


_audit_logger = logging.getLogger("blpremote.audit")

# Tee-to-file is opt-in (settings.audit_log_path). One lock guards the
# file handle so concurrent execute() calls don't interleave bytes.
_file_lock = threading.Lock()
_file_handle: Optional[Any] = None
_file_path_at_open: Optional[str] = None


def _get_file_handle() -> Optional[Any]:
    global _file_handle, _file_path_at_open
    path = settings.audit_log_path
    if not path:
        return None
    with _file_lock:
        if _file_handle is None or _file_path_at_open != path:
            if _file_handle is not None:
                try:
                    _file_handle.close()
                except OSError:
                    pass
            try:
                _file_handle = open(path, "a", encoding="utf-8", buffering=1)
                _file_path_at_open = path
            except OSError as e:
                _audit_logger.warning(
                    "audit log file open failed; falling back to logger only",
                    extra={"path": path, "error": str(e)},
                )
                _file_handle = None
                _file_path_at_open = None
        return _file_handle


def reset_for_tests() -> None:
    """Close the file handle so a test can change settings.audit_log_path."""
    global _file_handle, _file_path_at_open
    with _file_lock:
        if _file_handle is not None:
            try:
                _file_handle.close()
            except OSError:
                pass
        _file_handle = None
        _file_path_at_open = None


def _stable_json(value: Any) -> str:
    """Canonical JSON: sorted keys, no whitespace, default=str fallback.

    Used for hashing — we want identical plans to produce identical
    digests regardless of dict iteration order or Pydantic serialisation
    quirks.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash16(value: Any) -> str:
    """First 16 hex chars of sha256(stable JSON). Same shape we use
    on schema_cache ETags — keeps the audit grep-friendly without
    needing the full digest."""
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()[:16]


def hash_plan(plan: ExecutionPlan) -> str:
    """Hash the IR ops list (excluding the auth token, which is ephemeral)."""
    ops_dump = [op.model_dump() for op in plan.ops]
    return _hash16({"protocol_version": plan.protocol_version, "ops": ops_dump})


def hash_result(result: ExecutionResult) -> str:
    """Hash the result data (excluding timing). Two identical plans
    that produce identical data have identical result_hashes — useful
    for cache-hit detection in M4(C)."""
    return _hash16({"data": result.data, "errors": [e.model_dump() for e in result.errors]})


def summarise_plan(plan: ExecutionPlan) -> str:
    """One-line, grep-friendly summary derived from the IR.

    Conservative: if the plan doesn't fit any known shape, fall back
    to "<N>op plan" so we never crash audit on a novel IR.
    """
    create_op: Optional[CreateRequestOp] = None
    sets: dict[str, Any] = {}
    appends: dict[str, list[Any]] = {}
    for op in plan.ops:
        if isinstance(op, CreateRequestOp) and create_op is None:
            create_op = op
        elif isinstance(op, SetOp):
            sets[op.path] = op.value
        elif isinstance(op, AppendOp):
            appends.setdefault(op.path, []).append(op.value)

    if create_op is None:
        return f"{len(plan.ops)}op plan"

    req = create_op.request
    if req == "ReferenceDataRequest":
        nsec = len(appends.get("securities", []))
        nfld = len(appends.get("fields", []))
        return f"ReferenceDataRequest {nsec}sec×{nfld}fld"
    if req == "HistoricalDataRequest":
        nsec = len(appends.get("securities", []))
        nfld = len(appends.get("fields", []))
        period = sets.get("periodicitySelection", "?")
        return (
            f"HistoricalDataRequest {nsec}sec×{nfld}fld "
            f"{sets.get('startDate', '?')}-{sets.get('endDate', '?')} {period}"
        )
    if req == "IntradayBarRequest":
        return (
            f"IntradayBarRequest {sets.get('security', '?')} "
            f"{sets.get('eventType', '?')} {sets.get('interval', '?')}m"
        )
    if req == "IntradayTickRequest":
        n_evt = len(appends.get("eventTypes", []))
        return (
            f"IntradayTickRequest {sets.get('security', '?')} "
            f"{n_evt}eventTypes"
        )
    if req == "FieldInfoRequest":
        n_id = len(appends.get("id", []))
        return f"FieldInfoRequest {n_id}fields"
    return f"{req} ({len(plan.ops)}op)"


def audit_execute(
    *,
    plan: ExecutionPlan,
    result: ExecutionResult,
    user: str,
    elapsed_ms: int,
    cache_hit: bool = False,
) -> None:
    """Emit one audit entry for an executed plan.

    Always logs via the ``blpremote.audit`` logger. Additionally writes
    to ``settings.audit_log_path`` when set (tee). File-tee failures
    fall back to logger-only and emit a single warning per startup.

    ``cache_hit`` is set by the M4(C) request-cache short-circuit so
    grep-by-cache-hit on the audit log is straightforward (and so the
    `elapsed_ms` field doesn't mislead — a cache-served result has
    sub-ms latency that would otherwise look like an instrumented
    fast path rather than a cache hit).
    """
    entry: dict[str, Any] = {
        "user": user,
        "request_id": str(plan.request_id),
        "summary": summarise_plan(plan),
        "ir_hash": hash_plan(plan),
        "result_hash": hash_result(result),
        "elapsed_ms": elapsed_ms,
        "status": result.status,
        "errors_count": len(result.errors),
        "warnings_count": len(result.warnings),
        "cache_hit": cache_hit,
    }
    if result.errors:
        entry["error_codes"] = sorted({e.code for e in result.errors})
    if result.warnings:
        entry["warning_codes"] = sorted({w.code for w in result.warnings})
    if settings.audit_include_raw_ir:
        entry["ir"] = [op.model_dump() for op in plan.ops]

    # Structured logger picks the entry up via `extra=`.
    _audit_logger.info("execute", extra=entry)
    # M4 (B): also bump the audit-lines counter for /metrics.
    try:
        from blpremote_server.metrics import audit_lines_total
        audit_lines_total.inc()
    except Exception:
        # Metrics shouldn't ever throw, but if they do don't take down the audit.
        pass

    # Optional file tee for offline grep / ingestion. We rebuild the
    # full JSON line here (logger-side `extra` flow is JSON-formatter
    # specific; tee-to-file should always be the canonical shape).
    fh = _get_file_handle()
    if fh is not None:
        with _file_lock:
            try:
                # Add ts at write-time so the tee line matches the JSON
                # logger output even when log_format=text.
                line = dict(entry)
                line["ts"] = _utc_iso_z()
                fh.write(json.dumps(line, separators=(",", ":")) + os.linesep)
            except OSError as e:
                _audit_logger.warning(
                    "audit tee write failed",
                    extra={"path": settings.audit_log_path, "error": str(e)},
                )


def _utc_iso_z() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
