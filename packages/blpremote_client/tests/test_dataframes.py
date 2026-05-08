"""Tests for the M7 pandas/polars wrappers.

Pandas/polars are optional deps; tests skip individually when the
corresponding library isn't installed in the test env so the whole
suite still passes for users who only want one (or neither).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from blpremote_client.dataframes import (
    _require_pandas,
    _require_polars,
    pd_bars,
    pd_history,
    pd_ticks,
    pl_bars,
    pl_history,
    pl_ticks,
)
from blpremote_client.models import ExecutionResult


pandas = pytest.importorskip("pandas", reason="pandas not installed in this env")
# Polars is gated separately via has_polars below since it's
# heavier and some envs may not have it even if pandas is present.
try:
    import polars  # noqa: F401
    _HAS_POLARS = True
except ImportError:
    _HAS_POLARS = False


class _FakeHost:
    def __init__(self, response_data: dict[str, Any] | None = None):
        self._data = response_data or {}
        self.captured_plan = None

    def _get_token(self) -> str:
        return "t"

    def execute(self, plan):
        self.captured_plan = plan
        return ExecutionResult(
            request_id=str(plan.request_id),
            status="ok",
            data=self._data,
        )


# Sample shapes that mimic the M2 dispatcher output.

SAMPLE_HISTORY = {
    "AAPL US Equity": {
        "dates": ["2026-05-01", "2026-05-02", "2026-05-03"],
        "PX_LAST": [285.0, 286.5, 287.1],
        "VOLUME": [1_200_000, 1_300_000, 1_100_000],
    },
    "MSFT US Equity": {
        "dates": ["2026-05-01", "2026-05-02", "2026-05-03"],
        "PX_LAST": [410.0, 411.2, 412.5],
        "VOLUME": [800_000, 850_000, 900_000],
    },
}

SAMPLE_BARS = [
    {"time": "2026-05-08T15:00:00", "open": 285.0, "high": 286.0,
     "low": 285.0, "close": 285.5, "volume": 1000},
    {"time": "2026-05-08T15:01:00", "open": 285.5, "high": 286.5,
     "low": 285.5, "close": 286.0, "volume": 1500},
]

SAMPLE_TICKS = [
    {"time": "2026-05-08T15:00:00.123", "type": "TRADE", "value": 285.5, "size": 100},
    {"time": "2026-05-08T15:00:00.456", "type": "TRADE", "value": 285.6, "size": 50},
]


class TestRequireGuards:
    def test_pandas_available(self):
        # Test env has pandas (gated by importorskip above) so this is
        # a sanity check that the require helper returns the module.
        pd = _require_pandas()
        assert pd is pandas

    def test_polars_optional_passes_through_when_installed(self):
        if not _HAS_POLARS:
            pytest.skip("polars not installed; require would raise")
        pl = _require_polars()
        assert pl.__name__ == "polars"


class TestPandasHistory:
    def test_single_security_single_field(self):
        host = _FakeHost(response_data={
            "AAPL US Equity": SAMPLE_HISTORY["AAPL US Equity"],
        })
        df = pd_history(host, "AAPL US Equity", "PX_LAST", "20260501", "20260503")
        assert isinstance(df, pandas.DataFrame)
        assert len(df) == 3
        # MultiIndex columns: (security, field) — even on a single combination.
        assert df.columns.tolist() == [("AAPL US Equity", "PX_LAST")]
        # Index parsed to datetime.
        assert pandas.api.types.is_datetime64_any_dtype(df.index)

    def test_multi_security_multi_field(self):
        host = _FakeHost(response_data=SAMPLE_HISTORY)
        df = pd_history(
            host, ["AAPL US Equity", "MSFT US Equity"], ["PX_LAST", "VOLUME"],
            "20260501", "20260503",
        )
        # 2 securities × 2 fields = 4 columns, all under MultiIndex.
        assert df.shape == (3, 4)
        assert ("AAPL US Equity", "PX_LAST") in df.columns
        assert ("MSFT US Equity", "VOLUME") in df.columns
        # Sample values intact.
        assert df[("AAPL US Equity", "PX_LAST")].iloc[0] == 285.0
        assert df[("MSFT US Equity", "VOLUME")].iloc[2] == 900_000

    def test_empty_response_returns_empty_df(self):
        host = _FakeHost(response_data={})
        df = pd_history(host, "AAPL US Equity", "PX_LAST", "20260501", "20260503")
        assert isinstance(df, pandas.DataFrame)
        assert df.empty

    def test_date_index_is_sorted(self):
        # Out-of-order dates — pd_history should sort the index.
        out_of_order = {
            "AAPL US Equity": {
                "dates": ["2026-05-03", "2026-05-01", "2026-05-02"],
                "PX_LAST": [287.1, 285.0, 286.5],
            },
        }
        host = _FakeHost(response_data=out_of_order)
        df = pd_history(host, "AAPL US Equity", "PX_LAST", "20260501", "20260503")
        # Index should be sorted ascending after `sort_index()`.
        assert df.index.is_monotonic_increasing


class TestPandasBars:
    def test_returns_time_indexed_df(self):
        host = _FakeHost(response_data={"AAPL US Equity": SAMPLE_BARS})
        df = pd_bars(
            host, "AAPL US Equity", "TRADE",
            datetime(2026, 5, 8, 15, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 8, 15, 5, tzinfo=timezone.utc),
        )
        assert isinstance(df, pandas.DataFrame)
        assert df.index.name == "time"
        assert pandas.api.types.is_datetime64_any_dtype(df.index)
        assert {"open", "high", "low", "close", "volume"}.issubset(df.columns)
        assert df.iloc[0]["close"] == 285.5

    def test_empty_bars_empty_df(self):
        host = _FakeHost(response_data={})
        df = pd_bars(
            host, "AAPL US Equity", "TRADE",
            datetime(2026, 5, 8, 15, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 8, 15, 5, tzinfo=timezone.utc),
        )
        assert df.empty


class TestPandasTicks:
    def test_returns_time_indexed_df_with_subsecond_precision(self):
        host = _FakeHost(response_data={"AAPL US Equity": SAMPLE_TICKS})
        df = pd_ticks(
            host, "AAPL US Equity", "TRADE",
            datetime(2026, 5, 8, 15, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 8, 15, 1, tzinfo=timezone.utc),
        )
        assert df.index.name == "time"
        # Sub-second offsets between rows preserved through to_datetime.
        assert df.index[1] > df.index[0]
        assert df.iloc[0]["size"] == 100


@pytest.mark.skipif(not _HAS_POLARS, reason="polars not installed")
class TestPolarsHistory:
    def test_returns_long_form_df(self):
        host = _FakeHost(response_data=SAMPLE_HISTORY)
        df = pl_history(
            host, ["AAPL US Equity", "MSFT US Equity"], ["PX_LAST", "VOLUME"],
            "20260501", "20260503",
        )
        # 2 sec × 2 fld × 3 dates = 12 rows.
        assert df.height == 12
        assert set(df.columns) == {"security", "field", "date", "value"}

    def test_empty_returns_empty_schema_intact(self):
        host = _FakeHost(response_data={})
        df = pl_history(host, "AAPL US Equity", "PX_LAST", "20260501", "20260503")
        assert df.height == 0
        # Schema columns still present even when empty so callers can
        # plug into a pipeline that depends on the column set.
        assert set(df.columns) == {"security", "field", "date", "value"}


@pytest.mark.skipif(not _HAS_POLARS, reason="polars not installed")
class TestPolarsBars:
    def test_returns_df_with_time_column(self):
        host = _FakeHost(response_data={"AAPL US Equity": SAMPLE_BARS})
        df = pl_bars(
            host, "AAPL US Equity", "TRADE",
            datetime(2026, 5, 8, 15, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 8, 15, 5, tzinfo=timezone.utc),
        )
        assert df.height == 2
        assert "time" in df.columns
        assert {"open", "high", "low", "close", "volume"}.issubset(set(df.columns))


@pytest.mark.skipif(not _HAS_POLARS, reason="polars not installed")
class TestPolarsTicks:
    def test_returns_df(self):
        host = _FakeHost(response_data={"AAPL US Equity": SAMPLE_TICKS})
        df = pl_ticks(
            host, "AAPL US Equity", "TRADE",
            datetime(2026, 5, 8, 15, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 8, 15, 1, tzinfo=timezone.utc),
        )
        assert df.height == 2
        assert "value" in df.columns
