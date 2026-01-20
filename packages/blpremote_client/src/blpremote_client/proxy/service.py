"""Bloomberg-like Service proxy."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from blpremote_client.proxy.session import Session


class Service:
    """
    Bloomberg-like Service proxy.

    Mimics the blpapi.Service interface but accumulates operations
    for remote execution instead of local execution.
    """

    def __init__(self, session: "Session", name: str):
        self._session = session
        self._name = name
        self._request_counter = 0

    def createRequest(self, request_type: str) -> "Request":
        """
        Create a new request of the specified type.

        Args:
            request_type: Type of request (e.g., "ReferenceDataRequest")

        Returns:
            A Request proxy object
        """
        from blpremote_client.proxy.request import Request

        self._request_counter += 1
        request_id = f"req{self._request_counter}"

        # Record the create_request operation in the session
        self._session._add_op({
            "op": "create_request",
            "service": self._name,
            "request": request_type,
            "id": request_id,
        })

        return Request(self, request_type, request_id)

    @property
    def name(self) -> str:
        """Get the service name."""
        return self._name
