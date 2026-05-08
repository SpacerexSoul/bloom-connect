"""Tests for blpremote_client.llm.ask().

The ``anthropic`` SDK is mocked via the ``_client`` test hook so the
suite has no network dependency and runs without an API key. We
assert: system block layout, cache_control placement, request
parameters, auth-token injection, schema fetch + cache, API-key
resolution chain.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from blpremote_client import llm as llm_module
from blpremote_client.llm import (
    LLMPlanResponse,
    _PlanWithoutAuth,
    _build_system,
    _resolve_api_key,
    _service_schema_block,
    ask,
)
from blpremote_client.models import (
    AppendOp,
    CollectResponseOp,
    CreateRequestOp,
    OpenServiceOp,
    SendRequestOp,
    StartSessionOp,
)


SAMPLE_SCHEMA = {
    "service": "//blp/refdata",
    "operations": [
        {"name": "ReferenceDataRequest", "request_elements": [], "response_elements": []},
        {"name": "HistoricalDataRequest", "request_elements": [], "response_elements": []},
    ],
}


class _FakeHost:
    """Minimal RemoteHost stub. Captures everything ask() touches."""

    def __init__(self, schema=None, schema_etag="etag-1", token="fake-token"):
        self.host = "https://fake.example"
        self._schema = schema if schema is not None else SAMPLE_SCHEMA
        self._etag = schema_etag
        self._token = token
        self.get_schema_calls: list[tuple[str, str | None]] = []
        self.get_token_calls = 0

    def get_schema(self, service, etag=None):
        self.get_schema_calls.append((service, etag))
        return self._schema, self._etag

    def _get_token(self) -> str:
        self.get_token_calls += 1
        return self._token


def _build_fake_client(parsed_output: LLMPlanResponse) -> MagicMock:
    """Anthropic client stub: ``client.messages.parse()`` returns an
    object whose ``.parsed_output`` is the canned ``LLMPlanResponse``."""
    client = MagicMock()
    response = MagicMock()
    response.parsed_output = parsed_output
    client.messages.parse.return_value = response
    return client


def _refdata_plan() -> _PlanWithoutAuth:
    """A plausible LLM-emitted plan for 'AAPL last price'."""
    return _PlanWithoutAuth(
        protocol_version="1.1",
        ops=[
            StartSessionOp(),
            OpenServiceOp(service="//blp/refdata"),
            CreateRequestOp(
                service="//blp/refdata",
                request="ReferenceDataRequest",
                id="r1",
            ),
            AppendOp(id="r1", path="securities", value="AAPL US Equity"),
            AppendOp(id="r1", path="fields", value="PX_LAST"),
            SendRequestOp(id="r1", correlation_id="cid-1"),
            CollectResponseOp(correlation_id="cid-1", timeout_ms=10000),
        ],
    )


class TestBuildSystem:
    def test_two_blocks_safety_first_schema_second(self):
        blocks = _build_system("//blp/refdata", SAMPLE_SCHEMA)
        assert len(blocks) == 2
        assert blocks[0]["type"] == "text"
        # Safety prompt mentions hard rules
        assert "trade" in blocks[0]["text"].lower()
        assert "research" in blocks[0]["text"].lower() or "read-only" in blocks[0]["text"].lower()
        # Safety block is NOT cached on its own — cache fires at the
        # last block (schema), which covers everything before it too.
        assert "cache_control" not in blocks[0]

    def test_schema_block_carries_cache_control(self):
        blocks = _build_system("//blp/refdata", SAMPLE_SCHEMA)
        assert blocks[1]["cache_control"] == {"type": "ephemeral"}
        assert "//blp/refdata" in blocks[1]["text"]

    def test_schema_serialised_with_sort_keys_for_cache_stability(self):
        # Same dict in different insertion orders must produce the
        # same rendered text — otherwise the cache key flips and we
        # never get a hit.
        a = {"service": "//blp/refdata", "operations": [{"name": "A"}]}
        b = {"operations": [{"name": "A"}], "service": "//blp/refdata"}
        assert _service_schema_block("//blp/refdata", a) == _service_schema_block("//blp/refdata", b)

    def test_schema_payload_renders_inside_block_text(self):
        block_text = _service_schema_block("//blp/apiflds", {"x": 1})
        assert "//blp/apiflds" in block_text
        assert '"x": 1' in block_text


class TestAskHappyPath:
    def test_returns_full_plan_with_auth_injected(self):
        host = _FakeHost()
        fake = _build_fake_client(LLMPlanResponse(plan=_refdata_plan(), explain="ref data for AAPL"))

        out = ask(host, "AAPL last price", _client=fake)

        # Auth token came from host, not the LLM.
        assert out["plan"].auth.token == "fake-token"
        assert host.get_token_calls == 1
        # The plan's IR is intact.
        assert out["plan"].ops[0].op == "start_session"
        assert any(
            getattr(op, "request", None) == "ReferenceDataRequest"
            for op in out["plan"].ops
        )
        # The explain string is passed through.
        assert out["explain"] == "ref data for AAPL"

    def test_schema_is_fetched_from_host_for_default_service(self):
        host = _FakeHost()
        fake = _build_fake_client(LLMPlanResponse(plan=_refdata_plan(), explain="…"))

        ask(host, "AAPL last price", _client=fake)

        assert host.get_schema_calls == [("//blp/refdata", None)]

    def test_custom_service_is_passed_through(self):
        host = _FakeHost()
        fake = _build_fake_client(LLMPlanResponse(plan=_refdata_plan(), explain="…"))

        ask(host, "describe PX_LAST", _client=fake, service="//blp/apiflds")

        assert host.get_schema_calls == [("//blp/apiflds", None)]

    def test_anthropic_request_args_match_design(self):
        host = _FakeHost()
        fake = _build_fake_client(LLMPlanResponse(plan=_refdata_plan(), explain="…"))

        ask(host, "AAPL last price", _client=fake)

        fake.messages.parse.assert_called_once()
        kwargs = fake.messages.parse.call_args.kwargs
        # Default model + deterministic temperature
        assert kwargs["model"] == "claude-haiku-4-5"
        assert kwargs["temperature"] == 0.0
        assert kwargs["max_tokens"] == 2048
        # The prompt is a single user turn, no prefill or assistant
        # message — structured outputs handles JSON shape.
        assert kwargs["messages"] == [{"role": "user", "content": "AAPL last price"}]
        # System block carries cache_control on the schema half.
        assert isinstance(kwargs["system"], list)
        assert kwargs["system"][1].get("cache_control") == {"type": "ephemeral"}
        # output_format is the wrapper Pydantic model.
        assert kwargs["output_format"] is LLMPlanResponse

    def test_caller_can_override_model_and_temperature(self):
        host = _FakeHost()
        fake = _build_fake_client(LLMPlanResponse(plan=_refdata_plan(), explain="…"))

        ask(host, "hard prompt", _client=fake,
            model="claude-sonnet-4-6", temperature=0.3)

        kwargs = fake.messages.parse.call_args.kwargs
        assert kwargs["model"] == "claude-sonnet-4-6"
        assert kwargs["temperature"] == 0.3


class TestAuthIsolation:
    def test_llm_plan_shape_has_no_auth_field(self):
        # _PlanWithoutAuth must NOT carry auth — that's the security
        # invariant. If someone adds an auth field to it later, this
        # test fails loudly.
        assert "auth" not in _PlanWithoutAuth.model_fields

    def test_full_plan_carries_caller_token_not_a_placeholder(self):
        host = _FakeHost(token="real-caller-token")
        # LLM tries to emit a plan with a placeholder token in extra
        # fields — _PlanWithoutAuth strips it via Pydantic field set.
        fake = _build_fake_client(LLMPlanResponse(plan=_refdata_plan(), explain="…"))

        out = ask(host, "AAPL last price", _client=fake)

        assert out["plan"].auth.token == "real-caller-token"


class TestApiKeyResolution:
    def test_explicit_kwarg_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        cred = tmp_path / ".blpremote" / "anthropic.json"
        cred.parent.mkdir(parents=True)
        cred.write_text(json.dumps({"api_key": "from-file"}))

        assert _resolve_api_key("from-kwarg") == "from-kwarg"

    def test_env_wins_over_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        cred = tmp_path / ".blpremote" / "anthropic.json"
        cred.parent.mkdir(parents=True)
        cred.write_text(json.dumps({"api_key": "from-file"}))

        assert _resolve_api_key(None) == "from-env"

    def test_file_used_when_no_env(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        cred = tmp_path / ".blpremote" / "anthropic.json"
        cred.parent.mkdir(parents=True)
        cred.write_text(json.dumps({"api_key": "from-file"}))

        assert _resolve_api_key(None) == "from-file"

    def test_corrupt_json_falls_through_to_error(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        cred = tmp_path / ".blpremote" / "anthropic.json"
        cred.parent.mkdir(parents=True)
        cred.write_text("not valid json {{")

        with pytest.raises(RuntimeError, match="No Anthropic API key"):
            _resolve_api_key(None)

    def test_missing_required_field_in_file_falls_through(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        cred = tmp_path / ".blpremote" / "anthropic.json"
        cred.parent.mkdir(parents=True)
        cred.write_text(json.dumps({"not_the_key": "..."}))

        with pytest.raises(RuntimeError, match="No Anthropic API key"):
            _resolve_api_key(None)

    def test_no_source_raises_with_helpful_message(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        with pytest.raises(RuntimeError) as exc:
            _resolve_api_key(None)
        msg = str(exc.value)
        assert "ANTHROPIC_API_KEY" in msg
        assert "anthropic.json" in msg
        assert "api_key=" in msg


class TestImportError:
    def test_missing_anthropic_raises_with_install_hint(self, monkeypatch):
        host = _FakeHost()

        # Force the lazy import inside ask() to fail.
        import sys

        original_anthropic = sys.modules.pop("anthropic", None)
        monkeypatch.setattr(
            "builtins.__import__",
            _patched_import("anthropic", original=__import__),
        )
        try:
            with pytest.raises(ImportError, match="pip install anthropic"):
                ask(host, "anything")
        finally:
            if original_anthropic is not None:
                sys.modules["anthropic"] = original_anthropic


def _patched_import(blocked: str, *, original):
    def _imp(name, *args, **kwargs):
        if name == blocked:
            raise ImportError(f"{name} blocked for test")
        return original(name, *args, **kwargs)
    return _imp
