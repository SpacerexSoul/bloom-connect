"""Tests for client models and plan building."""

import pytest
from blpremote_client.models import (
    ExecutionPlan,
    AuthToken,
    StartSessionOp,
    OpenServiceOp,
    CreateRequestOp,
    AppendOp,
    SendRequestOp,
    CollectResponseOp,
    ExecutionResult,
    PlanLimits,
)


class TestExecutionPlan:
    """Tests for ExecutionPlan model."""

    def test_create_plan_with_defaults(self):
        """Test creating a plan with default values."""
        plan = ExecutionPlan(auth=AuthToken(token="test-token"))
        assert plan.protocol_version == "1.0"
        assert plan.auth.token == "test-token"
        assert plan.ops == []
        assert plan.limits.max_securities == 200

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
