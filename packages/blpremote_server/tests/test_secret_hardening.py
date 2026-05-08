"""Tests for the M5(A) JWT secret hardening guard."""

from __future__ import annotations

import logging

import pytest

from blpremote_server.auth import (
    DEFAULT_SECRET_SENTINEL,
    InsecureSecretError,
    assert_secret_is_safe,
)
from blpremote_server.config import settings


@pytest.fixture(autouse=True)
def _isolate_secret_settings():
    """Snapshot/restore the three knobs this test suite touches."""
    saved = (
        settings.secret_key,
        settings.allow_default_secret,
        settings.rotate_secret_at_boot,
    )
    yield
    (
        settings.secret_key,
        settings.allow_default_secret,
        settings.rotate_secret_at_boot,
    ) = saved


class TestAssertSecretIsSafe:
    def test_default_secret_without_opt_in_raises(self):
        settings.secret_key = DEFAULT_SECRET_SENTINEL
        settings.allow_default_secret = False
        settings.rotate_secret_at_boot = False
        with pytest.raises(InsecureSecretError, match="sentinel default"):
            assert_secret_is_safe()

    def test_default_secret_with_opt_in_warns_but_allows(self, caplog):
        settings.secret_key = DEFAULT_SECRET_SENTINEL
        settings.allow_default_secret = True
        settings.rotate_secret_at_boot = False
        with caplog.at_level(logging.WARNING, logger="blpremote_server.auth"):
            assert_secret_is_safe()
        # Warning emitted, no raise, secret_key unchanged.
        assert any("ALLOW_DEFAULT_SECRET" in r.message for r in caplog.records)
        assert settings.secret_key == DEFAULT_SECRET_SENTINEL

    def test_user_configured_secret_passes_silently(self, caplog):
        settings.secret_key = "a-real-high-entropy-secret-from-env"
        settings.allow_default_secret = False
        settings.rotate_secret_at_boot = False
        with caplog.at_level(logging.WARNING, logger="blpremote_server.auth"):
            assert_secret_is_safe()
        # No warnings emitted — user did the right thing.
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert warnings == []
        assert settings.secret_key == "a-real-high-entropy-secret-from-env"

    def test_rotate_at_boot_replaces_default(self, caplog):
        settings.secret_key = DEFAULT_SECRET_SENTINEL
        settings.allow_default_secret = False  # would normally raise
        settings.rotate_secret_at_boot = True   # but this overrides
        with caplog.at_level(logging.WARNING, logger="blpremote_server.auth"):
            assert_secret_is_safe()
        # Secret was replaced with something high-entropy.
        assert settings.secret_key != DEFAULT_SECRET_SENTINEL
        assert len(settings.secret_key) >= 60  # token_urlsafe(48) -> ~64 chars
        # And we logged the rotation.
        assert any("ROTATE_SECRET_AT_BOOT" in r.message for r in caplog.records)

    def test_rotate_replaces_user_configured_too(self):
        """Rotation is unconditional — even a user-configured secret
        is replaced. Operators choosing rotation are saying 'I want
        the strongest possible isolation between server lifetimes.'"""
        settings.secret_key = "previously-configured-secret"
        settings.rotate_secret_at_boot = True
        assert_secret_is_safe()
        assert settings.secret_key != "previously-configured-secret"
        assert len(settings.secret_key) >= 60

    def test_rotate_each_call_yields_distinct_secret(self):
        """Two consecutive boots with rotation produce different
        secrets (statistically — token_urlsafe(48) collision is
        astronomically unlikely)."""
        settings.secret_key = DEFAULT_SECRET_SENTINEL
        settings.rotate_secret_at_boot = True
        assert_secret_is_safe()
        first = settings.secret_key
        assert_secret_is_safe()
        second = settings.secret_key
        assert first != second

    def test_error_message_includes_helpful_remediation(self):
        settings.secret_key = DEFAULT_SECRET_SENTINEL
        settings.allow_default_secret = False
        settings.rotate_secret_at_boot = False
        with pytest.raises(InsecureSecretError) as exc:
            assert_secret_is_safe()
        msg = str(exc.value)
        # Guides the operator to an actionable fix, not just a refusal.
        assert "BLPREMOTE_SECRET_KEY" in msg
        assert "secrets.token_urlsafe" in msg
        assert "BLPREMOTE_ALLOW_DEFAULT_SECRET" in msg
