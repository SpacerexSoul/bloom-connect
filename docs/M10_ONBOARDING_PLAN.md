# M10 — first-run onboarding wizard

> Status: planned, not implemented. Next session picks up here.
> Driver: Krishna 2026-05-12 — "user downloads bloom-connect, picks
> client or host, wizard handles the rest. Bloomberg is the only
> manual prereq on the host PC."

## Goal

Replace the current "edit identity.json by hand + run setup.ps1
with the right flags + remember to install ngrok + paste an
OpenRouter key" flow with a single guided experience per OS.

The intended UX, end-to-end:

1. User downloads bloom-connect (zip or git clone).
2. Double-clicks one thing.
3. Picks **Host** (Windows + Bloomberg) or **Client** (Mac).
4. Wizard handles: Python check, dep install, ngrok auto-download,
   JWT secret generation, user account creation, ngrok authtoken,
   OpenRouter key prompt, pairing-code exchange.
5. Wizard hands off to the appropriate main UI (server window on
   host, Connect window on client).

The only thing the user touches manually is opening Bloomberg
Terminal on the host PC. Everything else is either auto-detected,
auto-downloaded, or prompted for in plain English.

## Tier split

### Tier B (pragmatic, ships first — recommended)

- `Welcome.command` (Mac) and `Welcome.bat` (Win) launchers at repo
  root. Double-clickable.
- `tools/welcome.py` Tkinter wizard with three steps:
  1. Role pick: `[Host (Windows + Bloomberg)]` / `[Client]`
  2. Per-role setup (calls into enhanced setup.ps1 / setup.sh
     under the hood, captures stdout in a logs tail, advances on
     exit-0)
  3. Done → auto-launches the main UI for that role
- User still does `git clone` or downloads a zip, but everything
  after that is one click.

Estimate: ~6-8h split. Most of the work is wiring + setup script
enhancements; the wizard itself is ~150 LOC of Tkinter.

### Tier A (real distribution, follow-up)

- PyInstaller bundles: `Welcome.app` (Mac, via py2app or
  PyInstaller) and `Welcome.exe` (Win, PyInstaller `--onefile
  --windowed`).
- ~150 MB per platform. Ships as a single downloadable zip with
  one bundle per OS.
- True "download → double-click → onboard" experience.

Tier A only makes sense if Tier B is shipped and the install gets
re-used outside Krishna's two-box setup. Skip until that need
shows up.

## Per-role flows

### Host flow (Windows)

```
[Welcome] -> [I'm a Host]
              |
              v
[Bloomberg check]   Bloomberg Terminal running?
              |    No  -> "Open Bloomberg Terminal first" with retry button
              |    Yes -> continue
              v
[Python check]      Python 3.10+ installed?
              |    No  -> show "winget install Python.Python.3.12" + a Run button
              |    Yes -> continue
              v
[ngrok bake-in]    ngrok.exe present?
              |    No  -> Invoke-WebRequest from ngrok official URL +
              |          Expand-Archive into .\tools\ngrok\
              |    Yes -> continue
              v
[ngrok authtoken]  Have an authtoken in ngrok config?
              |    No  -> open ngrok dashboard in browser,
              |          prompt for paste, run `ngrok config add-authtoken`
              |    Yes -> continue
              v
[JWT secret]       Auto-generate 32-byte random secret to
                   %USERPROFILE%\.blpremote\server_secret.txt
                   (chmod equiv, never displayed). setup.ps1
                   sources it from there on launch.
              v
[First user]       Prompt for username + password.
                   Auto-add to packages/blpremote_server/users.json.
              v
[Install deps]     pip install blpapi + blpremote_server[ ... ]
              v
[Start server]     setup.ps1 -CoordSend (none) -- direct Popen, no coord.
                   Wait for /health.
              v
[Pairing code]     Generate a single-string code that bundles:
                   {ngrok_url, username, password, jwt_secret_hint}
                   base64-JSON. Display in a copyable text field.
                   "Send this to your client."
              v
[Server UI opens]  blpremote-server-ui launches, status all green.
```

### Client flow (Mac)

```
[Welcome] -> [I'm a Client]
              |
              v
[Python check]    Python 3.10+ installed?
              |    No  -> show "brew install python" or python.org link
              |    Yes -> continue
              v
[Install deps]   ./setup.sh --skip-prompts under the hood:
                  venv + pip install blpremote_client[llm,pandas,polars]
              v
[Pairing input]   [Paste pairing code from host]  (recommended)
                  or
                  [Enter manually: URL, username, password]
              v
[Decode + write]  Decode pairing code → write ~/.blpremote/identity.json
              v
[OpenRouter key]  Optional. "Paste an OpenRouter key, or skip — you
                  can add it later." If pasted → ~/.blpremote/openrouter.json.
              v
[Test connect]    RemoteHost(url).health() to verify before handoff.
                  Green ✓ on success, retry on failure.
              v
[Client UI opens] blpremote-ui launches, Connect button pre-clicked,
                  status LED green.
```

## Pairing code

One opaque string the host generates and the client pastes — the
"three things to coordinate" (URL, username, password) collapse to
one. Shape:

```
base64( JSON {
  "v": 1,
  "url": "https://nest-eligibly-dork.ngrok-free.dev",
  "user": "mac",
  "pass": "<the password the host just created>"
} )
```

Plain base64, no signing — this is local-network or one-shot
sharing, not a public credential token. Credentials are plaintext
once decoded; **the doc must spell out that pairing codes are
shareable-via-trusted-channel only, never paste in public channels**.

For future polish (M11 territory): swap to a short-lived signed
JWT or a registration nonce so the credential never leaves the
host. Not worth the complexity for v1.

## Locked design decisions

1. **Python auto-install: NO.** Detect-and-instruct only. Show the
   exact `winget` or `brew` command, link to python.org as
   fallback. Auto-installing Python is gnarly across platforms,
   often needs admin, and the failure modes are worse than just
   asking. Worth revisiting in M11 if friction shows up.

2. **JWT secret: auto-generated, never shown.** A random 32-byte
   secret written to `%USERPROFILE%\.blpremote\server_secret.txt`
   (perms-locked) and sourced by setup.ps1. User doesn't pick a
   weak secret because the user doesn't pick at all.

3. **First user account: prompted on host setup.** Auto-add to
   users.json. Doesn't replace the existing manual `users.json`
   path for multi-user setups — just covers the "I'm one person
   pairing with myself" case which is 95% of usage.

4. **ngrok auto-download: YES.** From the official binary URL.
   Their TOS allows redistribution for non-resale use; download
   without repacking is uncontested. Cache the result in
   `.\tools\ngrok\ngrok.exe` so re-runs don't re-download.

5. **OpenRouter key on client: skippable.** Test-query pane in
   the main UI shows "Missing — Test query disabled" if absent;
   user can paste later via `~/.blpremote/openrouter.json` or a
   future Settings panel.

6. **Cross-platform welcome script: ONE Tkinter file.** Same
   `tools/welcome.py` runs on Mac and Windows; role selection
   gates the OS-specific bits. Single source of truth means
   onboarding bug fixes don't have to be made twice.

## Implementation breakdown

Roughly 6-8 hours of split work, mirrors the M9 chunk model.

### Win side (~3h)

a. **`setup.ps1` ngrok auto-download** — detect `.\tools\ngrok\ngrok.exe`
   or PATH; if absent, Invoke-WebRequest the official zip,
   Expand-Archive, run `--version` smoke. ~30 LOC.
b. **`setup.ps1` ngrok authtoken prompt** — only when
   `ngrok config check` reports no token. Open ngrok dashboard
   via `Start-Process`, prompt for paste, run
   `ngrok config add-authtoken`. ~20 LOC.
c. **`setup.ps1` JWT secret auto-generate** — create
   `%USERPROFILE%\.blpremote\server_secret.txt` with
   `[System.Web.Security.Membership]::GeneratePassword(64, 10)` if
   absent. Set `BLPREMOTE_SECRET_KEY` env before uvicorn launch.
   ~15 LOC.
d. **First-user prompt + users.json write** — only when users.json
   is empty or the default-admin-only. ~20 LOC.
e. **Pairing-code generator** — emit a base64-JSON code on
   successful first-time setup. Display in the welcome wizard's
   final pane. ~15 LOC of PowerShell + the wizard's display widget.
f. **Tests for the new setup.ps1 logic** — Pester tests or
   exercise via PowerShell -Command harness. ~40 LOC.

### Mac side (~2h)

a. **`setup.sh` server URL / username / password prompts** —
   replace the password-stub-only write of identity.json with full
   prompts when fields are empty. ~25 LOC.
b. **Pairing-code parser** — `tools/welcome.py` decodes base64-JSON
   and writes the full identity.json. ~20 LOC.
c. **Tests for the parser** — pytest, covering valid / invalid /
   corrupt-base64 / missing-fields. ~40 LOC.

### Shared welcome wizard (~2-3h, split)

a. **`tools/welcome.py`** — Tkinter, ~200 LOC. Role-pick screen,
   per-role step machine, logs pane, completion screen with
   pairing-code display (host) or success readout (client).
   Cross-platform: same file used by both `Welcome.command` and
   `Welcome.bat`.
b. **`Welcome.command` (Mac) + `Welcome.bat` (Win)** — thin
   launchers. ~10 LOC each.
c. **Hand-off to main UI** — wizard ends by spawning the
   appropriate main UI (`blpremote-ui` on client,
   `blpremote-server-ui` on host) and exiting.
d. **Wizard tests** — pure helpers (role-pick state machine,
   step-advance logic) testable Tkinter-free. ~50 LOC.

## Branch plan

Same review-then-merge pattern as M9:

- `win/m10-onboarding`   — setup.ps1 enhancements + win-side wizard
                           hand-off.
- `mac/m10-onboarding`   — setup.sh enhancements + pairing parser
                           + welcome.py wizard shared module.

`tools/welcome.py` ownership ambiguous — proposal: mac drives the
file (since Tkinter on Mac is the lower-trust render to validate)
and win reviews the win-side step logic. Or: both PRs touch it,
land mac's first, win adds his cases.

## What's NOT in M10

- True .exe / .app PyInstaller bundling (Tier A) — defer to M11
  if distribution outside Krishna's two-box setup ever matters.
- Auto-update mechanism.
- Onboarding migration for existing installs — the existing
  identity.json keeps working; wizard only kicks in when files are
  absent. No back-fill, no upgrade flow.
- Multi-user / family-of-N on host. First user via wizard;
  subsequent users via the existing `users.json` edit. Document.
- Cloudflare Tunnel / alternative tunneling. ngrok stays.

## Open questions

- **Pairing code expiry?** v1: no expiry, codes are valid until
  the host changes URL / restarts ngrok. Pro: simple, matches
  "set it up once, paste once" UX. Con: stale codes embed
  passwords. Lean ship-without-expiry, document the trust model.
- **Pairing-code QR display?** Future polish — render the same
  base64 string as a QR code in the host's wizard, scan with
  phone, type into client. Out of scope for v1; doc as M11
  candidate.
- **Where does the pairing code live after display?** Lean: shown
  once, never persisted. Host can regenerate on demand via a
  "Show pairing code" button in the main server UI (M9-bis
  scope) — small follow-up.

## Estimate

| Tier B end-to-end                    | ~6-8h split          |
| Tier A (.exe/.app) follow-up         | +6-10h               |
| Pairing-code QR / expiry polish (M11)| out of scope         |

Best first attack: Tier B in one or two sessions; live-verify on
both boxes; ship. Decide on Tier A only if friction shows up.
