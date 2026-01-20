"""FastAPI application for Bloomberg remote execution."""

from datetime import timedelta
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from blpremote_server import __version__
from blpremote_server.auth import (
    create_access_token,
    user_store,
    verify_token,
)
from blpremote_server.config import settings
from blpremote_server.exceptions import ValidationError as PlanValidationError
from blpremote_server.executor import execute_plan, validate_plan
from blpremote_server.models import (
    ExecutionPlan,
    ExecutionResult,
    ErrorDetail,
    HealthResponse,
    LoginRequest,
    LoginResponse,
    VersionResponse,
)


app = FastAPI(
    title="Bloomberg Remote Server",
    description="Remote execution server for Bloomberg BLPAPI operations",
    version=__version__,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security
security = HTTPBearer(auto_error=False)


def get_client_ip(request: Request) -> str:
    """Get the client IP address."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_ip_allowlist(request: Request) -> None:
    """Check if the client IP is in the allowlist."""
    if not settings.ip_allowlist:
        return  # No allowlist = allow all

    client_ip = get_client_ip(request)
    if client_ip not in settings.ip_allowlist:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"IP {client_ip} not in allowlist",
        )


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> str:
    """Validate the JWT token and return the username."""
    check_ip_allowlist(request)

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    username = verify_token(credentials.credentials)
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return username


# Health and version endpoints
@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Check server health."""
    # Try to detect if Bloomberg is available
    try:
        import blpapi
        bloomberg_available = True
    except ImportError:
        bloomberg_available = False

    return HealthResponse(status="healthy", bloomberg_connected=bloomberg_available)


@app.get("/version", response_model=VersionResponse)
async def get_version() -> VersionResponse:
    """Get server version."""
    return VersionResponse(version=__version__)


# Authentication endpoints
@app.post("/v1/auth/login", response_model=LoginResponse)
async def login(request: Request, login_data: LoginRequest) -> LoginResponse:
    """Authenticate and get an access token."""
    check_ip_allowlist(request)

    # Verify credentials
    if not user_store.verify_user(login_data.username, login_data.password):
        # For MVP, auto-create user if they don't exist
        if not user_store.user_exists(login_data.username):
            user_store.create_user(login_data.username, login_data.password)
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
            )

    # Create token
    expires_delta = timedelta(minutes=settings.token_expire_minutes)
    token = create_access_token(login_data.username, expires_delta)

    return LoginResponse(
        token=token,
        expires_in=settings.token_expire_minutes * 60,
    )


# Execution endpoint
@app.post("/v1/execute", response_model=ExecutionResult)
async def execute(
    plan: ExecutionPlan,
    username: str = Depends(get_current_user),
) -> ExecutionResult:
    """Execute a validated Bloomberg execution plan."""
    try:
        # Validate the plan
        validate_plan(plan)

        # Execute the plan
        result = execute_plan(plan)
        return result

    except PlanValidationError as e:
        return ExecutionResult(
            request_id=plan.request_id,
            status="error",
            data={},
            errors=[ErrorDetail(code=e.code, message=e.message)],
        )
    except Exception as e:
        return ExecutionResult(
            request_id=plan.request_id,
            status="error",
            data={},
            errors=[ErrorDetail(code="EXECUTION_FAILED", message=str(e))],
        )


# Admin endpoint to create users (optional, for setup)
@app.post("/v1/admin/create-user", status_code=status.HTTP_201_CREATED)
async def create_user(
    request: Request,
    user_data: LoginRequest,
) -> dict:
    """Create a new user (for initial setup)."""
    check_ip_allowlist(request)

    if user_store.user_exists(user_data.username):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already exists",
        )

    user_store.create_user(user_data.username, user_data.password)
    return {"message": f"User '{user_data.username}' created successfully"}


def main():
    """Run the server."""
    import uvicorn
    uvicorn.run(
        "blpremote_server.app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    main()
