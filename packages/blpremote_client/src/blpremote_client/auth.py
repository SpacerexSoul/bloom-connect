"""Authentication and token management for the client."""

import json
import os
import time
from pathlib import Path
from typing import Optional

import httpx

from blpremote_client.exceptions import AuthenticationError


class TokenManager:
    """Manages authentication tokens for remote Bloomberg host."""

    TOKEN_DIR = Path.home() / ".blpremote"
    TOKEN_FILE = TOKEN_DIR / "tokens.json"
    TOKEN_EXPIRY_BUFFER = 60  # Refresh token 60 seconds before expiry

    def __init__(self, host: str):
        self.host = host.rstrip("/")
        self._tokens: dict[str, dict] = {}
        self._load_tokens()

    def _load_tokens(self) -> None:
        """Load tokens from disk."""
        if self.TOKEN_FILE.exists():
            try:
                with open(self.TOKEN_FILE, "r") as f:
                    self._tokens = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._tokens = {}

    def _save_tokens(self) -> None:
        """Save tokens to disk."""
        self.TOKEN_DIR.mkdir(parents=True, exist_ok=True)
        with open(self.TOKEN_FILE, "w") as f:
            json.dump(self._tokens, f, indent=2)
        # Restrict permissions
        os.chmod(self.TOKEN_FILE, 0o600)

    def _host_key(self) -> str:
        """Generate a key for this host."""
        return self.host.replace("://", "_").replace("/", "_").replace(":", "_")

    def get_token(self) -> Optional[str]:
        """Get the current valid token, if any."""
        key = self._host_key()
        if key not in self._tokens:
            return None
        token_data = self._tokens[key]
        expires_at = token_data.get("expires_at", 0)
        if time.time() >= expires_at - self.TOKEN_EXPIRY_BUFFER:
            return None
        return token_data.get("token")

    def store_token(self, token: str, expires_in: int) -> None:
        """Store a new token with expiration."""
        key = self._host_key()
        self._tokens[key] = {
            "token": token,
            "expires_at": time.time() + expires_in,
            "host": self.host,
        }
        self._save_tokens()

    def clear_token(self) -> None:
        """Clear the token for this host."""
        key = self._host_key()
        if key in self._tokens:
            del self._tokens[key]
            self._save_tokens()

    def login(self, username: str, password: str, timeout: float = 10.0) -> str:
        """Authenticate with the server and store the token."""
        url = f"{self.host}/v1/auth/login"
        try:
            response = httpx.post(
                url,
                json={"username": username, "password": password},
                timeout=timeout,
            )
            if response.status_code == 401:
                raise AuthenticationError("Invalid username or password")
            if response.status_code != 200:
                raise AuthenticationError(f"Login failed: HTTP {response.status_code}")

            data = response.json()
            token = data["token"]
            expires_in = data.get("expires_in", 3600)
            self.store_token(token, expires_in)
            return token

        except httpx.ConnectError as e:
            raise AuthenticationError(f"Cannot connect to {self.host}: {e}")
        except httpx.TimeoutException:
            raise AuthenticationError(f"Connection to {self.host} timed out")

    def ensure_token(self, username: str, password: str) -> str:
        """Get existing token or login to get a new one."""
        token = self.get_token()
        if token:
            return token
        return self.login(username, password)
