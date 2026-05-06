"""FastAPI application for Bloomberg remote execution."""

import logging
import os
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
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
from blpremote_server.schema_cache import (
    SchemaCache,
    get_schema_cache,
    set_schema_cache,
)
from blpremote_server.session_manager import (
    BLPAPI_AVAILABLE,
    SessionState,
    get_manager,
)
from blpremote_server.sse import stream_subscription

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
    # M4: configure structured logging once per process. Uvicorn's
    # default handlers stay; we just take over the root logger so
    # extras flow through to JSON when configured.
    from blpremote_server.logging_setup import configure_logging
    configure_logging(
        log_format=settings.log_format,
        log_level=settings.log_level,
    )
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
        # Wire up SchemaCache once the session is alive (or attempted).
        cache = SchemaCache(mgr)
        set_schema_cache(cache)
        if os.environ.get("BLPREMOTE_SCHEMA_PREWARM") == "1" and mgr.is_connected():
            for svc_name in settings.allowed_services:
                try:
                    cache.warm(svc_name)
                except Exception:
                    logger.warning(
                        "schema prewarm failed for %s", svc_name, exc_info=True
                    )
    else:
        logger.warning(
            "blpapi not installed; running in mock-execute mode. "
            "/health will report status=unavailable."
        )
    try:
        yield
    finally:
        set_schema_cache(None)
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


# M4 (B): per-request metrics middleware. Times every request and
# records the outcome by endpoint + status. Long-lived /v1/subscribe
# connections only land in metrics at disconnect (one observation per
# connection); the per-frame events are out-of-scope for this counter
# the same way they're out-of-scope for the audit log.
@app.middleware("http")
async def _metrics_middleware(request: Request, call_next):
    import time as _time
    from blpremote_server.metrics import requests_total, request_duration

    started = _time.monotonic()
    status_label = "error"
    response = None
    try:
        response = await call_next(request)
        status_label = (
            "ok" if 200 <= response.status_code < 400
            else "client_error" if 400 <= response.status_code < 500
            else "server_error"
        )
        return response
    finally:
        elapsed = _time.monotonic() - started
        # Use the matched ROUTE TEMPLATE, not the captured path —
        # otherwise /v1/schema/{service:path} would create a fresh
        # series per service hit, blowing up Prometheus cardinality
        # the moment we open the allowlist. Per-service breakdown
        # already lives in schema_cache_hits/misses_total. Falls back
        # to the raw URL path on 404s where no route matched.
        route = request.scope.get("route")
        endpoint = getattr(route, "path", None) or request.url.path
        try:
            request_duration.observe(elapsed, endpoint=endpoint)
            requests_total.inc(endpoint=endpoint, status=status_label)
        except Exception:
            logger.exception("metrics middleware bookkeeping failed")

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


# M4 (B): Prometheus-format metrics. Bearer-auth required by default
# so a server reachable over ngrok doesn't leak counters to the
# internet at large; set BLPREMOTE_METRICS_REQUIRE_AUTH=false (a
# future settings flag) for a private network deployment.
@app.get("/metrics")
async def metrics(
    username: str = Depends(get_current_user),
):
    from blpremote_server.metrics import render_exposition

    return Response(
        content=render_exposition(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


# Schema introspection endpoint — bearer-auth, ETag/304, served from the
# in-process SchemaCache rather than firing an IR plan. Per M2 contract
# §5.1, this path is excluded from the M4 audit log because it is a
# read-only metadata lookup, not a business operation.
@app.get("/v1/schema/{service:path}", response_model=None)
async def get_schema(
    service: str,
    response: Response,
    if_none_match: Optional[str] = Header(default=None, alias="If-None-Match"),
    username: str = Depends(get_current_user),
):
    """Return cached schema for a Bloomberg service.

    The path captures everything after ``/v1/schema/``. Clients should
    URL-encode service names containing slashes (e.g.
    ``//blp/refdata`` -> ``%2F%2Fblp%2Frefdata``) so the captured
    value preserves both leading slashes.
    """
    if not BLPAPI_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="blpapi not available on this server",
        )
    # FastAPI's path converter strips a single leading slash; restore
    # it so '//blp/refdata' arrives intact even if the client only
    # encoded one of the slashes.
    if not service.startswith("//"):
        service = "/" + service if service.startswith("/") else "//" + service
    if service not in settings.allowed_services:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Service '{service}' is not allowed. "
            f"Allowed: {settings.allowed_services}",
        )
    cache = get_schema_cache()
    if cache is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="schema cache not initialised",
        )
    try:
        data, etag = cache.get_or_warm(service)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"failed to introspect schema: {e}",
        )
    if if_none_match == etag:
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED,
            headers={"ETag": etag},
        )
    response.headers["ETag"] = etag
    return data


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
    import time as _time
    from blpremote_server.audit import audit_execute

    request_started = _time.time()
    try:
        # Validate the plan
        validate_plan(plan)

        # Execute the plan
        result = execute_plan(plan)
        elapsed_ms = int((_time.time() - request_started) * 1000)
        try:
            audit_execute(plan=plan, result=result, user=username, elapsed_ms=elapsed_ms)
        except Exception:
            logger.exception("audit_execute failed (logging only — request still served)")
        return result

    except PlanValidationError as e:
        result = ExecutionResult(
            request_id=plan.request_id,
            status="error",
            data={},
            errors=[ErrorDetail(code=e.code, message=e.message)],
        )
        try:
            audit_execute(
                plan=plan, result=result, user=username,
                elapsed_ms=int((_time.time() - request_started) * 1000),
            )
        except Exception:
            logger.exception("audit_execute failed (validation path)")
        return result
    except Exception as e:
        result = ExecutionResult(
            request_id=plan.request_id,
            status="error",
            data={},
            errors=[ErrorDetail(code="EXECUTION_FAILED", message=str(e))],
        )
        try:
            audit_execute(
                plan=plan, result=result, user=username,
                elapsed_ms=int((_time.time() - request_started) * 1000),
            )
        except Exception:
            logger.exception("audit_execute failed (exception path)")
        return result


# SSE streaming subscription endpoint (M3).
@app.get("/v1/subscribe")
async def subscribe(
    topic: str = Query(..., description="Bloomberg security e.g. 'AAPL US Equity'"),
    fields: str = Query(..., description="Comma-separated field list e.g. 'LAST_PRICE,BID,ASK'"),
    options: str = Query("", description="Optional blpapi subscription options string"),
    username: str = Depends(get_current_user),
):
    """Stream live Bloomberg subscription events as Server-Sent Events.

    One subscription per connection. Disconnecting (client closes the
    stream) unsubscribes cleanly. Heartbeat ``ping`` events fire every
    ~25s during silent periods to keep proxies from idling-out the
    connection.
    """
    if not BLPAPI_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="blpapi not available on this server",
        )
    field_list = [f.strip() for f in fields.split(",") if f.strip()]
    if not field_list:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="fields query param must contain at least one field",
        )
    mgr = get_manager()
    return StreamingResponse(
        stream_subscription(mgr, topic=topic, fields=field_list, options=options),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx response buffering
        },
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
