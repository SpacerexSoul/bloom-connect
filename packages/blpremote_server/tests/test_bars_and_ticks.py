"""Tests for IntradayBar + IntradayTick normalisers (M2 c2).

Both response types require a security_hint because the response
itself doesn't echo the requested security back. Hint flows from the
executor (which built the request and knows the security) down to
normalize_message via security_hint=.
"""

from __future__ import annotations

from typing import Any

import pytest

from blpremote_server.executor.normalize import normalize_message


# Reuse the _FakeElement pattern from test_field_exceptions to avoid
# a real blpapi dependency. Kept inline so each test file is
# self-contained — once we add a third user we'll lift it into
# tests/conftest.py.


class _FakeElement:
    def __init__(self, node: Any, name: str = ""):
        self._n = node
        self._name = name

    def name(self) -> str:
        return self._name

    def hasElement(self, name: str) -> bool:
        return isinstance(self._n, dict) and name in self._n

    def getElement(self, key):
        if isinstance(key, int):
            assert isinstance(self._n, dict)
            k = list(self._n.keys())[key]
            return _FakeElement(self._n[k], name=k)
        return _FakeElement(self._n[key], name=key)

    def getElementAsString(self, name: str) -> str:
        return str(self._n[name])

    def numElements(self) -> int:
        return len(self._n) if isinstance(self._n, dict) else 0

    def isArray(self) -> bool:
        return isinstance(self._n, list)

    def numValues(self) -> int:
        return len(self._n) if isinstance(self._n, list) else 0

    def getValueAsElement(self, i: int) -> "_FakeElement":
        return _FakeElement(self._n[i])

    def isNull(self) -> bool:
        return self._n is None

    def datatype(self) -> int:
        if isinstance(self._n, bool):
            return 1
        if isinstance(self._n, int):
            return 5
        if isinstance(self._n, float):
            return 9
        return 11

    def getValueAsBool(self) -> bool:
        return bool(self._n)

    def getValueAsInteger(self) -> int:
        return int(self._n)

    def getValueAsFloat(self) -> float:
        return float(self._n)

    def getValueAsString(self) -> str:
        return str(self._n)

    def getValue(self) -> Any:
        return self._n


# --- Bar tests -------------------------------------------------------


def _make_bar_message(bars: list[dict]) -> _FakeElement:
    return _FakeElement(
        {"barData": {"eventType": "TRADE", "barTickData": bars}}
    )


def test_bar_data_keyed_by_security_hint():
    msg = _make_bar_message(
        [
            {
                "time": "2026-05-06T14:30:00",
                "open": 285.0,
                "high": 285.5,
                "low": 284.8,
                "close": 285.3,
                "volume": 1000,
                "numEvents": 12,
                "value": 285200.0,
            },
            {
                "time": "2026-05-06T14:31:00",
                "open": 285.3,
                "high": 285.7,
                "low": 285.1,
                "close": 285.6,
                "volume": 1500,
                "numEvents": 18,
                "value": 428400.0,
            },
        ]
    )
    norm = normalize_message(msg, security_hint="AAPL US Equity")
    assert "AAPL US Equity" in norm.data
    bars = norm.data["AAPL US Equity"]
    assert len(bars) == 2
    assert bars[0]["open"] == 285.0
    assert bars[0]["volume"] == 1000
    assert bars[1]["close"] == 285.6
    assert norm.errors == []
    assert norm.warnings == []


def test_bar_data_falls_back_to_underscore_bars_without_hint():
    msg = _make_bar_message([{"time": "t", "open": 1.0, "close": 1.1}])
    norm = normalize_message(msg)  # no security_hint
    assert "_bars" in norm.data
    assert len(norm.data["_bars"]) == 1


def test_bar_data_empty_response_returns_empty_list():
    """If barData is present but barTickData is empty, return [] for
    the security key — not a missing key, so callers can rely on the
    presence of the security as 'we got a valid response, just no bars'."""
    msg = _make_bar_message([])
    norm = normalize_message(msg, security_hint="AAPL US Equity")
    assert norm.data["AAPL US Equity"] == []


# --- Tick tests ------------------------------------------------------


def _make_tick_message(ticks: list[dict]) -> _FakeElement:
    # Note: real BBG response is tickData.tickData[] — double key.
    return _FakeElement({"tickData": {"tickData": ticks}})


def test_tick_data_keyed_by_security_hint():
    msg = _make_tick_message(
        [
            {
                "time": "2026-05-06T14:30:00.123",
                "type": "TRADE",
                "value": 285.12,
                "size": 100,
            },
            {
                "time": "2026-05-06T14:30:01.456",
                "type": "BID",
                "value": 285.10,
                "size": 200,
            },
        ]
    )
    norm = normalize_message(msg, security_hint="AAPL US Equity")
    ticks = norm.data["AAPL US Equity"]
    assert len(ticks) == 2
    assert ticks[0]["type"] == "TRADE"
    assert ticks[0]["value"] == 285.12
    assert ticks[1]["type"] == "BID"
    assert ticks[1]["size"] == 200


def test_tick_data_falls_back_to_underscore_ticks_without_hint():
    msg = _make_tick_message([{"time": "t", "type": "TRADE", "value": 1.0}])
    norm = normalize_message(msg)
    assert "_ticks" in norm.data


def test_tick_data_handles_missing_inner_tickdata():
    """If the outer tickData wraps something other than another
    tickData (defensive — shouldn't happen against real BBG), return
    an empty NormalizedMessage rather than crashing."""
    msg = _FakeElement({"tickData": {"eventType": "TRADE"}})  # no inner
    norm = normalize_message(msg, security_hint="AAPL US Equity")
    assert norm.data == {}
