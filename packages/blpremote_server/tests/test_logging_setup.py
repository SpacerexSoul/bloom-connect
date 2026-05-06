"""Tests for the structured logging configuration."""

from __future__ import annotations

import io
import json
import logging

import pytest

from blpremote_server.logging_setup import JsonFormatter, configure_logging


class TestJsonFormatter:
    def _format(self, record_kwargs: dict) -> dict:
        formatter = JsonFormatter()
        record = logging.LogRecord(**record_kwargs)
        return json.loads(formatter.format(record))

    def test_required_fields_present(self):
        out = self._format(dict(
            name="x", level=logging.INFO, pathname="/p", lineno=1,
            msg="hello %s", args=("world",), exc_info=None,
        ))
        assert "ts" in out
        assert out["ts"].endswith("Z")
        assert out["level"] == "INFO"
        assert out["logger"] == "x"
        assert out["message"] == "hello world"

    def test_extras_land_at_top_level(self):
        rec = logging.LogRecord(
            name="x", level=logging.INFO, pathname="/p", lineno=1,
            msg="m", args=(), exc_info=None,
        )
        rec.user = "mac"
        rec.elapsed_ms = 327
        rec.summary = "ReferenceDataRequest 1sec×1fld"
        out = json.loads(JsonFormatter().format(rec))
        assert out["user"] == "mac"
        assert out["elapsed_ms"] == 327
        assert out["summary"] == "ReferenceDataRequest 1sec×1fld"

    def test_pydantic_model_extras_serialise_via_model_dump(self):
        from blpremote_server.models import ErrorDetail

        rec = logging.LogRecord(
            name="x", level=logging.WARNING, pathname="/p", lineno=1,
            msg="warn", args=(), exc_info=None,
        )
        rec.detail = ErrorDetail(code="X", message="m")
        out = json.loads(JsonFormatter().format(rec))
        assert out["detail"]["code"] == "X"
        assert out["detail"]["message"] == "m"

    def test_exc_info_includes_traceback(self):
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            import sys
            exc_info = sys.exc_info()
            rec = logging.LogRecord(
                name="x", level=logging.ERROR, pathname="/p", lineno=1,
                msg="m", args=(), exc_info=exc_info,
            )
            out = json.loads(JsonFormatter().format(rec))
            assert "exc_info" in out
            assert "RuntimeError: boom" in out["exc_info"]

    def test_output_is_one_line(self):
        rec = logging.LogRecord(
            name="x", level=logging.INFO, pathname="/p", lineno=1,
            msg="multi\nline\nmessage", args=(), exc_info=None,
        )
        formatted = JsonFormatter().format(rec)
        # JSON itself is one physical line; embedded newlines in fields
        # are escaped as \n inside the JSON string.
        assert "\n" not in formatted
        assert json.loads(formatted)["message"] == "multi\nline\nmessage"


class TestConfigureLogging:
    @pytest.fixture(autouse=True)
    def _reset_root(self):
        root = logging.getLogger()
        saved_handlers = list(root.handlers)
        saved_level = root.level
        try:
            yield
        finally:
            for h in list(root.handlers):
                root.removeHandler(h)
            for h in saved_handlers:
                root.addHandler(h)
            root.setLevel(saved_level)

    def test_text_format_uses_human_formatter(self, capsys):
        configure_logging(log_format="text", log_level="INFO")
        logging.getLogger("test").info("hello")
        err = capsys.readouterr().err
        assert "[INFO]" in err
        assert "test:" in err

    def test_json_format_emits_parsable_lines(self, capsys):
        configure_logging(log_format="json", log_level="INFO")
        logging.getLogger("test").info("hello", extra={"user": "mac"})
        line = capsys.readouterr().err.strip().splitlines()[-1]
        out = json.loads(line)
        assert out["message"] == "hello"
        assert out["user"] == "mac"
        assert out["level"] == "INFO"

    def test_idempotent_does_not_double_emit(self, capsys):
        configure_logging(log_format="text")
        configure_logging(log_format="text")
        logging.getLogger("test").info("once")
        err = capsys.readouterr().err
        # Exactly one line per emit, not two.
        assert err.count("once") == 1

    def test_log_level_filters_below_threshold(self, capsys):
        configure_logging(log_format="text", log_level="WARNING")
        log = logging.getLogger("test")
        log.info("should be filtered")
        log.warning("should appear")
        err = capsys.readouterr().err
        assert "should be filtered" not in err
        assert "should appear" in err
