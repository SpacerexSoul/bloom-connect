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

| ID | Title                                         | Owner | Status   | Branch | Notes |
|----|-----------------------------------------------|-------|----------|--------|-------|
| M0 | Tag current `main` as `legacy-v1`             | mac   | idle     | —      | snapshot before any breaking change |
| M1 | Long-lived blpapi session + honest `/health`  | win   | idle     |        | needs live BBG to verify |
| M2 | Generalize IR for all request types           | split | idle     |        | mac: client+tests · win: server+live verify |
| M3 | Streaming subscriptions over SSE              | split | idle     |        | win: server · mac: client |
| M4 | Request cache + structured logging + /metrics | mac   | idle     |        | LRU+TTL, JSON logs, Prometheus |
| M5 | Auth hardening (JWT secret, rate limits)      | mac   | idle     |        | env-driven secret, per-user limits |
| M6 | `setup.ps1` (python, venv, DLL, server+ngrok) | win   | idle     |        | pushes live ngrok URL to .coord/ |
| M7 | pandas/polars client surface                  | mac   | idle     |        | `pd_history()`, `pl_history()` |
| M8 | LLM query builder (stretch)                   | split | idle     |        | NL -> validated IR; client-side confirm |

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

## Open questions (active)

1. Sequence — M3 before M2, or M2 first? (M2 first locks IR shape; M3
   first de-risks the session-management redesign.)
2. LLM piece — server endpoint or client confirm-then-execute?
3. Anything missing from win's daily-driver pov?

(Answers to the above will update this section + their corresponding
milestone rows.)

## Stop conditions for autonomous mode

The dev cycle runs autonomously while:
- All milestones are not `done`, AND
- No coord message says `STOP`, AND
- Krishna hasn't intervened in the last N minutes (set by the /loop).

If you (either side) have nothing to do this tick: send one
`standing by` message on coord, then exit silently until next tick.
