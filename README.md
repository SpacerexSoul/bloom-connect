<div align="center">

# bloom-connect

**Remote Bloomberg API access from macOS, through a thin server on the Windows box where Bloomberg Terminal actually runs.**

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows-lightgrey)](#)
[![Tests](https://img.shields.io/badge/tests-348%20passing-green)](#)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Status](https://img.shields.io/badge/status-9%2F9%20milestones-success)](.coord/PLAN.md)

</div>

---

## The pitch

Bloomberg Terminal runs on Windows. You don't. `bloom-connect` puts a small,
validated FastAPI server in front of `blpapi` on the Windows host, exposes it
over an ngrok tunnel, and ships a Mac client that talks to it like a normal
Python SDK — pandas DataFrames, polars LazyFrames, streaming subscriptions,
and a natural-language → IR plan helper if you want it.

```
┌─────────────────────────────┐         ┌──────────────────────────────┐
│  macOS client               │  HTTPS  │  Windows server              │
│                             │ ───────►│                              │
│  RemoteHost · IR plan       │         │  blpapi.Session lifecycle    │
│  pandas / polars wrappers   │ ◄───────│  per-corr-id dispatch        │
│  NL → IR via LLM (optional) │         │  schema cache · audit log    │
│  Tkinter Connect window     │         │  /metrics · per-user limits  │
└─────────────────────────────┘         └──────────────────────────────┘
```

Zero `blpapi` install on the Mac. Zero arbitrary-code execution on the
server — every request is a Pydantic-validated `ExecutionPlan` against
a locked IR contract.

---

## Why it exists

- **Bloomberg sits on Windows.** Your dev tooling, your data science stack,
  your model code — most of that lives on macOS or Linux. Rather than dual-boot
  or VM-hop, run a small server on the Bloomberg machine and call it from
  wherever you actually work.
- **Networks are messy.** University firewalls, locked-down trading floors,
  guest Wi-Fi — direct sockets are a pain. ngrok's reserved-domain tunnel
  collapses the network problem into one URL that holds across restarts.
- **The model boundary is the trust boundary.** The server only accepts
  Pydantic-validated execution plans against an allowlisted IR contract.
  There's no `eval`, no shell, no `place_order` op. A misbehaving client
  cannot make the server route a trade — because that surface doesn't exist.

---

## Use cases

| Scenario | What you do |
|---|---|
| **Research notebooks** | `from blpremote_client import RemoteHost; bdh(host, "AAPL US Equity", "PX_LAST", ...)` from your Mac. Plot in matplotlib like any other data. |
| **DataFrames-first analysis** | `pd_history(host, [...], [...], start, end)` returns a wide pandas frame, MultiIndex columns. Polars long-form via `pl_history(...)`. |
| **Streaming dashboards** | `for tick in subscribe(host, "AAPL US Equity", "LAST_PRICE,BID,ASK"): ...` — SSE under the hood, typed exceptions on auth/network failures. |
| **Natural-language exploration** | `ask(host, "AAPL last price")` → an IR plan you can review before executing. Routes through OpenRouter; default `deepseek/deepseek-chat` ≈ $0.0001 per call. |
| **Cross-machine dev workflows** | The same coord channel that bootstraps the system carries messages between dev sessions — useful when the server and client live on different OSes. |

**Read-only by design.** No order routing, no trade execution. The IR
contract does not contain an op for it; the model layer refuses prompts
that ask for it. Research, learning, prototyping — that's the scope.

---

## Quick start

> **Two install tracks per OS.** The bundled `.app` / `.exe` is the
> nicest user experience but doesn't survive on machines running
> corporate EDR (SentinelOne, CrowdStrike, etc.) without code
> signing. The `setup.ps1` / `setup.sh` path is AV-tolerant —
> uses the system Python, normal pip installs, normal `.bat`
> launchers — and is the right pick for university trading-floor
> PCs and other locked-down environments. **Pick the one your
> environment actually allows; both produce the same running app.**

### macOS client

| Track | When to use | Steps |
|---|---|---|
| **Bundled `.app` (preferred)** | Personal / home macOS, no MDM, ok with Gatekeeper warning on first launch | Open `Bloomberg Remote.dmg`, drag to **Applications**, eject. Right-click → Open the first time (unsigned bypass), then double-click like any normal app. |
| **`setup.sh` (EDR-friendly)** | Managed Mac, or you prefer working from source | `git clone … && cd bloom-connect && ./setup.sh` — sets up `.venv`, installs the client, bootstraps `~/.blpremote/`, drops `Connect.command` on your Desktop. |

First launch (either track): the Settings dialog auto-opens. Paste a
**pairing code** from the host (or fill URL / username / password manually
+ optional OpenRouter key), Save. Click **Connect** — green LED, you're
talking to Bloomberg.

Build the `.app` + `.dmg` yourself: `./scripts/build_mac_app.sh`.

### Windows server

Prereq: Bloomberg Terminal installed and logged in. The only thing
you have to install by hand.

| Track | When to use | Steps |
|---|---|---|
| **`setup.ps1` (recommended)** | Anywhere — including university / corporate machines running EDR | `git clone …; cd bloom-connect; .\setup.ps1 -CoordSend mac`. Finds Python (skips the MS Store alias trap), creates venv, installs `blpapi` + server, starts uvicorn + ngrok, posts the public URL to your Mac via the coord channel. |
| **`.exe` installer (personal / home)** | Home PC, no EDR. Wraps the same stack in a real installer with Start Menu shortcut + icon. | Download `Bloomberg-Remote-Server-Setup.exe`, run, double-click the new Start Menu entry. **Won't work behind SentinelOne / CrowdStrike / MDM-managed Defender** — those EDRs block unsigned PyInstaller bundles. |

Optional Server UI either way: double-click `START_SERVER_UI.bat`.
Tkinter window shows status LEDs, ngrok URL with Copy button,
Start/Stop/Send-URL buttons, scrolled logs.

Build the `.exe` yourself: `.\scripts\build_win_exe.ps1` (requires
`choco install innosetup` for the installer wrap; the bundle dir
builds without it).

---

## Programmatic API

The UIs are thin shells over the client SDK. Anything you can do in the
window, you can script:

```python
from blpremote_client import RemoteHost, px_last, ref_data
from blpremote_client.data import bdh, bds
from blpremote_client.dataframes import pd_history, pl_history, pd_bars
from blpremote_client.subscribe import subscribe
from blpremote_client.llm import ask

host = RemoteHost()  # reads ~/.blpremote/identity.json

# Convenience
print(px_last(host, "AAPL US Equity"))
print(ref_data(host, ["AAPL US Equity", "MSFT US Equity"], ["PX_LAST", "VOLUME"]))

# Historical → DataFrame
df = pd_history(host, ["AAPL US Equity", "MSFT US Equity"],
                ["PX_LAST", "VOLUME"], "20260101", "20260131")

# Intraday bars
bars = pd_bars(host, "AAPL US Equity", "TRADE",
               "2026-05-08T14:00:00+00:00", "2026-05-08T20:00:00+00:00", interval=1)

# Streaming
for frame in subscribe(host, "AAPL US Equity", "LAST_PRICE,BID,ASK"):
    print(frame.fields)
    if some_condition: break

# Natural language → IR plan → confirm → execute
out = ask(host, "AAPL last price")
print(out["explain"])
result = host.execute(out["plan"])
```

---

## Architecture cliff notes

- **IR contract (`docs/M2_IR_CONTRACT.md`).** Every request is an
  `ExecutionPlan` — a list of allowlisted ops with Pydantic validation:
  `start_session · open_service · create_request · set · append ·
  send_request · collect_response`. Locked at protocol version 1.1.
- **Session model.** Long-lived `blpapi.Session` per worker with
  reconnect on transient failures; per-correlation-id queues dispatch
  responses to the right caller.
- **Schema cache.** Server-side LRU keyed on service name, ETag-validated;
  client-side `RemoteHost.get_schema` cache makes second-and-onwards
  calls sub-millisecond.
- **Streaming.** Server-Sent Events on `/v1/subscribe`, single topic
  per connection, ~25 s heartbeat ping. Client iterator filters pings
  by default; typed exceptions for 401 / 5xx / connect errors.
- **Request cache.** LRU + TTL keyed on `ir_hash`; identical replays
  inside the TTL serve from cache in tens of ms instead of the 800ms
  BBG round-trip. Counters on `/metrics`.
- **Observability.** JSON structured logging, JSONL audit per request
  (with `ir_hash`, `result_hash`, `cache_hit`), Prometheus `/metrics`,
  per-user rate-limit counters labelled by user identity.
- **Auth.** JWT bearer tokens; secret refuses default sentinel at boot
  unless `BLPREMOTE_ALLOW_DEFAULT_SECRET=1`. Token-bucket rate limit
  on `/v1/execute` (300/min × 30 burst). Identity file
  `~/.blpremote/identity.json` shared by `RemoteHost` and the coord CLI.
- **LLM auth-isolation.** The model emits a plan with no `auth` field
  (`_PlanWithoutAuth` Pydantic shape); `ask()` injects the token
  post-parse so the model never sees credentials. Tested as a security
  invariant.

---

## Install & build

### Run from source

```bash
# Mac client
./setup.sh
.venv/bin/python tools/blpremote-client-ui.py

# Windows server
.\setup.ps1
START_SERVER_UI.bat
```

### Build the macOS .app + .dmg installer

```bash
./scripts/build_mac_app.sh
# → build_artifacts/dist/Bloomberg Remote.app       (~40 MB)
# → build_artifacts/Bloomberg Remote.dmg            (~20 MB)
```

The script uses a clean isolated `.venv-build` so PyInstaller doesn't
pick up unrelated site-packages from system Python (a naïve build
ballooned to 2 GB before this fix).

### Build the Windows .exe installer

```powershell
.\scripts\build_win_exe.ps1
# → build_artifacts\dist\Bloomberg Remote Server\
# → build_artifacts\Bloomberg-Remote-Server-Setup.exe (with Inno Setup installed)
```

Requires `choco install innosetup` on the build box.

---

## Common questions

<details>
<summary><b>The <code>.exe</code> installer launched and immediately closed. What happened?</b></summary>

Almost certainly your corporate / university EDR (SentinelOne,
CrowdStrike, MDM-managed Defender). Unsigned PyInstaller bundles
trip behavioural detection on those products and get killed silently
before the Tk window paints.

Two fixes:
1. **Use `setup.ps1` instead** — the AV-tolerant track. Same stack
   underneath, no PyInstaller-wrapped binary, gets along with EDR.
2. **Get an exclusion** from your IT team. Often a pain to obtain
   for personal-use software; usually faster to just take track 1.

Code signing (EV cert, ~$200/yr) is the only general fix; deferred
until distribution outside the original two-box setup becomes a
real need.

</details>

<details>
<summary><b>Why ngrok, not just open a firewall port?</b></summary>

University networks, locked-down corporate networks, and home routers
all make direct sockets painful. ngrok's reserved-domain feature gives
a stable HTTPS URL that holds across server restarts, with no firewall
rules to maintain. The trade-off is the free-tier dependency; swap to
Cloudflare Tunnel or a self-hosted tunnel if that's a concern.

</details>

<details>
<summary><b>Can this place trades?</b></summary>

**No.** There is no `place_order` op in the IR contract. The model
layer (for natural-language queries) is explicitly instructed to
refuse trade-execution prompts. The validator is the trust boundary —
adding a trade op would require server-side code changes, server tests,
and a protocol-version bump. This is research / learning / prototyping
tooling.

</details>

<details>
<summary><b>What about latency?</b></summary>

Cold path: ngrok edge → Windows box → blpapi → BBG.
Typical reference-data request: 200–500 ms wall clock, dominated by
the BBG round-trip (server itself processes in 30–80 ms).
Identical replays inside the TTL window: ~40 ms (request cache hit).
Streaming: ~50 ms tick-to-client.

This is not low-latency trading infrastructure. It's research tooling
that talks to a terminal that talks to Bloomberg's backbone.

</details>

<details>
<summary><b>Does the LLM see my Bloomberg credentials?</b></summary>

No. The model emits a plan with a `_PlanWithoutAuth` shape — no `auth`
field exists in the model's output schema, so it physically cannot
include a token. `ask()` injects the caller's bearer token after the
model returns, before the plan reaches `host.execute()`. There's a
test (`test_llm_plan_shape_has_no_auth_field`) that asserts the auth
field is absent; it fails loudly if anyone adds it.

</details>

<details>
<summary><b>Which LLM model?</b></summary>

Default: `deepseek/deepseek-chat` via OpenRouter. ≈ $0.0001 per call
for the typical "AAPL last price"-shaped prompt. Override with
`model=` for harder prompts (`anthropic/claude-haiku-4-5`,
`openai/gpt-4o-mini`, etc — anything OpenRouter exposes). The IR
fixer (`_normalise_array_paths`) deterministically corrects the one
known drift mode where cheap models emit `set` for array fields.

Full docs: `docs/M8_LLM_GUIDE.md`.

</details>

<details>
<summary><b>I see <code>using insecure default JWT secret</code> at the server. Is that bad?</b></summary>

Yes — set `BLPREMOTE_SECRET_KEY` to a real value in production, or
opt into the default for dev with `BLPREMOTE_ALLOW_DEFAULT_SECRET=1`.
The server refuses to boot with the default secret by default; this
is by design (M5(A) hardening). Future M10 work auto-generates a
random secret on first `setup.ps1` run.

</details>

<details>
<summary><b>Is there a Linux client?</b></summary>

The Mac client should run on Linux as-is (it's pure Python + Tkinter),
but it's not tested and `setup.sh` is macOS-flavoured (Homebrew hints,
`open` for the .command launcher). The `.app` bundling is Mac-specific;
Linux users would run from source.

</details>

<details>
<summary><b>Why two packages instead of one?</b></summary>

`blpremote_client` has no `blpapi` dependency — it ships pure Python
+ httpx + pydantic. `blpremote_server` depends on `blpapi`, which is
Windows-only and ~200 MB. Splitting them means a Mac install never
needs Bloomberg's SDK; the Mac install set is 40 MB total bundled.

</details>

---

## Development

```bash
# Per-package (the project's actual testing pattern)
pytest packages/blpremote_client/tests -q   # 179 + IR-fixer + UI tests
pytest packages/blpremote_server/tests -q   # 175 + 5 skipped (Win-gated)

# Lint / format
ruff check . && black --check .
```

Tests are isolated per package — each has its own `conftest.py` that
adds its own `src/` to `sys.path`. There's no top-level monorepo
pytest config; running `pytest` from the repo root with both
directories together hits a module-name collision (both packages have
a `test_ui.py`).

CI is intentionally out of scope. Two-person project, both sides
verify before merging to `main`. The plan scoreboard at
[`.coord/PLAN.md`](.coord/PLAN.md) records who verified what.

---

## Repository layout

```
bloom-connect/
├── README.md                         # this file
├── setup.ps1                         # Windows server one-shot bring-up
├── setup.sh                          # macOS client one-shot bring-up
├── Connect.command                   # macOS double-click launcher (client UI)
├── START_SERVER_UI.bat               # Windows double-click launcher (server UI)
├── scripts/
│   ├── build_mac_app.sh              # PyInstaller + hdiutil → .app + .dmg
│   └── build_win_exe.ps1             # PyInstaller + Inno Setup → .exe installer
├── packages/
│   ├── blpremote_server/             # FastAPI server (Windows side)
│   │   └── src/blpremote_server/
│   │       ├── app.py · session.py
│   │       ├── audit.py · metrics.py · request_cache.py · rate_limit.py
│   │       └── ui.py                 # Server Tkinter UI controller
│   └── blpremote_client/             # client SDK (macOS side)
│       └── src/blpremote_client/
│           ├── host.py · models.py
│           ├── data.py · dataframes.py · subscribe.py
│           ├── llm.py · cli.py
│           └── ui.py                 # Client Tkinter UI controller
├── tools/
│   ├── coord.py                      # cross-machine dev channel
│   ├── blpremote-client-ui.py        # client UI entry
│   ├── blpremote-server-ui.py        # server UI entry
│   └── m9_mockups/                   # design previews (throwaway)
├── docs/
│   ├── M2_IR_CONTRACT.md             # locked IR contract
│   ├── M8_LLM_GUIDE.md               # NL → IR deep-dive
│   ├── M9_UI_PLAN.md                 # UI design + state machine
│   ├── M10_ONBOARDING_PLAN.md        # installer + onboarding design
│   ├── NGROK_SETUP.md · WINDOWS_SETUP.md
│   └── screenshots/
└── .coord/
    └── PLAN.md                       # milestone scoreboard
```

---

## License

MIT — see [LICENSE](LICENSE).
