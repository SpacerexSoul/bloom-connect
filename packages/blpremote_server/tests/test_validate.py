"""Tests for server plan validation."""

import pytest
from blpremote_server.config import settings
from blpremote_server.exceptions import ValidationError
from blpremote_server.executor.validate import validate_plan
from blpremote_server.models import (
    AppendOp,
    AuthToken,
    CollectRefdataResponseOp,
    CollectResponseOp,
    CreateRequestOp,
    ExecutionPlan,
    OpenServiceOp,
    PlanLimits,
    SendRequestOp,
    SetOp,
    StartSessionOp,
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
                request="DefinitelyNotARealRequestType",  # Not in allowlist
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
            # Need at least one field so the required-element check
            # passes; the test is about the security count, not shape.
            AppendOp(id="req1", path="fields", value="PX_LAST"),
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
            # Need at least one security so the required-element check
            # passes; the test is about field count.
            AppendOp(id="req1", path="securities", value="AAPL US Equity"),
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
            AppendOp(id="req1", path="fields", value="PX_LAST"),
        ]
        # Add 15 securities (under server 200 but over plan 10 limit)
        for i in range(15):
            ops.append(AppendOp(id="req1", path="securities", value=f"SEC{i} Equity"))

        plan = make_plan(ops, limits=PlanLimits(max_securities=10))

        with pytest.raises(ValidationError, match="Too many securities"):
            validate_plan(plan)


class TestRequiredElements:
    """Per-request-type required-element checks (M4 D)."""

    def test_intraday_bar_missing_interval_rejected(self):
        ops = [
            StartSessionOp(),
            OpenServiceOp(service="//blp/refdata"),
            CreateRequestOp(
                service="//blp/refdata",
                request="IntradayBarRequest",
                id="req1",
            ),
            SetOp(id="req1", path="security", value="AAPL US Equity"),
            SetOp(id="req1", path="eventType", value="TRADE"),
            # MISSING: interval
            SetOp(id="req1", path="startDateTime", value="2026-05-08T14:00:00+00:00"),
            SetOp(id="req1", path="endDateTime", value="2026-05-08T15:00:00+00:00"),
            SendRequestOp(id="req1", correlation_id="cid"),
            CollectResponseOp(correlation_id="cid", timeout_ms=10000),
        ]
        plan = make_plan(ops)
        with pytest.raises(ValidationError, match="interval"):
            validate_plan(plan)

    def test_intraday_bar_with_interval_passes(self):
        ops = [
            StartSessionOp(),
            OpenServiceOp(service="//blp/refdata"),
            CreateRequestOp(
                service="//blp/refdata",
                request="IntradayBarRequest",
                id="req1",
            ),
            SetOp(id="req1", path="security", value="AAPL US Equity"),
            SetOp(id="req1", path="eventType", value="TRADE"),
            SetOp(id="req1", path="interval", value=1),
            SetOp(id="req1", path="startDateTime", value="2026-05-08T14:00:00+00:00"),
            SetOp(id="req1", path="endDateTime", value="2026-05-08T15:00:00+00:00"),
            SendRequestOp(id="req1", correlation_id="cid"),
            CollectResponseOp(correlation_id="cid", timeout_ms=10000),
        ]
        plan = make_plan(ops)
        warnings = validate_plan(plan)
        assert warnings == []

    def test_intraday_tick_missing_event_types_rejected(self):
        ops = [
            StartSessionOp(),
            OpenServiceOp(service="//blp/refdata"),
            CreateRequestOp(
                service="//blp/refdata",
                request="IntradayTickRequest",
                id="req1",
            ),
            SetOp(id="req1", path="security", value="AAPL US Equity"),
            # MISSING: eventTypes (no AppendOp)
            SetOp(id="req1", path="startDateTime", value="2026-05-08T14:00:00+00:00"),
            SetOp(id="req1", path="endDateTime", value="2026-05-08T14:05:00+00:00"),
            SendRequestOp(id="req1", correlation_id="cid"),
            CollectResponseOp(correlation_id="cid", timeout_ms=10000),
        ]
        plan = make_plan(ops)
        with pytest.raises(ValidationError, match="eventTypes"):
            validate_plan(plan)

    def test_historical_missing_start_date_rejected(self):
        ops = [
            StartSessionOp(),
            OpenServiceOp(service="//blp/refdata"),
            CreateRequestOp(
                service="//blp/refdata",
                request="HistoricalDataRequest",
                id="req1",
            ),
            AppendOp(id="req1", path="securities", value="AAPL US Equity"),
            AppendOp(id="req1", path="fields", value="PX_LAST"),
            # MISSING: startDate
            SetOp(id="req1", path="endDate", value="20260508"),
            SendRequestOp(id="req1", correlation_id="cid"),
            CollectResponseOp(correlation_id="cid", timeout_ms=10000),
        ]
        plan = make_plan(ops)
        with pytest.raises(ValidationError, match="startDate"):
            validate_plan(plan)

    def test_field_info_missing_id_rejected(self):
        ops = [
            StartSessionOp(),
            OpenServiceOp(service="//blp/apiflds"),
            CreateRequestOp(
                service="//blp/apiflds",
                request="FieldInfoRequest",
                id="req1",
            ),
            # MISSING: id
            SendRequestOp(id="req1", correlation_id="cid"),
            CollectResponseOp(correlation_id="cid", timeout_ms=10000),
        ]
        plan = make_plan(ops)
        with pytest.raises(ValidationError, match="'id'"):
            validate_plan(plan)

    def test_error_lists_multiple_missing_elements(self):
        """When several required elements are missing, the error lists
        all of them — not just the first."""
        ops = [
            StartSessionOp(),
            OpenServiceOp(service="//blp/refdata"),
            CreateRequestOp(
                service="//blp/refdata",
                request="IntradayBarRequest",
                id="req1",
            ),
            # Only set 'security' — missing eventType, interval,
            # startDateTime, endDateTime.
            SetOp(id="req1", path="security", value="AAPL US Equity"),
            SendRequestOp(id="req1", correlation_id="cid"),
            CollectResponseOp(correlation_id="cid", timeout_ms=10000),
        ]
        plan = make_plan(ops)
        with pytest.raises(ValidationError) as exc:
            validate_plan(plan)
        msg = str(exc.value)
        for missing in ("eventType", "interval", "startDateTime", "endDateTime"):
            assert missing in msg


class TestUnverifiedServiceWarning:
    """IR_UNVERIFIED_SERVICE warning when service is allowed but not
    in the hard-coded SUPPORTED_SERVICES set (M4 D)."""

    def test_supported_service_no_warning(self):
        """//blp/refdata is in both allowed AND supported — no warning."""
        ops = [
            StartSessionOp(),
            OpenServiceOp(service="//blp/refdata"),
            CreateRequestOp(
                service="//blp/refdata",
                request="ReferenceDataRequest",
                id="req1",
            ),
            AppendOp(id="req1", path="securities", value="AAPL US Equity"),
            AppendOp(id="req1", path="fields", value="PX_LAST"),
            SendRequestOp(id="req1", correlation_id="cid"),
            CollectResponseOp(correlation_id="cid", timeout_ms=5000),
        ]
        plan = make_plan(ops)
        warnings = validate_plan(plan)
        assert warnings == []

    def test_unverified_service_emits_warning(self, monkeypatch):
        """Service in allowed but not in SUPPORTED_SERVICES emits one
        IR_UNVERIFIED_SERVICE warning."""
        monkeypatch.setattr(
            settings, "allowed_services",
            ["//blp/refdata", "//blp/apiflds", "//blp/srcref"],
        )
        # Need allowed_request_types entry too, otherwise we'd reject
        # the bare CreateRequestOp before getting to the service check.
        monkeypatch.setattr(
            settings, "allowed_request_types",
            settings.allowed_request_types + ["SecuritySearchRequest"],
        )
        ops = [
            StartSessionOp(),
            OpenServiceOp(service="//blp/srcref"),
            # Note: //blp/srcref isn't in REQUIRED_ELEMENTS so the
            # required-element loop just skips it (fall-through).
        ]
        plan = make_plan(ops)
        warnings = validate_plan(plan)
        assert len(warnings) == 1
        assert warnings[0].code == "IR_UNVERIFIED_SERVICE"
        assert "//blp/srcref" in warnings[0].message

    def test_unverified_warning_emitted_only_once_per_service(self, monkeypatch):
        """Same service appearing in both an OpenServiceOp and a
        CreateRequestOp should warn exactly once."""
        monkeypatch.setattr(
            settings, "allowed_services",
            ["//blp/refdata", "//blp/apiflds", "//blp/srcref"],
        )
        monkeypatch.setattr(
            settings, "allowed_request_types",
            settings.allowed_request_types + ["SearchRequest"],
        )
        ops = [
            StartSessionOp(),
            OpenServiceOp(service="//blp/srcref"),
            CreateRequestOp(
                service="//blp/srcref",
                request="SearchRequest",
                id="req1",
            ),
            SendRequestOp(id="req1", correlation_id="cid"),
            CollectResponseOp(correlation_id="cid", timeout_ms=5000),
        ]
        plan = make_plan(ops)
        warnings = validate_plan(plan)
        assert len(warnings) == 1
        assert warnings[0].code == "IR_UNVERIFIED_SERVICE"

    def test_protocol_version_1_0_accepted(self):
        plan = make_plan([StartSessionOp()])
        plan.protocol_version = "1.0"
        validate_plan(plan)  # should not raise

    def test_protocol_version_1_1_accepted(self):
        plan = make_plan([StartSessionOp()])
        assert plan.protocol_version == "1.1"  # default
        validate_plan(plan)  # should not raise

    def test_unknown_protocol_version_rejected(self):
        plan = make_plan([StartSessionOp()])
        plan.protocol_version = "9.9"
        with pytest.raises(ValidationError, match="protocol_version"):
            validate_plan(plan)

    def test_deprecated_collect_op_passes_validator(self):
        """Validator must accept the deprecated alias for timeout checks
        — the deprecation notice is emitted by the executor, not the
        validator."""
        ops = [
            StartSessionOp(),
            CollectRefdataResponseOp(correlation_id="cid1", timeout_ms=5000),
        ]
        plan = make_plan(ops)
        validate_plan(plan)  # should not raise

    def test_deprecated_collect_op_timeout_still_enforced(self):
        ops = [
            StartSessionOp(),
            CollectRefdataResponseOp(correlation_id="cid1", timeout_ms=60000),
        ]
        plan = make_plan(ops)
        with pytest.raises(ValidationError, match="Timeout"):
            validate_plan(plan)
