"""Bloomberg Remote — Server (Windows desktop UI).

Controller + pure helpers for the M9 Tkinter UI. Mirrors mac's
``blpremote_client.ui`` shape: pure probe helpers at module scope so
tests can drive them without importing tkinter, and a
``ServerUIController`` class that owns the window + threads.

Entry point: ``tools/blpremote-server-ui.py`` (~5-line wrapper).

See docs/M9_UI_PLAN.md for the locked widget tree + LED palette.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional


# --- LED palette (locked in docs/M9_UI_PLAN.md adef805) -------------
# Keys harmonised with mac client UI (blpremote_client.ui) so any
# future audit/grep across both UIs lines up.
LED_GREY = "#8b8d91"   # unknown / probing (first render before any poll)
LED_GREEN = "#2ea043"  # detected / up / configured / connected
LED_AMBER = "#d29922"  # connecting / reconnecting / starting
LED_RED = "#cf222e"    # not running / down / using-default-secret / disconnected

LED_COLOUR: dict[str, str] = {
    "unknown": LED_GREY,
    "happy": LED_GREEN,
    "transient": LED_AMBER,
    "unhappy": LED_RED,
}

# Probe configuration (overridable by tests / future settings).
DEFAULT_HEALTH_URL = "http://127.0.0.1:8000/health"
DEFAULT_HEALTH_TIMEOUT_S = 1.5
BBG_PROCESS_NAME = "wintrv.exe"
DEFAULT_SECRET_PREFIX = "change-me-in-production"  # M5(A) sentinel


# --- DPI awareness (Windows only; no-op elsewhere) ------------------
def enable_hidpi_on_windows() -> None:
    """Must run before any Tk window is created or text/fonts render
    fuzzy on scaled displays (Win 8+ default 125% / 150% / 200%)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
    except Exception:
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass  # very old win or non-Microsoft Python; bail quietly


# --- Pure probe helpers ---------------------------------------------
# Tests drive these directly; controller methods are thin wrappers.


def detect_bloomberg(platform: str = sys.platform) -> tuple[str, str]:
    """Detect Bloomberg Terminal via tasklist (Windows-native). On
    non-Windows we report 'unknown' since BBG Terminal doesn't run
    there anyway and the file should still load for cross-platform
    smoke tests."""
    if platform != "win32":
        return ("unknown", "non-Windows host")
    try:
        cp = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {BBG_PROCESS_NAME}"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if BBG_PROCESS_NAME.lower() in cp.stdout.lower():
            return ("happy", "Detected")
        return ("unhappy", "Not running")
    except Exception as exc:
        return ("unhappy", f"detect failed: {exc!r}")


def parse_health(
    payload: Optional[dict[str, Any]],
    port: str = "8000",
) -> tuple[str, str]:
    """Pure: turn a parsed /health JSON payload into a (state, text).
    None payload → 'Down' (probe failed). Used by both the live
    ``probe_server`` wrapper and the unit tests."""
    if payload is None:
        return ("unhappy", "Down")
    if payload.get("bloomberg_connected"):
        return ("happy", f"Up · :{port}")
    state = payload.get("session_state") or "unknown"
    return ("transient", f"Up but session={state}")


def probe_server(
    url: str = DEFAULT_HEALTH_URL,
    timeout_s: float = DEFAULT_HEALTH_TIMEOUT_S,
) -> tuple[str, str]:
    """Live wrapper: GET /health, hand the parsed payload to
    parse_health. Catches network failures and returns Down."""
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as r:
            payload = json.loads(r.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        payload = None
    port = url.rsplit(":", 1)[-1].split("/")[0]
    return parse_health(payload, port=port)


def probe_jwt(env: Optional[dict[str, str]] = None) -> tuple[str, str]:
    """Read BLPREMOTE_SECRET_KEY (and ALLOW_DEFAULT_SECRET) from the
    given env (defaults to os.environ). Pure when env is supplied."""
    e = os.environ if env is None else env
    secret = e.get("BLPREMOTE_SECRET_KEY", "")
    allow_default = e.get("BLPREMOTE_ALLOW_DEFAULT_SECRET") == "1"
    if not secret or secret.startswith(DEFAULT_SECRET_PREFIX):
        if allow_default:
            return ("unhappy", "Insecure default (allow=1)")
        return ("unhappy", "Using insecure default")
    return ("happy", "Configured")


def read_ngrok_url(path: Path) -> tuple[str, str]:
    """Read setup.ps1's stash file. utf-8-sig strips a leading BOM
    if PowerShell's Out-File added one."""
    try:
        if path.exists():
            url = path.read_text(encoding="utf-8-sig").strip()
            if url:
                return ("happy", url)
    except OSError:
        pass
    return ("unhappy", "")


# --- Settings-dialog helpers (M10 item 5) ---------------------------


def read_pairing_code(path: Path) -> Optional[str]:
    """Return setup.ps1's stashed pairing code, or None if absent.
    Same BOM-tolerance as read_ngrok_url."""
    try:
        if path.exists():
            code = path.read_text(encoding="utf-8-sig").strip()
            return code or None
    except OSError:
        pass
    return None


def regenerate_jwt_secret(path: Path) -> str:
    """Write a new 64-char base64 secret to the user-only secret
    file and return it. Mirrors setup.ps1 chunk (c)'s gen path so
    the server picks up the same shape on next boot. Caller is
    responsible for surfacing 'restart server to apply' to the user."""
    import secrets

    path.parent.mkdir(parents=True, exist_ok=True)
    new_secret = secrets.token_urlsafe(48)  # 64 chars, URL-safe
    path.write_text(new_secret, encoding="utf-8")
    # Best-effort POSIX perms; on Windows os.chmod is mostly a no-op
    # but the parent dir is %USERPROFILE% which is user-isolated.
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return new_secret


def ngrok_authtoken_status(cfg_path: Path) -> tuple[str, str]:
    """Probe the ngrok yml (same path setup.ps1 chunk b checks).
    Returns (state, label) ready for the Settings dialog row."""
    import re

    try:
        if cfg_path.exists():
            text = cfg_path.read_text(encoding="utf-8-sig")
            if re.search(r"(?m)^\s*authtoken:", text):
                return ("happy", "Configured")
    except OSError:
        pass
    return ("unhappy", "Missing")


def start_button_state(bbg_state: str) -> str:
    """Pure: gate the [Start Server] button on the BBG probe result.
    Per M9 plan adef805 option (a): only enabled when BBG detected."""
    return "normal" if bbg_state == "happy" else "disabled"


# --- Command builders (pure; tests live in test_ui.py) --------------


def build_start_command(
    repo_root: Path,
    coord_target: Optional[str] = "mac",
    skip_install: bool = True,
) -> list[str]:
    """Argv for Popen-ing setup.ps1. -SkipInstall by default since
    the UI launch path assumes setup ran once already; the script's
    /health short-circuit handles the already-up case anyway."""
    setup = repo_root / "setup.ps1"
    cmd: list[str] = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", str(setup),
        "-Force",  # restart if /health says healthy (we want fresh logs)
    ]
    if skip_install:
        cmd.append("-SkipInstall")
    if coord_target:
        cmd.extend(["-CoordSend", coord_target])
    return cmd


def build_stop_command(port: int = 8000) -> list[str]:
    """Argv for Popen-ing a PowerShell one-liner that kills whatever
    is listening on the chosen port + any ngrok process. Brute-force
    by design: setup.ps1 spawns uvicorn + ngrok as detached children
    so we can't track them via the Popen handle that started them."""
    snippet = (
        f"Get-NetTCPConnection -LocalPort {port} -State Listen "
        "-ErrorAction SilentlyContinue | "
        "ForEach-Object { Stop-Process -Id $_.OwningProcess -Force "
        "-ErrorAction SilentlyContinue }; "
        "Get-Process ngrok -ErrorAction SilentlyContinue | "
        "Stop-Process -Force -ErrorAction SilentlyContinue; "
        "Write-Host 'stop: complete'"
    )
    return [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-Command", snippet,
    ]


def build_send_url_command(
    venv_python: str,
    coord_script: Path,
    target: str,
    message_file: Path,
) -> list[str]:
    """Argv for invoking tools/coord.py to post a message. Body is
    file-driven so PowerShell parens / em-dashes / unicode in the
    URL don't get mangled by shell quoting."""
    return [
        venv_python,
        str(coord_script),
        "send",
        target,
        "--file",
        str(message_file),
    ]


# --- Controller -----------------------------------------------------
# Tkinter import deferred to inside the controller so the module is
# importable + testable on hosts without Tk display.


class ServerUIController:
    """Owns the window + all widgets. Pure probe logic lives at
    module scope so tests don't need a Tk display."""

    POLL_INTERVAL_S = 2.0
    DRAIN_INTERVAL_MS = 100

    REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
    NGROK_URL_FILE = REPO_ROOT / ".coord" / "last_ngrok_url.txt"
    PAIRING_CODE_FILE = REPO_ROOT / ".coord" / "last_pairing_code.txt"
    COORD_SCRIPT = REPO_ROOT / "tools" / "coord.py"
    MAC_TARGET = "mac"
    SECRET_FILE = Path.home() / ".blpremote" / "server_secret.txt"
    NGROK_CFG = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ngrok" / "ngrok.yml"

    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        self._tk = tk
        self._ttk = ttk

        self.root = tk.Tk()
        self.root.title("Bloomberg Remote — Server")
        self.root.geometry("460x540+80+80")
        self.root.minsize(440, 480)

        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")

        pad = {"padx": 14, "pady": (10, 0)}

        # Status block
        status = ttk.LabelFrame(self.root, text="Status")
        status.pack(fill="x", **pad)
        self.row_bbg = self._make_status_row(status, "Bloomberg Terminal")
        self.row_server = self._make_status_row(status, "Server")
        self.row_jwt = self._make_status_row(status, "JWT secret")
        self.row_ngrok = self._make_status_row(status, "ngrok")
        ttk.Frame(status, height=4).pack()

        # ngrok URL row
        url_frame = ttk.LabelFrame(self.root, text="ngrok URL")
        url_frame.pack(fill="x", **pad)
        url_row = ttk.Frame(url_frame)
        url_row.pack(fill="x", pady=4)
        self.url_var = tk.StringVar(value="")
        ttk.Entry(url_row, textvariable=self.url_var, state="readonly").pack(
            side="left", fill="x", expand=True, padx=(2, 6)
        )
        self.btn_copy = ttk.Button(
            url_row, text="Copy", width=8, command=self._on_copy_url
        )
        self.btn_copy.pack(side="left")

        # Action buttons
        btns = ttk.Frame(self.root)
        btns.pack(fill="x", **pad)
        self.btn_start = ttk.Button(
            btns, text="Start Server", width=14, command=self._on_start
        )
        self.btn_start.pack(side="left", padx=(0, 6))
        self.btn_stop = ttk.Button(
            btns, text="Stop Server", width=14, command=self._on_stop
        )
        self.btn_stop.pack(side="left", padx=(0, 6))
        self.btn_send_url = ttk.Button(
            btns, text="Send URL to Mac", width=18, command=self._on_send_url
        )
        self.btn_send_url.pack(side="left")
        # Settings on the right edge so the action triple stays visually
        # primary; the gear glyph is U+2699 which clam renders cleanly.
        self.btn_settings = ttk.Button(
            btns, text="⚙ Settings", width=12, command=self._on_open_settings
        )
        self.btn_settings.pack(side="right")

        # Logs tail
        logs_frame = ttk.LabelFrame(self.root, text="Logs")
        logs_frame.pack(fill="both", expand=True, padx=14, pady=(10, 14))
        self.logs = tk.Text(
            logs_frame,
            height=10,
            wrap="none",
            bg="#f6f8fa",
            font=("Consolas", 10) if sys.platform == "win32" else ("Menlo", 11),
            state="disabled",
        )
        sb = ttk.Scrollbar(logs_frame, command=self.logs.yview)
        self.logs.configure(yscrollcommand=sb.set)
        self.logs.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Initial render
        for row in (self.row_bbg, self.row_server, self.row_jwt, self.row_ngrok):
            self._set_row(row, "unknown", "probing…")

        # Background probe loop + log streaming queue (chunk c).
        self._probe_q: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._logs_q: "queue.Queue[str]" = queue.Queue()
        self._stop_event = threading.Event()
        self._probe_thread = threading.Thread(
            target=self._probe_loop, daemon=True, name="status-probe"
        )
        self._probe_thread.start()
        self.root.after(self.DRAIN_INTERVAL_MS, self._drain_probe_queue)
        self.root.after(self.DRAIN_INTERVAL_MS, self._drain_logs_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Subprocess handles tracked so we can avoid stomping ourselves.
        self._active_proc: Optional[subprocess.Popen] = None

        self._append_log("UI started — status block polling every 2s.")

    # --- Widget helpers --------------------------------------------
    def _make_status_row(self, parent: Any, label: str) -> dict[str, Any]:
        """One row = (frame, LED canvas, status text var). Returned
        as a dict so _set_row can update both the LED and the text."""
        ttk = self._ttk
        tk = self._tk
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=2)
        ttk.Label(frame, text=label, width=20, anchor="w").pack(side="left")
        canvas = tk.Canvas(frame, width=14, height=14, highlightthickness=0)
        led = canvas.create_oval(2, 2, 12, 12, fill=LED_GREY, outline="")
        canvas.pack(side="left", padx=(0, 6))
        text_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=text_var, anchor="w").pack(side="left")
        return {"canvas": canvas, "led": led, "text_var": text_var}

    def _set_row(self, row: dict[str, Any], state: str, text: str) -> None:
        row["canvas"].itemconfigure(
            row["led"], fill=LED_COLOUR.get(state, LED_GREY)
        )
        row["text_var"].set(text)

    # --- Polling ---------------------------------------------------
    def _probe_loop(self) -> None:
        """Background thread: probe every POLL_INTERVAL_S."""
        while not self._stop_event.is_set():
            try:
                result = {
                    "bbg": detect_bloomberg(),
                    "server": probe_server(),
                    "jwt": probe_jwt(),
                    "ngrok": read_ngrok_url(self.NGROK_URL_FILE),
                }
            except Exception as exc:
                result = {"error": f"probe loop crashed: {exc!r}"}
            self._probe_q.put(result)
            self._stop_event.wait(self.POLL_INTERVAL_S)

    def _drain_probe_queue(self) -> None:
        try:
            while True:
                self._apply_probe(self._probe_q.get_nowait())
        except queue.Empty:
            pass
        if not self._stop_event.is_set():
            self.root.after(self.DRAIN_INTERVAL_MS, self._drain_probe_queue)

    def _apply_probe(self, result: dict[str, Any]) -> None:
        if "error" in result:
            self._append_log(result["error"])
            return
        bbg_state, bbg_text = result["bbg"]
        self._set_row(self.row_bbg, bbg_state, bbg_text)
        self.btn_start.configure(state=start_button_state(bbg_state))

        srv_state, srv_text = result["server"]
        self._set_row(self.row_server, srv_state, srv_text)

        jwt_state, jwt_text = result["jwt"]
        self._set_row(self.row_jwt, jwt_state, jwt_text)

        _, ngrok_url = result["ngrok"]
        if ngrok_url:
            self._set_row(self.row_ngrok, "happy", "Connected")
            if self.url_var.get() != ngrok_url:
                self.url_var.set(ngrok_url)
        else:
            self._set_row(self.row_ngrok, "unhappy", "no URL published")
            self.url_var.set("")

    # --- Button callbacks (chunk c) --------------------------------
    def _on_start(self) -> None:
        # Fast-path: if /health is already happy, don't re-spawn.
        # Per Mac's chunk-(a) review nit + plan adef805 §"State machine".
        state, text = probe_server()
        if state == "happy":
            self._append_log(f"[start] /health = {text} — already running, no relaunch")
            return
        if self._active_proc is not None and self._active_proc.poll() is None:
            self._append_log("[start] another start is already in flight — ignoring")
            return
        cmd = build_start_command(self.REPO_ROOT, coord_target=self.MAC_TARGET)
        self._append_log(f"[start] {' '.join(cmd[:5])} ... -CoordSend {self.MAC_TARGET}")
        self._spawn_streaming(cmd, prefix="setup", retain=True)

    def _on_stop(self) -> None:
        cmd = build_stop_command(port=8000)
        self._append_log("[stop] killing port-8000 listener + ngrok")
        self._spawn_streaming(cmd, prefix="stop", retain=False)

    def _on_send_url(self) -> None:
        url = self.url_var.get().strip()
        if not url:
            self._append_log("[send-url] no URL to send (ngrok probe is unhappy)")
            return
        # Body via temp file so any unicode / parens in the URL don't
        # get mangled by shell quoting.
        import tempfile
        body = f"server up at {url} (manual re-send from server UI)"
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".txt", delete=False
        ) as tf:
            tf.write(body)
            tmp_path = Path(tf.name)
        cmd = build_send_url_command(
            venv_python=sys.executable,
            coord_script=self.COORD_SCRIPT,
            target=self.MAC_TARGET,
            message_file=tmp_path,
        )
        self._append_log(f"[send-url] coord.py send {self.MAC_TARGET} ...")
        # File cleanup runs after the subprocess completes — wire as a
        # post-exit hook on the streaming thread.
        self._spawn_streaming(
            cmd, prefix="send-url", retain=False, on_exit=lambda: tmp_path.unlink(missing_ok=True)
        )

    def _on_open_settings(self) -> None:
        """Open the Settings modal. M10 item 5 mirror of mac's
        client-side _open_settings_dialog. All actions are real, no
        stub placeholders."""
        _open_settings_dialog(
            parent=self.root,
            secret_file=self.SECRET_FILE,
            ngrok_cfg=self.NGROK_CFG,
            pairing_code_file=self.PAIRING_CODE_FILE,
            ngrok_exe=self._resolve_ngrok_exe(),
            log=self._append_log,
        )

    def _resolve_ngrok_exe(self) -> Optional[Path]:
        """Same search order as setup.ps1 chunk (a). None if not found."""
        candidates = [
            self.REPO_ROOT / "tools" / "ngrok" / "ngrok.exe",
            self.REPO_ROOT / "tools" / "ngrok.exe",
        ]
        for p in candidates:
            if p.exists():
                return p
        return None

    # --- Subprocess streaming helper -------------------------------
    def _spawn_streaming(
        self,
        cmd: list[str],
        prefix: str,
        retain: bool = False,
        on_exit: Optional[Any] = None,
    ) -> None:
        """Popen the command, stream stdout/stderr lines into the
        logs queue (drained by main thread). Set retain=True to
        track the Popen as `self._active_proc` so the UI can refuse
        overlapping starts."""
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except FileNotFoundError as exc:
            self._append_log(f"[{prefix}] failed to spawn: {exc}")
            return
        if retain:
            self._active_proc = proc

        def _pump() -> None:
            try:
                if proc.stdout:
                    for line in proc.stdout:
                        self._logs_q.put(f"[{prefix}] {line.rstrip()}")
            except Exception as exc:
                self._logs_q.put(f"[{prefix}] stream error: {exc!r}")
            finally:
                rc = proc.wait()
                self._logs_q.put(f"[{prefix}] exit {rc}")
                if retain and self._active_proc is proc:
                    self._active_proc = None
                if on_exit is not None:
                    try:
                        on_exit()
                    except Exception as exc:
                        self._logs_q.put(f"[{prefix}] on_exit failed: {exc!r}")

        threading.Thread(target=_pump, daemon=True, name=f"{prefix}-pump").start()

    def _drain_logs_queue(self) -> None:
        """Main-thread drain: pull subprocess output into the logs widget."""
        try:
            while True:
                self._append_log(self._logs_q.get_nowait())
        except queue.Empty:
            pass
        if not self._stop_event.is_set():
            self.root.after(self.DRAIN_INTERVAL_MS, self._drain_logs_queue)

    def _on_copy_url(self) -> None:
        url = self.url_var.get()
        if not url:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self._append_log(f"copied URL to clipboard: {url}")

    # --- Logs helper ------------------------------------------------
    def _append_log(self, line: str) -> None:
        self.logs.configure(state="normal")
        self.logs.insert("end", line.rstrip() + "\n")
        self.logs.see("end")
        self.logs.configure(state="disabled")

    # --- Lifecycle --------------------------------------------------
    def _on_close(self) -> None:
        self._stop_event.set()
        self.root.destroy()

    def run(self) -> int:
        self.root.mainloop()
        return 0


def _open_settings_dialog(  # pragma: no cover (Tkinter)
    parent: Any,
    secret_file: Path,
    ngrok_cfg: Path,
    pairing_code_file: Path,
    ngrok_exe: Optional[Path],
    log: Any,
) -> None:
    """Modal Settings dialog. Mirrors mac client UI's
    ``_open_settings_dialog`` shape but the fields are server-side
    concerns: pairing code (display + copy + regen), JWT secret
    (status + regen), ngrok authtoken (status + set)."""
    import tkinter as tk
    from tkinter import ttk

    dlg = tk.Toplevel(parent)
    dlg.title("Settings")
    dlg.transient(parent)
    dlg.grab_set()
    dlg.geometry("520x360")
    dlg.resizable(False, False)

    main = ttk.Frame(dlg, padding=14)
    main.pack(fill="both", expand=True)

    msg_var = tk.StringVar(value="")

    # ── Pairing code ────────────────────────────────────────────
    pair_frame = ttk.LabelFrame(main, text="Pairing code (hand to client)")
    pair_frame.grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 12))
    code = read_pairing_code(pairing_code_file) or ""
    pair_var = tk.StringVar(value=code)
    pair_entry = ttk.Entry(pair_frame, textvariable=pair_var, state="readonly")
    pair_entry.pack(side="left", fill="x", expand=True, padx=(6, 4), pady=6)

    def do_copy_pair():
        if not pair_var.get():
            msg_var.set("no pairing code on file -- run setup.ps1 to create the first user")
            return
        dlg.clipboard_clear()
        dlg.clipboard_append(pair_var.get())
        msg_var.set("pairing code copied to clipboard")
        log("[settings] pairing code copied to clipboard")

    ttk.Button(pair_frame, text="Copy", width=8, command=do_copy_pair).pack(
        side="left", padx=(0, 6), pady=6
    )

    # ── JWT secret ──────────────────────────────────────────────
    jwt_state, jwt_text = probe_jwt()
    ttk.Label(main, text="JWT secret", width=18, anchor="w").grid(
        row=1, column=0, sticky="w", pady=4
    )
    jwt_status_var = tk.StringVar(value=jwt_text)
    ttk.Label(main, textvariable=jwt_status_var, anchor="w").grid(
        row=1, column=1, sticky="w", pady=4
    )

    def do_regen_jwt():
        try:
            new_secret = regenerate_jwt_secret(secret_file)
        except Exception as e:
            msg_var.set(f"jwt regen failed: {type(e).__name__}: {e}")
            return
        jwt_status_var.set("Regenerated -- restart server to apply")
        msg_var.set(
            f"new secret written to {secret_file} ({len(new_secret)} chars). "
            "uvicorn won't pick it up until you Stop + Start."
        )
        log(f"[settings] regenerated JWT secret -> {secret_file} (restart pending)")

    ttk.Button(main, text="Regenerate", width=12, command=do_regen_jwt).grid(
        row=1, column=2, padx=(8, 0), sticky="e", pady=4
    )

    # ── ngrok authtoken ─────────────────────────────────────────
    auth_state, auth_text = ngrok_authtoken_status(ngrok_cfg)
    ttk.Label(main, text="ngrok authtoken", width=18, anchor="w").grid(
        row=2, column=0, sticky="w", pady=4
    )
    auth_status_var = tk.StringVar(value=auth_text)
    ttk.Label(main, textvariable=auth_status_var, anchor="w").grid(
        row=2, column=1, sticky="w", pady=4
    )

    def do_set_authtoken():
        if ngrok_exe is None:
            msg_var.set("ngrok.exe not found in tools/ -- run setup.ps1 to install it first")
            return
        # Open the dashboard for the user, then ask for the token.
        try:
            import webbrowser

            webbrowser.open("https://dashboard.ngrok.com/get-started/your-authtoken")
        except Exception:
            pass
        # Use a small sub-modal for the token paste rather than
        # tkinter.simpledialog (which trips a focus quirk on some
        # Win10 themes).
        token_dlg = tk.Toplevel(dlg)
        token_dlg.title("Set ngrok authtoken")
        token_dlg.transient(dlg)
        token_dlg.grab_set()
        token_dlg.geometry("420x110")
        token_dlg.resizable(False, False)
        ttk.Label(token_dlg, text="Paste authtoken from the dashboard:").pack(
            padx=12, pady=(12, 4), anchor="w"
        )
        tok_var = tk.StringVar()
        ttk.Entry(token_dlg, textvariable=tok_var, width=50).pack(padx=12, fill="x")

        def apply_token():
            token = tok_var.get().strip()
            if not token:
                token_dlg.destroy()
                return
            try:
                cp = subprocess.run(
                    [str(ngrok_exe), "config", "add-authtoken", token],
                    capture_output=True, text=True, timeout=10,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if cp.returncode != 0:
                    raise RuntimeError(cp.stderr.strip() or f"exit {cp.returncode}")
            except Exception as e:
                msg_var.set(f"add-authtoken failed: {type(e).__name__}: {e}")
                token_dlg.destroy()
                return
            new_state, new_text = ngrok_authtoken_status(ngrok_cfg)
            auth_status_var.set(new_text)
            msg_var.set("ngrok authtoken saved -- restart server to refresh tunnel")
            log("[settings] ngrok authtoken saved (restart pending for new tunnel)")
            token_dlg.destroy()

        btns_t = ttk.Frame(token_dlg)
        btns_t.pack(fill="x", padx=12, pady=(8, 12))
        ttk.Button(btns_t, text="Cancel", width=10, command=token_dlg.destroy).pack(
            side="right", padx=(6, 0)
        )
        ttk.Button(btns_t, text="Save", width=10, command=apply_token).pack(side="right")
        token_dlg.wait_window()

    ttk.Button(main, text="Set...", width=12, command=do_set_authtoken).grid(
        row=2, column=2, padx=(8, 0), sticky="e", pady=4
    )

    # ── Status message ──────────────────────────────────────────
    ttk.Label(main, textvariable=msg_var, foreground="#666", wraplength=480).grid(
        row=3, column=0, columnspan=3, sticky="w", pady=(14, 0)
    )

    # ── Close ───────────────────────────────────────────────────
    btns = ttk.Frame(main)
    btns.grid(row=4, column=0, columnspan=3, sticky="e", pady=(18, 0))
    ttk.Button(btns, text="Close", width=10, command=dlg.destroy).pack(side="right")

    main.columnconfigure(1, weight=1)
    dlg.wait_window()


def main() -> int:
    enable_hidpi_on_windows()
    return ServerUIController().run()
