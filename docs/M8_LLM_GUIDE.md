# M8 — LLM-assisted query builder

> Status: M8(A) ask() core landed @ 3ef7de7. M8(B) CLI demo + this guide on `mac/m8-B-cli`.

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

## Install

`anthropic` is an optional dependency:

```sh
pip install -e packages/blpremote_client[llm]
# or just
pip install anthropic
```

Without `anthropic`, `from blpremote_client.llm import ask` works
fine — the import is lazy. Calling `ask()` raises `ImportError`
with an install hint pointing at the `[llm]` extra.

## Auth

Resolution order (matches the M5(C) identity pattern so callers
don't have to think about which knob wins):

1. `api_key=` kwarg passed to `ask()`
2. `ANTHROPIC_API_KEY` env var
3. `~/.blpremote/anthropic.json` with `{"api_key": "sk-ant-..."}`

If none of those resolve, `ask()` raises `RuntimeError` with a
message that names all three sources.

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
- `--model claude-sonnet-4-6` — override the default
  `claude-haiku-4-5` for harder prompts.

## Architecture

### Anthropic Messages API + structured outputs

```python
client.messages.parse(
    model="claude-haiku-4-5",
    system=[safety_block, schema_block_with_cache_control],
    messages=[{"role": "user", "content": prompt}],
    output_format=LLMPlanResponse,  # Pydantic model
)
```

`messages.parse()` validates the model's JSON output against the
Pydantic shape and returns a parsed instance. No prefill juggling,
no manual JSON decode. If the model emits something off-shape the
SDK raises before our code sees it.

### Prompt caching

The system message has two text blocks:

1. **Safety prompt** — hard rules (refuse trade execution / order
   routing / synthetic data / unknown services), op-shape reference,
   datetime conventions. Stable bytes — never changes between
   requests.
2. **Service schema** — JSON-rendered with `sort_keys=True` for
   determinism, gated by `cache_control: {"type": "ephemeral"}`.

The cache breakpoint at the end of block 2 covers everything before
it, so the only varying tail is the user's prompt in `messages`.
Schema bytes change only when BBG renegotiates services
(~never per request) so cache hits are the steady state and per-call
cost drops ~90%.

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

## Where to extend

- **Multi-service prompts**: `service=` is a single string. A
  prompt like "AAPL last price and field info for PX_LAST" needs
  schemas from both `//blp/refdata` and `//blp/apiflds`. Either
  feed both in the system message or do two `ask()` calls.
- **Few-shot examples**: the safety prompt has op shapes but no
  full-plan examples. Adding 1-2 worked examples in the system
  message would help on edge-case prompts (e.g. options chains,
  intraday across DST boundaries). Cost: extra cached tokens.
- **Model auto-pick**: `claude-haiku-4-5` is the right default for
  this constrained transcription task. Hard prompts (multi-leg
  request building, complex date math) probably want
  `claude-sonnet-4-6` — could route automatically based on prompt
  length or detected complexity.

## Tests

`tests/test_llm.py` (18 tests) covers system block layout,
`cache_control` placement, request args, the auth-isolation
invariant, the API key resolution chain, and the ImportError path.
`tests/test_cli.py` (8 tests) covers CLI flow — dry-run, confirm,
`--yes`, `--service`, `--json`, ImportError. Both run without
network and without `anthropic` installed.
