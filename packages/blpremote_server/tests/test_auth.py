"""Tests for server authentication."""

import pytest
from datetime import timedelta

from blpremote_server.auth import (
    hash_password,
    verify_password,
    create_access_token,
    verify_token,
    UserStore,
)


class TestPasswordHashing:
    """Tests for password hashing."""

    def test_hash_password(self):
        """Test that password hashing produces a hash."""
        password = "test-password-123"
        hashed = hash_password(password)
        assert hashed != password
        assert len(hashed) > 20

    def test_verify_correct_password(self):
        """Test verifying correct password."""
        password = "test-password-123"
        hashed = hash_password(password)
        assert verify_password(password, hashed) is True

    def test_verify_wrong_password(self):
        """Test verifying wrong password."""
        password = "test-password-123"
        hashed = hash_password(password)
        assert verify_password("wrong-password", hashed) is False


class TestJWTTokens:
    """Tests for JWT token handling."""

    def test_create_token(self):
        """Test creating a token."""
        token = create_access_token("test-user")
        assert token is not None
        assert len(token) > 50

    def test_verify_valid_token(self):
        """Test verifying a valid token."""
        token = create_access_token("test-user")
        username = verify_token(token)
        assert username == "test-user"

    def test_verify_invalid_token(self):
        """Test verifying an invalid token."""
        username = verify_token("invalid-token")
        assert username is None

    def test_verify_expired_token(self):
        """Test that expired tokens are rejected."""
        # Create a token that expired in the past
        token = create_access_token("test-user", expires_delta=timedelta(seconds=-1))
        username = verify_token(token)
        assert username is None


class TestUserStore:
    """Tests for user store."""

    def test_create_user(self, tmp_path):
        """Test creating a user."""
        store = UserStore(str(tmp_path / "users.json"))
        result = store.create_user("testuser", "testpass")
        assert result is True
        assert store.user_exists("testuser") is True

    def test_create_duplicate_user(self, tmp_path):
        """Test that duplicate users are rejected."""
        store = UserStore(str(tmp_path / "users.json"))
        store.create_user("testuser", "testpass")
        result = store.create_user("testuser", "testpass2")
        assert result is False

    def test_verify_user(self, tmp_path):
        """Test verifying user credentials."""
        store = UserStore(str(tmp_path / "users.json"))
        store.create_user("testuser", "testpass")

        assert store.verify_user("testuser", "testpass") is True
        assert store.verify_user("testuser", "wrongpass") is False
        assert store.verify_user("nonexistent", "testpass") is False

    def test_get_user(self, tmp_path):
        """Test getting user info."""
        store = UserStore(str(tmp_path / "users.json"))
        store.create_user("testuser", "testpass")

        user = store.get_user("testuser")
        assert user is not None
        assert user["username"] == "testuser"
        assert "password_hash" not in user  # Should be excluded
