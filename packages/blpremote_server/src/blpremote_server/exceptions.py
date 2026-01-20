"""Exception classes for the server."""

from typing import Optional


class BlpServerError(Exception):
    """Base server error."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.message = message
        self.code = code


class AuthError(BlpServerError):
    """Authentication error."""

    def __init__(self, message: str = "Authentication failed"):
        super().__init__(message, code="AUTH_FAILED")


class ValidationError(BlpServerError):
    """Plan validation error."""

    def __init__(self, message: str = "Plan validation failed"):
        super().__init__(message, code="PLAN_INVALID")


class SessionError(BlpServerError):
    """Bloomberg session error."""

    def __init__(self, message: str = "Bloomberg session error"):
        super().__init__(message, code="BLP_SESSION_FAIL")


class TimeoutError(BlpServerError):
    """Request timeout."""

    def __init__(self, message: str = "Request timed out"):
        super().__init__(message, code="BLP_TIMEOUT")


class SecurityError(BlpServerError):
    """Invalid security."""

    def __init__(self, security: str, message: Optional[str] = None):
        self.security = security
        msg = message or f"Invalid security: {security}"
        super().__init__(msg, code="BLP_SECURITY_ERROR")


class FieldError(BlpServerError):
    """Invalid field."""

    def __init__(self, field: str, message: Optional[str] = None):
        self.field = field
        msg = message or f"Invalid field: {field}"
        super().__init__(msg, code="BLP_FIELD_ERROR")
