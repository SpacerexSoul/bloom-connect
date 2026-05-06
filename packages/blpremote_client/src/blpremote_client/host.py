"""Remote host connection and execution."""

from typing import Any, Optional

import httpx

from blpremote_client.auth import TokenManager
from blpremote_client.exceptions import (
    AuthenticationError,
    ConnectionError,
    ExecutionError,
    from_error_code,
)
from blpremote_client.models import AuthToken, ExecutionPlan, ExecutionResult


class RemoteHost:
    """Connection to a remote Bloomberg host running blpremote_server."""

    def __init__(
        self,
        host: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: float = 30.0,
    ):
        """
        Initialize connection to a remote Bloomberg host.

        Args:
            host: URL of the remote server (e.g., "http://192.168.1.100:8000")
            username: Username for authentication
            password: Password for authentication
            timeout: Request timeout in seconds
        """
        self.host = host.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self._token_manager = TokenManager(host)
        self._client = httpx.Client(timeout=timeout)

    def _get_token(self) -> str:
        """Get a valid authentication token."""
        if not self.username or not self.password:
            token = self._token_manager.get_token()
            if not token:
                raise AuthenticationError(
                    "No valid token and no credentials provided. "
                    "Please provide username and password or run 'pair' first."
                )
            return token
        return self._token_manager.ensure_token(self.username, self.password)

    def health(self) -> dict[str, Any]:
        """Check server health."""
        try:
            response = self._client.get(f"{self.host}/health")
            response.raise_for_status()
            return response.json()
        except httpx.ConnectError as e:
            raise ConnectionError(f"Cannot connect to {self.host}", host=self.host) from e
        except httpx.HTTPStatusError as e:
            raise ConnectionError(f"Health check failed: {e}") from e

    def version(self) -> str:
        """Get server version."""
        try:
            response = self._client.get(f"{self.host}/version")
            response.raise_for_status()
            return response.json().get("version", "unknown")
        except httpx.HTTPError as e:
            raise ConnectionError(f"Version check failed: {e}") from e

    def get_schema(
        self,
        service: str,
        etag: Optional[str] = None,
    ) -> tuple[Optional[dict[str, Any]], Optional[str]]:
        """Fetch a Bloomberg service schema from the server's SchemaCache.

        Bearer-authenticated. Returns ``(body, etag)``:

        - ``body`` is the parsed schema dict (``{"service", "operations"}``)
          on a fresh fetch, or ``None`` if the server returned 304.
        - ``etag`` is the response ETag — always set on 200 and 304;
          callers should hand it back on the next call to short-circuit.

        Path encoding: the FastAPI handler captures everything after
        ``/v1/schema/`` and restores a leading ``//`` if only one
        survives. We always percent-encode here so a service like
        ``//blp/refdata`` round-trips as ``%2F%2Fblp%2Frefdata``.
        """
        from urllib.parse import quote

        token = self._get_token()
        encoded = quote(service, safe="")
        headers: dict[str, str] = {"Authorization": f"Bearer {token}"}
        if etag:
            headers["If-None-Match"] = etag
        try:
            response = self._client.get(
                f"{self.host}/v1/schema/{encoded}", headers=headers
            )
            if response.status_code == 304:
                return None, response.headers.get("ETag", etag)
            if response.status_code == 401:
                self._token_manager.clear_token()
                raise AuthenticationError("Token expired or invalid")
            response.raise_for_status()
            return response.json(), response.headers.get("ETag")
        except httpx.ConnectError as e:
            raise ConnectionError(
                f"Cannot connect to {self.host}", host=self.host
            ) from e
        except httpx.HTTPStatusError as e:
            raise ConnectionError(
                f"Schema fetch failed: HTTP {e.response.status_code}"
            ) from e

    def execute(self, plan: ExecutionPlan) -> ExecutionResult:
        """Execute a plan on the remote host."""
        try:
            response = self._client.post(
                f"{self.host}/v1/execute",
                json=plan.model_dump(),
                headers={"Authorization": f"Bearer {plan.auth.token}"},
            )

            if response.status_code == 401:
                self._token_manager.clear_token()
                raise AuthenticationError("Token expired or invalid")

            if response.status_code == 422:
                error_detail = response.json().get("detail", "Validation failed")
                raise ExecutionError(str(error_detail), code="PLAN_INVALID")

            response.raise_for_status()
            result = ExecutionResult(**response.json())

            # Raise exception if there are errors and status is "error"
            if result.status == "error" and result.errors:
                first_error = result.errors[0]
                raise from_error_code(first_error.code, first_error.message)

            return result

        except httpx.ConnectError as e:
            raise ConnectionError(f"Cannot connect to {self.host}", host=self.host) from e
        except httpx.TimeoutException:
            raise ExecutionError("Request timed out", code="BLP_TIMEOUT")
        except httpx.HTTPStatusError as e:
            raise ExecutionError(f"HTTP error: {e.response.status_code}") from e

    def execute_with_auth(self, plan: ExecutionPlan) -> ExecutionResult:
        """Execute a plan, automatically adding authentication."""
        token = self._get_token()
        plan.auth = AuthToken(token=token)
        return self.execute(plan)

    def pair(self, username: str, password: str) -> bool:
        """
        Pair with the remote host by authenticating and storing the token.

        Returns True if pairing was successful.
        """
        try:
            self._token_manager.login(username, password)
            # Verify connection works
            self.health()
            return True
        except (AuthenticationError, ConnectionError):
            return False

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self) -> "RemoteHost":
        return self

    def __exit__(self, *args) -> None:
        self.close()
