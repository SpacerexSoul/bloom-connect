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
| M1 | Session lifecycle: long-lived + reconnect + honest `/health` + sub PoC | win   | review   | win/m1-session-lifecycle | mac-side verification done 2026-05-06: /health new shape via ngrok ✓, ref_data round-trip matches win's smoke ✓, subscriptions not exposed on client (M3 input) ✓; 41/41 tests pass — clear to merge |
| M2 | Generalize IR for all request types + schema cache | split | idle     |        | mac: client+tests · win: server+live verify · validator becomes trust boundary (LLM dep) · cache `SchemaRequest`/`FieldInfo` at first hit |
| M3 | Streaming subscriptions over SSE                   | split | idle     |        | win: server · mac: client |
| M4 | Request cache + structured logging + /metrics + JSONL audit | mac   | idle     |        | LRU+TTL · JSON logs · Prometheus · one-line-per-execute audit log (ts, user, req_id, compact IR, result hash, elapsed) |
| M5 | Auth hardening (JWT secret, rate limits)           | mac   | idle     |        | env-driven secret, per-user limits |
| M6 | `setup.ps1` (python, venv, DLL, server+ngrok)      | win   | idle     |        | pushes live ngrok URL to .coord/ |
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
