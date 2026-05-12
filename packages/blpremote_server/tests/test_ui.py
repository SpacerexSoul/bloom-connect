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
