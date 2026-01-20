"""Bloomberg Remote Client - macOS package for remote Bloomberg API execution."""

from blpremote_client.host import RemoteHost
from blpremote_client.client import px_last, ref_data
from blpremote_client.data import bdh, bds, get_index_members, get_historical_prices
from blpremote_client.exceptions import (
    BlpRemoteError,
    AuthenticationError,
    ConnectionError,
    ExecutionError,
    ValidationError,
)

__version__ = "0.1.0"
__all__ = [
    "RemoteHost",
    "px_last",
    "ref_data",
    "bdh",
    "bds",
    "get_index_members",
    "get_historical_prices",
    "BlpRemoteError",
    "AuthenticationError",
    "ConnectionError",
    "ExecutionError",
    "ValidationError",
]
