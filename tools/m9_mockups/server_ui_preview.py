"""M9 server UI mockup — Windows side.

NO BEHAVIOUR. Renders the widget tree exactly as planned in
docs/M9_UI_PLAN.md so we have a real screenshot before win starts
his chunk plan. Tkinter is cross-platform, so this renders on Mac
too — the actual win render will look near-identical (ttk theme
`clam` is the same on both OSes).

Run:
    python tools/m9_mockups/server_ui_preview.py

Quit:
    close the window
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk


def _set_hidpi_on_windows() -> None:
    """Win-only DPI awareness fix (no-op on macOS/Linux). Real
    skeleton will do this at process start before importing tkinter."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _make_status_row(parent, label, indicator_colour, status_text):
    row = ttk.Frame(parent)
    row.pack(fill="x", pady=2)
    ttk.Label(row, text=label, width=20, anchor="w").pack(side="left")
    canvas = tk.Canvas(row, width=14, height=14, highlightthickness=0)
    canvas.create_oval(2, 2, 12, 12, fill=indicator_colour, outline="")
    canvas.pack(side="left", padx=(0, 6))
    ttk.Label(row, text=status_text, anchor="w").pack(side="left")


def main() -> int:
    _set_hidpi_on_windows()
    root = tk.Tk()
    root.title("Bloomberg Remote — Server")
    root.geometry("460x540+80+80")
    root.minsize(440, 480)

    style = ttk.Style()
    if "clam" in style.theme_names():
        style.theme_use("clam")

    pad = {"padx": 14, "pady": (10, 0)}

    # ── Status block ────────────────────────────────────────────
    status_frame = ttk.LabelFrame(root, text="Status")
    status_frame.pack(fill="x", **pad)

    _make_status_row(status_frame, "Bloomberg Terminal", "#2ea043", "Detected")
    _make_status_row(status_frame, "Server",             "#2ea043", "Up · :8000")
    _make_status_row(status_frame, "JWT secret",         "#2ea043", "Configured")
    _make_status_row(status_frame, "ngrok",              "#2ea043", "Connected")

    # spacer
    ttk.Frame(status_frame, height=4).pack()

    # ── ngrok URL row ───────────────────────────────────────────
    url_frame = ttk.LabelFrame(root, text="ngrok URL")
    url_frame.pack(fill="x", **pad)
    url_row = ttk.Frame(url_frame)
    url_row.pack(fill="x", pady=4)
    url_var = tk.StringVar(value="https://nest-eligibly-dork.ngrok-free.dev")
    ttk.Entry(url_row, textvariable=url_var, state="readonly").pack(
        side="left", fill="x", expand=True, padx=(2, 6)
    )
    ttk.Button(url_row, text="Copy", width=8).pack(side="left")

    # ── Action buttons ──────────────────────────────────────────
    btns = ttk.Frame(root)
    btns.pack(fill="x", **pad)
    ttk.Button(btns, text="Start Server", width=14).pack(side="left", padx=(0, 6))
    ttk.Button(btns, text="Stop Server",  width=14).pack(side="left", padx=(0, 6))
    ttk.Button(btns, text="Send URL to Mac", width=18).pack(side="left")

    # ── Logs tail ───────────────────────────────────────────────
    logs_frame = ttk.LabelFrame(root, text="Logs")
    logs_frame.pack(fill="both", expand=True, padx=14, pady=(10, 14))

    logs_text = tk.Text(logs_frame, height=10, wrap="none", bg="#f6f8fa",
                        font=("Menlo", 11) if sys.platform == "darwin" else ("Consolas", 10))
    sb = ttk.Scrollbar(logs_frame, command=logs_text.yview)
    logs_text.configure(yscrollcommand=sb.set)
    logs_text.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")

    sample_logs = (
        "17:30:14 INFO  uvicorn running on http://127.0.0.1:8000\n"
        "17:30:14 INFO  ngrok tunnel established\n"
        "17:30:15 INFO  /health 200 (1ms)\n"
        "17:30:17 INFO  -CoordSend mac · posted ngrok URL via coord\n"
        "17:31:02 INFO  /v1/execute 200 (43ms) ir_hash=… cache=miss\n"
        "17:31:08 INFO  /v1/execute 200 (8ms)  ir_hash=… cache=hit\n"
    )
    logs_text.insert("1.0", sample_logs)
    logs_text.configure(state="disabled")

    root.update_idletasks()
    root.attributes("-topmost", True)
    root.lift()
    root.focus_force()
    # Emit window geometry to a sidecar so the snap wrapper can crop precisely.
    import os
    geom_file = os.environ.get("M9_GEOM_FILE")
    if geom_file:
        try:
            with open(geom_file, "w") as f:
                f.write(f"{root.winfo_x()},{root.winfo_y()},{root.winfo_width()},{root.winfo_height()}\n")
        except OSError:
            pass
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
