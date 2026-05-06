"""Tests for per-field error code refinement (M2 §2.3, win-side (b)).

Loads the synth fixture from packages/blpremote_server/tests/fixtures/
refdata_with_field_exceptions.json and feeds it through a fake
blpapi.Element wrapper so we can exercise the executor's
_check_security_errors path without a live Bloomberg session.

Phase 2: replace the synth fixture with a live-captured one and
re-run the same assertions to confirm BBG actually emits these
category strings.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from blpremote_server.executor.blp_exec import BloombergExecutor
from blpremote_server.models import ErrorDetail


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "refdata_with_field_exceptions.json"
)


# --- Fake blpapi.Element wrapper -------------------------------------


class _FakeElement:
    """Wraps a JSON node as a blpapi.Element-shaped object.

    Supports the subset _check_security_errors needs: hasElement,
    getElement, getElementAsString, isArray, numValues,
    getValueAsElement.

    A node passed in as a list is treated as an array.
    """

    def __init__(self, node: Any):
        self._n = node

    def hasElement(self, name: str) -> bool:
        return isinstance(self._n, dict) and name in self._n

    def getElement(self, name: str) -> "_FakeElement":
        return _FakeElement(self._n[name])

    def getElementAsString(self, name: str) -> str:
        return str(self._n[name])

    def isArray(self) -> bool:
        return isinstance(self._n, list)

    def numValues(self) -> int:
        return len(self._n) if isinstance(self._n, list) else 0

    def getValueAsElement(self, i: int) -> "_FakeElement":
        return _FakeElement(self._n[i])


# --- Fixture loader --------------------------------------------------


def _load_security_records() -> list[_FakeElement]:
    """Return the fixture's securityData[] records as fake elements."""
    payload = json.loads(FIXTURE.read_text())
    sec_array = payload["ReferenceDataResponse"]["securityData"]
    return [_FakeElement(rec) for rec in sec_array]


# --- Tests -----------------------------------------------------------


def test_field_exceptions_emit_category_specific_codes():
    """Each fieldException becomes BLP_FIELD_<CATEGORY> with security
    + field populated. Synth fixture has BAD_FLD and
    NOT_APPLICABLE_TO_REF_DATA."""
    errors: list[ErrorDetail] = []
    for sec_record in _load_security_records():
        BloombergExecutor._check_security_errors(sec_record, errors)

    assert len(errors) == 2

    by_field = {e.field: e for e in errors}
    assert "NOT_A_REAL_FIELD" in by_field
    assert "NOT_APPLICABLE_FIELD" in by_field

    bad_fld = by_field["NOT_A_REAL_FIELD"]
    assert bad_fld.code == "BLP_FIELD_BAD_FLD"
    assert bad_fld.security == "AAPL US Equity"
    assert "Field not valid" in bad_fld.message

    not_applicable = by_field["NOT_APPLICABLE_FIELD"]
    assert not_applicable.code == "BLP_FIELD_NOT_APPLICABLE_TO_REF_DATA"
    assert not_applicable.security == "AAPL US Equity"
    assert "Field not applicable" in not_applicable.message


def test_field_exceptions_with_no_category_fall_back_to_unknown():
    """If errorInfo lacks a category element (defensive), code falls
    back to BLP_FIELD_UNKNOWN rather than crashing."""
    sec_record = _FakeElement(
        {
            "security": "TEST US Equity",
            "fieldExceptions": [
                {
                    "fieldId": "MISSING_CATEGORY_FIELD",
                    "errorInfo": {
                        # Note: no `category` key.
                        "source": "test",
                        "code": 0,
                        "message": "no category set in errorInfo",
                    },
                }
            ],
        }
    )
    errors: list[ErrorDetail] = []
    BloombergExecutor._check_security_errors(sec_record, errors)
    assert len(errors) == 1
    assert errors[0].code == "BLP_FIELD_UNKNOWN"
    assert errors[0].security == "TEST US Equity"
    assert errors[0].field == "MISSING_CATEGORY_FIELD"


def test_security_error_still_works_alongside_field_exceptions():
    """If a record has both securityError and fieldExceptions, both
    surface as separate ErrorDetail entries."""
    sec_record = _FakeElement(
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
        }
    )
    errors: list[ErrorDetail] = []
    BloombergExecutor._check_security_errors(sec_record, errors)
    assert len(errors) == 2
    codes = sorted(e.code for e in errors)
    assert codes == [
        "BLP_FIELD_NOT_APPLICABLE_TO_REF_DATA",
        "BLP_SECURITY_ERROR",
    ]
