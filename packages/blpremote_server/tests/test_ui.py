"""Tests for blpremote_server.ui pure helpers (M9 chunk b).

No Tkinter import — all tests drive the module-scope helpers
directly. Mirrors the shape of mac's blpremote_client.tests.test_ui.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest import mock

import pytest

from blpremote_server.ui import (
    LED_COLOUR,
    build_send_url_command,
    build_start_command,
    build_stop_command,
    count_users,
    create_user_inline,
    detect_bloomberg,
    is_first_run,
    ngrok_authtoken_status,
    parse_health,
    probe_jwt,
    read_ngrok_url,
    read_pairing_code,
    regenerate_jwt_secret,
    reset_install_state,
    set_ngrok_authtoken_inline,
    start_button_state,
)


# 1. LED palette key set fails loud if the doc drifts.
def test_led_palette_keys_match_locked_doc():
    assert set(LED_COLOUR.keys()) == {"unknown", "happy", "transient", "unhappy"}
    # Hex values per docs/M9_UI_PLAN.md adef805. Don't drift these.
    assert LED_COLOUR["unknown"] == "#8b8d91"
    assert LED_COLOUR["happy"] == "#2ea043"
    assert LED_COLOUR["transient"] == "#d29922"
    assert LED_COLOUR["unhappy"] == "#cf222e"


# 2. detect_bloomberg on non-Windows hosts.
def test_detect_bloomberg_non_windows_returns_unknown():
    assert detect_bloomberg(platform="darwin") == ("unknown", "non-Windows host")
    assert detect_bloomberg(platform="linux") == ("unknown", "non-Windows host")


# 3. parse_health for the four shape variants.
class TestParseHealth:
    def test_healthy_connected(self):
        payload = {
            "status": "healthy",
            "bloomberg_connected": True,
            "session_state": "connected",
        }
        assert parse_health(payload, port="8000") == ("happy", "Up · :8000")

    def test_healthy_but_session_degraded(self):
        payload = {
            "status": "degraded",
            "bloomberg_connected": False,
            "session_state": "reconnecting",
        }
        assert parse_health(payload) == ("transient", "Up but session=reconnecting")

    def test_session_state_missing_falls_back_to_unknown(self):
        # Defensive: server returned 200 but no session_state field.
        payload = {"status": "degraded", "bloomberg_connected": False}
        assert parse_health(payload) == ("transient", "Up but session=unknown")

    def test_none_payload_means_probe_failed(self):
        # urlopen raised; probe_server passes None to parse_health.
        assert parse_health(None) == ("unhappy", "Down")

    def test_port_threaded_through(self):
        payload = {"bloomberg_connected": True}
        assert parse_health(payload, port="9001") == ("happy", "Up · :9001")


# 4. probe_jwt covers the four env permutations.
class TestProbeJwt:
    def test_unset(self):
        assert probe_jwt(env={}) == ("unhappy", "Using insecure default")

    def test_sentinel_match(self):
        assert probe_jwt(env={"BLPREMOTE_SECRET_KEY": "change-me-in-production-x"}) == (
            "unhappy",
            "Using insecure default",
        )

    def test_sentinel_with_allow_default_flag(self):
        # M5(A) dev opt-in path — distinct label so the user knows
        # they're in dev-permissive mode, not production-secret-set.
        assert probe_jwt(
            env={
                "BLPREMOTE_SECRET_KEY": "change-me-in-production",
                "BLPREMOTE_ALLOW_DEFAULT_SECRET": "1",
            }
        ) == ("unhappy", "Insecure default (allow=1)")

    def test_real_secret_set(self):
        assert probe_jwt(env={"BLPREMOTE_SECRET_KEY": "actually-random-bytes-here"}) == (
            "happy",
            "Configured",
        )


# 5. read_ngrok_url across file states.
class TestReadNgrokUrl:
    def test_file_absent(self, tmp_path: Path):
        assert read_ngrok_url(tmp_path / "nope.txt") == ("unhappy", "")

    def test_file_clean_utf8(self, tmp_path: Path):
        p = tmp_path / "url.txt"
        p.write_text("https://nest-eligibly-dork.ngrok-free.dev\n", encoding="utf-8")
        state, url = read_ngrok_url(p)
        assert state == "happy"
        assert url == "https://nest-eligibly-dork.ngrok-free.dev"
        assert not url.startswith("﻿")

    def test_file_with_powershell_bom(self, tmp_path: Path):
        # PowerShell 5.1 Out-File -Encoding utf8 prepends a BOM.
        # utf-8-sig must strip it; otherwise the URL displays as
        # "﻿https://..." in the Tk Entry widget.
        p = tmp_path / "url.txt"
        p.write_bytes(
            "﻿https://nest-eligibly-dork.ngrok-free.dev".encode("utf-8")
        )
        state, url = read_ngrok_url(p)
        assert state == "happy"
        assert url == "https://nest-eligibly-dork.ngrok-free.dev"

    def test_file_present_but_empty(self, tmp_path: Path):
        p = tmp_path / "url.txt"
        p.write_text("   \n", encoding="utf-8")
        assert read_ngrok_url(p) == ("unhappy", "")


# 6. start_button_state gating.
class TestStartButtonState:
    def test_disabled_when_bbg_unhappy(self):
        assert start_button_state("unhappy") == "disabled"

    def test_disabled_when_bbg_unknown(self):
        # First-render or non-Windows host — keep disabled until
        # we know BBG is up.
        assert start_button_state("unknown") == "disabled"

    def test_disabled_when_bbg_transient(self):
        # Belt-and-braces; BBG itself doesn't really go transient
        # (process is on/off) but if a future probe surfaces e.g.
        # "Detected but bbcomm down" we shouldn't enable Start.
        assert start_button_state("transient") == "disabled"

    def test_enabled_only_when_bbg_happy(self):
        assert start_button_state("happy") == "normal"


# 7. Command builders — pure argv shaping (M9 chunk c + M10.7 fix).
class TestBuildStartCommand:
    @staticmethod
    def _populate_venv_indicator(repo_root: Path) -> None:
        """Mimic a venv that's been pip install -e blpremote-server'd
        by dropping the entry-point .exe at the path the auto-detect
        helper checks."""
        scripts = repo_root / ".venv" / "Scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        (scripts / "blpremote-server.exe").write_bytes(b"")

    def test_auto_detect_skips_install_when_venv_has_deps(self, tmp_path):
        """Steady-state: venv already populated -> setup.ps1's slow
        pip step is unnecessary on every Start click."""
        self._populate_venv_indicator(tmp_path)
        cmd = build_start_command(tmp_path)
        assert cmd[0] == "powershell.exe"
        assert cmd[cmd.index("-File") + 1] == str(tmp_path / "setup.ps1")
        assert "-Force" in cmd
        assert "-SkipInstall" in cmd  # populated -> can skip
        assert cmd[-2:] == ["-CoordSend", "mac"]

    def test_auto_detect_runs_install_when_venv_missing(self, tmp_path):
        """First-ever launch in a fresh checkout: no .venv at all.
        Auto-detect MUST omit -SkipInstall so step 3 of setup.ps1
        actually populates the venv it just created in step 1.
        Regression guard for the M10.7 bug."""
        cmd = build_start_command(tmp_path)
        assert "-SkipInstall" not in cmd

    def test_auto_detect_runs_install_when_venv_empty(self, tmp_path):
        """Half-finished setup.ps1 left an empty venv (Scripts dir
        exists but blpremote-server.exe missing because pip was
        skipped). Re-Start should trigger install, not skip again."""
        (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
        cmd = build_start_command(tmp_path)
        assert "-SkipInstall" not in cmd

    def test_explicit_skip_install_true_overrides_auto_detect(self, tmp_path):
        """Caller can force-skip even on an empty venv (e.g. for
        scripted flows where they know what they're doing)."""
        cmd = build_start_command(tmp_path, skip_install=True)
        assert "-SkipInstall" in cmd

    def test_explicit_skip_install_false_overrides_auto_detect(self, tmp_path):
        """Caller can force-install even on a populated venv (e.g.
        post-pull when they know deps need refreshing)."""
        self._populate_venv_indicator(tmp_path)
        cmd = build_start_command(tmp_path, skip_install=False)
        assert "-SkipInstall" not in cmd

    def test_no_coord_target_drops_coord_args(self, tmp_path):
        cmd = build_start_command(tmp_path, coord_target=None)
        assert "-CoordSend" not in cmd


class TestVenvHasBlpremoteServer:
    def test_no_venv_at_all(self, tmp_path):
        from blpremote_server.ui import venv_has_blpremote_server

        assert venv_has_blpremote_server(tmp_path) is False

    def test_empty_venv(self, tmp_path):
        from blpremote_server.ui import venv_has_blpremote_server

        (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
        assert venv_has_blpremote_server(tmp_path) is False

    def test_populated_venv(self, tmp_path):
        from blpremote_server.ui import venv_has_blpremote_server

        scripts = tmp_path / ".venv" / "Scripts"
        scripts.mkdir(parents=True)
        (scripts / "blpremote-server.exe").write_bytes(b"")
        assert venv_has_blpremote_server(tmp_path) is True


class TestBuildStopCommand:
    def test_default_targets_port_8000(self):
        cmd = build_stop_command()
        assert cmd[0] == "powershell.exe"
        assert any("LocalPort 8000" in arg for arg in cmd)
        assert any("Get-Process ngrok" in arg for arg in cmd)
        # Sentinel write-host so the user sees "stop: complete" in
        # the logs tail when the kill cycle finishes.
        assert any("stop: complete" in arg for arg in cmd)

    def test_custom_port(self):
        cmd = build_stop_command(port=9001)
        assert any("LocalPort 9001" in arg for arg in cmd)
        assert not any("LocalPort 8000" in arg for arg in cmd)


class TestBuildSendUrlCommand:
    def test_uses_file_to_avoid_shell_quoting(self, tmp_path):
        msg_file = tmp_path / "msg.txt"
        cmd = build_send_url_command(
            venv_python=r"C:\venv\python.exe",
            coord_script=tmp_path / "tools" / "coord.py",
            target="mac",
            message_file=msg_file,
        )
        assert cmd[0] == r"C:\venv\python.exe"
        assert cmd[-3:] == ["mac", "--file", str(msg_file)]
        assert "send" in cmd
        # No raw body in argv — that's the whole point of the file.
        assert not any("server up at" in arg for arg in cmd)


# 8. Settings-dialog helpers (M10 item 5).
class TestReadPairingCode:
    def test_absent_returns_none(self, tmp_path):
        assert read_pairing_code(tmp_path / "nope.txt") is None

    def test_present_returns_stripped(self, tmp_path):
        p = tmp_path / "code.txt"
        p.write_text("eyJ2IjoxLCJ1cmwiOiJ4In0=  \n", encoding="utf-8")
        assert read_pairing_code(p) == "eyJ2IjoxLCJ1cmwiOiJ4In0="

    def test_present_with_powershell_bom(self, tmp_path):
        # setup.ps1 chunk (e) writes via Out-File which leaves a BOM
        # on PS5; same trap as read_ngrok_url. utf-8-sig must strip.
        p = tmp_path / "code.txt"
        p.write_bytes("﻿eyJ2IjoxLCJ1cmwiOiJ4In0=".encode("utf-8"))
        assert read_pairing_code(p) == "eyJ2IjoxLCJ1cmwiOiJ4In0="

    def test_whitespace_only_treated_as_absent(self, tmp_path):
        p = tmp_path / "code.txt"
        p.write_text("   \n", encoding="utf-8")
        assert read_pairing_code(p) is None


class TestRegenerateJwtSecret:
    def test_writes_64_char_secret_and_returns_it(self, tmp_path):
        path = tmp_path / "subdir" / "server_secret.txt"
        new_secret = regenerate_jwt_secret(path)
        # token_urlsafe(48) emits ~64 url-safe chars (no padding).
        assert len(new_secret) >= 60
        assert path.read_text(encoding="utf-8").strip() == new_secret
        # Parent dir created if absent.
        assert path.parent.is_dir()

    def test_idempotent_overwrite(self, tmp_path):
        path = tmp_path / "server_secret.txt"
        first = regenerate_jwt_secret(path)
        second = regenerate_jwt_secret(path)
        # New secret each call -- that's the whole point.
        assert first != second
        # File holds the latest.
        assert path.read_text(encoding="utf-8").strip() == second


class TestNgrokAuthtokenStatus:
    def test_missing_file(self, tmp_path):
        assert ngrok_authtoken_status(tmp_path / "ngrok.yml") == ("unhappy", "Missing")

    def test_present_with_authtoken(self, tmp_path):
        p = tmp_path / "ngrok.yml"
        p.write_text("version: 2\nauthtoken: 12345abcde\n", encoding="utf-8")
        assert ngrok_authtoken_status(p) == ("happy", "Configured")

    def test_present_without_authtoken_is_missing(self, tmp_path):
        # A cfg file can exist (e.g. region: us) without an authtoken.
        p = tmp_path / "ngrok.yml"
        p.write_text("version: 2\nregion: us\n", encoding="utf-8")
        assert ngrok_authtoken_status(p) == ("unhappy", "Missing")

    def test_authtoken_must_be_a_top_level_key(self, tmp_path):
        # Defensive: a tunnels-block child key called authtoken_id
        # (hypothetical) shouldn't trigger a false-positive.
        p = tmp_path / "ngrok.yml"
        p.write_text("tunnels:\n  some_authtoken_id: x\n", encoding="utf-8")
        assert ngrok_authtoken_status(p) == ("unhappy", "Missing")


# 9. M10.5 first-run + reset helpers.
class TestCountUsers:
    def test_missing_file(self, tmp_path):
        assert count_users(tmp_path / "nope.json") == 0

    def test_empty_dict(self, tmp_path):
        p = tmp_path / "users.json"
        p.write_text("{}", encoding="utf-8")
        assert count_users(p) == 0

    def test_one_user(self, tmp_path):
        p = tmp_path / "users.json"
        p.write_text('{"alice": {"username": "alice", "password_hash": "x"}}', encoding="utf-8")
        assert count_users(p) == 1

    def test_unparseable_treated_as_zero(self, tmp_path):
        p = tmp_path / "users.json"
        p.write_text("{ not json", encoding="utf-8")
        assert count_users(p) == 0

    def test_non_dict_treated_as_zero(self, tmp_path):
        # If users.json somehow held a list (data drift), don't crash.
        p = tmp_path / "users.json"
        p.write_text("[]", encoding="utf-8")
        assert count_users(p) == 0


class TestIsFirstRun:
    def _stub_ngrok(self, tmp_path, configured: bool):
        p = tmp_path / "ngrok.yml"
        if configured:
            p.write_text("authtoken: abc\n", encoding="utf-8")
        return p

    def test_all_three_present_returns_false(self, tmp_path):
        secret = tmp_path / "secret.txt"
        secret.write_text("S")
        users = tmp_path / "users.json"
        users.write_text('{"u": {}}')
        ngrok = self._stub_ngrok(tmp_path, configured=True)
        assert is_first_run(secret, users, ngrok) is False

    def test_missing_secret(self, tmp_path):
        users = tmp_path / "users.json"
        users.write_text('{"u": {}}')
        ngrok = self._stub_ngrok(tmp_path, configured=True)
        assert is_first_run(tmp_path / "no-secret.txt", users, ngrok) is True

    def test_no_users(self, tmp_path):
        secret = tmp_path / "secret.txt"
        secret.write_text("S")
        ngrok = self._stub_ngrok(tmp_path, configured=True)
        assert is_first_run(secret, tmp_path / "no-users.json", ngrok) is True

    def test_no_authtoken(self, tmp_path):
        secret = tmp_path / "secret.txt"
        secret.write_text("S")
        users = tmp_path / "users.json"
        users.write_text('{"u": {}}')
        assert is_first_run(secret, users, tmp_path / "no-ngrok.yml") is True


class TestCreateUserInline:
    def test_creates_then_returns_true_then_false_on_dup(self, tmp_path):
        users = tmp_path / "users.json"
        assert create_user_inline(users, "alice", "pw") is True
        # Same name -> create_user_inline returns False per UserStore contract.
        assert create_user_inline(users, "alice", "pw2") is False
        # Different name -> True.
        assert create_user_inline(users, "bob", "pw3") is True

    def test_password_is_bcrypt_hashed_not_plaintext(self, tmp_path):
        # Belt-and-braces: the file must NOT contain the plaintext.
        users = tmp_path / "users.json"
        create_user_inline(users, "alice", "supersecret-12345")
        body = users.read_text(encoding="utf-8")
        assert "supersecret-12345" not in body
        assert "password_hash" in body
        assert body.count("$2b$") >= 1  # bcrypt prefix


class TestSetNgrokAuthtokenInline:
    def test_no_exe_returns_false(self):
        ok, msg = set_ngrok_authtoken_inline(None, "anything")
        assert ok is False
        assert "ngrok.exe not found" in msg

    def test_blank_token_returns_false(self, tmp_path):
        # Pretend we have an exe (path just needs to exist).
        fake_exe = tmp_path / "ngrok.exe"
        fake_exe.write_text("")
        ok, msg = set_ngrok_authtoken_inline(fake_exe, "   ")
        assert ok is False
        assert "empty" in msg.lower()


class TestResetInstallState:
    def test_nothing_to_back_up_returns_none(self, tmp_path):
        # All three target files absent -> no backup dir created.
        assert reset_install_state(
            secret_file=tmp_path / "no-secret.txt",
            users_json_path=tmp_path / "no-users.json",
            pairing_code_file=tmp_path / "no-pair.txt",
            backup_root=tmp_path / "backup-root",
        ) is None
        assert not (tmp_path / "backup-root").exists()

    def test_moves_existing_files_to_timestamped_backup(self, tmp_path):
        secret = tmp_path / "secret.txt"
        secret.write_text("S")
        users = tmp_path / "users.json"
        users.write_text('{"alice": {}}')
        pair = tmp_path / "pair.txt"
        pair.write_text("CODE")
        backup_root = tmp_path / "backup-root"

        backup = reset_install_state(secret, users, pair, backup_root)
        assert backup is not None
        assert backup.parent == backup_root
        assert backup.name.startswith("backup-")
        # Original files gone, backup copies present.
        assert not secret.exists() and (backup / "secret.txt").read_text() == "S"
        assert not users.exists() and (backup / "users.json").read_text() == '{"alice": {}}'
        assert not pair.exists() and (backup / "pair.txt").read_text() == "CODE"

    def test_partial_state_only_backs_up_what_exists(self, tmp_path):
        secret = tmp_path / "secret.txt"
        secret.write_text("S")
        # users + pair absent on purpose.
        backup = reset_install_state(
            secret, tmp_path / "u.json", tmp_path / "p.txt", tmp_path / "br"
        )
        assert backup is not None
        assert (backup / "secret.txt").exists()
        assert not (backup / "u.json").exists()
        assert not (backup / "p.txt").exists()
