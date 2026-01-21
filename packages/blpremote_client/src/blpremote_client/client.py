"""High-level convenience API for Bloomberg data retrieval."""

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
    StartSessionOp,
)


def _build_refdata_plan(
    token: str,
    securities: list[str],
    fields: list[str],
    timeout_ms: int = 10000,
) -> ExecutionPlan:
    """Build an execution plan for reference data request."""
    ops = [
        StartSessionOp(),
        OpenServiceOp(service="//blp/refdata"),
        CreateRequestOp(
            service="//blp/refdata",
            request="ReferenceDataRequest",
            id="req1",
        ),
    ]

    # Add securities
    for sec in securities:
        ops.append(AppendOp(id="req1", path="securities", value=sec))

    # Add fields
    for field in fields:
        ops.append(AppendOp(id="req1", path="fields", value=field))

    # Send and collect
    ops.append(SendRequestOp(id="req1", correlation_id="cid1"))
    ops.append(CollectResponseOp(correlation_id="cid1", timeout_ms=timeout_ms))

    return ExecutionPlan(
        auth=AuthToken(token=token),
        ops=ops,
    )


def px_last(
    host: RemoteHost,
    security: str,
    timeout_ms: int = 10000,
) -> Optional[float]:
    """
    Get the last price for a security.

    Args:
        host: Connected RemoteHost instance
        security: Bloomberg security identifier (e.g., "IBM US Equity")
        timeout_ms: Timeout in milliseconds

    Returns:
        The last price as a float, or None if unavailable

    Example:
        >>> from blpremote_client import RemoteHost, px_last
        >>> host = RemoteHost("http://192.168.1.100:8000", username="krishna", password="***")
        >>> price = px_last(host, "IBM US Equity")
        >>> print(price)
        123.45
    """
    token = host._get_token()
    plan = _build_refdata_plan(token, [security], ["PX_LAST"], timeout_ms)
    result = host.execute(plan)

    if security in result.data:
        return result.data[security].get("PX_LAST")
    return None


def ref_data(
    host: RemoteHost,
    securities: Union[str, list[str]],
    fields: Union[str, list[str]],
    timeout_ms: int = 10000,
) -> dict[str, dict[str, Any]]:
    """
    Get reference data for securities and fields.

    Args:
        host: Connected RemoteHost instance
        securities: Security or list of securities
        fields: Field or list of fields
        timeout_ms: Timeout in milliseconds

    Returns:
        Dictionary mapping security -> {field: value}

    Example:
        >>> from blpremote_client import RemoteHost, ref_data
        >>> host = RemoteHost("http://192.168.1.100:8000", username="krishna", password="***")
        >>> data = ref_data(host, ["IBM US Equity", "AAPL US Equity"], ["PX_LAST", "NAME"])
        >>> print(data)
        {'IBM US Equity': {'PX_LAST': 123.45, 'NAME': 'International Business Machines'}, ...}
    """
    if isinstance(securities, str):
        securities = [securities]
    if isinstance(fields, str):
        fields = [fields]

    token = host._get_token()
    plan = _build_refdata_plan(token, securities, fields, timeout_ms)
    result = host.execute(plan)

    return result.data
