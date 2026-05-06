"""Tests for per-field error code refinement (M2 §2.3).

Drives the synth fixture through ``normalize_message`` (M2 §2.2
dispatcher) and asserts the resulting NormalizedMessage carries
BLP_FIELD_<CATEGORY> errors with security + field populated.

Phase 2: replace the synth fixture with a live-captured one and
re-run the same assertions to confirm BBG actually emits these
category strings.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from blpremote_server.executor.normalize import normalize_message
from blpremote_server.models import ErrorDetail


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "refdata_with_field_exceptions.json"
)


# --- Fake blpapi.Element / Message wrapper ---------------------------


class _FakeElement:
    """Wraps a JSON node as a blpapi.Element-shaped object.

    Supports: hasElement, getElement, getElementAsString, isArray,
    numValues, numElements, getValueAsElement, getElement(index),
    name. Lists become arrays.
    """

    def __init__(self, node: Any, name: str = ""):
        self._n = node
        self._name = name

    # Identity / metadata
    def name(self) -> str:
        return self._name

    # Containment
    def hasElement(self, name: str) -> bool:
        return isinstance(self._n, dict) and name in self._n

    def getElement(self, key):
        if isinstance(key, int):
            # Index into a dict's items in order.
            assert isinstance(self._n, dict)
            k = list(self._n.keys())[key]
            return _FakeElement(self._n[k], name=k)
        return _FakeElement(self._n[key], name=key)

    def getElementAsString(self, name: str) -> str:
        return str(self._n[name])

    def numElements(self) -> int:
        return len(self._n) if isinstance(self._n, dict) else 0

    # Array view
    def isArray(self) -> bool:
        return isinstance(self._n, list)

    def numValues(self) -> int:
        return len(self._n) if isinstance(self._n, list) else 0

    def getValueAsElement(self, i: int) -> "_FakeElement":
        return _FakeElement(self._n[i])

    # Value extraction (used by the generic fallback path; not exercised here)
    def isNull(self) -> bool:
        return self._n is None

    def datatype(self) -> int:
        # Map Python type → blpapi.DataType integer (subset).
        if isinstance(self._n, bool):
            return 1
        if isinstance(self._n, int):
            return 5
        if isinstance(self._n, float):
            return 9
        return 11  # STRING / fallback

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


def _load_response_message() -> _FakeElement:
    """Load the fixture and return a fake Message at the
    ReferenceDataResponse level."""
    payload = json.loads(FIXTURE.read_text())
    return _FakeElement(payload["ReferenceDataResponse"])


# --- Tests -----------------------------------------------------------


def test_field_exceptions_emit_category_specific_codes():
    msg = _load_response_message()
    norm = normalize_message(msg)

    assert "AAPL US Equity" in norm.data
    fields = norm.data["AAPL US Equity"]
    assert fields["PX_LAST"] == 285.0
    assert fields["NAME"] == "APPLE INC"

    assert len(norm.errors) == 2
    by_field = {e.field: e for e in norm.errors}
    bad = by_field["NOT_A_REAL_FIELD"]
    assert bad.code == "BLP_FIELD_BAD_FLD"
    assert bad.security == "AAPL US Equity"
    assert "Field not valid" in bad.message

    notapp = by_field["NOT_APPLICABLE_FIELD"]
    assert notapp.code == "BLP_FIELD_NOT_APPLICABLE_TO_REF_DATA"
    assert notapp.security == "AAPL US Equity"
    assert "Field not applicable" in notapp.message

    assert norm.warnings == []


def test_field_exceptions_with_no_category_fall_back_to_unknown():
    msg = _FakeElement(
        {
            "securityData": [
                {
                    "security": "TEST US Equity",
                    "fieldExceptions": [
                        {
                            "fieldId": "MISSING_CATEGORY_FIELD",
                            "errorInfo": {
                                "source": "test",
                                "code": 0,
                                "message": "no category set in errorInfo",
                            },
                        }
                    ],
                    "fieldData": {},
                }
            ]
        }
    )
    norm = normalize_message(msg)
    assert len(norm.errors) == 1
    e = norm.errors[0]
    assert e.code == "BLP_FIELD_UNKNOWN"
    assert e.security == "TEST US Equity"
    assert e.field == "MISSING_CATEGORY_FIELD"


def test_security_error_coexists_with_field_exceptions():
    msg = _FakeElement(
        {
            "securityData": [
                {
                    "security": "BAD_TICKER",
                    "securityError": {
                        "source": "test",
                        "code": 1,
                        "category": "BAD_SEC",
                        "message": "Unknown/Invalid Security",
                    },
                    "fieldExceptions": [
                        {
                            "fieldId": "PX_LAST",
                            "errorInfo": {
                                "category": "NOT_APPLICABLE_TO_REF_DATA",
                                "message": "field not applicable",
                            },
                        }
                    ],
                    "fieldData": {},
                }
            ]
        }
    )
    norm = normalize_message(msg)
    assert len(norm.errors) == 2
    codes = sorted(e.code for e in norm.errors)
    assert codes == [
        "BLP_FIELD_NOT_APPLICABLE_TO_REF_DATA",
        "BLP_SECURITY_ERROR",
    ]


def test_response_error_short_circuits_dispatch():
    """If the message has responseError, no securityData processing
    happens — only the response-level error surfaces."""
    msg = _FakeElement(
        {
            "responseError": {
                "code": 99,
                "category": "AUTHORIZATION_FAILURE",
                "message": "auth failed",
            },
            # securityData would normally be processed too — confirm
            # responseError takes precedence and nothing else happens.
            "securityData": [
                {
                    "security": "AAPL US Equity",
                    "fieldData": {"PX_LAST": 285},
                }
            ],
        }
    )
    norm = normalize_message(msg)
    assert norm.data == {}
    assert len(norm.errors) == 1
    assert norm.errors[0].code == "BLP_RESPONSE_ERROR"
    assert "auth failed" in norm.errors[0].message
