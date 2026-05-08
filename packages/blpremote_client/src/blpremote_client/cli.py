"""``blpremote-ask`` — tiny CLI demo of the M8 trust pattern.

Translates an English prompt to an IR plan via :func:`blpremote_client.llm.ask`,
prints the plan + a one-line explanation, and asks the user to confirm
before calling ``host.execute()``. The validator is the trust boundary;
this script is the human-in-the-loop confirmation step on top.

Usage::

    blpremote-ask "AAPL last price"
    blpremote-ask --service //blp/apiflds "describe PX_LAST"
    blpremote-ask --yes "AAPL last price"     # skip confirmation
    blpremote-ask --dry-run "AAPL last price" # never execute, just show plan
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from blpremote_client.host import RemoteHost


def _print_plan(plan: Any, explain: str) -> None:
    print(f"\nplan: {explain}")
    print(f"ops ({len(plan.ops)}):")
    for i, op in enumerate(plan.ops):
        # Compact one-liner per op — full IR is verbose, the gist
        # ("ReferenceDataRequest, securities=AAPL, fields=PX_LAST")
        # is what the user needs to confirm what's about to run.
        d = op.model_dump(exclude_none=True)
        op_name = d.pop("op")
        rest = ", ".join(f"{k}={v}" for k, v in d.items())
        print(f"  {i+1}. {op_name}({rest})")


def _confirm() -> bool:
    try:
        ans = input("\nrun this plan? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in ("y", "yes")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="blpremote-ask", description=__doc__.splitlines()[0])
    p.add_argument("prompt", help="natural-language Bloomberg query")
    p.add_argument("--service", default="//blp/refdata", help="schema scope (default: //blp/refdata)")
    p.add_argument("--model", default=None, help="anthropic model id (default: claude-haiku-4-5)")
    p.add_argument("--yes", "-y", action="store_true", help="skip the confirm prompt")
    p.add_argument("--dry-run", action="store_true", help="show plan but never execute")
    p.add_argument("--json", action="store_true", help="emit ExecutionResult as JSON")
    args = p.parse_args(argv)

    try:
        from blpremote_client.llm import DEFAULT_MODEL, ask
    except ImportError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    host = RemoteHost()
    out = ask(host, args.prompt, service=args.service, model=args.model or DEFAULT_MODEL)
    plan, explain = out["plan"], out["explain"]
    _print_plan(plan, explain)

    if args.dry_run:
        return 0
    if not args.yes and not _confirm():
        print("aborted.")
        return 1

    result = host.execute(plan)
    if args.json:
        print(json.dumps(result.model_dump(), indent=2, default=str))
    else:
        print(f"\nstatus: {result.status}")
        if result.warnings:
            print(f"warnings: {result.warnings}")
        print(f"data: {json.dumps(result.data, indent=2, default=str)[:2000]}")
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
