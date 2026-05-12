#!/usr/bin/env python3
"""Thin entry point for the mac client UI.

The real implementation lives in :mod:`blpremote_client.ui`; this
script exists so ``Connect.command`` (Finder double-click) and
``python tools/blpremote-client-ui.py`` (terminal) both work
without needing the package on the path.
"""

from __future__ import annotations

import os
import sys

# Allow running from a checkout without `pip install -e`.
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "packages", "blpremote_client", "src")
if os.path.isdir(SRC):
    sys.path.insert(0, SRC)

from blpremote_client.ui import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
