"""Subscription-lifecycle proof-of-concept for M1.

Proves the long-lived async SessionManager handles subscription event
flows in addition to request/response. This is the M1 scope-check
that M3 (SSE streaming) won't need a session rewrite — same session,
same dispatch queues, just a different op mix on top.

Run on a Windows box with Bloomberg Terminal logged in:

    python -m blpremote_server.subscription_poc

It subscribes to AAPL US Equity LAST_PRICE for 5 seconds and prints
each event delivered through the SessionManager's per-cid queue,
then unsubscribes cleanly.
"""

from __future__ import annotations

import logging
import sys
import time

from blpremote_server.session_manager import (
    BLPAPI_AVAILABLE,
    SessionManager,
    blpapi,
)


def main(duration_s: float = 5.0) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if not BLPAPI_AVAILABLE:
        print("blpapi not available — install Bloomberg API SDK first", file=sys.stderr)
        return 2

    mgr = SessionManager(services=["//blp/mktdata"])
    try:
        mgr.start()
    except Exception as exc:
        print(f"failed to start session: {exc}", file=sys.stderr)
        return 2

    cid_value = "sub-poc-aapl"
    q = mgr.register_queue(cid_value)

    sub_list = blpapi.SubscriptionList()
    sub_list.add(
        "AAPL US Equity",
        "LAST_PRICE",
        "",
        blpapi.CorrelationId(cid_value),
    )
    print(f"subscribing to AAPL US Equity LAST_PRICE for {duration_s}s...")
    mgr.session().subscribe(sub_list)

    deadline = time.time() + duration_s
    seen = 0
    counts: dict[str, int] = {}
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        try:
            event_type, msg = q.get(timeout=remaining)
        except Exception:
            break
        seen += 1
        try:
            mtype = str(msg.messageType())
        except Exception:
            mtype = "<unknown>"
        counts[mtype] = counts.get(mtype, 0) + 1
        # Print the first occurrence of each message type, then suppress
        # repeats so the PoC output stays readable on a busy ticker.
        if counts[mtype] == 1:
            print(f"  first {mtype}: event_type={event_type}")

    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    print(f"unsubscribing (saw {seen} event(s) in {duration_s}s: {summary})")
    try:
        mgr.session().unsubscribe(sub_list)
    finally:
        mgr.unregister_queue(cid_value)
        mgr.stop()

    # Success if we got any events at all (status or data).
    return 0 if seen > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
