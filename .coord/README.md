# Cross-Machine Coordination Channel

Two transports keep the mac and win dev sessions in sync.

- **mac** — runs on Krishna's macOS box. No Bloomberg. Owns client code,
  refactors, design, anything that doesn't need live BLPAPI.
- **win** — runs on Krishna's Windows box with Bloomberg Terminal +
  `blpapi`. Owns server code, integration tests against real BLPAPI.

## Live channel (primary): the bloom-connect server itself

Endpoints (require Bearer token from `/v1/auth/login`):

- `POST /v1/coord/send` — body `{"to": "win", "body": "..."}`
- `GET  /v1/coord/inbox[?peek=true]` — drains messages for the
  authenticated user

In-memory only. Server restart wipes pending messages — that's why we
also keep the git log below.

### Using `tools/coord.py`

Pure stdlib CLI on both sides. Configure once via env vars or
`~/.blpremote/coord.json`:

```
BLPCOORD_URL    http://<windows-host>:8000   (or ngrok URL)
BLPCOORD_USER   mac    (on the Mac box)   or   win    (on Windows)
BLPCOORD_PASS   <password>
```

Then:

```
python tools/coord.py send win "ready to ship the IR refactor — pull SHA abc123"
python tools/coord.py inbox          # drain pending
python tools/coord.py inbox --peek   # read without clearing
python tools/coord.py watch          # poll loop until ctrl-c
```

To make the receiving side near-instant without typing, run a `/loop`
against `coord inbox` (e.g. every 30s). Each tick the dev session on
that machine drains the inbox and acts on whatever's there.

## Durable channel (fallback + audit log): git

Two append-only files. Use these for messages you want preserved across
server restarts, or as the official record of decisions.

- `mac-to-win.md` — mac side appends, Windows reads.
- `win-to-mac.md` — win side appends, Mac reads.

Format:

```
## 2026-05-06T14:30Z mac -> win

Body of the message.
```

Commit + push (`coord:` prefix on commits that only touch `.coord/`).
The other side `git pull`s and reads.

## When to use which

- **Live channel** — quick-turn back-and-forth, "run this and tell me
  the result", anything in active work. Default for ongoing dev.
- **Git channel** — decisions, scope locks, anything you'd want to grep
  three weeks later. Also the only option when the server is down.

## Conventions

- Timestamps in UTC, ISO-8601 (`2026-05-06T14:30Z`).
- One specific question per message when a decision is needed.
- Reference commit SHAs when you ship code the other side should pull.
- While scoping the revamp, both sides work directly on `main`. Switch
  to feature branches once scope is locked.
