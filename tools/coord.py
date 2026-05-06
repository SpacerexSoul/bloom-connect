#!/usr/bin/env python3
"""Cross-machine coordination CLI for the bloom-connect server.

Both dev sessions (Mac and Windows) use this to send messages and
drain their inbox via the FastAPI server. Pure stdlib, no extra deps.

Usage:
    python tools/coord.py send <to> <body>           # body inline
    python tools/coord.py send <to> --file path.md   # body from file
    cat msg | python tools/coord.py send <to>        # body from stdin
    python tools/coord.py inbox [--peek] [--json]
    python tools/coord.py watch [--interval 5]

Config (env vars or ~/.blpremote/coord.json):
    BLPCOORD_URL    e.g. http://192.168.1.20:8000  or  https://abc.ngrok.app
    BLPCOORD_USER   identity for THIS side ("mac" or "win")
    BLPCOORD_PASS   password (used to mint a token)

Token cached at ~/.blpremote/coord_token.json. Auto-refreshed on 401.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CFG_DIR = Path.home() / ".blpremote"
TOKEN_FILE = CFG_DIR / "coord_token.json"
CONFIG_FILE = CFG_DIR / "coord.json"


def _load_config() -> dict[str, str]:
    cfg: dict[str, str] = {}
    if CONFIG_FILE.exists():
        cfg.update(json.loads(CONFIG_FILE.read_text()))
    for key, env in (("url", "BLPCOORD_URL"), ("user", "BLPCOORD_USER"), ("password", "BLPCOORD_PASS")):
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    missing = [k for k in ("url", "user", "password") if not cfg.get(k)]
    if missing:
        sys.exit(
            "missing config: " + ", ".join(missing) +
            f"\nset env vars BLPCOORD_URL/USER/PASS or write {CONFIG_FILE}"
        )
    cfg["url"] = cfg["url"].rstrip("/")
    return cfg


def _login(cfg: dict[str, str]) -> str:
    body = json.dumps({"username": cfg["user"], "password": cfg["password"]}).encode()
    req = urllib.request.Request(
        f"{cfg['url']}/v1/auth/login",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
    CFG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({
        "token": data["token"],
        "expires_at": time.time() + data["expires_in"] - 60,
        "user": cfg["user"],
        "url": cfg["url"],
    }))
    return data["token"]


def _token(cfg: dict[str, str]) -> str:
    if TOKEN_FILE.exists():
        try:
            d = json.loads(TOKEN_FILE.read_text())
            if (
                d.get("expires_at", 0) > time.time()
                and d.get("user") == cfg["user"]
                and d.get("url") == cfg["url"]
            ):
                return d["token"]
        except (json.JSONDecodeError, OSError):
            pass
    return _login(cfg)


def _request(cfg: dict[str, str], method: str, path: str, body: dict | None = None, retry: bool = True) -> dict:
    url = cfg["url"] + path
    headers = {"Authorization": f"Bearer {_token(cfg)}"}
    data: bytes | None = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 401 and retry:
            TOKEN_FILE.unlink(missing_ok=True)
            return _request(cfg, method, path, body, retry=False)
        sys.exit(f"HTTP {e.code}: {e.read().decode(errors='replace')}")
    except urllib.error.URLError as e:
        sys.exit(f"connection error: {e.reason}")


def _read_body(args: argparse.Namespace) -> str:
    if args.file:
        return sys.stdin.read() if args.file == "-" else Path(args.file).read_text()
    if args.body is not None:
        return args.body
    if not sys.stdin.isatty():
        return sys.stdin.read()
    sys.exit("no body provided (positional, --file, or stdin)")


def cmd_send(args: argparse.Namespace) -> None:
    cfg = _load_config()
    body = _read_body(args)
    if not body.strip():
        sys.exit("body is empty")
    r = _request(cfg, "POST", "/v1/coord/send", {"to": args.to, "body": body})
    print(f"sent to {args.to} at {r['ts']}")


def _print_messages(msgs: list[dict], as_json: bool) -> None:
    if as_json:
        print(json.dumps(msgs, indent=2))
        return
    if not msgs:
        print("(empty)")
        return
    for m in msgs:
        print(f"--- {m['ts']} from {m['sender']} ---")
        print(m["body"].rstrip())
        print()


def cmd_inbox(args: argparse.Namespace) -> None:
    cfg = _load_config()
    qs = "?peek=true" if args.peek else ""
    r = _request(cfg, "GET", f"/v1/coord/inbox{qs}")
    _print_messages(r["messages"], args.json)


def cmd_watch(args: argparse.Namespace) -> None:
    cfg = _load_config()
    print(f"watching inbox for {cfg['user']} every {args.interval}s — ctrl-c to stop", file=sys.stderr)
    try:
        while True:
            r = _request(cfg, "GET", "/v1/coord/inbox")
            if r["messages"]:
                _print_messages(r["messages"], args.json)
                sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


def main() -> None:
    p = argparse.ArgumentParser(prog="coord", description=__doc__.split("\n\n")[0] if __doc__ else None)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("send", help="send a message to the other side")
    s.add_argument("to", help="recipient username (e.g. mac or win)")
    s.add_argument("body", nargs="?", help="message body (omit to read from stdin or --file)")
    s.add_argument("-f", "--file", help="read body from file ('-' for stdin)")
    s.set_defaults(fn=cmd_send)

    i = sub.add_parser("inbox", help="drain pending messages for this user")
    i.add_argument("--peek", action="store_true", help="don't clear after reading")
    i.add_argument("--json", action="store_true", help="raw JSON output")
    i.set_defaults(fn=cmd_inbox)

    w = sub.add_parser("watch", help="poll inbox until interrupted")
    w.add_argument("--interval", type=int, default=5)
    w.add_argument("--json", action="store_true")
    w.set_defaults(fn=cmd_watch)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
