"""Tests for exception handling and error mapping."""

from blpremote_client.exceptions import (
    AuthenticationError,
    BlpRemoteError,
    ConnectionError,
    ExecutionError,
    FieldError,
    SecurityError,
    SessionError,
    TimeoutError,
    ValidationError,
    from_error_code,
)


class TestExceptions:
    """Tests for exception classes."""

    def test_base_error_with_code(self):
        """Test BlpRemoteError with error code."""
        err = BlpRemoteError("Test error", code="TEST_CODE")
        assert err.message == "Test error"
        assert err.code == "TEST_CODE"
        assert str(err) == "[TEST_CODE] Test error"

    def test_base_error_without_code(self):
        """Test BlpRemoteError without error code."""
        err = BlpRemoteError("Test error")
        assert str(err) == "Test error"

    def test_authentication_error(self):
        """Test AuthenticationError."""
        err = AuthenticationError()
        assert err.code == "AUTH_FAILED"
        assert "Authentication failed" in str(err)

    def test_connection_error_with_host(self):
        """Test ConnectionError with host info."""
        err = ConnectionError("Cannot connect", host="192.168.1.100")
        assert err.host == "192.168.1.100"
        assert err.code == "CONNECTION_FAILED"

    def test_security_error(self):
        """Test SecurityError with security name."""
        err = SecurityError("INVALID Equity")
        assert err.security == "INVALID Equity"
        assert err.code == "BLP_SECURITY_ERROR"
        assert "INVALID Equity" in str(err)

    def test_field_error(self):
        """Test FieldError with field name."""
        err = FieldError("INVALID_FIELD")
        assert err.field == "INVALID_FIELD"
        assert err.code == "BLP_FIELD_ERROR"


class TestFromErrorCode:
    """Tests for error code to exception mapping."""

    def test_auth_failed_code(self):
        """Test mapping AUTH_FAILED code."""
        err = from_error_code("AUTH_FAILED", "Invalid credentials")
        assert isinstance(err, AuthenticationError)

    def test_connection_failed_code(self):
        """Test mapping CONNECTION_FAILED code."""
        err = from_error_code("CONNECTION_FAILED", "Cannot connect")
        assert isinstance(err, ConnectionError)

    def test_plan_invalid_code(self):
        """Test mapping PLAN_INVALID code."""
        err = from_error_code("PLAN_INVALID", "Bad plan")
        assert isinstance(err, ValidationError)

    def test_session_fail_code(self):
        """Test mapping BLP_SESSION_FAIL code."""
        err = from_error_code("BLP_SESSION_FAIL", "Session failed")
        assert isinstance(err, SessionError)

    def test_timeout_code(self):
        """Test mapping BLP_TIMEOUT code."""
        err = from_error_code("BLP_TIMEOUT", "Timed out")
        assert isinstance(err, TimeoutError)

    def test_unknown_code(self):
        """Test mapping unknown code falls back to ExecutionError."""
        err = from_error_code("UNKNOWN_CODE", "Unknown error")
        assert isinstance(err, ExecutionError)
        assert err.code == "EXECUTION_FAILED"  # Unknown codes use default ExecutionError code
