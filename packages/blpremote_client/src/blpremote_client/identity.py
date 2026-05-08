"""Shared identity loader for blpremote client surfaces.

A single ``~/.blpremote/identity.json`` file holds the (url, user,
password) tuple consumed by both ``tools/coord.py`` and
``RemoteHost``. Yesterday's password-desync glitch came from each
surface keeping its own credential store; consolidating closes that
class of bug.

Schema::

    {"url": "https://nest-eligibly-dork.ngrok-free.dev",
     "user": "mac",
     "password": "<server-side-bcrypt-source-string>"}

Backwards compatibility: if ``identity.json`` is absent but the
older ``coord.json`` is present, ``load_identity()`` returns the
older file's contents. Migration is automatic on first
``write_identity()`` call — no operator action required.

Discovery order (highest priority first):

1. Explicit path passed to ``load_identity(path=...)``.
2. ``~/.blpremote/identity.json``.
3. ``~/.blpremote/coord.json`` (legacy).
4. ``None`` — caller falls back to its own defaults / explicit args.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Optional, TypedDict


CONFIG_DIR = Path.home() / ".blpremote"
IDENTITY_FILE = CONFIG_DIR / "identity.json"
LEGACY_COORD_FILE = CONFIG_DIR / "coord.json"

REQUIRED_KEYS: tuple[str, ...] = ("url", "user", "password")


class Identity(TypedDict):
    url: str
    user: str
    password: str


def load_identity(path: Optional[Path] = None) -> Optional[Identity]:
    """Return the (url, user, password) trio from identity.json,
    falling back to coord.json. Returns ``None`` if no identity
    file exists or the loaded JSON is missing required fields.
    """
    candidates: list[Path]
    if path is not None:
        candidates = [path]
    else:
        candidates = [IDENTITY_FILE, LEGACY_COORD_FILE]
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        if not all(k in data and isinstance(data[k], str) for k in REQUIRED_KEYS):
            continue
        # Strip a trailing slash from url for canonical comparison.
        return Identity(
            url=data["url"].rstrip("/"),
            user=data["user"],
            password=data["password"],
        )
    return None


def write_identity(identity: Identity, *, path: Optional[Path] = None) -> Path:
    """Persist an identity. Creates ``~/.blpremote/`` if needed and
    chmods the file to 0600 on POSIX. Returns the actual path written.
    """
    target = path if path is not None else IDENTITY_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dict(identity), indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Windows or otherwise restricted FS — best-effort.
        pass
    return target


def env_overrides() -> dict[str, str]:
    """Read BLPCOORD_URL / BLPCOORD_USER / BLPCOORD_PASS env vars,
    if set. Returns a dict containing whichever fields were set —
    not necessarily a complete identity. Caller layers these on
    top of the file-loaded identity (env wins).
    """
    out: dict[str, str] = {}
    mapping = (
        ("url", "BLPCOORD_URL"),
        ("user", "BLPCOORD_USER"),
        ("password", "BLPCOORD_PASS"),
    )
    for key, env in mapping:
        val = os.environ.get(env)
        if val:
            out[key] = val.rstrip("/") if key == "url" else val
    return out


def resolve_identity(
    *,
    url: Optional[str] = None,
    user: Optional[str] = None,
    password: Optional[str] = None,
    path: Optional[Path] = None,
) -> Optional[Identity]:
    """Resolution order, highest priority first:

    1. Explicit kwargs (any non-None field overrides every other source).
    2. ``BLPCOORD_*`` env vars.
    3. The identity file (identity.json then coord.json).

    If after merging all three the result has every required field,
    a complete ``Identity`` is returned. Otherwise ``None``, and the
    caller decides how to error out.
    """
    file_id = load_identity(path=path)
    env = env_overrides()
    merged: dict[str, str] = {}
    if file_id is not None:
        merged.update(file_id)
    merged.update(env)
    explicit = {"url": url, "user": user, "password": password}
    for k, v in explicit.items():
        if v is not None:
            merged[k] = v.rstrip("/") if k == "url" else v
    if all(k in merged and merged[k] for k in REQUIRED_KEYS):
        return Identity(
            url=merged["url"],
            user=merged["user"],
            password=merged["password"],
        )
    return None
