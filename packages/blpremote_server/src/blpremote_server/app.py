"""FastAPI application for Bloomberg remote execution."""

import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from blpremote_server import __version__, coord
from blpremote_server.auth import (
    create_access_token,
    user_store,
    verify_token,
)
from blpremote_server.config import settings
from blpremote_server.exceptions import ValidationError as PlanValidationError
from blpremote_server.executor import execute_plan, validate_plan
from blpremote_server.models import (
    CoordInboxResponse,
    CoordMessage,
    CoordSendRequest,
    CoordSendResponse,
    ErrorDetail,
    ExecutionPlan,
    ExecutionResult,
    HealthResponse,
    LoginRequest,
    LoginResponse,
    VersionResponse,
)
from blpremote_server.session_manager import (
    BLPAPI_AVAILABLE,
    SessionState,
    get_manager,
)

logger = logging.getLogger(__name__)

# Map SessionManager state -> top-level /health status string.
_HEALTH_STATUS_BY_STATE = {
    SessionState.CONNECTED: "healthy",
    SessionState.STARTING: "degraded",
    SessionState.DEGRADED: "degraded",
    SessionState.RECONNECTING: "degraded",
    SessionState.FAILED: "unavailable",
    SessionState.STOPPED: "unavailable",
    SessionState.SHUTTING_DOWN: "unavailable",
    SessionState.UNINITIALIZED: "unavailable",
}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Start the long-lived blpapi session at boot, stop at shutdown.

    If blpapi isn't installed we log and skip — /health will surface
    `status=unavailable, bloomberg_connected=false` so the client side
    can detect this without the server crashing.
    """
    mgr = get_manager()
    if BLPAPI_AVAILABLE:
        try:
            mgr.start()
            logger.info("bloomberg session started (state=%s)", mgr.state.value)
        except Exception:
            logger.exception(
                "failed to start bloomberg session at boot — /health will "
                "report unavailable until reconnect succeeds"
            )
    else:
        logger.warning(
            "blpapi not installed; running in mock-execute mode. "
            "/health will report status=unavailable."
        )
    try:
        yield
    finally:
        if BLPAPI_AVAILABLE:
            try:
                mgr.stop()
                logger.info("bloomberg session stopped")
            except Exception:
                logger.exception("error stopping bloomberg session")


app = FastAPI(
    title="Bloomberg Remote Server",
    description="Remote execution server for Bloomberg BLPAPI operations",
    version=__version__,
    lifespan=lifespan,
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
    """Check server health.

    Reflects the live SessionManager state — if Bloomberg Terminal
    drops or bbcomm dies, this flips to ``degraded`` (during reconnect)
    or ``unavailable`` (after reconnect gives up). It does NOT just
    check whether the blpapi module is importable.
    """
    if not BLPAPI_AVAILABLE:
        return HealthResponse(
            status="unavailable",
            bloomberg_connected=False,
            session_state=None,
        )
    mgr = get_manager()
    return HealthResponse(
        status=_HEALTH_STATUS_BY_STATE.get(mgr.state, "unavailable"),
        bloomberg_connected=mgr.is_connected(),
        session_state=mgr.state.value,
        last_reconnect_ts=mgr.last_reconnect_ts,
        reconnect_attempts=mgr.reconnect_attempts,
    )


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


# Coordination channel — cross-machine messaging for paired dev sessions
@app.post("/v1/coord/send", response_model=CoordSendResponse)
async def coord_send(
    payload: CoordSendRequest,
    username: str = Depends(get_current_user),
) -> CoordSendResponse:
    try:
        msg = coord.post(to=payload.to, sender=username, body=payload.body)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(e),
        )
    return CoordSendResponse(ts=msg["ts"])


# Sync (not async) so the long-poll wait runs in FastAPI's threadpool and
# doesn't block the event loop. wait_ms is capped server-side at 30s so a
# misbehaving client can't pin a worker thread forever.
_INBOX_MAX_WAIT_MS = 30000


@app.get("/v1/coord/inbox", response_model=CoordInboxResponse)
def coord_inbox(
    peek: bool = False,
    wait_ms: int = 0,
    username: str = Depends(get_current_user),
) -> CoordInboxResponse:
    wait_ms = max(0, min(wait_ms, _INBOX_MAX_WAIT_MS))
    msgs = coord.drain(username, peek=peek, wait_ms=wait_ms)
    return CoordInboxResponse(
        user=username,
        messages=[CoordMessage(**m) for m in msgs],
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
