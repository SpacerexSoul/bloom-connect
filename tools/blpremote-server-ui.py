"""Bloomberg Remote — Server (Windows desktop UI).

Tier B (Tkinter) per docs/M9_UI_PLAN.md. Tiny single-window controller
over setup.ps1 + the running server. The UI is read-only-by-default
status + three buttons; no business logic lives here, only Tkinter glue
and subprocess spawn / kill.

Chunk (a) — skeleton ONLY: widget layout, controller class, LED
palette, stub callbacks. No polling, no subprocess, no /health calls.
Chunks (b) and (c) wire those in.

Launch:
    python tools/blpremote-server-ui.py
or double-click START_SERVER_UI.bat (chunk d).

Tested manually on Windows 11 + Python 3.11.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
from pathlib import Path
from tkinter import ttk
from typing import Any, Callable, Optional


# --- DPI awareness (Windows only; no-op elsewhere) ------------------
# Must run before any Tk window is created or text/fonts render fuzzy
# on scaled displays (Win 8+ default 125% / 150% / 200%).
def _enable_hidpi_on_windows() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        # PROCESS_SYSTEM_DPI_AWARE = 1; for per-monitor V2 use 2.
        # System-aware is enough for a small fixed-size window.
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass  # very old win or non-Microsoft Python; bail quietly


_enable_hidpi_on_windows()


# --- LED palette (locked in docs/M9_UI_PLAN.md adef805) -------------
LED_GREY = "#8b8d91"   # unknown / probing (first render before any poll)
LED_GREEN = "#2ea043"  # detected / up / configured / connected
LED_AMBER = "#d29922"  # connecting / reconnecting / starting
LED_RED = "#cf222e"    # not running / down / using-default-secret / disconnected

# Keys harmonised with mac client UI (packages/blpremote_client/.../ui.py)
# per Mac's chunk-(a) review nit: same hex values, same key names so any
# future audit/grep across both UIs lines up cleanly.
LED_COLOUR: dict[str, str] = {
    "unknown": LED_GREY,
    "happy": LED_GREEN,
    "transient": LED_AMBER,
    "unhappy": LED_RED,
}


# --- Status row helper ----------------------------------------------
class StatusRow:
    """One row of (label, LED, status text). The LED + text update
    independently via .set(state, text). Defaults to grey / 'probing…'."""

    def __init__(self, parent: tk.Widget, label: str):
        self.frame = ttk.Frame(parent)
        self.frame.pack(fill="x", pady=2)
        ttk.Label(self.frame, text=label, width=20, anchor="w").pack(side="left")
        self.canvas = tk.Canvas(self.frame, width=14, height=14, highlightthickness=0)
        self._led = self.canvas.create_oval(2, 2, 12, 12, fill=LED_GREY, outline="")
        self.canvas.pack(side="left", padx=(0, 6))
        self._text_var = tk.StringVar(value="probing…")
        ttk.Label(self.frame, textvariable=self._text_var, anchor="w").pack(side="left")

    def set(self, state: str, text: str) -> None:
        self.canvas.itemconfigure(self._led, fill=LED_COLOUR.get(state, LED_GREY))
        self._text_var.set(text)


# --- Controller -----------------------------------------------------
class ServerUIController:
    """Owns the window + all widgets. Chunk-(a) stubs out behaviour."""

    POLL_INTERVAL_S = 2.0     # status-block probe cadence (chunk b)
    DRAIN_INTERVAL_MS = 100   # main-thread queue drain cadence
    HEALTH_URL = "http://127.0.0.1:8000/health"
    HEALTH_TIMEOUT_S = 1.5
    BBG_PROCESS_NAME = "wintrv.exe"  # Bloomberg Terminal main process

    # Repo root = parent of tools/. Used to find .coord/last_ngrok_url.txt.
    REPO_ROOT = Path(__file__).resolve().parent.parent
    NGROK_URL_FILE = REPO_ROOT / ".coord" / "last_ngrok_url.txt"
    DEFAULT_SECRET_PREFIX = "change-me-in-production"  # M5(A) sentinel

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Bloomberg Remote — Server")
        self.root.geometry("460x540+80+80")
        self.root.minsize(440, 480)

        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")

        pad = {"padx": 14, "pady": (10, 0)}

        # Status block ────────────────────────────────────────────────
        status = ttk.LabelFrame(self.root, text="Status")
        status.pack(fill="x", **pad)
        self.row_bbg = StatusRow(status, "Bloomberg Terminal")
        self.row_server = StatusRow(status, "Server")
        self.row_jwt = StatusRow(status, "JWT secret")
        self.row_ngrok = StatusRow(status, "ngrok")
        ttk.Frame(status, height=4).pack()  # spacer

        # ngrok URL row ───────────────────────────────────────────────
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

        # Action buttons ──────────────────────────────────────────────
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

        # Logs tail ───────────────────────────────────────────────────
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

        # Initial render: everything at "probing…" until the first
        # poll lands. Avoids a red flash on startup.
        for row in (self.row_bbg, self.row_server, self.row_jwt, self.row_ngrok):
            row.set("unknown", "probing…")

        # Background-thread probe loop pushes results into a queue;
        # main thread drains and updates widgets via root.after.
        # Keeps Tk responsive even if a probe (tasklist, /health)
        # takes longer than expected.
        self._probe_q: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._stop_event = threading.Event()
        self._probe_thread = threading.Thread(
            target=self._probe_loop, daemon=True, name="status-probe"
        )
        self._probe_thread.start()
        self.root.after(self.DRAIN_INTERVAL_MS, self._drain_probe_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._append_log("UI started — status block polling every 2s.")

    # --- Stub callbacks (chunks b + c wire these) -------------------
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

    # --- Status polling (chunk b) -----------------------------------
    def _probe_loop(self) -> None:
        """Background thread: probe every POLL_INTERVAL_S. Push raw
        result dicts into the queue; main thread does Tk updates."""
        while not self._stop_event.is_set():
            try:
                result = {
                    "bbg": self._probe_bbg(),
                    "server": self._probe_server(),
                    "jwt": self._probe_jwt(),
                    "ngrok": self._probe_ngrok_url(),
                }
            except Exception as exc:
                result = {"error": f"probe loop crashed: {exc!r}"}
            self._probe_q.put(result)
            self._stop_event.wait(self.POLL_INTERVAL_S)

    def _drain_probe_queue(self) -> None:
        """Main thread: drain whatever the probe thread queued and
        apply to widgets. Reschedules itself."""
        try:
            while True:
                result = self._probe_q.get_nowait()
                self._apply_probe(result)
        except queue.Empty:
            pass
        if not self._stop_event.is_set():
            self.root.after(self.DRAIN_INTERVAL_MS, self._drain_probe_queue)

    def _apply_probe(self, result: dict[str, Any]) -> None:
        if "error" in result:
            self._append_log(result["error"])
            return
        bbg_state, bbg_text = result["bbg"]
        self.row_bbg.set(bbg_state, bbg_text)
        # [Start Server] disabled when BBG not detected (per M9 plan
        # adef805 option a). Tooltip is a chunk-(c) polish item.
        self.btn_start.configure(
            state="normal" if bbg_state == "happy" else "disabled"
        )

        srv_state, srv_text = result["server"]
        self.row_server.set(srv_state, srv_text)

        jwt_state, jwt_text = result["jwt"]
        self.row_jwt.set(jwt_state, jwt_text)

        ngrok_state, ngrok_url = result["ngrok"]
        if ngrok_url:
            self.row_ngrok.set("happy", "Connected")
            if self.url_var.get() != ngrok_url:
                self.url_var.set(ngrok_url)
        else:
            self.row_ngrok.set(ngrok_state, "no URL published")
            self.url_var.set("")

    # --- Individual probes ------------------------------------------
    def _probe_bbg(self) -> tuple[str, str]:
        """Detect Bloomberg Terminal via tasklist (Windows-native, no
        psutil dep). On non-Windows we just report 'unknown' because
        BBG Terminal doesn't run there anyway."""
        if sys.platform != "win32":
            return ("unknown", "non-Windows host")
        try:
            cp = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {self.BBG_PROCESS_NAME}"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if self.BBG_PROCESS_NAME.lower() in cp.stdout.lower():
                return ("happy", "Detected")
            return ("unhappy", "Not running")
        except Exception as exc:
            return ("unhappy", f"detect failed: {exc!r}")

    def _probe_server(self) -> tuple[str, str]:
        """GET /health. Returns ok/warn/bad based on
        bloomberg_connected + session_state."""
        try:
            with urllib.request.urlopen(
                self.HEALTH_URL, timeout=self.HEALTH_TIMEOUT_S
            ) as r:
                data = json.loads(r.read())
        except urllib.error.URLError:
            return ("unhappy", "Down")
        except Exception as exc:
            return ("unhappy", f"probe error: {exc!r}")
        if data.get("bloomberg_connected"):
            port = self.HEALTH_URL.rsplit(":", 1)[-1].split("/")[0]
            return ("happy", f"Up · :{port}")
        state = data.get("session_state") or "unknown"
        return ("transient", f"Up but session={state}")

    def _probe_jwt(self) -> tuple[str, str]:
        """Check the JWT secret the server WILL use. Reads env
        BLPREMOTE_SECRET_KEY (the same env setup.ps1 / lifespan
        consume). If unset or matches the default sentinel, 'bad';
        otherwise 'ok'. M5(A) refuses to boot at all on default
        unless BLPREMOTE_ALLOW_DEFAULT_SECRET=1, but the UI surfaces
        the underlying state regardless."""
        secret = os.environ.get("BLPREMOTE_SECRET_KEY", "")
        allow_default = os.environ.get("BLPREMOTE_ALLOW_DEFAULT_SECRET") == "1"
        if not secret or secret.startswith(self.DEFAULT_SECRET_PREFIX):
            label = "Insecure default (allow=1)" if allow_default else "Using insecure default"
            return ("unhappy", label)
        return ("happy", "Configured")

    def _probe_ngrok_url(self) -> tuple[str, str]:
        """Read the URL setup.ps1 stashes after a successful tunnel.
        utf-8-sig strips a BOM if PowerShell's Out-File added one."""
        try:
            if self.NGROK_URL_FILE.exists():
                url = self.NGROK_URL_FILE.read_text(encoding="utf-8-sig").strip()
                if url:
                    return ("happy", url)
        except OSError:
            pass
        return ("unhappy", "")

    def _on_close(self) -> None:
        """Clean shutdown of the probe thread before destroying Tk."""
        self._stop_event.set()
        self.root.destroy()

    # --- Logs helper ------------------------------------------------
    def _append_log(self, line: str) -> None:
        self.logs.configure(state="normal")
        self.logs.insert("end", line.rstrip() + "\n")
        self.logs.see("end")
        self.logs.configure(state="disabled")

    # --- Lifecycle --------------------------------------------------
    def run(self) -> int:
        self.root.mainloop()
        return 0


def main() -> int:
    return ServerUIController().run()


if __name__ == "__main__":
    raise SystemExit(main())
