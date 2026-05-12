"""Tests for the M9 client UI controller.

Only the :class:`ClientController` is tested — the Tkinter
``build_window`` is glue code that needs an actual display server,
so we leave it to the integration / smoke tier (double-click,
manual verify). The controller is Tkinter-free by design exactly
so this is easy.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from blpremote_client.ui import (
    ClientController,
    LED_COLOUR,
    _format_execute,
    _format_plan,
    decode_pairing_code,
    encode_pairing_code,
)
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


def _sample_plan() -> ExecutionPlan:
    return ExecutionPlan(
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


class TestLedPalette:
    def test_four_states_all_hex(self):
        # The state machine doc (M9_UI_PLAN.md) locks four states.
        # If the palette keys drift, the UI wiring will quietly fall
        # back to whatever colour was last set — fail loudly instead.
        assert set(LED_COLOUR.keys()) == {"happy", "transient", "unhappy", "unknown"}
        for state, colour in LED_COLOUR.items():
            assert colour.startswith("#") and len(colour) == 7, f"{state}={colour}"


class TestOpenRouterKeyProbe:
    def test_env_var_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
        ctrl = ClientController(identity_path=tmp_path / "identity.json")
        ctrl.openrouter_path = tmp_path / "missing.json"
        assert ctrl.has_openrouter_key() is True

    def test_file_present_no_env(self, monkeypatch, tmp_path):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        kf = tmp_path / "openrouter.json"
        kf.write_text(json.dumps({"api_key": "sk-or-real"}))
        ctrl = ClientController(identity_path=tmp_path / "identity.json")
        ctrl.openrouter_path = kf
        assert ctrl.has_openrouter_key() is True

    def test_neither_present(self, monkeypatch, tmp_path):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        ctrl = ClientController(identity_path=tmp_path / "identity.json")
        ctrl.openrouter_path = tmp_path / "nope.json"
        assert ctrl.has_openrouter_key() is False

    def test_corrupt_file_returns_false(self, monkeypatch, tmp_path):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        kf = tmp_path / "openrouter.json"
        kf.write_text("not valid json {{")
        ctrl = ClientController(identity_path=tmp_path / "identity.json")
        ctrl.openrouter_path = kf
        assert ctrl.has_openrouter_key() is False


class TestIdentityProbe:
    def test_reads_username_and_url(self, tmp_path):
        idf = tmp_path / "identity.json"
        idf.write_text(json.dumps({"user": "mac", "url": "https://x/", "password": "secret"}))
        ctrl = ClientController(identity_path=idf)
        out = ctrl.get_identity()
        assert out["username"] == "mac"
        assert out["url"] == "https://x/"
        # password must NOT leak into the readout dict — it would
        # show up on the UI; defence in depth.
        assert "password" not in out

    def test_missing_file_returns_empty(self, tmp_path):
        ctrl = ClientController(identity_path=tmp_path / "absent.json")
        assert ctrl.get_identity() == {"username": "", "url": ""}


class TestPairingCode:
    """Host emits one base64-JSON string; client pastes it once.
    Replaces typing three fields by hand. Round-trip + every failure
    mode must yield None (never raise) so the UI can show a friendly
    error."""

    def test_roundtrip(self):
        code = encode_pairing_code("https://x.example/", "mac", "secret-pw")
        decoded = decode_pairing_code(code)
        assert decoded == {"url": "https://x.example", "user": "mac", "password": "secret-pw"}

    def test_url_trailing_slash_stripped_on_encode(self):
        code = encode_pairing_code("https://x/", "mac", "p")
        assert decode_pairing_code(code)["url"] == "https://x"

    def test_decode_handles_whitespace(self):
        code = encode_pairing_code("https://x", "mac", "p")
        # User might paste with leading/trailing whitespace from clipboard.
        assert decode_pairing_code("   " + code + "\n  ") is not None

    def test_decode_empty_returns_none(self):
        assert decode_pairing_code("") is None
        assert decode_pairing_code("   ") is None

    def test_decode_garbage_returns_none(self):
        # Not base64
        assert decode_pairing_code("not a real code!") is None

    def test_decode_valid_base64_but_not_json_returns_none(self):
        import base64
        bad = base64.urlsafe_b64encode(b"not json").decode("ascii")
        assert decode_pairing_code(bad) is None

    def test_decode_wrong_version_returns_none(self):
        import base64, json
        payload = json.dumps({"v": 99, "url": "x", "user": "u", "pass": "p"}).encode("utf-8")
        code = base64.urlsafe_b64encode(payload).decode("ascii")
        assert decode_pairing_code(code) is None

    def test_decode_missing_field_returns_none(self):
        import base64, json
        payload = json.dumps({"v": 1, "url": "x", "user": "u"}).encode("utf-8")  # no pass
        code = base64.urlsafe_b64encode(payload).decode("ascii")
        assert decode_pairing_code(code) is None

    def test_decode_wrong_type_returns_none(self):
        import base64, json
        payload = json.dumps({"v": 1, "url": "x", "user": 123, "pass": "p"}).encode("utf-8")
        code = base64.urlsafe_b64encode(payload).decode("ascii")
        assert decode_pairing_code(code) is None

    def test_decode_non_string_arg_returns_none(self):
        # Defensive — UI shouldn't pass None but if it does, no crash.
        assert decode_pairing_code(None) is None  # type: ignore[arg-type]


class TestSaveSettings:
    """The Settings dialog's persistence layer. Each kwarg is optional;
    None preserves the existing value so a user editing one field
    doesn't have to re-type the others. Empty string clears."""

    def test_writes_full_identity(self, tmp_path):
        idf = tmp_path / "identity.json"
        ctrl = ClientController(identity_path=idf)
        ctrl.openrouter_path = tmp_path / "openrouter.json"
        ctrl.save_settings(url="https://x/", username="mac", password="secret")
        saved = json.loads(idf.read_text())
        assert saved == {"url": "https://x", "user": "mac", "password": "secret"}

    def test_preserves_unspecified_fields(self, tmp_path):
        idf = tmp_path / "identity.json"
        idf.write_text(json.dumps({"url": "https://old/", "user": "mac", "password": "p"}))
        ctrl = ClientController(identity_path=idf)
        ctrl.openrouter_path = tmp_path / "openrouter.json"
        # Edit only the URL — username + password must survive untouched.
        ctrl.save_settings(url="https://new/")
        saved = json.loads(idf.read_text())
        assert saved["url"] == "https://new"
        assert saved["user"] == "mac"
        assert saved["password"] == "p"

    def test_strips_trailing_slash_on_url(self, tmp_path):
        idf = tmp_path / "identity.json"
        ctrl = ClientController(identity_path=idf)
        ctrl.openrouter_path = tmp_path / "openrouter.json"
        ctrl.save_settings(url="https://x/")
        assert json.loads(idf.read_text())["url"] == "https://x"

    def test_openrouter_key_writes_to_separate_file(self, tmp_path):
        kf = tmp_path / "openrouter.json"
        ctrl = ClientController(identity_path=tmp_path / "identity.json")
        ctrl.openrouter_path = kf
        ctrl.save_settings(openrouter_key="sk-or-real")
        assert json.loads(kf.read_text()) == {"api_key": "sk-or-real"}

    def test_chmods_files_600(self, tmp_path):
        import stat
        idf = tmp_path / "identity.json"
        kf = tmp_path / "openrouter.json"
        ctrl = ClientController(identity_path=idf)
        ctrl.openrouter_path = kf
        ctrl.save_settings(url="https://x/", openrouter_key="sk-or-real")
        # 0o600 == owner read+write only — defense against accidental
        # secret leakage via wide perms.
        assert stat.S_IMODE(idf.stat().st_mode) == 0o600
        assert stat.S_IMODE(kf.stat().st_mode) == 0o600

    def test_no_kwargs_is_noop(self, tmp_path):
        # User opens Settings, clicks Save without changing anything →
        # nothing written, no file touched.
        ctrl = ClientController(identity_path=tmp_path / "identity.json")
        ctrl.openrouter_path = tmp_path / "openrouter.json"
        ctrl.save_settings()  # all defaults None
        assert not ctrl.identity_path.exists()
        assert not ctrl.openrouter_path.exists()


class TestConnect:
    def test_happy_path_persists_url_back(self, tmp_path):
        idf = tmp_path / "identity.json"
        idf.write_text(json.dumps({"user": "mac", "password": "p"}))  # url missing
        ctrl = ClientController(identity_path=idf)

        fake_host = MagicMock()
        fake_host.health.return_value = {"status": "ok"}
        fake_host.version.return_value = "1.2.3"

        with patch("blpremote_client.host.RemoteHost", return_value=fake_host):
            r = ctrl.connect("https://new.example/")

        assert r.ok is True
        assert r.server_version == "1.2.3"
        assert ctrl.is_connected()
        # URL persisted (rstripped) to identity.json for next launch.
        saved = json.loads(idf.read_text())
        assert saved["url"] == "https://new.example"
        # other fields preserved
        assert saved["user"] == "mac"
        assert saved["password"] == "p"

    def test_health_failure_returns_error(self, tmp_path):
        ctrl = ClientController(identity_path=tmp_path / "identity.json")

        fake_host = MagicMock()
        fake_host.health.side_effect = ConnectionError("nope")

        with patch("blpremote_client.host.RemoteHost", return_value=fake_host):
            r = ctrl.connect("https://broken/")

        assert r.ok is False
        assert "ConnectionError" in r.message
        assert not ctrl.is_connected()


class TestDisconnect:
    def test_drops_host_reference(self, tmp_path):
        ctrl = ClientController(identity_path=tmp_path / "identity.json")
        ctrl._host = MagicMock()  # pretend connected
        assert ctrl.is_connected()
        ctrl.disconnect()
        assert not ctrl.is_connected()


class TestAsk:
    def test_returns_error_when_not_connected(self, tmp_path):
        ctrl = ClientController(identity_path=tmp_path / "identity.json")
        r = ctrl.ask("AAPL last price")
        assert r.ok is False
        assert "not connected" in r.message

    def test_passes_through_to_llm_ask(self, tmp_path):
        ctrl = ClientController(identity_path=tmp_path / "identity.json")
        ctrl._host = MagicMock()
        plan = _sample_plan()
        with patch("blpremote_client.llm.ask",
                   return_value={"plan": plan, "explain": "ref data for AAPL"}):
            r = ctrl.ask("AAPL last price")
        assert r.ok is True
        assert r.explain == "ref data for AAPL"
        assert r.plan is plan


class TestExecute:
    def test_returns_error_when_not_connected(self, tmp_path):
        ctrl = ClientController(identity_path=tmp_path / "identity.json")
        r = ctrl.execute(_sample_plan())
        assert r.ok is False
        assert "not connected" in r.message

    def test_ok_status_returns_data(self, tmp_path):
        ctrl = ClientController(identity_path=tmp_path / "identity.json")
        fake_host = MagicMock()
        fake_host.execute.return_value = ExecutionResult(
            request_id="rq-1", status="ok",
            data={"AAPL US Equity": {"PX_LAST": 285.5}},
            server_timing_ms=42,
        )
        ctrl._host = fake_host
        r = ctrl.execute(_sample_plan())
        assert r.ok is True
        assert r.data == {"AAPL US Equity": {"PX_LAST": 285.5}}
        assert r.server_timing_ms == 42

    def test_error_status_marked_not_ok(self, tmp_path):
        ctrl = ClientController(identity_path=tmp_path / "identity.json")
        fake_host = MagicMock()
        fake_host.execute.return_value = ExecutionResult(
            request_id="rq-1", status="error",
            data={},
        )
        ctrl._host = fake_host
        r = ctrl.execute(_sample_plan())
        assert r.ok is False


class TestFormatters:
    def test_plan_formatter_includes_each_op(self):
        text = _format_plan(_sample_plan(), "ref data for AAPL")
        assert "explain: ref data for AAPL" in text
        assert "ops (7):" in text
        assert "start_session" in text
        assert "open_service" in text
        assert "ReferenceDataRequest" in text
        assert "AAPL US Equity" in text
        assert "PX_LAST" in text
        assert "collect_response" in text

    def test_execute_formatter_includes_status_and_data(self):
        from blpremote_client.ui import ExecuteResult
        r = ExecuteResult(
            ok=True, message="status: ok",
            data={"AAPL US Equity": {"PX_LAST": 285.5}},
            warnings=[], server_timing_ms=42,
        )
        text = _format_execute(r)
        assert "status: ok" in text
        assert "server_timing_ms: 42" in text
        assert "PX_LAST" in text
        assert "285.5" in text
