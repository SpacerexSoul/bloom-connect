"""
Trading Strategies Package

This package contains proprietary trading strategies that utilize the
Bloomberg Remote BLPAPI Wrapper for data acquisition and execution.

IMPORTANT: These strategies are for research and educational purposes.
Always backtest thoroughly before any live trading.
"""

from strategies.algebraic_topology import AlgebraicTopologyStrategy

__all__ = ["AlgebraicTopologyStrategy"]
