# bloom-connect

Remote Bloomberg API access from macOS via a Windows host running
Bloomberg Terminal. The Windows box runs a FastAPI server in front
of `blpapi`; the Mac client sends validated IR plans over HTTPS
(typically via an ngrok tunnel) and gets shaped responses back.

No `blpapi` install on the Mac. No arbitrary code execution on the
server — every request is a Pydantic-validated execution plan
against a locked IR contract.

```
┌─────────────────────────────┐         ┌──────────────────────────────┐
│  macOS client               │  HTTPS  │  Windows server              │
│                             │ ───────►│                              │
│  RemoteHost + IR plan       │         │  blpapi.Session lifecycle    │
│  pandas/polars wrappers     │ ◄───────│  per-corr-id dispatch        │
│  NL→IR via LLM (optional)   │         │  schema cache · audit log    │
│  Tkinter Connect window     │         │  /metrics · rate limit       │
└─────────────────────────────┘         └──────────────────────────────┘
```

## Status

All nine milestones from the revamp shipped. See
[`.coord/PLAN.md`](.coord/PLAN.md) for the scoreboard and
[`docs/`](docs/) for the per-milestone contracts and guides.

| Milestone | Title                                                        |
|-----------|--------------------------------------------------------------|
| M1        | Long-lived session, reconnect, sub PoC                        |
| M2        | Generalised IR + schema cache + dispatcher                    |
| M3        | SSE streaming subscriptions                                   |
| M4        | Audit log + Prometheus `/metrics` + request cache             |
| M5        | JWT secret hardening + per-user rate limit + identity         |
| M6        | One-shot `setup.ps1` server bring-up                          |
| M7        | pandas/polars DataFrame wrappers                              |
| M8        | LLM-assisted query builder (NL → IR via OpenRouter)           |
| M9        | Desktop Connect UI per side (in flight at time of writing)    |

---

## Quick start

### Windows server

1. Install Bloomberg Terminal + log in.
2. Clone this repo, open a PowerShell window, and run:

   ```powershell
   .\setup.ps1 -CoordSend mac
   ```

3. Done. `setup.ps1` finds Python, creates `.venv`, installs
   `blpapi` + the server package, starts uvicorn, starts ngrok,
   waits for `/health`, and posts the public ngrok URL to the Mac
   side via the coord channel.

Useful flags: `-Force` to restart even when healthy, `-NoNgrok` for
LAN-only deployment, `-SkipInstall` for a quick restart. Full
walkthrough in [`docs/WINDOWS_SETUP.md`](docs/WINDOWS_SETUP.md).

### macOS client

```bash
git clone <this repo> && cd bloom-connect
./setup.sh
```

`setup.sh` finds Python ≥3.10, creates `.venv`, installs
`blpremote_client[llm,pandas,polars]`, bootstraps `~/.blpremote/`
identity + OpenRouter key stubs, prompts for the OpenRouter key,
and symlinks `Connect.command` to your Desktop.

Then double-click `~/Desktop/Connect.command` — the Tkinter window
opens, server URL pre-filled from `~/.blpremote/identity.json`,
click **Connect**.

![client UI](docs/screenshots/m9-client-ui.png)

---

## Programmatic use (no UI)

The UI is a thin shell over `blpremote_client.RemoteHost`. Anything
the UI does, you can script:

### Reference data + history (dict API)

```python
from blpremote_client import RemoteHost, px_last, ref_data
from blpremote_client.data import bdh

host = RemoteHost()  # reads ~/.blpremote/identity.json

print(px_last(host, "AAPL US Equity"))
print(ref_data(host, ["AAPL US Equity", "MSFT US Equity"], ["PX_LAST", "VOLUME"]))
print(bdh(host, "AAPL US Equity", "PX_LAST", "20260101", "20260131"))
```

### DataFrames (M7)

```python
from blpremote_client.dataframes import pd_history, pl_history, pd_bars, pl_ticks

df = pd_history(host, ["AAPL US Equity", "MSFT US Equity"],
                ["PX_LAST", "VOLUME"], "20260101", "20260131")
# wide pandas frame, MultiIndex columns (security, field)

lf = pl_history(host, "AAPL US Equity", "PX_LAST", "20260101", "20260131")
# long polars frame: security · field · date · value
```

Both `[pandas]` and `[polars]` are optional extras. Each `pd_*` /
`pl_*` helper lazily imports its dep and raises a clear
`ImportError` with the install hint if it's not there.

### Streaming subscriptions (M3)

```python
from blpremote_client.subscribe import subscribe

for frame in subscribe(host, "AAPL US Equity", "LAST_PRICE,BID,ASK"):
    print(frame.fields)
```

SSE under the hood. Heartbeat pings filtered by default; pass
`with_pings=True` to see them. Network / auth / 5xx are mapped to
typed exceptions.

### Natural language → IR (M8)

```python
from blpremote_client.llm import ask

out = ask(host, "AAPL last price")
print(out["explain"])                # human-readable summary
result = host.execute(out["plan"])   # same dispatcher as everywhere else
print(result.data)
```

Default model is `deepseek/deepseek-chat` via OpenRouter
(~$0.0001/call) — flip the `model=` kwarg to route to any other
OpenRouter-exposed model when a hard prompt needs it. Full guide
in [`docs/M8_LLM_GUIDE.md`](docs/M8_LLM_GUIDE.md). CLI:

```bash
blpremote-ask "AAPL last price"
```

---

## IR contract

The Mac client never executes code on the server — it submits an
`ExecutionPlan` (a list of allowlisted ops) that's validated and
dispatched. Locked at protocol_version `1.1`; the full schema and
op-by-op semantics live in
[`docs/M2_IR_CONTRACT.md`](docs/M2_IR_CONTRACT.md).

```json
{
  "protocol_version": "1.1",
  "request_id": "uuid",
  "auth": { "token": "..." },
  "ops": [
    { "op": "start_session" },
    { "op": "open_service", "service": "//blp/refdata" },
    { "op": "create_request", "service": "//blp/refdata",
      "request": "ReferenceDataRequest", "id": "r1" },
    { "op": "append", "id": "r1", "path": "securities",
      "value": "AAPL US Equity" },
    { "op": "append", "id": "r1", "path": "fields", "value": "PX_LAST" },
    { "op": "send_request", "id": "r1", "correlation_id": "cid1" },
    { "op": "collect_response", "correlation_id": "cid1", "timeout_ms": 10000 }
  ]
}
```

---

## Server endpoints

| Endpoint                             | Method | Purpose                                       |
|--------------------------------------|--------|-----------------------------------------------|
| `/health`                            | GET    | server + Bloomberg session status             |
| `/version`                           | GET    | server version                                |
| `/v1/auth/login`                     | POST   | username + password → bearer token            |
| `/v1/execute`                        | POST   | run an ExecutionPlan                          |
| `/v1/subscribe?topic=&fields=`       | GET    | SSE stream of market data frames              |
| `/v1/schema/{service:path}`          | GET    | Bloomberg service schema (ETag-cached)        |
| `/metrics`                           | GET    | Prometheus exposition                         |
| `/v1/coord/send` · `/v1/coord/inbox` | both   | cross-machine dev coord channel               |

`/v1/execute` returns an `ExecutionResult` shaped:

```json
{
  "request_id": "rq-1",
  "status": "ok",
  "data": { ... },
  "warnings": [ ... ],
  "errors":   [ ... ],
  "server_timing_ms": 42
}
```

`status` is `ok` / `partial` / `error`. Per-request error codes:

| Code                       | Meaning                                                |
|----------------------------|--------------------------------------------------------|
| `AUTH_FAILED`              | invalid credentials or expired token                   |
| `PLAN_INVALID`             | execution plan validation failed                       |
| `BLP_SESSION_FAIL`         | Bloomberg session failed to start                      |
| `BLP_TIMEOUT`              | request timed out                                      |
| `BLP_SECURITY_ERROR`       | invalid security identifier                            |
| `BLP_FIELD_ERROR`          | invalid field                                          |
| `IR_DEPRECATED_OP`         | warning: deprecated op alias used                      |
| `IR_UNVERIFIED_SERVICE`    | warning: service allowed but not in supported list     |

---

## Observability

- **Audit log** (M4-A): every executed plan is appended as JSONL to
  `~/.blpremote/audit.log` with `ir_hash`, `result_hash`, and
  `cache_hit` so you can diff identical replays. Server-side
  structured JSON logging via the standard logger.
- **Prometheus `/metrics`** (M4-B): request counters, latency
  histograms, cache hit/miss counters, per-user rate-limit
  counters. Routes labelled by template, not literal path, so
  `/v1/schema/{service:path}` doesn't blow cardinality.
- **Request cache** (M4-C): LRU+TTL keyed by `ir_hash`. Identical
  replays inside the TTL serve from cache in ~40ms instead of the
  ~800ms BBG round-trip. Counter `blpremote_request_cache_hits_total`
  on `/metrics`.

---

## Security

- **JWT secret hardening** (M5-A): server refuses to boot with the
  default sentinel secret. Opt-in for dev convenience via
  `BLPREMOTE_ALLOW_DEFAULT_SECRET=1`; opt-in to rotate-on-boot via
  `BLPREMOTE_ROTATE_SECRET=1`. Production sets a real secret
  through env or settings file.
- **Per-user rate limit** (M5-B): token bucket on `/v1/execute`,
  default 300 req/min × 30 burst. 429s carry a `Retry-After`
  header. Counter `blpremote_rate_limited_total{user="..."}`.
- **Identity consolidation** (M5-C): one `~/.blpremote/identity.json`
  shared by `coord.py` + `RemoteHost`. Resolution priority is
  kwargs > env (`BLPCOORD_URL` / `_USER` / `_PASS`) > file. Legacy
  `coord.json` keeps reading for backwards compat.
- **LLM context never sees a token** (M8): the model emits a plan
  with no `auth` field (`_PlanWithoutAuth` Pydantic shape); `ask()`
  injects `host._get_token()` post-parse. Tested as a security
  invariant.

---

## Repository layout

```
bloom-connect/
├── README.md                              # this file
├── setup.ps1                              # M6, Windows server one-shot
├── setup.sh                               # M9, macOS client one-shot
├── Connect.command                        # M9, macOS double-click launcher
├── packages/
│   ├── blpremote_server/                  # FastAPI server (Windows side)
│   │   └── src/blpremote_server/
│   │       ├── app.py                     # routes
│   │       ├── session.py                 # blpapi lifecycle + reconnect
│   │       ├── audit.py · metrics.py · request_cache.py · rate_limit.py
│   │       └── ...
│   └── blpremote_client/                  # client SDK (macOS side)
│       └── src/blpremote_client/
│           ├── host.py · models.py        # RemoteHost + IR shapes
│           ├── data.py                    # bdh, bds, ref_data, etc.
│           ├── dataframes.py              # pandas/polars wrappers
│           ├── subscribe.py               # SSE iterator
│           ├── llm.py                     # NL → IR ask()
│           ├── cli.py                     # blpremote-ask
│           └── ui.py                      # blpremote-ui (Tkinter)
├── tools/
│   ├── coord.py                           # cross-machine dev channel
│   ├── blpremote-client-ui.py             # thin entry, equiv to blpremote-ui
│   └── m9_mockups/                        # design previews
├── docs/
│   ├── M2_IR_CONTRACT.md                  # locked IR contract
│   ├── M8_LLM_GUIDE.md                    # ask() + blpremote-ask deep dive
│   ├── M9_UI_PLAN.md                      # UI design + state machine
│   ├── NGROK_SETUP.md · WINDOWS_SETUP.md  # platform notes
│   └── screenshots/
└── .coord/
    └── PLAN.md                            # milestone scoreboard
```

---

## Development

```bash
# Server tests (Windows side)
pytest packages/blpremote_server/tests -q

# Client tests (macOS side)
pytest packages/blpremote_client/tests -q

# Lint / format
ruff check . && black --check .
```

CI is intentionally out of scope — this is a two-person project and
both sides verify before merging to `main`. The plan scoreboard at
[`.coord/PLAN.md`](.coord/PLAN.md) records who verified what.

---

## Coord channel

A small `/v1/coord/send` + `/v1/coord/inbox` pair on the server
lets the Mac and Windows dev sessions talk to each other via the
same FastAPI surface as everything else (bearer auth, identity
file, JSONL audit). `tools/coord.py` is the CLI:

```bash
python tools/coord.py send win "your message"
python tools/coord.py inbox       # drain
python tools/coord.py watch       # tail (used by /loop)
```

It's how `setup.ps1 -CoordSend mac` auto-publishes the new ngrok
URL after every restart — kills the "did you remember to update
the URL" friction.

---

## License

MIT — see [LICENSE](LICENSE).
