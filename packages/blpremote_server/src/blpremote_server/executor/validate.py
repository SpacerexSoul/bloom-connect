"""Execution plan validation."""

from blpremote_server.config import settings
from blpremote_server.exceptions import ValidationError
from blpremote_server.models import (
    AppendOp,
    CollectResponseOp,
    CreateRequestOp,
    ExecutionPlan,
    OpenServiceOp,
)


def validate_plan(plan: ExecutionPlan) -> None:
    """
    Validate an execution plan against allowlists and limits.

    Raises ValidationError if the plan is invalid.
    """
    # Track counts for limit validation
    security_count = 0
    field_count = 0

    for op in plan.ops:
        # Validate open_service
        if isinstance(op, OpenServiceOp):
            if op.service not in settings.allowed_services:
                raise ValidationError(
                    f"Service '{op.service}' is not allowed. "
                    f"Allowed services: {settings.allowed_services}"
                )

        # Validate create_request
        elif isinstance(op, CreateRequestOp):
            if op.service not in settings.allowed_services:
                raise ValidationError(
                    f"Service '{op.service}' is not allowed. "
                    f"Allowed services: {settings.allowed_services}"
                )
            if op.request not in settings.allowed_request_types:
                raise ValidationError(
                    f"Request type '{op.request}' is not allowed. "
                    f"Allowed types: {settings.allowed_request_types}"
                )

        # Track securities and fields
        elif isinstance(op, AppendOp):
            if op.path == "securities":
                security_count += 1
            elif op.path == "fields":
                field_count += 1

        # Validate timeout
        elif isinstance(op, CollectResponseOp):
            max_timeout = min(plan.limits.max_timeout_ms, settings.max_timeout_ms)
            if op.timeout_ms > max_timeout:
                raise ValidationError(f"Timeout {op.timeout_ms}ms exceeds maximum {max_timeout}ms")

    # Validate limits
    max_securities = min(plan.limits.max_securities, settings.max_securities)
    if security_count > max_securities:
        raise ValidationError(f"Too many securities ({security_count}). Maximum: {max_securities}")

    max_fields = min(plan.limits.max_fields, settings.max_fields)
    if field_count > max_fields:
        raise ValidationError(f"Too many fields ({field_count}). Maximum: {max_fields}")
