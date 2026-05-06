"""Tests for audit.py — summary derivation, hashing, JSONL tee.

Each test snapshots and restores ``settings.audit_log_path`` /
``settings.audit_include_raw_ir`` so global state doesn't leak.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

from blpremote_server import audit
from blpremote_server.config import settings
from blpremote_server.models import (
    AppendOp,
    AuthToken,
    CollectResponseOp,
    CreateRequestOp,
    ErrorDetail,
    ExecutionPlan,
    ExecutionResult,
    OpenServiceOp,
    SendRequestOp,
    SetOp,
    StartSessionOp,
)


def _refdata_plan(securities: list[str], fields: list[str]) -> ExecutionPlan:
    ops = [
        StartSessionOp(),
        OpenServiceOp(service="//blp/refdata"),
        CreateRequestOp(service="//blp/refdata", request="ReferenceDataRequest", id="r1"),
    ]
    for s in securities:
        ops.append(AppendOp(id="r1", path="securities", value=s))
    for f in fields:
        ops.append(AppendOp(id="r1", path="fields", value=f))
    ops += [
        SendRequestOp(id="r1", correlation_id="cid"),
        CollectResponseOp(correlation_id="cid", timeout_ms=10000),
    ]
    return ExecutionPlan(auth=AuthToken(token="t"), ops=ops)


def _bar_plan(security: str, event: str, interval: int) -> ExecutionPlan:
    return ExecutionPlan(
        auth=AuthToken(token="t"),
        ops=[
            StartSessionOp(),
            OpenServiceOp(service="//blp/refdata"),
            CreateRequestOp(service="//blp/refdata", request="IntradayBarRequest", id="r1"),
            SetOp(id="r1", path="security", value=security),
            SetOp(id="r1", path="eventType", value=event),
            SetOp(id="r1", path="interval", value=interval),
            SetOp(id="r1", path="startDateTime", value="2026-05-06T14:00:00+00:00"),
            SetOp(id="r1", path="endDateTime", value="2026-05-06T15:00:00+00:00"),
            SendRequestOp(id="r1", correlation_id="cid"),
            CollectResponseOp(correlation_id="cid", timeout_ms=10000),
        ],
    )


@pytest.fixture(autouse=True)
def _isolate_audit_state(tmp_path):
    """Snapshot+restore audit-related settings so tests don't leak."""
    saved_path = settings.audit_log_path
    saved_raw = settings.audit_include_raw_ir
    audit.reset_for_tests()
    try:
        yield tmp_path
    finally:
        settings.audit_log_path = saved_path
        settings.audit_include_raw_ir = saved_raw
        audit.reset_for_tests()


class TestSummarisePlan:
    def test_reference_data_request_counts_securities_and_fields(self):
        plan = _refdata_plan(["AAPL US Equity", "MSFT US Equity"], ["PX_LAST", "NAME", "VOLUME"])
        assert audit.summarise_plan(plan) == "ReferenceDataRequest 2sec×3fld"

    def test_intraday_bar_request_includes_security_and_interval(self):
        plan = _bar_plan("AAPL US Equity", "TRADE", 5)
        assert audit.summarise_plan(plan) == "IntradayBarRequest AAPL US Equity TRADE 5m"

    def test_intraday_tick_counts_event_types(self):
        ops = [
            StartSessionOp(),
            CreateRequestOp(service="//blp/refdata", request="IntradayTickRequest", id="r1"),
            SetOp(id="r1", path="security", value="AAPL US Equity"),
            AppendOp(id="r1", path="eventTypes", value="TRADE"),
            AppendOp(id="r1", path="eventTypes", value="BID"),
            AppendOp(id="r1", path="eventTypes", value="ASK"),
            SendRequestOp(id="r1", correlation_id="c"),
            CollectResponseOp(correlation_id="c", timeout_ms=1000),
        ]
        plan = ExecutionPlan(auth=AuthToken(token="t"), ops=ops)
        assert audit.summarise_plan(plan) == "IntradayTickRequest AAPL US Equity 3eventTypes"

    def test_field_info_counts_ids(self):
        ops = [
            StartSessionOp(),
            CreateRequestOp(service="//blp/apiflds", request="FieldInfoRequest", id="r1"),
            AppendOp(id="r1", path="id", value="PX_LAST"),
            AppendOp(id="r1", path="id", value="NAME"),
            SendRequestOp(id="r1", correlation_id="c"),
            CollectResponseOp(correlation_id="c", timeout_ms=1000),
        ]
        plan = ExecutionPlan(auth=AuthToken(token="t"), ops=ops)
        assert audit.summarise_plan(plan) == "FieldInfoRequest 2fields"

    def test_unknown_request_falls_back_to_request_name_and_op_count(self):
        ops = [
            StartSessionOp(),
            CreateRequestOp(service="//blp/news", request="NewsRequest", id="r1"),
            SendRequestOp(id="r1", correlation_id="c"),
            CollectResponseOp(correlation_id="c", timeout_ms=1000),
        ]
        plan = ExecutionPlan(auth=AuthToken(token="t"), ops=ops)
        # NewsRequest isn't a recognised summary shape — fallback to default.
        assert audit.summarise_plan(plan).startswith("NewsRequest ")

    def test_empty_plan_falls_back_to_op_count(self):
        plan = ExecutionPlan(auth=AuthToken(token="t"), ops=[])
        assert audit.summarise_plan(plan) == "0op plan"


class TestHashing:
    def test_hash_plan_is_deterministic(self):
        a = _refdata_plan(["AAPL US Equity"], ["PX_LAST"])
        b = _refdata_plan(["AAPL US Equity"], ["PX_LAST"])
        # Different request_ids — but those don't enter the IR hash.
        assert audit.hash_plan(a) == audit.hash_plan(b)

    def test_hash_plan_changes_when_ir_changes(self):
        a = _refdata_plan(["AAPL US Equity"], ["PX_LAST"])
        b = _refdata_plan(["AAPL US Equity"], ["PX_LAST", "VOLUME"])
        assert audit.hash_plan(a) != audit.hash_plan(b)

    def test_hash_result_is_deterministic(self):
        r1 = ExecutionResult(request_id="x", status="ok", data={"AAPL US Equity": {"PX_LAST": 285}})
        r2 = ExecutionResult(request_id="y", status="ok", data={"AAPL US Equity": {"PX_LAST": 285}})
        assert audit.hash_result(r1) == audit.hash_result(r2)

    def test_hash_is_16_hex_chars(self):
        plan = _refdata_plan(["AAPL US Equity"], ["PX_LAST"])
        h = audit.hash_plan(plan)
        assert len(h) == 16
        int(h, 16)  # parses as hex


class TestAuditExecute:
    def test_logs_one_entry_with_required_fields(self, caplog):
        plan = _refdata_plan(["AAPL US Equity"], ["PX_LAST"])
        result = ExecutionResult(
            request_id=str(plan.request_id),
            status="ok",
            data={"AAPL US Equity": {"PX_LAST": 285}},
        )
        with caplog.at_level(logging.INFO, logger="blpremote.audit"):
            audit.audit_execute(plan=plan, result=result, user="mac", elapsed_ms=327)
        records = [r for r in caplog.records if r.name == "blpremote.audit"]
        assert len(records) == 1
        rec = records[0]
        assert rec.user == "mac"
        assert rec.summary == "ReferenceDataRequest 1sec×1fld"
        assert rec.elapsed_ms == 327
        assert rec.status == "ok"
        assert rec.errors_count == 0
        assert rec.warnings_count == 0
        assert len(rec.ir_hash) == 16
        assert len(rec.result_hash) == 16
        # Raw IR is OFF by default.
        assert not hasattr(rec, "ir") or rec.ir == [op.model_dump() for op in plan.ops] or True
        # Stronger: explicitly assert raw IR is absent.
        assert "ir" not in {k for k in rec.__dict__ if not k.startswith("_")}

    def test_error_codes_surface_in_entry(self, caplog):
        plan = _refdata_plan(["AAPL US Equity"], ["PX_LAST", "NOT_REAL"])
        result = ExecutionResult(
            request_id=str(plan.request_id),
            status="partial",
            data={"AAPL US Equity": {"PX_LAST": 285}},
            errors=[
                ErrorDetail(
                    code="BLP_FIELD_BAD_FLD",
                    message="Field not valid",
                    security="AAPL US Equity",
                    field="NOT_REAL",
                ),
            ],
        )
        with caplog.at_level(logging.INFO, logger="blpremote.audit"):
            audit.audit_execute(plan=plan, result=result, user="mac", elapsed_ms=300)
        rec = next(r for r in caplog.records if r.name == "blpremote.audit")
        assert rec.errors_count == 1
        assert rec.error_codes == ["BLP_FIELD_BAD_FLD"]

    def test_warning_codes_surface(self, caplog):
        plan = _refdata_plan(["AAPL US Equity"], ["PX_LAST"])
        result = ExecutionResult(
            request_id=str(plan.request_id), status="ok",
            data={"AAPL US Equity": {"PX_LAST": 285}},
            warnings=[ErrorDetail(code="IR_DEPRECATED_OP", message="…")],
        )
        with caplog.at_level(logging.INFO, logger="blpremote.audit"):
            audit.audit_execute(plan=plan, result=result, user="mac", elapsed_ms=200)
        rec = next(r for r in caplog.records if r.name == "blpremote.audit")
        assert rec.warnings_count == 1
        assert rec.warning_codes == ["IR_DEPRECATED_OP"]

    def test_raw_ir_included_when_settings_enable(self, caplog):
        settings.audit_include_raw_ir = True
        plan = _refdata_plan(["AAPL US Equity"], ["PX_LAST"])
        result = ExecutionResult(
            request_id=str(plan.request_id), status="ok",
            data={"AAPL US Equity": {"PX_LAST": 285}},
        )
        with caplog.at_level(logging.INFO, logger="blpremote.audit"):
            audit.audit_execute(plan=plan, result=result, user="mac", elapsed_ms=300)
        rec = next(r for r in caplog.records if r.name == "blpremote.audit")
        assert hasattr(rec, "ir")
        assert isinstance(rec.ir, list)
        # Same length as the plan ops.
        assert len(rec.ir) == len(plan.ops)


class TestFileTee:
    def test_audit_writes_jsonl_to_path(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        settings.audit_log_path = str(audit_path)

        plan = _refdata_plan(["AAPL US Equity"], ["PX_LAST", "NAME"])
        result = ExecutionResult(
            request_id=str(plan.request_id), status="ok",
            data={"AAPL US Equity": {"PX_LAST": 285}},
        )
        audit.audit_execute(plan=plan, result=result, user="mac", elapsed_ms=327)
        audit.audit_execute(plan=plan, result=result, user="mac", elapsed_ms=412)

        lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            entry = json.loads(line)
            assert entry["user"] == "mac"
            assert entry["summary"] == "ReferenceDataRequest 1sec×2fld"
            assert entry["status"] == "ok"
            assert entry["ts"].endswith("Z")
            assert "ir_hash" in entry
            assert "result_hash" in entry

    def test_empty_path_skips_file_tee(self, tmp_path, caplog):
        settings.audit_log_path = ""
        plan = _refdata_plan(["AAPL US Equity"], ["PX_LAST"])
        result = ExecutionResult(
            request_id=str(plan.request_id), status="ok", data={},
        )
        # Should not raise and should not create any file.
        with caplog.at_level(logging.INFO, logger="blpremote.audit"):
            audit.audit_execute(plan=plan, result=result, user="mac", elapsed_ms=10)
        # No files written by audit (we just look for stray files).
        assert not list(tmp_path.glob("*.jsonl"))
        # But the structured log entry should still be there.
        assert any(r.name == "blpremote.audit" for r in caplog.records)

    def test_unwritable_path_falls_back_to_logger_only(self, tmp_path, caplog):
        settings.audit_log_path = "/dev/full/cannot/write/audit.jsonl"
        plan = _refdata_plan(["AAPL US Equity"], ["PX_LAST"])
        result = ExecutionResult(
            request_id=str(plan.request_id), status="ok", data={},
        )
        with caplog.at_level(logging.INFO):
            audit.audit_execute(plan=plan, result=result, user="mac", elapsed_ms=10)
        # The audit log line still arrives; only the file tee gets a warning.
        assert any(r.name == "blpremote.audit" and r.levelname == "INFO" for r in caplog.records)
