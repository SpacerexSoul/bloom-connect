#!/usr/bin/env python3
"""Manual smoke test for Bloomberg Remote Server.

This script tests the full end-to-end flow with a real Windows server.
Requires the server to be running with Bloomberg Terminal logged in.

Usage:
    python tools/manual_smoke_test.py --host http://192.168.1.100:8000 --username krishna --password your-pass
"""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description="Manual smoke test for Bloomberg Remote")
    parser.add_argument(
        "--host", required=True, help="Windows server URL (e.g., http://192.168.1.100:8000)"
    )
    parser.add_argument("--username", required=True, help="Username for authentication")
    parser.add_argument("--password", required=True, help="Password for authentication")
    parser.add_argument("--security", default="IBM US Equity", help="Security to test")
    args = parser.parse_args()

    print("=" * 60)
    print("Bloomberg Remote Manual Smoke Test")
    print("=" * 60)
    print(f"\nHost: {args.host}")
    print(f"Username: {args.username}")
    print(f"Security: {args.security}")

    try:
        from blpremote_client import RemoteHost, px_last, ref_data
        from blpremote_client.proxy import Session, SessionOptions

        # Test 1: Health check
        print("\n[1/5] Testing health endpoint...")
        host = RemoteHost(args.host, username=args.username, password=args.password)
        health = host.health()
        print(f"      ✓ Health: {health}")

        # Test 2: Version check
        print("\n[2/5] Testing version endpoint...")
        version = host.version()
        print(f"      ✓ Version: {version}")

        # Test 3: Convenience API - px_last
        print(f"\n[3/5] Testing px_last({args.security})...")
        price = px_last(host, args.security)
        print(f"      ✓ PX_LAST: {price}")

        # Test 4: Convenience API - ref_data
        print("\n[4/5] Testing ref_data with multiple fields...")
        data = ref_data(host, args.security, ["PX_LAST", "NAME", "VOLUME"])
        print(f"      ✓ Data: {data}")

        # Test 5: Proxy API
        print("\n[5/5] Testing proxy Session/Service/Request API...")
        opts = SessionOptions()
        with Session(
            opts, remote_host=args.host, username=args.username, password=args.password
        ) as session:
            session.start()
            session.openService("//blp/refdata")
            svc = session.getService("//blp/refdata")

            req = svc.createRequest("ReferenceDataRequest")
            req.getElement("securities").appendValue(args.security)
            req.getElement("fields").appendValue("PX_LAST")

            cid = session.sendRequest(req)
            result = session.collectResponse(cid)

            print(f"      ✓ Status: {result.status}")
            print(f"      ✓ Data: {result.to_dict()}")
            print(f"      ✓ Server timing: {result.server_timing_ms}ms")

            if result.errors:
                print(f"      ⚠ Errors: {result.errors}")

        print("\n" + "=" * 60)
        print("All tests passed! ✓")
        print("=" * 60)
        return 0

    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
