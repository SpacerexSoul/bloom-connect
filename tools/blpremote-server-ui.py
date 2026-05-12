"""Thin entry point for the M9 server UI.

All logic lives in ``blpremote_server.ui`` (so tests can import it
without standing up a Tk display). Run via:

    python tools/blpremote-server-ui.py

Or double-click START_SERVER_UI.bat (chunk d).
"""

from __future__ import annotations

from blpremote_server.ui import main

if __name__ == "__main__":
    raise SystemExit(main())
