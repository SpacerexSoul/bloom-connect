"""Server-side authentication: password hashing and JWT tokens.

Uses bcrypt directly (instead of passlib) for simpler deployment
and better version compatibility.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import bcrypt
from jose import JWTError, jwt

from blpremote_server.config import settings


class UserStore:
    """Simple JSON-based user store for MVP."""

    def __init__(self, path: Optional[str] = None):
        self.path = Path(path or settings.user_store_path)
        self._users: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        """Load users from file."""
        if self.path.exists():
            try:
                with open(self.path, "r") as f:
                    self._users = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._users = {}

    def _save(self) -> None:
        """Save users to file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self._users, f, indent=2)
        # Restrict permissions (works on Windows too)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass  # Windows may not support chmod

    def create_user(self, username: str, password: str) -> bool:
        """Create a new user with hashed password."""
        if username in self._users:
            return False
        self._users[username] = {
            "username": username,
            "password_hash": hash_password(password),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save()
        return True

    def verify_user(self, username: str, password: str) -> bool:
        """Verify username and password."""
        if username not in self._users:
            return False
        return verify_password(password, self._users[username]["password_hash"])

    def get_user(self, username: str) -> Optional[dict]:
        """Get user data (excluding password hash)."""
        if username not in self._users:
            return None
        user = self._users[username].copy()
        del user["password_hash"]
        return user

    def user_exists(self, username: str) -> bool:
        """Check if a user exists."""
        return username in self._users


def hash_password(password: str) -> str:
    """Hash a password using bcrypt.

    Note: bcrypt has a 72-byte limit on passwords. We truncate to ensure compatibility.
    """
    # Encode and truncate to 72 bytes (bcrypt limit)
    password_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its bcrypt hash.

    Note: bcrypt has a 72-byte limit on passwords. We truncate to ensure compatibility.
    """
    try:
        # Encode and truncate to 72 bytes (bcrypt limit)
        password_bytes = plain_password.encode("utf-8")[:72]
        hashed_bytes = hashed_password.encode("utf-8")
        return bcrypt.checkpw(password_bytes, hashed_bytes)
    except (ValueError, TypeError):
        return False


def create_access_token(username: str, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token."""
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.token_expire_minutes)
    )
    to_encode = {
        "sub": username,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


def verify_token(token: str) -> Optional[str]:
    """
    Verify a JWT token and return the username.

    Returns None if the token is invalid or expired.
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        username: str = payload.get("sub")
        if username is None:
            return None
        return username
    except JWTError:
        return None


# Global user store instance
user_store = UserStore()
