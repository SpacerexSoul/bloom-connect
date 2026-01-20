"""Extended Bloomberg data functions for historical and bulk data requests."""

from typing import Any, Optional, Union
from datetime import date, datetime

from blpremote_client.host import RemoteHost
from blpremote_client.models import (
    ExecutionPlan,
    AuthToken,
    StartSessionOp,
    OpenServiceOp,
    CreateRequestOp,
    AppendOp,
    SetOp,
    SendRequestOp,
    CollectResponseOp,
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
