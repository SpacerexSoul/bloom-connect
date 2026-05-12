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


def start_button_state(bbg_state: str) -> str:
    """Pure: gate the [Start Server] button on the BBG probe result.
    Per M9 plan adef805 option (a): only enabled when BBG detected."""
    return "normal" if bbg_state == "happy" else "disabled"


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

        # Background probe loop
        self._probe_q: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._stop_event = threading.Event()
        self._probe_thread = threading.Thread(
            target=self._probe_loop, daemon=True, name="status-probe"
        )
        self._probe_thread.start()
        self.root.after(self.DRAIN_INTERVAL_MS, self._drain_probe_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

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

    # --- Stub callbacks (chunk c wires for real) -------------------
    def _on_start(self) -> None:
        self._append_log("[stub] Start Server pressed — chunk (c) wires setup.ps1 Popen")

    def _on_stop(self) -> None:
        self._append_log("[stub] Stop Server pressed — chunk (c) wires Stop-Process")

    def _on_send_url(self) -> None:
        self._append_log("[stub] Send URL to Mac pressed — chunk (c) wires coord.py send")

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


def main() -> int:
    enable_hidpi_on_windows()
    return ServerUIController().run()
