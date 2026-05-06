"""Structured logging configuration.

Configures stdlib ``logging`` with either a human-friendly text formatter
(dev) or a JSON formatter (prod / log-shipper-friendly). Selection is via
``settings.log_format`` (env: ``BLPREMOTE_LOG_FORMAT``).

Both formatters preserve any extras passed via ``logger.info(..., extra={...})``,
so structured fields flow through to JSON output without each call site
having to know the wire format.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

# Standard LogRecord attributes — anything else on the record is treated
# as a structured ``extra`` and serialised. Keeping this list explicit
# rather than asking the formatter to "diff against vars(LogRecord())"
# means we deterministically route a known set of fields.
_STD_RECORD_FIELDS = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname",
        "filename", "module", "exc_info", "exc_text", "stack_info",
        "lineno", "funcName", "created", "msecs", "relativeCreated",
        "thread", "threadName", "processName", "process", "message",
        "asctime", "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """Render each LogRecord as a single-line JSON object.

    Required fields: ``ts`` (UTC ISO-8601 with Z), ``level``,
    ``logger``, ``message``. All other ``extra`` fields land at the
    top level. Exception info, when present, lands as ``exc_info``
    (already-formatted multi-line traceback).
    """

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        payload: dict[str, Any] = {
            "ts": (
                datetime.fromtimestamp(record.created, tz=timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Pick up any extras the caller passed via logger.info(..., extra={...}).
        for key, value in record.__dict__.items():
            if key in _STD_RECORD_FIELDS or key.startswith("_"):
                continue
            payload[key] = _safe_json_value(value)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def _safe_json_value(value: Any) -> Any:
    """Best-effort conversion to a JSON-serialisable value.

    ``json.dumps(default=str)`` already handles most odd types. This
    helper catches a few common cases that would otherwise serialise
    as their ``repr()`` — primarily Pydantic models, which carry
    structured data we want to preserve.
    """
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except Exception:
            return str(value)
    return value


def configure_logging(*, log_format: str = "text", log_level: str = "INFO") -> None:
    """Initialise root-logger handlers idempotently.

    Tearing down any pre-existing handlers means re-running this
    function (e.g. across pytest sessions) doesn't duplicate output.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(stream=sys.stderr)
    if log_format.lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
    root.addHandler(handler)
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))
