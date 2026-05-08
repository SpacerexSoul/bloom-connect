"""Natural-language → validated Bloomberg IR plan.

One-shot translation: the user describes a query in English, an LLM
emits a JSON plan that's validated against the same Pydantic
``ExecutionPlan`` model the server uses, and the caller decides whether
to run it. The validator stays the trust boundary — the LLM is just
sugar on top.

Design points:

- **OpenRouter as the default gateway.** OpenRouter speaks the
  OpenAI Chat Completions API and routes to dozens of models, so
  the same call works against DeepSeek, Llama, Qwen, Gemini, or
  Anthropic — pick whichever is cheap enough for the task without
  rewriting client code. Default model is ``deepseek/deepseek-chat``:
  ~$0.14/$0.28 per 1M (vs claude-haiku-4-5 at $1/$5), and it's
  perfectly capable at constrained JSON transcription. Override via
  ``model=`` for harder prompts (``anthropic/claude-haiku-4-5``,
  ``openai/gpt-4o-mini``, etc — anything OpenRouter exposes).

- **Structured output via ``client.beta.chat.completions.parse()``.**
  The OpenAI SDK accepts a Pydantic model as ``response_format`` and
  returns a parsed instance. No prefill juggling, no manual JSON
  decode. If the model emits something off-shape the SDK raises
  before our code sees it.

- **Auth stripped.** The LLM never sees or invents an auth token —
  the model emits a plan WITHOUT the auth field, and ``ask()``
  injects ``host._get_token()`` after parsing. Keeps secrets out of
  LLM context entirely. Tested as a security invariant.

- **Safety system prompt.** Per Krishna's hard rule (no live trades,
  no order routing, research/learning only) the system prompt
  instructs the model to refuse trade-execution requests. The
  validator is the ultimate guard, but a refusal at the model layer
  is cheaper and produces a clearer error.

- **Optional dep.** ``openai`` lives behind a lazy import. Without
  it the module imports fine but ``ask()`` raises ImportError with an
  install hint pointing at the ``[llm]`` extra.

Usage::

    from blpremote_client import RemoteHost
    from blpremote_client.llm import ask

    host = RemoteHost()
    out = ask(host, "AAPL last price")
    print(out["explain"])         # "Retrieves the last price for AAPL US Equity."
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


# Default schema target. The LLM only sees one service's schema per
# call — covers >95% of typical refdata / historical / intraday /
# field-info queries. Callers can override via ``service=``.
DEFAULT_SERVICE = "//blp/refdata"

# OpenRouter base URL — same surface as OpenAI's, fronted with model
# routing across providers. Override via ``base_url=`` if pointing at
# a different OpenAI-compatible gateway (e.g. direct DeepSeek or a
# self-hosted vLLM endpoint).
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

# DeepSeek's chat model is the right default for this task: cheap,
# fast, and reliable at JSON-schema-constrained transcription. About
# 50× cheaper per call than claude-haiku-4-5 for our prompt+output
# size. Override via ``model=`` for harder prompts.
DEFAULT_MODEL = "deepseek/deepseek-chat"


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
    """Structured-output response. The model produces this shape; the
    OpenAI SDK validates and returns a parsed instance."""

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

FIELD NAMES: use Bloomberg mnemonics, not natural-language paraphrases.
"last price" → PX_LAST (NOT LAST_PRICE). "volume" → VOLUME. "bid" →
PX_BID. "ask" → PX_ASK. "open" → PX_OPEN. "high" → PX_HIGH. "low" →
PX_LOW. "close" → PX_LAST or PX_CLOSE. When unsure, prefer PX_LAST.

Datetime format for intraday endpoints: ISO 8601 with timezone
(e.g. "2026-05-08T14:00:00+00:00"). Date format for historical
endpoints: YYYYMMDD (e.g. "20260508").
"""


def _service_schema_block(service: str, schema: dict[str, Any]) -> str:
    """Render the cached schema as a system block — bounded length,
    deterministic ordering for stability across calls."""
    return (
        f"## Schema for {service}\n\n"
        f"{json.dumps(schema, indent=2, sort_keys=True)}"
    )


def _build_system(service: str, schema: dict[str, Any]) -> str:
    """Single combined system message: safety prompt then schema.

    OpenRouter / OpenAI accepts a single string for ``role: system``;
    we concatenate so providers that auto-cache prefixes (e.g. some
    behind OpenRouter) can match identical bytes across calls. The
    user's prompt at the end of ``messages`` is the only varying tail.
    """
    return f"{_SAFETY_SYSTEM}\n\n{_service_schema_block(service, schema)}"


# ── API key resolution ──────────────────────────────────────────────


def _resolve_api_key(api_key: Optional[str]) -> str:
    """Resolution order: explicit kwarg > env > ~/.blpremote/openrouter.json
    {"api_key": "..."}. Mirrors the M5(C) identity pattern so callers
    never have to think about which knob wins.
    """
    if api_key:
        return api_key
    env = os.environ.get("OPENROUTER_API_KEY")
    if env:
        return env
    from pathlib import Path

    cred_file = Path.home() / ".blpremote" / "openrouter.json"
    if cred_file.exists():
        try:
            data = json.loads(cred_file.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("api_key"), str):
                return data["api_key"]
        except (OSError, json.JSONDecodeError):
            pass
    raise RuntimeError(
        "No OpenRouter API key found. Pass api_key=..., set "
        "OPENROUTER_API_KEY in the env, or write "
        "~/.blpremote/openrouter.json with {\"api_key\": \"sk-or-...\"}."
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
    base_url: str = DEFAULT_BASE_URL,
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
        model:       OpenRouter model id. Default ``deepseek/deepseek-chat``;
                     override with anything OpenRouter exposes (e.g.
                     ``anthropic/claude-haiku-4-5``, ``openai/gpt-4o-mini``,
                     ``meta-llama/llama-3.3-70b-instruct``).
        temperature: 0.0 by default for deterministic translation.
        api_key:     OpenRouter API key. Falls back to env, then file.
        base_url:    OpenAI-compatible endpoint. Default OpenRouter; can
                     be repointed at any compatible gateway.
        max_tokens:  Output cap. 2048 is plenty for any single plan.
        _client:     Test hook — inject a fake OpenAI-shaped client.

    Returns:
        ``{"plan": ExecutionPlan, "explain": str}``. The plan has the
        caller's auth token already injected.

    Raises:
        ImportError:        ``openai`` not installed.
        RuntimeError:       No API key resolvable.
        ValidationError:    Model emitted a plan that doesn't fit the
                            ExecutionPlan shape (Pydantic raises).
        openai.APIError:    Network / auth / refusal at the API layer.
    """
    if _client is None:
        try:
            from openai import OpenAI
        except ImportError as e:
            raise ImportError(
                "blpremote_client.llm requires the `openai` package. "
                "Install with: pip install openai\n"
                "Or, if you installed blpremote_client editable, add the "
                "extra: pip install -e packages/blpremote_client[llm]"
            ) from e
        client = OpenAI(api_key=_resolve_api_key(api_key), base_url=base_url)
    else:
        client = _client

    # Pull the schema we'll feed as context. M2(e) caches client-side
    # via ETag, so this is sub-ms after the first call per service.
    schema_body, _etag = host.get_schema(service)
    schema_payload = schema_body if schema_body is not None else {}

    system_text = _build_system(service, schema_payload)
    response = client.beta.chat.completions.parse(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system_text},
            {"role": "user", "content": prompt},
        ],
        response_format=LLMPlanResponse,
    )
    parsed: LLMPlanResponse = response.choices[0].message.parsed

    # Attach auth post-parse — the model never saw it, never could.
    full_plan = ExecutionPlan(
        protocol_version=parsed.plan.protocol_version,
        auth=AuthToken(token=host._get_token()),
        ops=parsed.plan.ops,
        limits=parsed.plan.limits,
    )
    return {"plan": full_plan, "explain": parsed.explain}
