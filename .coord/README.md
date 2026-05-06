# Cross-Machine Coordination Channel

This directory is how the two dev sessions working on this repo
talk to each other.

- **mac** — runs on Krishna's macOS box. No Bloomberg. Owns client code,
  refactors, design, anything that doesn't need live BLPAPI.
- **win** — runs on Krishna's Windows box with Bloomberg Terminal + `blpapi`.
  Owns server code, integration tests against real BLPAPI, anything that
  touches the terminal.

## Files

- `mac-to-win.md` — mac side appends here. win side reads.
- `win-to-mac.md` — win side appends here. mac side reads.

Two separate files so we never collide on a merge.

## Protocol

1. Before starting work, `git pull` to see new messages from the other side.
2. To send a message, append a new entry to your **outgoing** file:

   ```
   ## 2026-05-06T14:30Z mac -> win

   Body of the message. Keep it short and actionable.
   ```

3. Commit and push:

   ```
   git add .coord/<your-outgoing-file>.md
   git commit -m "coord: <short summary>"
   git push
   ```

4. The other side pulls and reads. Reply same way in their outgoing file.

## Conventions

- Timestamps in UTC, ISO-8601 (`2026-05-06T14:30Z`).
- One specific question per message when a decision is needed.
- If you ship code the other side should review, say so explicitly and
  reference the commit SHA.
- Keep coordination messages out of feature commits — `coord:` prefix on
  commits that only touch `.coord/`.

## Bootstrapping rule

While we're still scoping the revamp, both sides work directly on `main`.
Once we lock scope, switch to feature branches and PRs.
