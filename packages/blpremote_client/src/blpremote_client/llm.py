"""Natural-language → validated Bloomberg IR plan.

One-shot translation: the user describes a query in English, the model
emits a JSON plan that's validated against the same Pydantic
``ExecutionPlan`` model the server uses, and the caller decides whether
to run it. The validator stays the trust boundary — the LLM is just
sugar on top.

Design points:

- **Anthropic Messages API + ``messages.parse()`` for structured output.**
  We pass a Pydantic ``LLMPlanResponse`` as ``output_format`` and the SDK
  returns a parsed instance (or raises). No prefill juggling, no manual
  JSON-decode-then-validate. The schema is sent to the model as part of
  request construction.

- **Prompt caching on the system + schema block.** The schema cache from
  M2(d) changes only when BBG renegotiates services (~never per
  request); we put the schema in the system message with
  ``cache_control: ephemeral`` so the steady-state per-call cost drops
  ~90%. The user's prompt is the only varying tail.

- **Safety system prompt.** Per Krishna's hard rule (no live trades, no
  order routing, research/learning only) the system prompt instructs
  the model to refuse trade-execution requests. The validator is the
  ultimate guard, but a refusal at the model layer is cheaper and
  produces a clearer error.

- **Auth stripped.** The model never sees or invents an auth token —
  the LLM emits a plan WITHOUT the auth field, and ``ask()`` injects
  ``host._get_token()`` after parsing. Keeps secrets out of LLM
  context entirely.

- **Optional dep.** ``anthropic`` lives behind a lazy import. Without
  it the module imports fine but ``ask()`` raises ImportError with an
  install hint.

Usage::

    from blpremote_client import RemoteHost
    from blpremote_client.llm import ask

    host = RemoteHost()
    out = ask(host, "AAPL last price")
    print(out["explain"])         # "Reference data for AAPL US Equity, PX_LAST"
    result = host.execute(out["plan"])
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from pydantic import BaseModel, Field

from blpremote_client.host import RemoteHost
from blpremote_client.models import (
    AuthToken,
    ExecutionPlan,
    Op,
    PlanLimits,
)


# Default schema target. Anthropic only sees one service's schema per
# call — covers >95% of typical refdata / historical / intraday /
# field-info queries. Callers can override via ``service=``.
DEFAULT_SERVICE = "//blp/refdata"

# claude-haiku-4-5 is the right default for this task: constrained
# transcription against a fixed schema, no heavy reasoning. Cheap +
# fast. Override via ``model=`` for hard prompts.
DEFAULT_MODEL = "claude-haiku-4-5"


# ── Internal Pydantic shapes ─────────────────────────────────────────


class _PlanWithoutAuth(BaseModel):
    """Same shape as ``ExecutionPlan`` minus the auth token.

    The LLM emits this; ``ask()`` adds the auth post-parse so the
    model never sees or invents a token.
    """

    protocol_version: str = "1.1"
    ops: list[Op]
    limits: PlanLimits = Field(default_factory=PlanLimits)


class LLMPlanResponse(BaseModel):
    """Anthropic's ``output_format``. The model produces this shape;
    the SDK validates and returns a parsed instance."""

    plan: _PlanWithoutAuth
    explain: str = Field(
        description=(
            "One-line, human-readable explanation of what the plan "
            "will do — shown to the user for confirmation before "
            "execute(). Examples: 'Reference data for AAPL US Equity, "
            "PX_LAST', 'Historical daily prices for MSFT 2026-01-01 "
            "through 2026-04-30'."
        )
    )


# ── System prompt ────────────────────────────────────────────────────


_SAFETY_SYSTEM = """\
You convert natural-language Bloomberg data queries into validated IR
plans (the schema is given below). Output only the plan + a one-line
explanation; the validator and the user decide whether to run it.

HARD RULES — refuse and explain instead of producing a plan if any
of these apply:
- Trade execution / order routing / "buy", "sell", "place an order",
  "execute trade". This system is read-only research/learning.
  Refuse with: "This system is read-only — it cannot route orders or
  execute trades. Reformulate as a data query (e.g. 'last price',
  'order book', 'historical')."
- Synthetic / hypothetical / made-up data ("pretend AAPL is at $500").
  Refuse — only real BBG queries.
- Service or request types not in the schema below. Refuse rather
  than emit an unvalidated plan.

PROTOCOL VERSION: 1.1.

OP SHAPES (build a list of these in `ops`):

  start_session             {"op": "start_session"}
  open_service              {"op": "open_service", "service": "//blp/..."}
  create_request            {"op": "create_request", "service": "//blp/...",
                             "request": "<RequestType>", "id": "<local-id>"}
  set                       {"op": "set", "id": "<local-id>",
                             "path": "<element-name>", "value": <scalar>}
  append                    {"op": "append", "id": "<local-id>",
                             "path": "<element-name>", "value": <scalar>}
  send_request              {"op": "send_request", "id": "<local-id>",
                             "correlation_id": "<cid>"}
  collect_response          {"op": "collect_response",
                             "correlation_id": "<cid>",
                             "timeout_ms": 10000}

Use `set` for scalar single-value elements (security, eventType,
interval, startDateTime, etc). Use `append` for array elements
(securities, fields, eventTypes, id). Always finish with
`send_request` then `collect_response` using the same correlation_id.

Plans MUST start with start_session + open_service. The id field on
create_request is your own local label (e.g. "r1") — used to bind
later ops to that request.

Datetime format for intraday endpoints: ISO 8601 with timezone
(e.g. "2026-05-08T14:00:00+00:00"). Date format for historical
endpoints: YYYYMMDD (e.g. "20260508").
"""


def _service_schema_block(service: str, schema: dict[str, Any]) -> str:
    """Render the cached schema as a system block — bounded length,
    deterministic ordering for cache stability."""
    return (
        f"## Schema for {service}\n\n"
        f"{json.dumps(schema, indent=2, sort_keys=True)}"
    )


def _build_system(service: str, schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Two-block system message: stable safety prompt first, then the
    schema with ``cache_control``. Both are hashed into the cache
    prefix; only the user's prompt at the end of ``messages`` varies.
    """
    return [
        {"type": "text", "text": _SAFETY_SYSTEM},
        {
            "type": "text",
            "text": _service_schema_block(service, schema),
            # Caches everything up to and including this block.
            "cache_control": {"type": "ephemeral"},
        },
    ]


# ── API key resolution ──────────────────────────────────────────────


def _resolve_api_key(api_key: Optional[str]) -> str:
    """Resolution order: explicit kwarg > env > ~/.blpremote/anthropic.json
    {"api_key": "..."}. Mirrors the M5(C) identity pattern so callers
    never have to think about which knob wins.
    """
    if api_key:
        return api_key
    env = os.environ.get("ANTHROPIC_API_KEY")
    if env:
        return env
    from pathlib import Path

    cred_file = Path.home() / ".blpremote" / "anthropic.json"
    if cred_file.exists():
        try:
            data = json.loads(cred_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("api_key"), str):
                return data["api_key"]
        except (OSError, json.JSONDecodeError):
            pass
    raise RuntimeError(
        "No Anthropic API key found. Pass api_key=..., set "
        "ANTHROPIC_API_KEY in the env, or write "
        "~/.blpremote/anthropic.json with {\"api_key\": \"sk-ant-...\"}."
    )


# ── Public surface ───────────────────────────────────────────────────


def ask(
    host: RemoteHost,
    prompt: str,
    *,
    service: str = DEFAULT_SERVICE,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.0,
    api_key: Optional[str] = None,
    max_tokens: int = 2048,
    _client: Any = None,
) -> dict[str, Any]:
    """Translate a natural-language prompt into a validated ExecutionPlan.

    Args:
        host:        Connected RemoteHost (used for schema fetch + auth token).
        prompt:      Natural-language query (e.g. "AAPL last price").
        service:     BBG service to scope the schema context.
                     Default ``//blp/refdata`` covers refdata + historical
                     + intraday. Use ``//blp/apiflds`` for FieldInfo queries.
        model:       Anthropic model id. Default ``claude-haiku-4-5``;
                     bump to ``claude-sonnet-4-6`` for hard prompts.
        temperature: 0.0 by default for deterministic translation.
        api_key:     Anthropic API key. Falls back to env, then file.
        max_tokens:  Output cap. 2048 is plenty for any single plan.
        _client:     Test hook — inject a fake Anthropic client.

    Returns:
        ``{"plan": ExecutionPlan, "explain": str}``. The plan has the
        caller's auth token already injected.

    Raises:
        ImportError:        ``anthropic`` not installed.
        RuntimeError:       No API key resolvable.
        ValidationError:    Model emitted a plan that doesn't fit the
                            ExecutionPlan shape (Pydantic raises).
        anthropic.APIError: Network / auth / refusal at the API layer.
    """
    if _client is None:
        try:
            import anthropic  # type: ignore[import-not-found]
        except ImportError as e:
            raise ImportError(
                "blpremote_client.llm requires the `anthropic` package. "
                "Install with: pip install anthropic\n"
                "Or, if you installed blpremote_client editable, add the "
                "extra: pip install -e packages/blpremote_client[llm]"
            ) from e
        client = anthropic.Anthropic(api_key=_resolve_api_key(api_key))
    else:
        client = _client

    # Pull the schema we'll feed as context. M2(e) caches client-side
    # via ETag, so this is sub-ms after the first call per service.
    schema_body, _etag = host.get_schema(service)
    schema_payload = schema_body if schema_body is not None else {}

    response = client.messages.parse(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=_build_system(service, schema_payload),
        messages=[{"role": "user", "content": prompt}],
        output_format=LLMPlanResponse,
    )
    parsed: LLMPlanResponse = response.parsed_output

    # Attach auth post-parse — the model never saw it, never could.
    full_plan = ExecutionPlan(
        protocol_version=parsed.plan.protocol_version,
        auth=AuthToken(token=host._get_token()),
        ops=parsed.plan.ops,
        limits=parsed.plan.limits,
    )
    return {"plan": full_plan, "explain": parsed.explain}
