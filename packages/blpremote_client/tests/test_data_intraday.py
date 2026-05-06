"""Tests for the intraday + field-info convenience funcs.

These don't hit a live server. Each test injects a fake RemoteHost
that captures the ExecutionPlan the function built and returns a
canned ExecutionResult, so we can assert the IR shape AND the result
reshape independently.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from blpremote_client.data import (
    _format_datetime,
    get_bars,
    get_field_info,
    get_ticks,
)
from blpremote_client.models import (
    AppendOp,
    CollectResponseOp,
    CreateRequestOp,
    ExecutionResult,
    OpenServiceOp,
    SendRequestOp,
    SetOp,
    StartSessionOp,
)


class _FakeHost:
    """Captures the plan and returns a pre-loaded ExecutionResult.

    Mirrors RemoteHost's public surface used by the convenience funcs
    (``_get_token`` and ``execute``). Keep it minimal so the tests
    fail loudly if a func grows a new dependency we should know about.
    """

    def __init__(self, response_data: dict[str, Any] | None = None):
        self.captured_plan = None
        self._response_data = response_data or {}

    def _get_token(self) -> str:
        return "fake-token"

    def execute(self, plan):
        self.captured_plan = plan
        return ExecutionResult(
            request_id=plan.request_id,
            status="ok",
            data=self._response_data,
        )


class TestFormatDatetime:
    def test_naive_datetime_treated_as_utc(self):
        dt = datetime(2026, 5, 6, 14, 30, 0)
        assert _format_datetime(dt) == "2026-05-06T14:30:00+00:00"

    def test_aware_datetime_preserves_offset(self):
        dt = datetime(2026, 5, 6, 14, 30, 0, tzinfo=timezone(timedelta(hours=-5)))
        assert _format_datetime(dt) == "2026-05-06T14:30:00-05:00"

    def test_string_passes_through(self):
        assert _format_datetime("2026-05-06T14:30:00") == "2026-05-06T14:30:00"

    def test_microseconds_dropped(self):
        dt = datetime(2026, 5, 6, 14, 30, 0, 123456, tzinfo=timezone.utc)
        assert _format_datetime(dt) == "2026-05-06T14:30:00+00:00"

    def test_other_types_raise(self):
        with pytest.raises(TypeError):
            _format_datetime(1234567890)


class TestGetBars:
    def _ops_by_type(self, plan, op_cls):
        return [op for op in plan.ops if isinstance(op, op_cls)]

    def test_builds_intraday_bar_request(self):
        host = _FakeHost(response_data={"AAPL US Equity": []})
        start = datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc)
        end = datetime(2026, 5, 6, 15, 0, tzinfo=timezone.utc)

        get_bars(host, "AAPL US Equity", "TRADE", start, end, interval=5)

        plan = host.captured_plan
        assert plan is not None

        # Verify the IR ops list shape
        creates = self._ops_by_type(plan, CreateRequestOp)
        assert len(creates) == 1
        assert creates[0].service == "//blp/refdata"
        assert creates[0].request == "IntradayBarRequest"

        sets = {op.path: op.value for op in self._ops_by_type(plan, SetOp)}
        assert sets["security"] == "AAPL US Equity"
        assert sets["eventType"] == "TRADE"
        assert sets["interval"] == 5
        assert sets["startDateTime"] == "2026-05-06T14:00:00+00:00"
        assert sets["endDateTime"] == "2026-05-06T15:00:00+00:00"

        # SendRequest + CollectResponse round it out
        assert len(self._ops_by_type(plan, SendRequestOp)) == 1
        assert len(self._ops_by_type(plan, CollectResponseOp)) == 1

        # And the new collect_response op (not the deprecated alias)
        collect = self._ops_by_type(plan, CollectResponseOp)[0]
        assert collect.op == "collect_response"

    def test_returns_bar_list_when_present(self):
        bars = [
            {"time": "2026-05-06T14:00:00", "open": 285.0, "high": 286.0,
             "low": 285.0, "close": 285.5, "volume": 1000},
            {"time": "2026-05-06T14:01:00", "open": 285.5, "high": 286.5,
             "low": 285.5, "close": 286.0, "volume": 1500},
        ]
        host = _FakeHost(response_data={"AAPL US Equity": bars})
        start = datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc)
        end = datetime(2026, 5, 6, 14, 5, tzinfo=timezone.utc)

        result = get_bars(host, "AAPL US Equity", "TRADE", start, end)
        assert result == bars
        assert result[0]["close"] == 285.5

    def test_returns_empty_list_when_security_absent(self):
        host = _FakeHost(response_data={})
        result = get_bars(
            host,
            "AAPL US Equity",
            "TRADE",
            datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 6, 15, 0, tzinfo=timezone.utc),
        )
        assert result == []

    def test_returns_empty_list_when_data_not_a_list(self):
        # Defensive: if a future server change keys differently we'd
        # rather return [] than blow up callers.
        host = _FakeHost(response_data={"AAPL US Equity": {"oops": "dict"}})
        result = get_bars(
            host,
            "AAPL US Equity",
            "TRADE",
            datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 6, 15, 0, tzinfo=timezone.utc),
        )
        assert result == []

    def test_default_interval_is_one_minute(self):
        host = _FakeHost(response_data={"AAPL US Equity": []})
        get_bars(
            host,
            "AAPL US Equity",
            "TRADE",
            datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 6, 15, 0, tzinfo=timezone.utc),
        )
        sets = {op.path: op.value for op in host.captured_plan.ops if isinstance(op, SetOp)}
        assert sets["interval"] == 1

    def test_plan_starts_with_session_and_service_ops(self):
        host = _FakeHost(response_data={"AAPL US Equity": []})
        get_bars(
            host,
            "AAPL US Equity",
            "TRADE",
            datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 6, 15, 0, tzinfo=timezone.utc),
        )
        ops = host.captured_plan.ops
        assert isinstance(ops[0], StartSessionOp)
        assert isinstance(ops[1], OpenServiceOp)
        assert ops[1].service == "//blp/refdata"


class TestGetTicks:
    def _ops_by_type(self, plan, op_cls):
        return [op for op in plan.ops if isinstance(op, op_cls)]

    def test_builds_intraday_tick_request_single_event(self):
        host = _FakeHost(response_data={"AAPL US Equity": []})
        start = datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc)
        end = datetime(2026, 5, 6, 14, 5, tzinfo=timezone.utc)

        get_ticks(host, "AAPL US Equity", "TRADE", start, end)

        plan = host.captured_plan
        creates = self._ops_by_type(plan, CreateRequestOp)
        assert creates[0].request == "IntradayTickRequest"

        sets = {op.path: op.value for op in self._ops_by_type(plan, SetOp)}
        assert sets["security"] == "AAPL US Equity"
        assert sets["startDateTime"] == "2026-05-06T14:00:00+00:00"
        assert sets["endDateTime"] == "2026-05-06T14:05:00+00:00"

        # eventTypes is an array — appended via AppendOp, not Set
        appends = [op for op in self._ops_by_type(plan, AppendOp) if op.path == "eventTypes"]
        assert [op.value for op in appends] == ["TRADE"]

    def test_builds_intraday_tick_request_multi_event(self):
        host = _FakeHost(response_data={"AAPL US Equity": []})
        get_ticks(
            host,
            "AAPL US Equity",
            ["TRADE", "BID", "ASK"],
            datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 6, 14, 5, tzinfo=timezone.utc),
        )

        appends = [
            op for op in host.captured_plan.ops
            if isinstance(op, AppendOp) and op.path == "eventTypes"
        ]
        assert [op.value for op in appends] == ["TRADE", "BID", "ASK"]

    def test_returns_tick_list_when_present(self):
        ticks = [
            {"time": "2026-05-06T14:00:00.123", "type": "TRADE", "value": 285.5, "size": 100},
            {"time": "2026-05-06T14:00:00.456", "type": "TRADE", "value": 285.6, "size": 50},
        ]
        host = _FakeHost(response_data={"AAPL US Equity": ticks})
        result = get_ticks(
            host,
            "AAPL US Equity",
            "TRADE",
            datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 6, 14, 5, tzinfo=timezone.utc),
        )
        assert result == ticks
        assert sum(t["size"] for t in result) == 150

    def test_returns_empty_list_when_no_ticks(self):
        host = _FakeHost(response_data={})
        result = get_ticks(
            host,
            "AAPL US Equity",
            ["TRADE"],
            datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 6, 14, 5, tzinfo=timezone.utc),
        )
        assert result == []

    def test_collect_response_uses_new_op(self):
        host = _FakeHost(response_data={"AAPL US Equity": []})
        get_ticks(
            host,
            "AAPL US Equity",
            "TRADE",
            datetime(2026, 5, 6, 14, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 6, 14, 5, tzinfo=timezone.utc),
        )
        collect = [op for op in host.captured_plan.ops if isinstance(op, CollectResponseOp)]
        assert len(collect) == 1
        assert collect[0].op == "collect_response"


class TestGetFieldInfo:
    def _ops_by_type(self, plan, op_cls):
        return [op for op in plan.ops if isinstance(op, op_cls)]

    def _canned_response(self, fields: list[str]) -> dict[str, Any]:
        """Mimics the c3 server-side normaliser shape."""
        return {
            "_field_info": {
                f: {
                    "mnemonic": f,
                    "datatype": "Price" if f == "PX_LAST" else "String",
                    "description": f"description of {f}",
                    "ftype": "Real Time",
                    "categoryName": ["Market Data"],
                    "property": [],
                    "overrides": [],
                    "documentation": f"long-form docs for {f}",
                }
                for f in fields
            }
        }

    def test_builds_field_info_request_single_id(self):
        host = _FakeHost(response_data=self._canned_response(["PX_LAST"]))
        get_field_info(host, "PX_LAST")

        plan = host.captured_plan
        creates = self._ops_by_type(plan, CreateRequestOp)
        assert len(creates) == 1
        assert creates[0].service == "//blp/apiflds"
        assert creates[0].request == "FieldInfoRequest"

        opens = self._ops_by_type(plan, OpenServiceOp)
        assert opens[0].service == "//blp/apiflds"

        ids = [op.value for op in self._ops_by_type(plan, AppendOp) if op.path == "id"]
        assert ids == ["PX_LAST"]

        sets = {op.path: op.value for op in self._ops_by_type(plan, SetOp)}
        assert sets["returnFieldDocumentation"] is True

    def test_builds_field_info_request_multi_id(self):
        host = _FakeHost(
            response_data=self._canned_response(["PX_LAST", "NAME", "VOLUME"])
        )
        get_field_info(host, ["PX_LAST", "NAME", "VOLUME"])

        ids = [
            op.value for op in host.captured_plan.ops
            if isinstance(op, AppendOp) and op.path == "id"
        ]
        assert ids == ["PX_LAST", "NAME", "VOLUME"]

    def test_returns_field_info_keyed_by_mnemonic(self):
        host = _FakeHost(response_data=self._canned_response(["PX_LAST", "NAME"]))
        info = get_field_info(host, ["PX_LAST", "NAME"])
        assert set(info.keys()) == {"PX_LAST", "NAME"}
        assert info["PX_LAST"]["datatype"] == "Price"
        assert info["NAME"]["datatype"] == "String"
        assert "documentation" in info["PX_LAST"]

    def test_with_documentation_false_propagates_to_ir(self):
        host = _FakeHost(response_data=self._canned_response(["PX_LAST"]))
        get_field_info(host, "PX_LAST", with_documentation=False)
        sets = {op.path: op.value for op in host.captured_plan.ops if isinstance(op, SetOp)}
        assert sets["returnFieldDocumentation"] is False

    def test_returns_empty_dict_when_field_info_missing(self):
        # Server-side might return an empty data dict if all fields
        # are unknown — convenience func should not blow up.
        host = _FakeHost(response_data={})
        info = get_field_info(host, ["ZZZ_UNKNOWN"])
        assert info == {}

    def test_returns_empty_dict_when_field_info_not_a_dict(self):
        host = _FakeHost(response_data={"_field_info": "oops not a dict"})
        info = get_field_info(host, ["PX_LAST"])
        assert info == {}

    def test_collect_response_uses_new_op(self):
        host = _FakeHost(response_data=self._canned_response(["PX_LAST"]))
        get_field_info(host, "PX_LAST")
        collect = [op for op in host.captured_plan.ops if isinstance(op, CollectResponseOp)]
        assert len(collect) == 1
        assert collect[0].op == "collect_response"
