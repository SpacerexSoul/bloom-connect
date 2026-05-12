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
    detect_bloomberg,
    parse_health,
    probe_jwt,
    read_ngrok_url,
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


# 7. Command builders — pure argv shaping (chunk c).
class TestBuildStartCommand:
    def test_default_includes_force_skipinstall_and_coordsend(self, tmp_path):
        cmd = build_start_command(tmp_path)
        assert cmd[0] == "powershell.exe"
        assert "-NoProfile" in cmd
        assert "-File" in cmd
        # The setup.ps1 path is passed as the next arg after -File.
        assert cmd[cmd.index("-File") + 1] == str(tmp_path / "setup.ps1")
        assert "-Force" in cmd
        assert "-SkipInstall" in cmd
        assert cmd[-2:] == ["-CoordSend", "mac"]

    def test_skip_install_false_drops_the_flag(self, tmp_path):
        cmd = build_start_command(tmp_path, skip_install=False)
        assert "-SkipInstall" not in cmd

    def test_no_coord_target_drops_coord_args(self, tmp_path):
        cmd = build_start_command(tmp_path, coord_target=None)
        assert "-CoordSend" not in cmd


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
