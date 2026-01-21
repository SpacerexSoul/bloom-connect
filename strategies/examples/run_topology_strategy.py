#!/usr/bin/env python3
"""
Example: Run the Algebraic Topology Market-Neutral Strategy

This script demonstrates how to use the trading strategy with the
Bloomberg Remote BLPAPI Wrapper.

Requirements:
- Windows machine running blpremote_server with Bloomberg Terminal
- Network connectivity from macOS to Windows
"""

import argparse
import sys
from pathlib import Path

# Add paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "packages/blpremote_client/src"))
sys.path.insert(0, str(Path(__file__).parent.parent))


def main():
    parser = argparse.ArgumentParser(description="Run Algebraic Topology Market-Neutral Strategy")
    parser.add_argument(
        "--host",
        required=True,
        help="Windows server URL (e.g., http://192.168.1.100:8000)",
    )
    parser.add_argument("--username", required=True, help="Username")
    parser.add_argument("--password", required=True, help="Password")
    parser.add_argument(
        "--index",
        default="SPX Index",
        help="Index for universe (default: SPX Index)",
    )
    parser.add_argument(
        "--max-securities",
        type=int,
        default=100,
        help="Maximum securities to analyze (default: 100)",
    )
    parser.add_argument(
        "--portfolio-value",
        type=float,
        default=1_000_000,
        help="Portfolio value for position sizing (default: 1,000,000)",
    )
    args = parser.parse_args()

    from blpremote_client import RemoteHost

    from strategies.algebraic_topology import AlgebraicTopologyStrategy, StrategyConfig

    print("=" * 70)
    print("  Algebraic Topology Market-Neutral Strategy")
    print("=" * 70)
    print()
    print(f"Server:     {args.host}")
    print(f"Index:      {args.index}")
    print(f"Max securities: {args.max_securities}")
    print(f"Portfolio:  ${args.portfolio_value:,.0f}")
    print()

    # Connect to Bloomberg server
    print("Connecting to Bloomberg server...")
    host = RemoteHost(args.host, username=args.username, password=args.password)

    # Verify connection
    try:
        health = host.health()
        print(f"Server health: {health}")
        if not health.get("bloomberg_connected", False):
            print("Warning: Bloomberg API not available - using mock data")
    except Exception as e:
        print(f"Error connecting: {e}")
        return 1

    # Configure strategy
    config = StrategyConfig(
        index=args.index,
        max_securities=args.max_securities,
        lookback_days=252 * 5,  # 5 years
    )

    # Run strategy
    strategy = AlgebraicTopologyStrategy(config)
    signals = strategy.run(host)

    if not signals:
        print("No signals generated")
        return 1

    # Compute positions
    print()
    print("=" * 70)
    print("  Position Sizing")
    print("=" * 70)
    print()

    positions = strategy.compute_positions(signals, args.portfolio_value)

    # Sort by absolute position size
    sorted_positions = sorted(positions.items(), key=lambda x: -abs(x[1]))

    # Top long positions
    print("Top Long Positions:")
    print("-" * 40)
    for security, pos in sorted_positions:
        if pos > 1000:
            print(f"  {security:30s} ${pos:>12,.0f}")

    print()

    # Top short positions
    print("Top Short Positions:")
    print("-" * 40)
    for security, pos in sorted_positions:
        if pos < -1000:
            print(f"  {security:30s} ${pos:>12,.0f}")

    # Summary
    print()
    print("=" * 70)
    print("  Summary")
    print("=" * 70)
    total_long = sum(p for p in positions.values() if p > 0)
    total_short = sum(p for p in positions.values() if p < 0)
    print(f"  Total Long:  ${total_long:>12,.0f}")
    print(f"  Total Short: ${total_short:>12,.0f}")
    print(f"  Net:         ${total_long + total_short:>12,.0f}")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
