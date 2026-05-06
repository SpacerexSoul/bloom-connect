"""Extended Bloomberg data functions for historical and bulk data requests."""

from datetime import date, datetime, timezone
from typing import Any, Optional, Union

from blpremote_client.host import RemoteHost
from blpremote_client.models import (
    AppendOp,
    AuthToken,
    CollectResponseOp,
    CreateRequestOp,
    ExecutionPlan,
    OpenServiceOp,
    SendRequestOp,
    SetOp,
    StartSessionOp,
)


def _format_date(d: Union[str, date, datetime]) -> str:
    """Format a date for Bloomberg API."""
    if isinstance(d, str):
        return d
    if isinstance(d, datetime):
        return d.strftime("%Y%m%d")
    if isinstance(d, date):
        return d.strftime("%Y%m%d")
    return str(d)


def _format_datetime(d: Union[str, datetime]) -> str:
    """Format a datetime for Bloomberg intraday requests.

    BBG accepts ISO-8601 with timezone for ``startDateTime``/``endDateTime``.
    Naive datetimes are treated as UTC since intraday endpoints reject
    ambiguous timestamps. Strings are passed through verbatim — useful
    when the caller already has a BBG-formatted string.
    """
    if isinstance(d, str):
        return d
    if isinstance(d, datetime):
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.replace(microsecond=0).isoformat()
    raise TypeError(f"expected datetime or str, got {type(d).__name__}")


def bdh(
    host: RemoteHost,
    securities: Union[str, list[str]],
    fields: Union[str, list[str]],
    start_date: Union[str, date, datetime],
    end_date: Union[str, date, datetime, None] = None,
    periodicity: str = "DAILY",
    timeout_ms: int = 30000,
) -> dict[str, dict[str, list[Any]]]:
    """
    Bloomberg Historical Data Request (BDH).

    Retrieves historical time-series data for securities and fields.

    Args:
        host: Connected RemoteHost instance
        securities: Security or list of securities (e.g., "IBM US Equity")
        fields: Field or list of fields (e.g., "PX_LAST", "VOLUME")
        start_date: Start date for historical data
        end_date: End date (defaults to today)
        periodicity: "DAILY", "WEEKLY", "MONTHLY", "QUARTERLY", "YEARLY"
        timeout_ms: Timeout in milliseconds

    Returns:
        Dictionary: {security: {field: [values], "dates": [dates]}}

    Example:
        >>> from blpremote_client import RemoteHost
        >>> from blpremote_client.data import bdh
        >>> host = RemoteHost("http://192.168.1.100:8000", username="krishna", password="...")
        >>> data = bdh(host, "IBM US Equity", ["PX_LAST", "VOLUME"], "20230101", "20231231")
        >>> print(data["IBM US Equity"]["PX_LAST"])
        [123.45, 124.50, ...]
    """
    if isinstance(securities, str):
        securities = [securities]
    if isinstance(fields, str):
        fields = [fields]

    start_str = _format_date(start_date)
    end_str = _format_date(end_date) if end_date else _format_date(datetime.now())

    token = host._get_token()

    ops = [
        StartSessionOp(),
        OpenServiceOp(service="//blp/refdata"),
        CreateRequestOp(
            service="//blp/refdata",
            request="HistoricalDataRequest",
            id="req1",
        ),
    ]

    # Add securities
    for sec in securities:
        ops.append(AppendOp(id="req1", path="securities", value=sec))

    # Add fields
    for field in fields:
        ops.append(AppendOp(id="req1", path="fields", value=field))

    # Set date range
    ops.append(SetOp(id="req1", path="startDate", value=start_str))
    ops.append(SetOp(id="req1", path="endDate", value=end_str))
    ops.append(SetOp(id="req1", path="periodicitySelection", value=periodicity))

    # Send and collect
    ops.append(SendRequestOp(id="req1", correlation_id="cid1"))
    ops.append(CollectResponseOp(correlation_id="cid1", timeout_ms=timeout_ms))

    plan = ExecutionPlan(auth=AuthToken(token=token), ops=ops)
    result = host.execute(plan)

    return result.data


def bds(
    host: RemoteHost,
    security: str,
    field: str,
    overrides: Optional[dict[str, str]] = None,
    timeout_ms: int = 30000,
) -> list[dict[str, Any]]:
    """
    Bloomberg Bulk Data Request (BDS).

    Retrieves bulk data like index members, option chains, etc.

    Args:
        host: Connected RemoteHost instance
        security: Security identifier (e.g., "SPX Index")
        field: Bulk field (e.g., "INDX_MEMBERS", "OPT_CHAIN")
        overrides: Optional field overrides
        timeout_ms: Timeout in milliseconds

    Returns:
        List of dictionaries with bulk data records

    Example:
        >>> from blpremote_client import RemoteHost
        >>> from blpremote_client.data import bds
        >>> host = RemoteHost("http://192.168.1.100:8000", username="krishna", password="...")
        >>> members = bds(host, "SPX Index", "INDX_MEMBERS")
        >>> print([m["Member Ticker and Exchange Code"] for m in members[:5]])
        ['AAPL UW', 'MSFT UW', 'AMZN UW', ...]
    """
    token = host._get_token()

    ops = [
        StartSessionOp(),
        OpenServiceOp(service="//blp/refdata"),
        CreateRequestOp(
            service="//blp/refdata",
            request="ReferenceDataRequest",
            id="req1",
        ),
    ]

    ops.append(AppendOp(id="req1", path="securities", value=security))
    ops.append(AppendOp(id="req1", path="fields", value=field))

    # Add overrides if provided
    if overrides:
        for key, value in overrides.items():
            # Overrides are added as nested elements
            ops.append(SetOp(id="req1", path=f"overrides.{key}", value=value))

    ops.append(SendRequestOp(id="req1", correlation_id="cid1"))
    ops.append(CollectResponseOp(correlation_id="cid1", timeout_ms=timeout_ms))

    plan = ExecutionPlan(auth=AuthToken(token=token), ops=ops)
    result = host.execute(plan)

    # Extract bulk data from result
    if security in result.data and field in result.data[security]:
        return result.data[security][field]
    return []


def get_index_members(
    host: RemoteHost,
    index: str,
    timeout_ms: int = 30000,
) -> list[str]:
    """
    Get members of an index.

    Args:
        host: Connected RemoteHost instance
        index: Index identifier (e.g., "SPX Index", "INDU Index")
        timeout_ms: Timeout in milliseconds

    Returns:
        List of member security identifiers

    Example:
        >>> members = get_index_members(host, "SPX Index")
        >>> print(f"S&P 500 has {len(members)} members")
    """
    bulk_data = bds(host, index, "INDX_MEMBERS", timeout_ms=timeout_ms)

    # Extract ticker from each record
    members = []
    for record in bulk_data:
        if isinstance(record, dict):
            # Common field names for member tickers
            ticker = (
                record.get("Member Ticker and Exchange Code")
                or record.get("MEMBER_TICKER_AND_EXCH_CODE")
                or record.get("ticker")
            )
            if ticker:
                members.append(ticker)
        elif isinstance(record, str):
            members.append(record)

    return members


def get_historical_prices(
    host: RemoteHost,
    securities: Union[str, list[str]],
    start_date: Union[str, date, datetime],
    end_date: Union[str, date, datetime, None] = None,
    adjusted: bool = True,
    timeout_ms: int = 30000,
) -> dict[str, dict[str, list[Any]]]:
    """
    Get historical prices for securities.

    Convenience wrapper around bdh() for price data.

    Args:
        host: Connected RemoteHost instance
        securities: Security or list of securities
        start_date: Start date
        end_date: End date (defaults to today)
        adjusted: Use adjusted close prices
        timeout_ms: Timeout in milliseconds

    Returns:
        Dictionary: {security: {"prices": [...], "dates": [...]}}
    """
    field = "PX_LAST" if adjusted else "PX_CLOSE"
    return bdh(host, securities, field, start_date, end_date, timeout_ms=timeout_ms)


def get_bars(
    host: RemoteHost,
    security: str,
    event_type: str,
    start: Union[str, datetime],
    end: Union[str, datetime],
    interval: int = 1,
    timeout_ms: int = 30000,
) -> list[dict[str, Any]]:
    """Bloomberg IntradayBarRequest — return one record per bar.

    Wraps an ``IntradayBarRequest`` against ``//blp/refdata`` and reshapes
    the server response into a list of ``{time, open, high, low, close,
    volume, numEvents, value}`` records.

    Args:
        host:        Connected RemoteHost.
        security:    Bloomberg ticker (e.g. ``"AAPL US Equity"``).
        event_type:  ``"TRADE"``, ``"BID"``, ``"ASK"``, ``"BEST_BID"`` etc.
        start, end:  Window bounds. ``datetime`` or ISO-8601 string;
                     naive datetimes are treated as UTC.
        interval:    Bar size in minutes (1, 5, 15, 60, ...).
        timeout_ms:  Server-side response timeout.

    Returns:
        ``[{"time": "2026-05-06T16:29:00", "open": 286, "high": 286,
            "low": 286, "close": 286, "volume": 60545,
            "numEvents": 637, "value": 17331926}, ...]``

        Empty list if BBG returned no bars in the window. Inspect
        ``host`` server-side errors via the underlying ``execute()`` call
        if you need them — this convenience wrapper trades errors for
        a clean shape.

    Example:
        >>> from datetime import datetime, timedelta, timezone
        >>> end = datetime.now(timezone.utc).replace(microsecond=0)
        >>> start = end - timedelta(minutes=30)
        >>> bars = get_bars(host, "AAPL US Equity", "TRADE", start, end, interval=1)
        >>> bars[0]["close"]
        286
    """
    token = host._get_token()

    ops = [
        StartSessionOp(),
        OpenServiceOp(service="//blp/refdata"),
        CreateRequestOp(
            service="//blp/refdata",
            request="IntradayBarRequest",
            id="req1",
        ),
        SetOp(id="req1", path="security", value=security),
        SetOp(id="req1", path="eventType", value=event_type),
        SetOp(id="req1", path="interval", value=interval),
        SetOp(id="req1", path="startDateTime", value=_format_datetime(start)),
        SetOp(id="req1", path="endDateTime", value=_format_datetime(end)),
        SendRequestOp(id="req1", correlation_id="cid1"),
        CollectResponseOp(correlation_id="cid1", timeout_ms=timeout_ms),
    ]

    plan = ExecutionPlan(auth=AuthToken(token=token), ops=ops)
    result = host.execute(plan)

    # Server-side normaliser keys bar lists by security name.
    bars = result.data.get(security)
    return bars if isinstance(bars, list) else []


def get_ticks(
    host: RemoteHost,
    security: str,
    event_types: Union[str, list[str]],
    start: Union[str, datetime],
    end: Union[str, datetime],
    timeout_ms: int = 30000,
) -> list[dict[str, Any]]:
    """Bloomberg IntradayTickRequest — return one record per tick.

    Args:
        host:         Connected RemoteHost.
        security:     Bloomberg ticker.
        event_types:  Single event type or list — ``"TRADE"``, ``"BID"``,
                      ``"ASK"``, ``"BEST_BID"``, ``"BEST_ASK"``.
        start, end:   Window bounds. ``datetime`` or ISO-8601; naive
                      datetimes treated as UTC.
        timeout_ms:   Server-side response timeout.

    Returns:
        ``[{"time": "2026-05-06T16:29:00.123", "type": "TRADE",
            "value": 286.05, "size": 100, ...}, ...]``

        Empty list if BBG returned no ticks. Tick density varies — a
        liquid name in trading hours can produce thousands per minute,
        so keep windows narrow.

    Example:
        >>> ticks = get_ticks(host, "AAPL US Equity", "TRADE",
        ...                   start, end)
        >>> sum(t["size"] for t in ticks)
        125_300
    """
    if isinstance(event_types, str):
        event_types = [event_types]

    token = host._get_token()

    ops: list = [
        StartSessionOp(),
        OpenServiceOp(service="//blp/refdata"),
        CreateRequestOp(
            service="//blp/refdata",
            request="IntradayTickRequest",
            id="req1",
        ),
        SetOp(id="req1", path="security", value=security),
    ]
    for et in event_types:
        ops.append(AppendOp(id="req1", path="eventTypes", value=et))
    ops.extend([
        SetOp(id="req1", path="startDateTime", value=_format_datetime(start)),
        SetOp(id="req1", path="endDateTime", value=_format_datetime(end)),
        SendRequestOp(id="req1", correlation_id="cid1"),
        CollectResponseOp(correlation_id="cid1", timeout_ms=timeout_ms),
    ])

    plan = ExecutionPlan(auth=AuthToken(token=token), ops=ops)
    result = host.execute(plan)

    ticks = result.data.get(security)
    return ticks if isinstance(ticks, list) else []


def get_returns_data(
    host: RemoteHost,
    securities: Union[str, list[str]],
    start_date: Union[str, date, datetime],
    end_date: Union[str, date, datetime, None] = None,
    timeout_ms: int = 60000,
) -> dict[str, dict[str, list[Any]]]:
    """
    Get historical price data suitable for computing returns.

    Args:
        host: Connected RemoteHost instance
        securities: Security or list of securities
        start_date: Start date
        end_date: End date (defaults to today)
        timeout_ms: Timeout in milliseconds

    Returns:
        Dictionary with price data for return calculations
    """
    return bdh(
        host,
        securities,
        ["PX_LAST", "VOLUME"],
        start_date,
        end_date,
        timeout_ms=timeout_ms,
    )
