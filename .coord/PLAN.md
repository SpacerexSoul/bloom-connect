# Revamp Plan — Scoreboard

Shared truth for the bloom-connect revamp. Either side can edit this
file. Update the **Status** and **Branch** columns as work progresses.

> Status legend: `idle` / `in-progress` / `review` / `done` / `blocked`

## Goals

- **A)** Full BLPAPI coverage — anything `blpapi` can do, the server can do.
- **B)** CV-grade feature set — long-lived session, streaming, cache,
  observability, hardened auth, DataFrame-native client.
- **C)** Stretch — LLM-assisted natural-language query builder.

## Milestones

| ID | Title                                              | Owner | Status   | Branch | Notes |
|----|----------------------------------------------------|-------|----------|--------|-------|
| M0 | Tag current `main` as `legacy-v1`                  | mac   | done     | —      | tagged at the pre-M1 baseline |
| M1 | Session lifecycle: long-lived + reconnect + honest `/health` + sub PoC | win   | done     | win/m1-session-lifecycle (merged f3a34f0) | both sides verified 2026-05-06; merged to main 2026-05-06 |
| M2 | Generalize IR for all request types + schema cache | split | done     | merged via main (a-e all in) | Contract LOCKED at `docs/M2_IR_CONTRACT.md` r2. **(a)** collect_response + warnings + 1.1. **(b)** BLP_FIELD_<CATEGORY>. **(c1)** NormalizedMessage dispatcher. **(c2)** Intraday bar+tick. **(c3)** FieldInfo+Schema. **(d)** SchemaCache + `GET /v1/schema/{service:path}` with ETag/304. **(e)** client surface: `get_bars`, `get_ticks`, `get_field_info`, `get_service_schema` with client-side ETag cache, `RemoteHost.get_schema`. All live-verified end-to-end against win's BBG-connected server. 141/141 tests pass. |
| M3 | Streaming subscriptions over SSE                   | split | done     | merged via main | **server** (win, 5ae869c): `GET /v1/subscribe?topic=&fields=&options=` SSE endpoint with bearer auth, single-topic-per-connection, ~25s heartbeat ping, finally-block cleanup on disconnect. **client** (mac, 376ed60): `subscribe(host, topic, fields, *, options=None, with_pings=False, with_status=True)` iterator wrapping the stream; SSE-spec parser; 401/5xx/connect-error mapped to typed exceptions; pings filtered by default. Live-verified end-to-end against AAPL US Equity (127+ market-data frames in 4s, real BBG fields). 160/160 tests. **Followup parked**: server-side asyncio.to_thread cancel can leave up-to-25s ghost subscription lag — fix is asyncio.Queue with notify-on-event in SessionManager dispatch (M3.5, not blocking). |
| M4 | Request cache + structured logging + /metrics + JSONL audit | mac   | done     | merged via main | **(A)** structured JSON logging + JSONL audit + ir_hash/result_hash + cache_hit field (2baf332, win-verified). **(B)** Prometheus /metrics + route-template label fix (eb46c1d, 655ca0a, win-verified). **(C)** LRU+TTL request cache keyed by ir_hash, hit/miss counters (c9fc9bf, both-side verified 2026-05-08: 867ms→42ms wall on cache hit, counters increment correctly). **(D)** validator now returns warnings list (IR_UNVERIFIED_SERVICE for allowed-but-unsupported services) + per-request required-element validation (e.g. IntradayBarRequest missing 'interval' rejected with clear error). 219/219 tests pass. M2 follow-ups absorbed and closed. |
| M5 | Auth hardening (JWT secret, rate limits, identity) | mac   | done     | merged via main | **(A)** JWT secret hardening — refuse default at boot, opt-in for dev, optional rotate-at-boot (292e7bd, win-verified). **(A-followup)** setup.ps1 default-secret opt-in for dev convenience (829d273, win-driven). **(B)** per-user token-bucket rate limit on /v1/execute, defaults 300/min × 30 burst, blpremote_rate_limited_total{user} counter (7f770b3). **(C)** identity consolidation: single ~/.blpremote/identity.json shared by coord.py + RemoteHost; resolve_identity() with kwargs>env>file priority; legacy coord.json keeps working (d6c0230, mac live-verified). 257/257 tests pass. |
| M6 | `setup.ps1` (python, venv, DLL, server+ngrok)      | win   | review   | merged via main (d9691ec) | One-shot idempotent bring-up; -CoordSend mac auto-posts the new ngrok URL on the live channel after each restart, killing the "did you remember to update the URL" friction. Short-circuit path verified by win against running setup. Full bring-up end-to-end pending a clean-checkout run (would have stomped win's running server during dev). Mac-side review: code reads clean — Python find dodges MS Store alias trap, blpapi DLL path registered, port-free + uvicorn detached + /health gate before ngrok, ngrok URL polled from local 4040 API. Flips to `done` after a clean-machine end-to-end OR observed `-CoordSend mac` URL post in the wild. |
| M7 | pandas/polars client surface                       | mac   | idle     |        | `pd_history()`, `pl_history()` |
| M8 | LLM query builder (stretch)                        | split | idle     |        | NL -> validated IR; client-side confirm |

**Backlog (post-M5, not yet scheduled):**
- Per-API-key (not per-user) rate limits with a key-name field, so we
  can attribute load to specific strategies running in parallel.

## Working agreements

- Branch naming: `mac/m<N>-<slug>` or `win/m<N>-<slug>`.
- Merge to `main` only when:
  1. Tests pass on the touched package, AND
  2. The other side has acked the change on the live coord channel.
- Never force-push to `main`.
- If something looks dangerous (could place an order, leak creds,
  destructive migration), STOP and ping the other side via coord.
- When you finish a milestone, update Status to `done` and post a
  message on coord linking the merge commit.
- **Cross-boundary milestones require both-side verification before
  `done`** (added 2026-05-06 after M1). Server-side green is not
  enough; the client / Mac side must independently exercise the
  contract and post results before the milestone flips from `review`
  to `done`.

## Decisions (locked)

1. **Sequence** — M2 before M3. IR generalization is foundational for
   M4/M5/M8. Subscription PoC folded into M1 (30 lines) to de-risk the
   session model.
2. **LLM piece** — client-side translate-then-show-IR-then-confirm.
   Validator stays the trust boundary; user always sees what's about
   to run.
3. **Daily-driver additions** — reconnect into M1, JSONL audit into
   M4, schema cache into M2. All folded above.

## Stop conditions for autonomous mode

The dev cycle runs autonomously while:
- All milestones are not `done`, AND
- No coord message says `STOP`, AND
- Krishna hasn't intervened in the last N minutes (set by the /loop).

If you (either side) have nothing to do this tick: send one
`standing by` message on coord, then exit silently until next tick.
