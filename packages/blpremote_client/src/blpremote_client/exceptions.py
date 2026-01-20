"""Exception classes for Bloomberg Remote Client."""

from typing import Optional


class BlpRemoteError(Exception):
    """Base exception for all Bloomberg Remote errors."""

    def __init__(self, message: str, code: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.code = code

    def __str__(self) -> str:
        if self.code:
            return f"[{self.code}] {self.message}"
        return self.message


class AuthenticationError(BlpRemoteError):
    """Authentication failed - invalid credentials or expired token."""

    def __init__(self, message: str = "Authentication failed"):
        super().__init__(message, code="AUTH_FAILED")


class ConnectionError(BlpRemoteError):
    """Failed to connect to the remote Bloomberg host."""

    def __init__(self, message: str = "Connection failed", host: Optional[str] = None):
        self.host = host
        super().__init__(message, code="CONNECTION_FAILED")


class ExecutionError(BlpRemoteError):
    """Error during Bloomberg API execution on the remote host."""

    def __init__(self, message: str, code: str = "EXECUTION_FAILED"):
        super().__init__(message, code=code)


class ValidationError(BlpRemoteError):
    """Execution plan validation failed."""

    def __init__(self, message: str = "Plan validation failed"):
        super().__init__(message, code="PLAN_INVALID")


class SessionError(ExecutionError):
    """Bloomberg session failed to start or was terminated."""

    def __init__(self, message: str = "Bloomberg session error"):
        super().__init__(message, code="BLP_SESSION_FAIL")


class TimeoutError(ExecutionError):
    """Request timed out waiting for Bloomberg response."""

    def __init__(self, message: str = "Request timed out"):
        super().__init__(message, code="BLP_TIMEOUT")


class SecurityError(ExecutionError):
    """Invalid or unavailable security identifier."""

    def __init__(self, security: str, message: Optional[str] = None):
        self.security = security
        msg = message or f"Invalid security: {security}"
        super().__init__(msg, code="BLP_SECURITY_ERROR")


class FieldError(ExecutionError):
    """Invalid or unavailable field."""

    def __init__(self, field: str, message: Optional[str] = None):
        self.field = field
        msg = message or f"Invalid field: {field}"
        super().__init__(msg, code="BLP_FIELD_ERROR")


# Error code to exception class mapping
ERROR_CODE_MAP = {
    "AUTH_FAILED": AuthenticationError,
    "CONNECTION_FAILED": ConnectionError,
    "PLAN_INVALID": ValidationError,
    "BLP_SESSION_FAIL": SessionError,
    "BLP_TIMEOUT": TimeoutError,
    "BLP_SECURITY_ERROR": SecurityError,
    "BLP_FIELD_ERROR": FieldError,
}


def from_error_code(code: str, message: str) -> BlpRemoteError:
    """Create an exception from an error code returned by the server."""
    exc_class = ERROR_CODE_MAP.get(code, ExecutionError)
    if exc_class in (SecurityError, FieldError):
        return ExecutionError(message, code=code)
    return exc_class(message)
