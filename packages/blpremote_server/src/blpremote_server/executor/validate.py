"""Execution plan validation.

Validator is the IR's trust boundary (per M2 contract §2.5): once a
plan passes ``validate_plan`` it's known-shaped and known-bounded.
The executor still surfaces blpapi-side errors (bad fields, BBG
session hiccups), but the structural contract is enforced here.

Returns a list of ``ErrorDetail`` *warnings* — non-fatal advisory
items the caller plumbs into ``ExecutionResult.warnings``. Hard
errors still raise ``ValidationError``.
"""

from collections import defaultdict
from typing import Optional

from blpremote_server.config import settings
from blpremote_server.exceptions import ValidationError
from blpremote_server.models import (
    AppendOp,
    CollectRefdataResponseOp,
    CollectResponseOp,
    CreateRequestOp,
    ErrorDetail,
    ExecutionPlan,
    OpenServiceOp,
    SetOp,
)

SUPPORTED_PROTOCOL_VERSIONS = ("1.0", "1.1")

# Hard-coded "fully verified by us" services. Anything in
# settings.allowed_services that is NOT here triggers an
# IR_UNVERIFIED_SERVICE warning so the caller knows they're past
# the well-trodden path. Unrelated to allow/deny — that's still
# settings.allowed_services. M8 (LLM query builder) layers
# schema-driven validation on top of this; the hard-coded set stays
# as the fallback so the validator never has to phone home.
SUPPORTED_SERVICES = frozenset({
    "//blp/refdata",
    "//blp/news",
    "//blp/apiflds",
    "//blp/instruments",
})

# Per-request-type required elements. The validator checks that each
# CreateRequestOp's request_id has Set/Append ops covering every
# element here before SendRequestOp. Catches client mistakes (forgot
# to set 'interval' on an IntradayBarRequest) at validate time
# rather than letting blpapi reject at send time with a less-clear
# message.
REQUIRED_ELEMENTS: dict[str, tuple[str, ...]] = {
    "ReferenceDataRequest": ("securities", "fields"),
    "HistoricalDataRequest": ("securities", "fields", "startDate", "endDate"),
    "IntradayBarRequest": (
        "security", "eventType", "interval",
        "startDateTime", "endDateTime",
    ),
    "IntradayTickRequest": (
        "security", "eventTypes", "startDateTime", "endDateTime",
    ),
    "FieldInfoRequest": ("id",),
}


def validate_plan(plan: ExecutionPlan) -> list[ErrorDetail]:
    """Validate an execution plan against allowlists, limits, and
    per-request required elements.

    Raises ``ValidationError`` if the plan is structurally invalid.
    Returns a (possibly empty) list of ``ErrorDetail`` *warnings* —
    e.g. ``IR_UNVERIFIED_SERVICE`` when a service is allowed but not
    in the hard-coded supported set. Callers should append these to
    ``ExecutionResult.warnings``.
    """
    if plan.protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
        raise ValidationError(
            f"Unsupported protocol_version '{plan.protocol_version}'. "
            f"Supported: {', '.join(SUPPORTED_PROTOCOL_VERSIONS)}"
        )

    warnings: list[ErrorDetail] = []
    # services we've already warned about — one warning per service,
    # not one per OpenServiceOp / CreateRequestOp.
    warned_services: set[str] = set()

    # Track per-request_id state for required-element checking.
    request_types: dict[str, str] = {}
    paths_seen: dict[str, set[str]] = defaultdict(set)

    security_count = 0
    field_count = 0

    for op in plan.ops:
        if isinstance(op, OpenServiceOp):
            _check_service(op.service, warnings, warned_services)

        elif isinstance(op, CreateRequestOp):
            _check_service(op.service, warnings, warned_services)
            if op.request not in settings.allowed_request_types:
                raise ValidationError(
                    f"Request type '{op.request}' is not allowed. "
                    f"Allowed types: {settings.allowed_request_types}"
                )
            request_types[op.id] = op.request

        elif isinstance(op, AppendOp):
            paths_seen[op.id].add(op.path)
            if op.path == "securities":
                security_count += 1
            elif op.path == "fields":
                field_count += 1

        elif isinstance(op, SetOp):
            paths_seen[op.id].add(op.path)

        elif isinstance(op, (CollectResponseOp, CollectRefdataResponseOp)):
            max_timeout = min(plan.limits.max_timeout_ms, settings.max_timeout_ms)
            if op.timeout_ms > max_timeout:
                raise ValidationError(
                    f"Timeout {op.timeout_ms}ms exceeds maximum {max_timeout}ms"
                )

    # Per-request required-element check — fast-fail before the
    # blpapi layer sees a half-built request.
    for req_id, req_type in request_types.items():
        required = REQUIRED_ELEMENTS.get(req_type)
        if required is None:
            # Unknown request type for our element table — covered by
            # the allowed_request_types gate above; don't double-fail.
            continue
        seen = paths_seen.get(req_id, set())
        missing = [r for r in required if r not in seen]
        if missing:
            raise ValidationError(
                f"{req_type} (id={req_id!r}) missing required "
                f"element{'s' if len(missing) > 1 else ''}: "
                f"{', '.join(repr(m) for m in missing)}"
            )

    max_securities = min(plan.limits.max_securities, settings.max_securities)
    if security_count > max_securities:
        raise ValidationError(
            f"Too many securities ({security_count}). Maximum: {max_securities}"
        )

    max_fields = min(plan.limits.max_fields, settings.max_fields)
    if field_count > max_fields:
        raise ValidationError(
            f"Too many fields ({field_count}). Maximum: {max_fields}"
        )

    return warnings


def _check_service(
    service: str,
    warnings: list[ErrorDetail],
    warned_services: set[str],
) -> None:
    """Reject if not in allowlist; warn if in allowlist but not in
    the hard-coded supported set."""
    if service not in settings.allowed_services:
        raise ValidationError(
            f"Service '{service}' is not allowed. "
            f"Allowed services: {settings.allowed_services}"
        )
    if service not in SUPPORTED_SERVICES and service not in warned_services:
        warned_services.add(service)
        warnings.append(
            ErrorDetail(
                code="IR_UNVERIFIED_SERVICE",
                message=(
                    f"Service '{service}' is allowed but not on the "
                    f"hard-coded supported list. Requests will execute, "
                    f"but the validator can't enforce per-request schema."
                ),
            )
        )
