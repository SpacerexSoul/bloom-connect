"""Long-lived Bloomberg session manager.

Owns a single ``blpapi.Session`` for the lifetime of the server. The
session runs in async mode: events are delivered to ``_on_event`` from
a blpapi-internal thread and dispatched into per-correlation-id
queues. Status events (SessionTerminated, SessionStartupFailure,
SessionConnectionDown) trigger reconnect with exponential backoff;
services pre-opened at startup are re-opened on every reconnect.

Used by:
  - ``executor.blp_exec`` for request/response (one CID per request)
  - subscription PoC / future SSE streaming for non-request flows
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from enum import Enum
from typing import Any, Iterable, Optional

from blpremote_server.config import settings
from blpremote_server.exceptions import SessionError

logger = logging.getLogger(__name__)

try:
    import blpapi  # type: ignore[import-not-found]

    BLPAPI_AVAILABLE = True
except ImportError:
    BLPAPI_AVAILABLE = False
    blpapi = None  # type: ignore[assignment]


_UNSET: Any = object()


class SessionState(str, Enum):
    UNINITIALIZED = "uninitialized"
    STARTING = "starting"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    RECONNECTING = "reconnecting"
    FAILED = "failed"
    SHUTTING_DOWN = "shutting_down"
    STOPPED = "stopped"


# Names of SESSION_STATUS messages we treat as fatal for the current session.
_LOST_MESSAGE_TYPES = frozenset(
    {"SessionTerminated", "SessionStartupFailure", "SessionConnectionDown"}
)


class SessionManager:
    """Owns the long-lived blpapi.Session and its services."""

    INITIAL_BACKOFF_S = 1.0
    MAX_BACKOFF_S = 60.0
    MAX_RECONNECT_ATTEMPTS = 10

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        services: Optional[Iterable[str]] = None,
        blpapi_module: Any = _UNSET,
    ):
        self._host = host or settings.bloomberg_server_host
        self._port = port or settings.bloomberg_server_port
        self._service_names: set[str] = set(services or [])
        self._services: dict[str, Any] = {}
        self._session: Any = None
        self._state = SessionState.UNINITIALIZED
        self._last_reconnect_ts: Optional[float] = None
        self._reconnect_attempts = 0

        # Queue dispatch: cid (string) -> Queue of (event_type, msg).
        # Subscriptions use the same mechanism.
        self._queues: dict[str, "queue.Queue[tuple[int, Any]]"] = {}
        self._queues_lock = threading.RLock()

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._reconnect_thread: Optional[threading.Thread] = None

        # Allow tests to inject a fake blpapi module (or None to simulate
        # blpapi-not-installed). _UNSET means "use whatever was imported".
        self._blpapi = blpapi if blpapi_module is _UNSET else blpapi_module

    # --- Public API --------------------------------------------------

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def last_reconnect_ts(self) -> Optional[float]:
        return self._last_reconnect_ts

    @property
    def reconnect_attempts(self) -> int:
        return self._reconnect_attempts

    def is_connected(self) -> bool:
        return self._state == SessionState.CONNECTED

    def session(self) -> Any:
        with self._lock:
            if self._session is None:
                raise SessionError("session not started")
            return self._session

    def get_service(self, name: str) -> Any:
        with self._lock:
            if name not in self._services:
                self._open_service_locked(name)
            return self._services[name]

    def register_queue(self, cid: str) -> "queue.Queue[tuple[int, Any]]":
        """Register a queue for the given correlation id and return it.

        The caller is responsible for calling ``unregister_queue(cid)``
        when done. Re-registering an existing cid replaces the queue.
        """
        q: queue.Queue = queue.Queue()
        with self._queues_lock:
            self._queues[cid] = q
        return q

    def unregister_queue(self, cid: str) -> None:
        with self._queues_lock:
            self._queues.pop(cid, None)

    def start(self) -> None:
        """Start the session and open pre-listed services.

        Raises ``SessionError`` on failure. Sets state to CONNECTED on
        success. Idempotent.
        """
        if self._blpapi is None:
            self._state = SessionState.FAILED
            raise SessionError("blpapi not available")

        with self._lock:
            if self._state in (SessionState.CONNECTED, SessionState.STARTING):
                return
            self._state = SessionState.STARTING
            self._stop.clear()
            try:
                self._open_session_locked()
                for svc in list(self._service_names):
                    self._open_service_locked(svc)
            except Exception:
                self._state = SessionState.FAILED
                raise
            self._state = SessionState.CONNECTED
            self._reconnect_attempts = 0

    def stop(self) -> None:
        """Stop the session and tear down all queues."""
        with self._lock:
            if self._state == SessionState.STOPPED:
                return
            self._state = SessionState.SHUTTING_DOWN
            self._stop.set()
            if self._session is not None:
                try:
                    self._session.stop()
                except Exception:
                    logger.exception("error during session.stop()")
                self._session = None
            self._services.clear()
            self._state = SessionState.STOPPED
        with self._queues_lock:
            self._queues.clear()

    # --- Internals ---------------------------------------------------

    def _open_session_locked(self) -> None:
        opts = self._blpapi.SessionOptions()
        opts.setServerHost(self._host)
        opts.setServerPort(self._port)
        # Async mode: events flow into _on_event, dispatched to queues.
        session = self._blpapi.Session(opts, self._on_event)
        if not session.start():
            self._session = None
            raise SessionError(
                f"failed to start blpapi session at {self._host}:{self._port}"
            )
        self._session = session

    def _open_service_locked(self, name: str) -> None:
        if self._session is None:
            raise SessionError("session not started")
        if not self._session.openService(name):
            raise SessionError(f"failed to open service: {name}")
        self._services[name] = self._session.getService(name)
        self._service_names.add(name)

    def _on_event(self, event: Any, _session: Any) -> None:
        """blpapi callback — runs on a blpapi-internal thread."""
        try:
            event_type = event.eventType()
        except Exception:
            logger.exception("event with no eventType()")
            return

        # Status events steer the state machine.
        if event_type in (
            self._blpapi.Event.SESSION_STATUS,
            self._blpapi.Event.SERVICE_STATUS,
        ):
            for msg in event:
                self._handle_status_message(msg)
            # Status events are not dispatched to caller queues.
            return

        # All other events are dispatched by correlation id.
        for msg in event:
            cids = []
            try:
                cids = list(msg.correlationIds())
            except Exception:
                pass
            for cid_obj in cids:
                try:
                    cid = str(cid_obj.value())
                except Exception:
                    continue
                with self._queues_lock:
                    q = self._queues.get(cid)
                if q is not None:
                    q.put((event_type, msg))

    def _handle_status_message(self, msg: Any) -> None:
        try:
            mtype = str(msg.messageType())
        except Exception:
            return
        logger.info("blpapi status: %s", mtype)
        if mtype in _LOST_MESSAGE_TYPES:
            self._handle_session_lost(mtype)

    def _handle_session_lost(self, reason: str) -> None:
        with self._lock:
            if self._state in (
                SessionState.SHUTTING_DOWN,
                SessionState.STOPPED,
                SessionState.RECONNECTING,
            ):
                return
            logger.warning(
                "bloomberg session lost (%s) — entering reconnect loop", reason
            )
            self._state = SessionState.DEGRADED
            self._reconnect_thread = threading.Thread(
                target=self._reconnect_loop,
                daemon=True,
                name="blp-reconnect",
            )
            self._reconnect_thread.start()

    def _reconnect_loop(self) -> None:
        with self._lock:
            self._state = SessionState.RECONNECTING
        backoff = self.INITIAL_BACKOFF_S
        for attempt in range(1, self.MAX_RECONNECT_ATTEMPTS + 1):
            # Wait first so we don't hammer immediately after a failure.
            if self._stop.wait(backoff):
                return
            try:
                with self._lock:
                    if self._session is not None:
                        try:
                            self._session.stop()
                        except Exception:
                            pass
                        self._session = None
                    self._services.clear()
                    self._open_session_locked()
                    for svc in list(self._service_names):
                        self._open_service_locked(svc)
                    self._reconnect_attempts = attempt
                    self._last_reconnect_ts = time.time()
                    self._state = SessionState.CONNECTED
                logger.info(
                    "bloomberg session reconnected on attempt %d", attempt
                )
                return
            except Exception:
                logger.warning(
                    "reconnect attempt %d failed", attempt, exc_info=True
                )
                backoff = min(backoff * 2, self.MAX_BACKOFF_S)

        with self._lock:
            self._state = SessionState.FAILED
        logger.error(
            "bloomberg session reconnect gave up after %d attempts",
            self.MAX_RECONNECT_ATTEMPTS,
        )


# --- Module-level singleton -----------------------------------------

_manager: Optional[SessionManager] = None
_manager_lock = threading.Lock()


def get_manager() -> SessionManager:
    """Return the process-wide SessionManager, creating it on first use."""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = SessionManager(services=settings.allowed_services)
        return _manager


def set_manager(mgr: Optional[SessionManager]) -> None:
    """Override the singleton. Pass ``None`` to clear (tests)."""
    global _manager
    with _manager_lock:
        _manager = mgr
