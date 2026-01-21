"""Bloomberg-like Request proxy that builds IR operations."""

from typing import Any, Union


class Element:
    """Proxy for Bloomberg Element that accumulates values."""

    def __init__(self, request: "Request", path: str):
        self._request = request
        self._path = path

    def appendValue(self, value: Union[str, int, float, bool]) -> None:
        """Append a value to this element (for arrays like securities/fields)."""
        self._request._append(self._path, value)

    def setValue(self, value: Union[str, int, float, bool]) -> None:
        """Set a single value for this element."""
        self._request._set(self._path, value)


class Request:
    """
    Bloomberg-like Request proxy that builds IR operations.

    This mimics the blpapi.Request interface but doesn't execute locally.
    Instead, it accumulates operations to be sent to the remote host.
    """

    def __init__(self, service: "Service", request_type: str, request_id: str):
        self._service = service
        self._request_type = request_type
        self._request_id = request_id
        self._operations: list[dict[str, Any]] = []

    def getElement(self, name: str) -> Element:
        """Get an element by name (returns a proxy Element)."""
        return Element(self, name)

    def set(self, name: str, value: Union[str, int, float, bool]) -> None:
        """Set a field value directly."""
        self._set(name, value)

    def append(self, name: str, value: Union[str, int, float, bool]) -> None:
        """Append a value to an array field."""
        self._append(name, value)

    def _append(self, path: str, value: Union[str, int, float, bool]) -> None:
        """Internal: record an append operation."""
        self._operations.append(
            {
                "type": "append",
                "path": path,
                "value": value,
            }
        )

    def _set(self, path: str, value: Union[str, int, float, bool]) -> None:
        """Internal: record a set operation."""
        self._operations.append(
            {
                "type": "set",
                "path": path,
                "value": value,
            }
        )

    def _get_operations(self) -> list[dict[str, Any]]:
        """Get all recorded operations."""
        return self._operations

    def _get_id(self) -> str:
        """Get the request ID."""
        return self._request_id

    @property
    def service_name(self) -> str:
        """Get the service name this request belongs to."""
        return self._service._name

    @property
    def request_type(self) -> str:
        """Get the request type."""
        return self._request_type


# Import at bottom to avoid circular imports
from blpremote_client.proxy.service import Service  # noqa: E402
