# M8 — LLM-assisted query builder

> Status: M8(A) ask() core + (B) blpremote-ask CLI on main. (C) pivot
> from Anthropic-only to OpenRouter on `mac/m8-C-openrouter-pivot`.

`blpremote_client.llm.ask()` translates an English query into a
validated Bloomberg IR plan that the same `RemoteHost.execute()`
runs. The validator stays the trust boundary — the LLM is sugar on
top of the contract locked in `M2_IR_CONTRACT.md`.

## Why this exists

Writing IR plans by hand is fine but tedious. `bdh(host, "AAPL US
Equity", "PX_LAST", "20260501", "20260508")` is concise; the
equivalent `ExecutionPlan` is seven ops. For exploratory work where
the user knows what they want in English but doesn't know the BBG
field name or request type yet, a one-shot translator that returns
*the same plan you would have written by hand* is a real ergonomic
win — and because the validator runs on every plan the model emits,
there's no novel security surface.

## Why OpenRouter, not Anthropic-direct

OpenRouter is an OpenAI-compatible gateway that fronts dozens of
providers. Three concrete wins:

1. **Cost.** This task is constrained JSON transcription against a
   fixed schema — it does not need a frontier model. Default model
   is `deepseek/deepseek-chat` at ~$0.14/$0.28 per 1M tokens, which
   is roughly 50× cheaper per call than `claude-haiku-4-5`. Same
   structure, same fields, same plan shape — for a fraction of the
   spend.
2. **Flexibility.** Override `model=` with anything OpenRouter
   exposes — `anthropic/claude-haiku-4-5`, `openai/gpt-4o-mini`,
   `meta-llama/llama-3.3-70b-instruct`, etc. — without rewriting
   client code. If a particular query needs a smarter model, swap
   the string and try again.
3. **Single SDK surface.** The `openai` SDK is widely installed and
   battle-tested; `client.beta.chat.completions.parse()` accepts a
   Pydantic model as `response_format` and returns a parsed
   instance, so the validation flow is identical to what we'd
   have with Anthropic structured outputs.

If you want Anthropic-direct (e.g. for prompt-cache pricing on the
schema block), point `base_url=` at `https://api.anthropic.com/v1/`
and pass an Anthropic key — the OpenAI SDK speaks Anthropic's
OpenAI-compat endpoint with no other changes.

## Install

`openai` is an optional dependency:

```sh
pip install -e packages/blpremote_client[llm]
# or just
pip install openai
```

Without `openai`, `from blpremote_client.llm import ask` works fine
— the import is lazy. Calling `ask()` raises `ImportError` with an
install hint pointing at the `[llm]` extra.

## Auth

Resolution order (matches the M5(C) identity pattern so callers
don't have to think about which knob wins):

1. `api_key=` kwarg passed to `ask()`
2. `OPENROUTER_API_KEY` env var
3. `~/.blpremote/openrouter.json` with `{"api_key": "sk-or-..."}`

If none of those resolve, `ask()` raises `RuntimeError` with a
message that names all three sources.

Get a key at https://openrouter.ai/ — they sell credit by the
dollar; deepseek-chat at the default prompt size costs ≈ $0.0001
per call.

## Programmatic use

```python
from blpremote_client import RemoteHost
from blpremote_client.llm import ask

host = RemoteHost()
out = ask(host, "AAPL last price")
print(out["explain"])               # one-line human-readable summary
plan = out["plan"]                  # full ExecutionPlan
result = host.execute(plan)         # same dispatcher as everywhere else
print(result.data)                  # {"AAPL US Equity": {"PX_LAST": ...}}
```

`out` is `{"plan": ExecutionPlan, "explain": str}`. The plan is
already validated against the M2 contract and has the caller's auth
token injected (the model never sees it).

To use a smarter (more expensive) model on a hard prompt:

```python
out = ask(host, "intraday 1-min bars for AAPL on 2026-05-08 between 9:30 and 10:00 ET",
          model="anthropic/claude-haiku-4-5")
```

## CLI

```sh
blpremote-ask "AAPL last price"
```

The CLI prints the plan + the explain string, asks `run this plan?
[y/N]`, and only calls `host.execute()` on `y`. Other flags:

- `--service //blp/apiflds` — use a different schema scope
  (default `//blp/refdata` covers refdata + historical + intraday).
- `--yes` / `-y` — skip the confirm prompt (for scripts).
- `--dry-run` — print the plan and exit without executing.
- `--json` — emit the `ExecutionResult` as JSON.
- `--model anthropic/claude-haiku-4-5` — override the default
  `deepseek/deepseek-chat` for harder prompts.

## Architecture

### Structured output via OpenAI SDK

```python
client = OpenAI(api_key=key, base_url="https://openrouter.ai/api/v1")
client.beta.chat.completions.parse(
    model="deepseek/deepseek-chat",
    messages=[
        {"role": "system", "content": SAFETY + SCHEMA},
        {"role": "user", "content": prompt},
    ],
    response_format=LLMPlanResponse,  # Pydantic model
)
```

`beta.chat.completions.parse()` validates the model's JSON output
against the Pydantic shape and returns a parsed instance. No prefill
juggling, no manual JSON decode. If the model emits something
off-shape the SDK raises before our code sees it.

### System prompt layout

The system message has the safety prompt first, then the service
schema rendered with `sort_keys=True` for byte-stability across
calls. Stable bytes first means providers behind OpenRouter that do
prefix caching (some do, some don't) get a cache hit; the user's
prompt at the end of `messages` is the only varying tail.

### Auth stripped from LLM context

The model emits `_PlanWithoutAuth` — same shape as `ExecutionPlan`
minus the `auth` field. `ask()` post-injects the caller's token
from `host._get_token()` after the SDK returns:

```python
full_plan = ExecutionPlan(
    protocol_version=parsed.plan.protocol_version,
    auth=AuthToken(token=host._get_token()),  # post-parse, never in prompt
    ops=parsed.plan.ops,
    limits=parsed.plan.limits,
)
```

Tested as a security invariant — `test_llm_plan_shape_has_no_auth_field`
asserts `"auth" not in _PlanWithoutAuth.model_fields`. If someone
adds an auth field to that model in the future, the test fails
loudly.

### Schema fed via the M2(e) cache

`host.get_schema(service)` is the same surface refdata uses for
schema-aware request shaping. First call hits the server, subsequent
calls are sub-ms (ETag-keyed client cache). The schema is the only
thing the model needs to know about the service's surface area — by
feeding it as context we don't have to keep the safety prompt in
sync with BBG's request types.

### Hard-rule refusal

Per Krishna's portfolio rule (research/learning only, no live
trades, no automation that places orders) the safety prompt
instructs the model to refuse trade-execution prompts:

> Refuse with: "This system is read-only — it cannot route orders
> or execute trades. Reformulate as a data query (e.g. 'last
> price', 'order book', 'historical')."

The validator is the ultimate guard (no `place_order` op exists in
the IR) but a refusal at the model layer is cheaper and produces a
clearer error.

### Field-name hints

Cheap models occasionally emit `LAST_PRICE` instead of `PX_LAST`
without explicit guidance. The safety prompt now spells out the BBG
mnemonic mapping (`last price → PX_LAST`, `volume → VOLUME`, `bid →
PX_BID`, etc) so we don't get bad fields back.

## Where to extend

- **Multi-service prompts**: `service=` is a single string. A
  prompt like "AAPL last price and field info for PX_LAST" needs
  schemas from both `//blp/refdata` and `//blp/apiflds`. Either
  feed both in the system message or do two `ask()` calls.
- **Few-shot examples**: the safety prompt has op shapes but no
  full-plan examples. Adding 1-2 worked examples in the system
  message would help on edge-case prompts (e.g. options chains,
  intraday across DST boundaries). Cost: extra cached tokens.
- **Auto model selection**: route simple prompts to deepseek-chat
  and complex ones to a smarter model based on prompt length or
  detected complexity. The `model=` kwarg makes this trivially
  upgradeable.

## Tests

`tests/test_llm.py` (19 tests) covers system message layout,
schema-block determinism, the field-mnemonic hint, request args
(model / temperature / response_format / message structure), the
auth-isolation invariant, the API key resolution chain, and the
ImportError path. `tests/test_cli.py` (8 tests) covers CLI flow —
dry-run, confirm, `--yes`, `--service`, `--json`, ImportError. Both
run without network and without `openai` installed.
