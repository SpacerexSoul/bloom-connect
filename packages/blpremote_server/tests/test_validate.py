"""Tests for server plan validation."""

import pytest
from blpremote_server.executor.validate import validate_plan
from blpremote_server.exceptions import ValidationError
from blpremote_server.models import (
    ExecutionPlan,
    AuthToken,
    StartSessionOp,
    OpenServiceOp,
    CreateRequestOp,
    AppendOp,
    SendRequestOp,
    CollectResponseOp,
    PlanLimits,
)


def make_plan(ops, limits=None):
    """Helper to create a test plan."""
    return ExecutionPlan(
        auth=AuthToken(token="test-token"),
        ops=ops,
        limits=limits or PlanLimits(),
    )


class TestValidatePlan:
    """Tests for plan validation."""

    def test_valid_plan(self):
        """Test that a valid plan passes validation."""
        ops = [
            StartSessionOp(),
            OpenServiceOp(service="//blp/refdata"),
            CreateRequestOp(
                service="//blp/refdata",
                request="ReferenceDataRequest",
                id="req1",
            ),
            AppendOp(id="req1", path="securities", value="IBM US Equity"),
            AppendOp(id="req1", path="fields", value="PX_LAST"),
            SendRequestOp(id="req1", correlation_id="cid1"),
            CollectResponseOp(correlation_id="cid1", timeout_ms=5000),
        ]
        plan = make_plan(ops)

        # Should not raise
        validate_plan(plan)

    def test_invalid_service(self):
        """Test that disallowed service is rejected."""
        ops = [
            StartSessionOp(),
            OpenServiceOp(service="//blp/mktdata"),  # Not in allowlist
        ]
        plan = make_plan(ops)

        with pytest.raises(ValidationError, match="not allowed"):
            validate_plan(plan)

    def test_invalid_request_type(self):
        """Test that disallowed request type is rejected."""
        ops = [
            StartSessionOp(),
            OpenServiceOp(service="//blp/refdata"),
            CreateRequestOp(
                service="//blp/refdata",
                request="HistoricalDataRequest",  # Not in allowlist
                id="req1",
            ),
        ]
        plan = make_plan(ops)

        with pytest.raises(ValidationError, match="not allowed"):
            validate_plan(plan)

    def test_too_many_securities(self):
        """Test that exceeding security limit is rejected."""
        ops = [
            StartSessionOp(),
            OpenServiceOp(service="//blp/refdata"),
            CreateRequestOp(
                service="//blp/refdata",
                request="ReferenceDataRequest",
                id="req1",
            ),
        ]
        # Add 250 securities (over default 200 limit)
        for i in range(250):
            ops.append(AppendOp(id="req1", path="securities", value=f"SEC{i} Equity"))

        plan = make_plan(ops)

        with pytest.raises(ValidationError, match="Too many securities"):
            validate_plan(plan)

    def test_too_many_fields(self):
        """Test that exceeding field limit is rejected."""
        ops = [
            StartSessionOp(),
            OpenServiceOp(service="//blp/refdata"),
            CreateRequestOp(
                service="//blp/refdata",
                request="ReferenceDataRequest",
                id="req1",
            ),
        ]
        # Add 250 fields (over default 200 limit)
        for i in range(250):
            ops.append(AppendOp(id="req1", path="fields", value=f"FIELD_{i}"))

        plan = make_plan(ops)

        with pytest.raises(ValidationError, match="Too many fields"):
            validate_plan(plan)

    def test_timeout_exceeds_limit(self):
        """Test that excessive timeout is rejected."""
        ops = [
            StartSessionOp(),
            CollectResponseOp(correlation_id="cid1", timeout_ms=60000),  # Over 30s limit
        ]
        plan = make_plan(ops)

        with pytest.raises(ValidationError, match="Timeout"):
            validate_plan(plan)

    def test_respects_plan_limits(self):
        """Test that plan limits take precedence when stricter."""
        ops = [
            StartSessionOp(),
            OpenServiceOp(service="//blp/refdata"),
            CreateRequestOp(
                service="//blp/refdata",
                request="ReferenceDataRequest",
                id="req1",
            ),
        ]
        # Add 15 securities (under server 200 but over plan 10 limit)
        for i in range(15):
            ops.append(AppendOp(id="req1", path="securities", value=f"SEC{i} Equity"))

        plan = make_plan(ops, limits=PlanLimits(max_securities=10))

        with pytest.raises(ValidationError, match="Too many securities"):
            validate_plan(plan)
