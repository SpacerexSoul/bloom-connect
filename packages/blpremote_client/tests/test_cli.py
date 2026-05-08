"""Tests for ``blpremote-ask`` (blpremote_client.cli).

Mocks the LLM and host so the CLI is exercised purely on its own
flow logic — argparse, plan printing, confirm gate, dry-run, --yes.
"""

from __future__ import annotations

import io
import sys
from typing import Any
from unittest.mock import patch

import pytest

from blpremote_client import cli
from blpremote_client.llm import LLMPlanResponse, _PlanWithoutAuth
from blpremote_client.models import (
    AppendOp,
    AuthToken,
    CollectResponseOp,
    CreateRequestOp,
    ExecutionPlan,
    ExecutionResult,
    OpenServiceOp,
    SendRequestOp,
    StartSessionOp,
)


def _sample_plan_dict() -> dict[str, Any]:
    plan = ExecutionPlan(
        protocol_version="1.1",
        auth=AuthToken(token="t"),
        ops=[
            StartSessionOp(),
            OpenServiceOp(service="//blp/refdata"),
            CreateRequestOp(service="//blp/refdata", request="ReferenceDataRequest", id="r1"),
            AppendOp(id="r1", path="securities", value="AAPL US Equity"),
            AppendOp(id="r1", path="fields", value="PX_LAST"),
            SendRequestOp(id="r1", correlation_id="cid-1"),
            CollectResponseOp(correlation_id="cid-1", timeout_ms=10000),
        ],
    )
    return {"plan": plan, "explain": "ref data for AAPL US Equity, PX_LAST"}


class _FakeHost:
    def __init__(self, status="ok"):
        self.executed = []
        self._status = status

    def execute(self, plan):
        self.executed.append(plan)
        return ExecutionResult(
            request_id="rq-1",
            status=self._status,
            data={"AAPL US Equity": {"PX_LAST": 285.5}},
        )


@pytest.fixture
def patched(monkeypatch):
    """Patch ask() and RemoteHost so the CLI runs offline."""
    host = _FakeHost()
    monkeypatch.setattr(cli, "RemoteHost", lambda *a, **kw: host)

    def fake_ask(host_arg, prompt, *, service="//blp/refdata", model=None, **kw):
        return _sample_plan_dict()

    # Patch the lazy import inside main() — main does
    # `from blpremote_client.llm import ask`, so we patch the module attr.
    import blpremote_client.llm as llm_mod
    monkeypatch.setattr(llm_mod, "ask", fake_ask)
    return host


class TestDryRun:
    def test_dry_run_prints_plan_and_does_not_execute(self, patched, capsys):
        rc = cli.main(["--dry-run", "AAPL last price"])
        assert rc == 0
        assert patched.executed == []
        out = capsys.readouterr().out
        assert "ref data for AAPL" in out
        assert "ReferenceDataRequest" in out
        assert "value=AAPL US Equity" in out


class TestConfirmFlow:
    def test_yes_skips_prompt_and_executes(self, patched, capsys):
        rc = cli.main(["--yes", "AAPL last price"])
        assert rc == 0
        assert len(patched.executed) == 1
        out = capsys.readouterr().out
        assert "status: ok" in out

    def test_user_declines_at_prompt(self, patched, capsys, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *a, **kw: "n")
        rc = cli.main(["AAPL last price"])
        assert rc == 1
        assert patched.executed == []
        assert "aborted" in capsys.readouterr().out

    def test_user_accepts_at_prompt(self, patched, capsys, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda *a, **kw: "y")
        rc = cli.main(["AAPL last price"])
        assert rc == 0
        assert len(patched.executed) == 1


class TestServiceOverride:
    def test_custom_service_passes_through(self, monkeypatch, capsys):
        host = _FakeHost()
        monkeypatch.setattr(cli, "RemoteHost", lambda *a, **kw: host)

        captured = {}
        def fake_ask(host_arg, prompt, *, service="//blp/refdata", model=None, **kw):
            captured["service"] = service
            return _sample_plan_dict()

        import blpremote_client.llm as llm_mod
        monkeypatch.setattr(llm_mod, "ask", fake_ask)

        cli.main(["--service", "//blp/apiflds", "--dry-run", "describe PX_LAST"])
        assert captured["service"] == "//blp/apiflds"


class TestJsonOutput:
    def test_json_flag_emits_full_executionresult(self, patched, capsys):
        cli.main(["--yes", "--json", "AAPL last price"])
        out = capsys.readouterr().out
        # Must include the JSON-shaped fields, not the human pretty form.
        assert '"status": "ok"' in out
        assert '"request_id"' in out
        assert "PX_LAST" in out


class TestErrorPath:
    def test_import_failure_returns_2(self, monkeypatch, capsys):
        # If `from blpremote_client.llm import ask` fails (e.g. anthropic
        # not installed and the lazy import propagates up — though the
        # current llm.py imports cleanly at module level either way, so
        # we simulate the surface by removing the `ask` attr, which
        # makes the from-import in main() raise ImportError).
        host = _FakeHost()
        monkeypatch.setattr(cli, "RemoteHost", lambda *a, **kw: host)

        import blpremote_client.llm as llm_mod
        monkeypatch.delattr(llm_mod, "ask")

        rc = cli.main(["AAPL last price"])
        assert rc == 2
        # CLI prints the ImportError message to stderr.
        assert capsys.readouterr().err.strip()


class TestExecutionFailure:
    def test_error_status_returns_nonzero(self, monkeypatch, capsys):
        host = _FakeHost(status="error")
        monkeypatch.setattr(cli, "RemoteHost", lambda *a, **kw: host)

        def fake_ask(*a, **kw):
            return _sample_plan_dict()
        import blpremote_client.llm as llm_mod
        monkeypatch.setattr(llm_mod, "ask", fake_ask)

        rc = cli.main(["--yes", "AAPL last price"])
        assert rc == 1
