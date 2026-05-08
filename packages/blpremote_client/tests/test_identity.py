"""Tests for the M5(C) shared identity loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blpremote_client import identity as identity_module
from blpremote_client.identity import (
    Identity,
    REQUIRED_KEYS,
    load_identity,
    resolve_identity,
    write_identity,
)


@pytest.fixture
def tmp_blpremote(tmp_path, monkeypatch):
    """Redirect ~/.blpremote/* to a tmp dir so we don't poison the
    real one. Patches the module-level path constants in place so
    every loader call sees the redirect."""
    config_dir = tmp_path / ".blpremote"
    config_dir.mkdir()
    monkeypatch.setattr(identity_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(
        identity_module, "IDENTITY_FILE", config_dir / "identity.json",
    )
    monkeypatch.setattr(
        identity_module, "LEGACY_COORD_FILE", config_dir / "coord.json",
    )
    return config_dir


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for env in ("BLPCOORD_URL", "BLPCOORD_USER", "BLPCOORD_PASS"):
        monkeypatch.delenv(env, raising=False)


SAMPLE: Identity = Identity(
    url="https://nest-eligibly-dork.ngrok-free.dev",
    user="mac",
    password="some-password",
)


class TestLoadIdentity:
    def test_returns_none_when_no_files(self, tmp_blpremote):
        assert load_identity() is None

    def test_loads_identity_json(self, tmp_blpremote):
        (tmp_blpremote / "identity.json").write_text(json.dumps(SAMPLE))
        loaded = load_identity()
        assert loaded == SAMPLE

    def test_falls_back_to_coord_json(self, tmp_blpremote):
        (tmp_blpremote / "coord.json").write_text(json.dumps(SAMPLE))
        loaded = load_identity()
        assert loaded == SAMPLE

    def test_identity_takes_precedence_over_coord(self, tmp_blpremote):
        identity_creds = {**SAMPLE, "user": "from-identity"}
        coord_creds = {**SAMPLE, "user": "from-coord"}
        (tmp_blpremote / "identity.json").write_text(json.dumps(identity_creds))
        (tmp_blpremote / "coord.json").write_text(json.dumps(coord_creds))
        loaded = load_identity()
        assert loaded["user"] == "from-identity"

    def test_strips_trailing_slash_from_url(self, tmp_blpremote):
        creds = {**SAMPLE, "url": SAMPLE["url"] + "/"}
        (tmp_blpremote / "identity.json").write_text(json.dumps(creds))
        loaded = load_identity()
        assert loaded["url"] == SAMPLE["url"]  # no trailing slash

    def test_missing_required_field_returns_none(self, tmp_blpremote):
        partial = {"url": SAMPLE["url"], "user": SAMPLE["user"]}  # no password
        (tmp_blpremote / "identity.json").write_text(json.dumps(partial))
        assert load_identity() is None

    def test_corrupt_json_falls_through_to_legacy(self, tmp_blpremote):
        (tmp_blpremote / "identity.json").write_text("{not valid json")
        (tmp_blpremote / "coord.json").write_text(json.dumps(SAMPLE))
        loaded = load_identity()
        assert loaded == SAMPLE

    def test_explicit_path_overrides_default_search(self, tmp_blpremote, tmp_path):
        custom = tmp_path / "custom-identity.json"
        custom.write_text(json.dumps({**SAMPLE, "user": "from-custom"}))
        (tmp_blpremote / "identity.json").write_text(
            json.dumps({**SAMPLE, "user": "from-default"}),
        )
        loaded = load_identity(path=custom)
        assert loaded["user"] == "from-custom"

    def test_non_dict_json_returns_none(self, tmp_blpremote):
        (tmp_blpremote / "identity.json").write_text(json.dumps([1, 2, 3]))
        assert load_identity() is None


class TestWriteIdentity:
    def test_creates_file_and_directory(self, tmp_blpremote):
        # Remove the dir to test parent.mkdir.
        for child in tmp_blpremote.iterdir():
            child.unlink()
        tmp_blpremote.rmdir()
        write_identity(SAMPLE)
        assert (tmp_blpremote / "identity.json").exists()
        loaded = load_identity()
        assert loaded == SAMPLE

    def test_explicit_path(self, tmp_path):
        target = tmp_path / "alt-identity.json"
        write_identity(SAMPLE, path=target)
        assert target.exists()
        assert load_identity(path=target) == SAMPLE

    def test_chmod_restricts_to_owner_on_posix(self, tmp_blpremote):
        import stat as _stat
        import sys

        if sys.platform == "win32":
            pytest.skip("Windows ACLs make chmod a no-op; not a meaningful check")
        write_identity(SAMPLE)
        mode = (tmp_blpremote / "identity.json").stat().st_mode & 0o777
        # 0o600 expected. Belt-and-braces: world+group must not have any bits.
        assert mode & (_stat.S_IRWXG | _stat.S_IRWXO) == 0


class TestResolveIdentity:
    def test_kwargs_win_over_env_and_file(self, tmp_blpremote, monkeypatch):
        (tmp_blpremote / "identity.json").write_text(json.dumps({
            **SAMPLE, "user": "from-file",
        }))
        monkeypatch.setenv("BLPCOORD_USER", "from-env")
        resolved = resolve_identity(user="from-kwarg")
        assert resolved["user"] == "from-kwarg"

    def test_env_wins_over_file_when_no_kwarg(self, tmp_blpremote, monkeypatch):
        (tmp_blpremote / "identity.json").write_text(json.dumps({
            **SAMPLE, "user": "from-file",
        }))
        monkeypatch.setenv("BLPCOORD_USER", "from-env")
        resolved = resolve_identity()
        assert resolved["user"] == "from-env"

    def test_file_wins_when_no_env_no_kwarg(self, tmp_blpremote):
        (tmp_blpremote / "identity.json").write_text(json.dumps({
            **SAMPLE, "user": "from-file",
        }))
        resolved = resolve_identity()
        assert resolved["user"] == "from-file"

    def test_returns_none_when_no_complete_source(self, tmp_blpremote, monkeypatch):
        # Only env user — missing url + password from any source.
        monkeypatch.setenv("BLPCOORD_USER", "alone")
        assert resolve_identity() is None

    def test_partial_kwargs_layer_over_file(self, tmp_blpremote):
        (tmp_blpremote / "identity.json").write_text(json.dumps(SAMPLE))
        # Override just the password — url + user come from file.
        resolved = resolve_identity(password="explicit-pass")
        assert resolved["url"] == SAMPLE["url"]
        assert resolved["user"] == SAMPLE["user"]
        assert resolved["password"] == "explicit-pass"

    def test_kwarg_url_strips_trailing_slash(self, tmp_blpremote):
        (tmp_blpremote / "identity.json").write_text(json.dumps(SAMPLE))
        resolved = resolve_identity(url="https://other.example/")
        assert resolved["url"] == "https://other.example"

    def test_env_url_strips_trailing_slash(self, tmp_blpremote, monkeypatch):
        (tmp_blpremote / "identity.json").write_text(json.dumps(SAMPLE))
        monkeypatch.setenv("BLPCOORD_URL", "https://other.example/")
        resolved = resolve_identity()
        assert resolved["url"] == "https://other.example"


class TestRemoteHostIntegration:
    def test_no_args_loads_from_identity_file(self, tmp_blpremote):
        from blpremote_client import RemoteHost

        (tmp_blpremote / "identity.json").write_text(json.dumps(SAMPLE))
        host = RemoteHost()
        assert host.host == SAMPLE["url"]
        assert host.username == SAMPLE["user"]
        assert host.password == SAMPLE["password"]

    def test_explicit_args_override_identity(self, tmp_blpremote):
        from blpremote_client import RemoteHost

        (tmp_blpremote / "identity.json").write_text(json.dumps({
            **SAMPLE, "user": "from-file",
        }))
        host = RemoteHost(host="https://other.example", username="from-arg", password="p")
        assert host.host == "https://other.example"
        assert host.username == "from-arg"
        assert host.password == "p"

    def test_no_identity_no_args_raises(self, tmp_blpremote):
        from blpremote_client import RemoteHost
        from blpremote_client.exceptions import AuthenticationError

        with pytest.raises(AuthenticationError, match="no host provided"):
            RemoteHost()
