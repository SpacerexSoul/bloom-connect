"""DataFrame-native wrappers over the dict-shaped client funcs.

Both pandas and polars are *optional* dependencies — installing
``blpremote_client`` doesn't pull either in. Each wrapper does an
``import`` lazily so a caller using only ``pd_*`` doesn't pay for
polars and vice versa, and a missing dep raises an ``ImportError``
with an actionable install hint instead of an unhelpful traceback
deep in the import chain.

Naming convention:

- ``pd_history(...)`` / ``pl_history(...)`` — wrap
  :func:`blpremote_client.data.bdh`, return a frame with a date
  index and one column per (security, field) pair.
- ``pd_bars(...)`` / ``pl_bars(...)`` — wrap
  :func:`blpremote_client.data.get_bars`, return a frame with a
  time index and OHLCV+ columns.
- ``pd_ticks(...)`` / ``pl_ticks(...)`` — wrap
  :func:`blpremote_client.data.get_ticks`.

The shape of the underlying dicts is unchanged from M2; this module
is pure presentation. If you'd rather work with dicts (cheaper, no
optional deps), keep using ``bdh`` / ``get_bars`` / ``get_ticks``
directly.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Optional, Union

from blpremote_client.data import bdh, get_bars, get_ticks
from blpremote_client.host import RemoteHost

if TYPE_CHECKING:
    import pandas as pd
    import polars as pl


_PANDAS_INSTALL_HINT = (
    "pandas is required for pd_* helpers. Install with one of:\n"
    "  pip install pandas\n"
    "  pip install -e packages/blpremote_client[pandas]\n"
)

_POLARS_INSTALL_HINT = (
    "polars is required for pl_* helpers. Install with one of:\n"
    "  pip install polars\n"
    "  pip install -e packages/blpremote_client[polars]\n"
)


def _require_pandas() -> Any:
    try:
        import pandas as pd  # noqa: F401
        return pd
    except ImportError as e:
        raise ImportError(_PANDAS_INSTALL_HINT) from e


def _require_polars() -> Any:
    try:
        import polars as pl  # noqa: F401
        return pl
    except ImportError as e:
        raise ImportError(_POLARS_INSTALL_HINT) from e


# --- pandas ----------------------------------------------------------

def pd_history(
    host: RemoteHost,
    securities: Union[str, list[str]],
    fields: Union[str, list[str]],
    start_date: Union[str, date, datetime],
    end_date: Union[str, date, datetime, None] = None,
    periodicity: str = "DAILY",
    timeout_ms: int = 30000,
) -> "pd.DataFrame":
    """Historical data as a pandas DataFrame indexed by date.

    Multi-security / multi-field requests produce a wide frame with
    a MultiIndex on the columns ``(security, field)``. Single-security
    single-field flattens to a 1-D Series-like column.

    Date arguments: pass ``datetime.date`` / ``datetime.datetime``
    instances, or ``"YYYYMMDD"`` strings. Bloomberg's HistoricalDataRequest
    rejects ISO-8601 dates ("2026-05-01") with "Invalid start date";
    string args are passed through verbatim, only ``date``/``datetime``
    objects get auto-formatted.
    """
    pd = _require_pandas()
    if isinstance(securities, str):
        securities = [securities]
    if isinstance(fields, str):
        fields = [fields]

    raw = bdh(host, securities, fields, start_date, end_date, periodicity, timeout_ms)

    frames: list["pd.DataFrame"] = []
    for sec in securities:
        sec_dict = raw.get(sec, {})
        if not sec_dict:
            continue
        dates = sec_dict.get("dates", [])
        cols = {f: sec_dict.get(f, []) for f in fields}
        df = pd.DataFrame(cols, index=pd.to_datetime(dates))
        df.index.name = "date"
        df.columns = pd.MultiIndex.from_product([[sec], df.columns], names=["security", "field"])
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, axis=1).sort_index()


def pd_bars(
    host: RemoteHost,
    security: str,
    event_type: str,
    start: Union[str, datetime],
    end: Union[str, datetime],
    interval: int = 1,
    timeout_ms: int = 30000,
) -> "pd.DataFrame":
    """IntradayBars as a pandas DataFrame, time-indexed."""
    pd = _require_pandas()
    bars = get_bars(host, security, event_type, start, end, interval, timeout_ms)
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars)
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"])
        df = df.set_index("time").sort_index()
    return df


def pd_ticks(
    host: RemoteHost,
    security: str,
    event_types: Union[str, list[str]],
    start: Union[str, datetime],
    end: Union[str, datetime],
    timeout_ms: int = 30000,
) -> "pd.DataFrame":
    """IntradayTicks as a pandas DataFrame, time-indexed."""
    pd = _require_pandas()
    ticks = get_ticks(host, security, event_types, start, end, timeout_ms)
    if not ticks:
        return pd.DataFrame()
    df = pd.DataFrame(ticks)
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"])
        df = df.set_index("time").sort_index()
    return df


# --- polars ----------------------------------------------------------

def pl_history(
    host: RemoteHost,
    securities: Union[str, list[str]],
    fields: Union[str, list[str]],
    start_date: Union[str, date, datetime],
    end_date: Union[str, date, datetime, None] = None,
    periodicity: str = "DAILY",
    timeout_ms: int = 30000,
) -> "pl.DataFrame":
    """Historical data as a polars DataFrame in long form.

    Polars doesn't have pandas' MultiIndex columns, so we return a
    *long* frame with explicit ``security`` / ``field`` / ``date`` /
    ``value`` columns. Reshape with ``.pivot()`` if you need wide.

    Date arguments follow the same convention as :func:`pd_history` —
    ``datetime.date`` / ``datetime.datetime`` get auto-formatted to
    ``YYYYMMDD``; ISO-8601 strings ("2026-05-01") are passed verbatim
    and rejected by BBG. Pass ``"YYYYMMDD"`` strings if you must use
    strings.
    """
    pl = _require_polars()
    if isinstance(securities, str):
        securities = [securities]
    if isinstance(fields, str):
        fields = [fields]

    raw = bdh(host, securities, fields, start_date, end_date, periodicity, timeout_ms)

    rows: list[dict[str, Any]] = []
    for sec in securities:
        sec_dict = raw.get(sec, {})
        dates = sec_dict.get("dates", [])
        for f in fields:
            values = sec_dict.get(f, [])
            for d, v in zip(dates, values):
                rows.append({"security": sec, "field": f, "date": d, "value": v})
    if not rows:
        return pl.DataFrame(schema={
            "security": pl.Utf8, "field": pl.Utf8, "date": pl.Utf8, "value": pl.Float64,
        })
    df = pl.DataFrame(rows)
    # parse date column to a Date dtype if it looks ISO; leave alone otherwise.
    if "date" in df.columns and df["date"].dtype == pl.Utf8:
        try:
            df = df.with_columns(pl.col("date").str.to_date(strict=False))
        except Exception:
            pass
    return df


def pl_bars(
    host: RemoteHost,
    security: str,
    event_type: str,
    start: Union[str, datetime],
    end: Union[str, datetime],
    interval: int = 1,
    timeout_ms: int = 30000,
) -> "pl.DataFrame":
    """IntradayBars as a polars DataFrame, time column parsed to datetime."""
    pl = _require_polars()
    bars = get_bars(host, security, event_type, start, end, interval, timeout_ms)
    if not bars:
        return pl.DataFrame()
    df = pl.DataFrame(bars)
    if "time" in df.columns and df["time"].dtype == pl.Utf8:
        try:
            df = df.with_columns(pl.col("time").str.to_datetime(strict=False))
        except Exception:
            pass
    return df


def pl_ticks(
    host: RemoteHost,
    security: str,
    event_types: Union[str, list[str]],
    start: Union[str, datetime],
    end: Union[str, datetime],
    timeout_ms: int = 30000,
) -> "pl.DataFrame":
    """IntradayTicks as a polars DataFrame, time column parsed."""
    pl = _require_polars()
    ticks = get_ticks(host, security, event_types, start, end, timeout_ms)
    if not ticks:
        return pl.DataFrame()
    df = pl.DataFrame(ticks)
    if "time" in df.columns and df["time"].dtype == pl.Utf8:
        try:
            df = df.with_columns(pl.col("time").str.to_datetime(strict=False))
        except Exception:
            pass
    return df
