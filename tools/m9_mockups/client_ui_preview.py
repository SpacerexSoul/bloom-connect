"""M9 client UI mockup — macOS side.

NO BEHAVIOUR. Renders the widget tree exactly as planned in
docs/M9_UI_PLAN.md so we have a real screenshot to argue layout
over before writing the real `tools/blpremote-client-ui.py`.

Run:
    python tools/m9_mockups/client_ui_preview.py
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk


def _led(parent, colour):
    c = tk.Canvas(parent, width=14, height=14, highlightthickness=0)
    c.create_oval(2, 2, 12, 12, fill=colour, outline="")
    return c


def main() -> int:
    root = tk.Tk()
    root.title("Bloomberg Remote — Client")
    root.geometry("480x540+560+80")
    root.minsize(460, 500)

    style = ttk.Style()
    if "clam" in style.theme_names():
        style.theme_use("clam")

    pad = {"padx": 14, "pady": (10, 0)}

    # ── Connection ──────────────────────────────────────────────
    conn = ttk.LabelFrame(root, text="Connection")
    conn.pack(fill="x", **pad)

    # Server URL
    row1 = ttk.Frame(conn)
    row1.pack(fill="x", pady=4)
    ttk.Label(row1, text="Server URL", width=14, anchor="w").pack(side="left")
    url_var = tk.StringVar(value="https://nest-eligibly-dork.ngrok-free.dev")
    ttk.Entry(row1, textvariable=url_var).pack(side="left", fill="x", expand=True)

    # Identity
    row2 = ttk.Frame(conn)
    row2.pack(fill="x", pady=2)
    ttk.Label(row2, text="Identity", width=14, anchor="w").pack(side="left")
    ttk.Label(row2, text="mac", anchor="w").pack(side="left")

    # OpenRouter
    row3 = ttk.Frame(conn)
    row3.pack(fill="x", pady=2)
    ttk.Label(row3, text="OpenRouter key", width=14, anchor="w").pack(side="left")
    _led(row3, "#2ea043").pack(side="left", padx=(0, 6))
    ttk.Label(row3, text="Configured", anchor="w").pack(side="left")

    # Status
    row4 = ttk.Frame(conn)
    row4.pack(fill="x", pady=(8, 4))
    ttk.Label(row4, text="Status", width=14, anchor="w").pack(side="left")
    _led(row4, "#8b8d91").pack(side="left", padx=(0, 6))
    ttk.Label(row4, text="Disconnected", anchor="w").pack(side="left")

    # Buttons
    btns = ttk.Frame(conn)
    btns.pack(fill="x", pady=(6, 6))
    ttk.Button(btns, text="Connect",    width=14).pack(side="left", padx=(0, 6))
    ttk.Button(btns, text="Disconnect", width=14).pack(side="left")

    # ── Test query ──────────────────────────────────────────────
    tq = ttk.LabelFrame(root, text="Test query")
    tq.pack(fill="both", expand=True, padx=14, pady=(10, 14))

    prompt_row = ttk.Frame(tq)
    prompt_row.pack(fill="x", pady=6)
    prompt_var = tk.StringVar(value="AAPL last price")
    ttk.Entry(prompt_row, textvariable=prompt_var).pack(
        side="left", fill="x", expand=True, padx=(2, 6)
    )
    ttk.Button(prompt_row, text="Ask", width=8).pack(side="left")

    plan_text = tk.Text(tq, height=10, wrap="word", bg="#f6f8fa",
                        font=("Menlo", 11) if sys.platform == "darwin" else ("Consolas", 10))
    plan_text.pack(fill="both", expand=True, padx=2, pady=(0, 6))
    plan_text.insert("1.0",
        "explain: Fetch AAPL's last price (PX_LAST) via ReferenceDataRequest.\n"
        "\n"
        "ops (7):\n"
        "  1. start_session\n"
        "  2. open_service //blp/refdata\n"
        "  3. create_request ReferenceDataRequest id=r1\n"
        "  4. append id=r1 path=securities value=AAPL US Equity\n"
        "  5. append id=r1 path=fields     value=PX_LAST\n"
        "  6. send_request id=r1 correlation_id=cid-1\n"
        "  7. collect_response correlation_id=cid-1 timeout_ms=10000\n"
    )
    plan_text.configure(state="disabled")

    run_row = ttk.Frame(tq)
    run_row.pack(fill="x", pady=(0, 4))
    ttk.Button(run_row, text="Run",    width=10).pack(side="left", padx=(2, 6))
    ttk.Button(run_row, text="Cancel", width=10).pack(side="left")

    root.update_idletasks()
    root.attributes("-topmost", True)
    root.lift()
    root.focus_force()
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
