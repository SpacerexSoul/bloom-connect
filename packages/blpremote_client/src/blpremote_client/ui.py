"""Desktop client UI — IBKR-Gateway-style connect window.

Tiny Tkinter app that wraps :class:`blpremote_client.host.RemoteHost`
and :func:`blpremote_client.llm.ask` behind a Connect button, a
status LED, and an optional "Test query" pane. Designed to be
double-clicked via ``Connect.command`` at the repo root.

Architecture:

- :class:`ClientController` holds all behaviour. No Tkinter
  references — pure logic, fully testable with a mocked
  :class:`RemoteHost`.
- :func:`build_window` wires the widgets to the controller using
  the standard Tkinter long-running-work pattern: each user action
  spawns a :class:`threading.Thread` whose result lands on a
  :class:`queue.Queue` drained by the main thread via
  ``root.after``. Keeps the event loop unblocked.
- :func:`main` constructs both and enters ``mainloop``. The
  ``blpremote-ui`` console script points here.

Optional deps:

- ``openai`` is required for the "Test query" pane (Ask / Run).
  Without it, the pane renders disabled with an install hint.
- The Connect / Disconnect path uses no LLM deps.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional


# ── State palette (mirrors M9_UI_PLAN.md, GitHub Primer-aligned) ─────

LED_COLOUR = {
    "happy":     "#2ea043",  # Detected · Up · Configured · Connected
    "transient": "#d29922",  # Starting · Connecting · Reconnecting
    "unhappy":   "#cf222e",  # Not running · Down · Disconnected · Error
    "unknown":   "#8b8d91",  # Pre-first-poll
}


@dataclass
class ConnectResult:
    ok: bool
    message: str
    server_version: Optional[str] = None


@dataclass
class AskResult:
    ok: bool
    message: str
    plan: Any = None      # ExecutionPlan when ok
    explain: str = ""


@dataclass
class ExecuteResult:
    ok: bool
    message: str
    data: Any = None
    warnings: list = None
    server_timing_ms: Optional[int] = None


class ClientController:
    """All client-side logic, Tkinter-free.

    The UI layer (:func:`build_window`) calls these methods from
    background threads and drains their return values onto the main
    thread via a ``queue.Queue``.
    """

    def __init__(self, identity_path: Optional[Path] = None):
        self.identity_path = identity_path or (Path.home() / ".blpremote" / "identity.json")
        self.openrouter_path = Path.home() / ".blpremote" / "openrouter.json"
        self._host: Any = None  # RemoteHost when connected

    # ── Probes (synchronous, fast — safe on main thread) ─────────

    def has_openrouter_key(self) -> bool:
        if os.environ.get("OPENROUTER_API_KEY"):
            return True
        if not self.openrouter_path.exists():
            return False
        try:
            data = json.loads(self.openrouter_path.read_text(encoding="utf-8"))
            return isinstance(data, dict) and isinstance(data.get("api_key"), str)
        except (OSError, json.JSONDecodeError):
            return False

    def get_identity(self) -> dict[str, Optional[str]]:
        """Return {"username", "url"} from the identity file, or
        empty strings if missing. Never raises."""
        out = {"username": "", "url": ""}
        if not self.identity_path.exists():
            return out
        try:
            data = json.loads(self.identity_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                out["username"] = data.get("user") or ""
                out["url"] = data.get("url") or ""
        except (OSError, json.JSONDecodeError):
            pass
        return out

    # ── Long-running actions (call from a worker thread) ─────────

    def connect(self, url: str) -> ConnectResult:
        """Construct a :class:`RemoteHost` pointing at ``url`` and
        verify by hitting ``/health``. Persists the URL back to the
        identity file so next launch picks it up."""
        from blpremote_client.host import RemoteHost

        try:
            host = RemoteHost(host=url)
            _ = host.health()
            version = host.version()
        except Exception as e:
            return ConnectResult(ok=False, message=f"{type(e).__name__}: {e}")

        self._host = host
        self._persist_url(url)
        return ConnectResult(ok=True, message="connected", server_version=version)

    def disconnect(self) -> None:
        """Drop the host reference. The cached token in
        ``~/.blpremote/coord_token.json`` stays — disconnecting the
        UI doesn't log out the identity."""
        self._host = None

    def is_connected(self) -> bool:
        return self._host is not None

    def ask(self, prompt: str) -> AskResult:
        if not self._host:
            return AskResult(ok=False, message="not connected")
        try:
            from blpremote_client.llm import ask as llm_ask
        except ImportError as e:
            return AskResult(ok=False, message=f"openai not installed: {e}")
        try:
            out = llm_ask(self._host, prompt)
        except Exception as e:
            return AskResult(ok=False, message=f"{type(e).__name__}: {e}")
        return AskResult(ok=True, message="ok", plan=out["plan"], explain=out["explain"])

    def execute(self, plan: Any) -> ExecuteResult:
        if not self._host:
            return ExecuteResult(ok=False, message="not connected")
        try:
            result = self._host.execute(plan)
        except Exception as e:
            return ExecuteResult(ok=False, message=f"{type(e).__name__}: {e}")
        return ExecuteResult(
            ok=(result.status == "ok"),
            message=f"status: {result.status}",
            data=result.data,
            warnings=list(result.warnings or []),
            server_timing_ms=result.server_timing_ms,
        )

    # ── Internal ────────────────────────────────────────────────

    def _persist_url(self, url: str) -> None:
        """Write the URL back to identity.json so next launch
        defaults to it. Preserves user/password if present."""
        try:
            self.identity_path.parent.mkdir(parents=True, exist_ok=True)
            data: dict[str, Any] = {}
            if self.identity_path.exists():
                try:
                    data = json.loads(self.identity_path.read_text(encoding="utf-8"))
                    if not isinstance(data, dict):
                        data = {}
                except (OSError, json.JSONDecodeError):
                    data = {}
            data["url"] = url.rstrip("/")
            self.identity_path.write_text(json.dumps(data, indent=2))
        except OSError:
            pass


# ── Tkinter glue ─────────────────────────────────────────────────────


def _format_plan(plan: Any, explain: str) -> str:
    lines = [f"explain: {explain}", "", f"ops ({len(plan.ops)}):"]
    for i, op in enumerate(plan.ops, 1):
        d = op.model_dump(exclude_none=True)
        op_name = d.pop("op")
        rest = " ".join(f"{k}={v}" for k, v in d.items())
        lines.append(f"  {i}. {op_name} {rest}".rstrip())
    return "\n".join(lines)


def _format_execute(result: ExecuteResult) -> str:
    parts = [result.message]
    if result.server_timing_ms is not None:
        parts.append(f"server_timing_ms: {result.server_timing_ms}")
    if result.warnings:
        parts.append("warnings:")
        for w in result.warnings:
            parts.append(f"  {w}")
    parts.append("")
    parts.append(json.dumps(result.data, indent=2, default=str)[:4000])
    return "\n".join(parts)


def build_window(controller: ClientController):  # pragma: no cover (Tkinter)
    """Construct the Tk root + all widgets, wire them to the
    controller through a queue+after pattern, return the root.
    """
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("Bloomberg Remote — Client")
    root.geometry("480x540+560+80")
    root.minsize(460, 500)

    style = ttk.Style()
    if "clam" in style.theme_names():
        style.theme_use("clam")

    q: queue.Queue = queue.Queue()

    def _led(parent, colour):
        c = tk.Canvas(parent, width=14, height=14, highlightthickness=0)
        oid = c.create_oval(2, 2, 12, 12, fill=colour, outline="")
        c.pack(side="left", padx=(0, 6))
        return c, oid

    # ── Connection ───────────────────────────────────────────────
    conn = ttk.LabelFrame(root, text="Connection")
    conn.pack(fill="x", padx=14, pady=(10, 0))

    ident = controller.get_identity()

    row1 = ttk.Frame(conn); row1.pack(fill="x", pady=4)
    ttk.Label(row1, text="Server URL", width=14, anchor="w").pack(side="left")
    url_var = tk.StringVar(value=ident["url"])
    url_entry = ttk.Entry(row1, textvariable=url_var)
    url_entry.pack(side="left", fill="x", expand=True)

    row2 = ttk.Frame(conn); row2.pack(fill="x", pady=2)
    ttk.Label(row2, text="Identity", width=14, anchor="w").pack(side="left")
    ttk.Label(row2, text=ident["username"] or "(none — write ~/.blpremote/identity.json)",
              anchor="w").pack(side="left")

    row3 = ttk.Frame(conn); row3.pack(fill="x", pady=2)
    ttk.Label(row3, text="OpenRouter key", width=14, anchor="w").pack(side="left")
    has_key = controller.has_openrouter_key()
    _led(row3, LED_COLOUR["happy" if has_key else "unhappy"])
    ttk.Label(row3, text="Configured" if has_key else "Missing", anchor="w").pack(side="left")

    row4 = ttk.Frame(conn); row4.pack(fill="x", pady=(8, 4))
    ttk.Label(row4, text="Status", width=14, anchor="w").pack(side="left")
    status_canvas, status_oval = _led(row4, LED_COLOUR["unhappy"])
    status_label = ttk.Label(row4, text="Disconnected", anchor="w")
    status_label.pack(side="left")

    def set_status(state: str, text: str) -> None:
        status_canvas.itemconfig(status_oval, fill=LED_COLOUR[state])
        status_label.configure(text=text)

    btns = ttk.Frame(conn); btns.pack(fill="x", pady=(6, 6))
    connect_btn = ttk.Button(btns, text="Connect", width=14)
    connect_btn.pack(side="left", padx=(0, 6))
    disconnect_btn = ttk.Button(btns, text="Disconnect", width=14, state="disabled")
    disconnect_btn.pack(side="left")

    # ── Test query ───────────────────────────────────────────────
    tq = ttk.LabelFrame(root, text="Test query")
    tq.pack(fill="both", expand=True, padx=14, pady=(10, 14))

    prompt_row = ttk.Frame(tq); prompt_row.pack(fill="x", pady=6)
    prompt_var = tk.StringVar(value="AAPL last price")
    prompt_entry = ttk.Entry(prompt_row, textvariable=prompt_var)
    prompt_entry.pack(side="left", fill="x", expand=True, padx=(2, 6))
    ask_btn = ttk.Button(prompt_row, text="Ask", width=8, state="disabled")
    ask_btn.pack(side="left")

    plan_text = tk.Text(
        tq, height=10, wrap="word", bg="#f6f8fa",
        font=("Menlo", 11) if sys.platform == "darwin" else ("Consolas", 10),
    )
    plan_text.pack(fill="both", expand=True, padx=2, pady=(0, 6))
    plan_text.configure(state="disabled")

    def set_plan_text(text: str) -> None:
        plan_text.configure(state="normal")
        plan_text.delete("1.0", "end")
        plan_text.insert("1.0", text)
        plan_text.configure(state="disabled")

    run_row = ttk.Frame(tq); run_row.pack(fill="x", pady=(0, 4))
    run_btn = ttk.Button(run_row, text="Run", width=10, state="disabled")
    run_btn.pack(side="left", padx=(2, 6))
    cancel_btn = ttk.Button(run_row, text="Cancel", width=10, state="disabled")
    cancel_btn.pack(side="left")

    # State held across handlers so Run can call execute on whatever Ask parsed.
    state = {"pending_plan": None}

    # ── Threading helpers ────────────────────────────────────────
    def run_in_thread(fn: Callable[[], Any], tag: str) -> None:
        def worker():
            try:
                result = fn()
            except Exception as e:  # belt + braces; controller already catches
                result = ("error", f"{type(e).__name__}: {e}")
            q.put((tag, result))
        threading.Thread(target=worker, daemon=True).start()

    def drain_queue() -> None:
        try:
            while True:
                tag, payload = q.get_nowait()
                handle_result(tag, payload)
        except queue.Empty:
            pass
        root.after(100, drain_queue)

    def handle_result(tag: str, payload: Any) -> None:
        if tag == "connect":
            r: ConnectResult = payload
            if r.ok:
                set_status("happy", f"Connected · {r.server_version}")
                disconnect_btn.configure(state="normal")
                connect_btn.configure(state="disabled")
                # Enable Ask only if we have an openrouter key.
                if controller.has_openrouter_key():
                    ask_btn.configure(state="normal")
            else:
                set_status("unhappy", f"Error · {r.message}")
                connect_btn.configure(state="normal")
        elif tag == "ask":
            r2: AskResult = payload
            if r2.ok:
                state["pending_plan"] = r2.plan
                set_plan_text(_format_plan(r2.plan, r2.explain))
                run_btn.configure(state="normal")
                cancel_btn.configure(state="normal")
            else:
                set_plan_text(f"ask failed: {r2.message}")
            ask_btn.configure(state="normal")
        elif tag == "execute":
            r3: ExecuteResult = payload
            set_plan_text(_format_execute(r3))
            run_btn.configure(state="disabled")
            cancel_btn.configure(state="disabled")
            state["pending_plan"] = None

    # ── Button handlers ──────────────────────────────────────────
    def do_connect():
        url = url_var.get().strip()
        if not url:
            set_status("unhappy", "Error · empty URL")
            return
        set_status("transient", "Connecting…")
        connect_btn.configure(state="disabled")
        run_in_thread(lambda: controller.connect(url), "connect")

    def do_disconnect():
        controller.disconnect()
        set_status("unhappy", "Disconnected")
        connect_btn.configure(state="normal")
        disconnect_btn.configure(state="disabled")
        ask_btn.configure(state="disabled")
        run_btn.configure(state="disabled")
        cancel_btn.configure(state="disabled")
        state["pending_plan"] = None
        set_plan_text("")

    def do_ask():
        prompt = prompt_var.get().strip()
        if not prompt:
            return
        set_plan_text("…")
        ask_btn.configure(state="disabled")
        run_btn.configure(state="disabled")
        cancel_btn.configure(state="disabled")
        run_in_thread(lambda: controller.ask(prompt), "ask")

    def do_run():
        plan = state["pending_plan"]
        if plan is None:
            return
        set_plan_text("running…")
        run_btn.configure(state="disabled")
        cancel_btn.configure(state="disabled")
        run_in_thread(lambda: controller.execute(plan), "execute")

    def do_cancel():
        state["pending_plan"] = None
        set_plan_text("(cancelled)")
        run_btn.configure(state="disabled")
        cancel_btn.configure(state="disabled")

    connect_btn.configure(command=do_connect)
    disconnect_btn.configure(command=do_disconnect)
    ask_btn.configure(command=do_ask)
    run_btn.configure(command=do_run)
    cancel_btn.configure(command=do_cancel)

    root.after(100, drain_queue)
    return root


def main() -> int:  # pragma: no cover (Tkinter)
    """Entry point for the ``blpremote-ui`` console script."""
    controller = ClientController()
    root = build_window(controller)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
