"""Tests for client models and plan building."""

import pytest

from blpremote_client.models import (
    AppendOp,
    AuthToken,
    CollectRefdataResponseOp,
    CollectResponseOp,
    CreateRequestOp,
    ErrorDetail,
    ExecutionPlan,
    ExecutionResult,
    OpenServiceOp,
    SendRequestOp,
    StartSessionOp,
)


class TestExecutionPlan:
    """Tests for ExecutionPlan model."""

    def test_create_plan_with_defaults(self):
        """Default protocol_version is 1.1 after the M2 IR contract bump."""
        plan = ExecutionPlan(auth=AuthToken(token="test-token"))
        assert plan.protocol_version == "1.1"
        assert plan.auth.token == "test-token"
        assert plan.ops == []
        assert plan.limits.max_securities == 200

    def test_legacy_protocol_version_still_constructible(self):
        """Clients pinned to the older shape can still build a plan; the
        server validator decides whether to accept them at parse time."""
        plan = ExecutionPlan(
            auth=AuthToken(token="t"), protocol_version="1.0"
        )
        assert plan.protocol_version == "1.0"

    def test_create_plan_with_ops(self):
        """Test creating a plan with operations."""
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
        plan = ExecutionPlan(auth=AuthToken(token="test-token"), ops=ops)
        assert len(plan.ops) == 7

    def test_plan_serialization(self):
        """Test that plan can be serialized to dict/JSON."""
        plan = ExecutionPlan(
            auth=AuthToken(token="test-token"),
            ops=[StartSessionOp()],
        )
        data = plan.model_dump()
        assert data["auth"]["token"] == "test-token"
        assert data["ops"][0]["op"] == "start_session"


class TestExecutionResult:
    """Tests for ExecutionResult model."""

    def test_result_to_dict(self):
        """Test converting result to dictionary."""
        result = ExecutionResult(
            request_id="test-id",
            status="ok",
            data={"IBM US Equity": {"PX_LAST": 123.45}},
        )
        assert result.to_dict() == {"IBM US Equity": {"PX_LAST": 123.45}}

    def test_result_with_errors(self):
        """Test result with error status."""
        from blpremote_client.models import ErrorDetail

        result = ExecutionResult(
            request_id="test-id",
            status="error",
            errors=[ErrorDetail(code="BLP_TIMEOUT", message="Request timed out")],
        )
        assert result.status == "error"
        assert len(result.errors) == 1

    def test_to_dataframe_requires_pandas(self):
        """Test that to_dataframe raises ImportError without pandas."""
        result = ExecutionResult(
            request_id="test-id",
            status="ok",
            data={"IBM US Equity": {"PX_LAST": 123.45}},
        )
        # This might pass or fail depending on whether pandas is installed
        try:
            df = result.to_dataframe()
            # If pandas is installed, verify the DataFrame
            assert len(df) == 1
        except ImportError:
            # Expected if pandas is not installed
            pass

    def test_warnings_default_is_empty_list(self):
        result = ExecutionResult(request_id="r", status="ok")
        assert result.warnings == []

    def test_warnings_carries_advisories(self):
        result = ExecutionResult(
            request_id="r",
            status="ok",
            warnings=[
                ErrorDetail(code="IR_DEPRECATED_OP", message="use collect_response"),
            ],
        )
        assert len(result.warnings) == 1
        assert result.warnings[0].code == "IR_DEPRECATED_OP"
        # Warnings live separately from errors.
        assert result.errors == []


class TestCollectResponseOps:
    """Tests for the new collect_response op + its deprecated alias."""

    def test_new_op_wire_literal(self):
        op = CollectResponseOp(correlation_id="cid1", timeout_ms=5000)
        assert op.op == "collect_response"

    def test_deprecated_alias_wire_literal(self):
        op = CollectRefdataResponseOp(correlation_id="cid1", timeout_ms=5000)
        assert op.op == "collect_refdata_response"

    @pytest.mark.parametrize(
        "wire_op,expected_class",
        [
            ("collect_response", CollectResponseOp),
            ("collect_refdata_response", CollectRefdataResponseOp),
        ],
    )
    def test_op_union_dispatches_by_wire_literal(self, wire_op, expected_class):
        """Round-trip through ExecutionPlan to confirm the discriminated
        union routes each wire literal to the right model class."""
        plan = ExecutionPlan(
            auth=AuthToken(token="t"),
            ops=[
                {"op": wire_op, "correlation_id": "cid1", "timeout_ms": 1000}
            ],
        )
        assert len(plan.ops) == 1
        assert isinstance(plan.ops[0], expected_class)

    def test_both_ops_serialise_round_trip(self):
        """Plan with both ops serialises and deserialises cleanly."""
        plan = ExecutionPlan(
            auth=AuthToken(token="t"),
            ops=[
                CollectResponseOp(correlation_id="a", timeout_ms=100),
                CollectRefdataResponseOp(correlation_id="b", timeout_ms=200),
            ],
        )
        data = plan.model_dump()
        assert data["ops"][0]["op"] == "collect_response"
        assert data["ops"][1]["op"] == "collect_refdata_response"

        plan2 = ExecutionPlan.model_validate(data)
        assert isinstance(plan2.ops[0], CollectResponseOp)
        assert isinstance(plan2.ops[1], CollectRefdataResponseOp)
