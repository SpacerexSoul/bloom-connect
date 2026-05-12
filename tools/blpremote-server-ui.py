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

import sys
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional


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

LED_COLOUR: dict[str, str] = {
    "unknown": LED_GREY,
    "ok": LED_GREEN,
    "warn": LED_AMBER,
    "bad": LED_RED,
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

    POLL_INTERVAL_MS = 2000  # /health + BBG-detect cadence — wired in chunk (b)

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

        # Initial render: everything at "probing…" until chunk (b)
        # wires the first /health + BBG-detect pass.
        for row in (self.row_bbg, self.row_server, self.row_jwt, self.row_ngrok):
            row.set("unknown", "probing…")
        self._append_log("UI started — chunk (a) skeleton, no behaviour wired yet.")

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
