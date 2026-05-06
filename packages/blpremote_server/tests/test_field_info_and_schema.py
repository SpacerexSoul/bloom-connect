"""Tests for FieldInfo + Schema normalisers (M2 c3)."""

from __future__ import annotations

from typing import Any

import pytest

from blpremote_server.executor.normalize import normalize_message


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


# --- FieldInfoResponse ----------------------------------------------


def test_field_info_keyed_by_field_id():
    msg = _FakeElement(
        {
            "fieldData": [
                {
                    "id": "PX_LAST",
                    "fieldInfo": {
                        "mnemonic": "PX_LAST",
                        "description": "Last Trade/Last Price",
                        "datatype": "Float64",
                    },
                },
                {
                    "id": "NAME",
                    "fieldInfo": {
                        "mnemonic": "NAME",
                        "description": "Name",
                        "datatype": "String",
                    },
                },
            ]
        }
    )
    norm = normalize_message(msg)
    fi = norm.data["_field_info"]
    assert set(fi.keys()) == {"PX_LAST", "NAME"}
    assert fi["PX_LAST"]["description"] == "Last Trade/Last Price"
    assert fi["NAME"]["datatype"] == "String"
    assert norm.errors == []


def test_field_info_record_with_field_error_surfaces_as_error():
    msg = _FakeElement(
        {
            "fieldData": [
                {
                    "id": "PX_LAST",
                    "fieldInfo": {"description": "ok"},
                },
                {
                    "id": "DOES_NOT_EXIST",
                    "fieldError": {"message": "field not found"},
                },
            ]
        }
    )
    norm = normalize_message(msg)
    assert "PX_LAST" in norm.data["_field_info"]
    assert "DOES_NOT_EXIST" not in norm.data["_field_info"]
    assert len(norm.errors) == 1
    assert norm.errors[0].code == "BLP_FIELD_INFO_ERROR"
    assert norm.errors[0].field == "DOES_NOT_EXIST"
    assert "not found" in norm.errors[0].message


def test_field_info_missing_id_fallback():
    """Records without an id field fall back to a synthetic key
    rather than crashing — defensive."""
    msg = _FakeElement(
        {
            "fieldData": [
                {"fieldInfo": {"description": "no id field"}},
            ]
        }
    )
    norm = normalize_message(msg)
    fi = norm.data["_field_info"]
    assert len(fi) == 1
    key = next(iter(fi))
    assert key.startswith("_unknown_")


# --- SchemaResponse -------------------------------------------------


def test_schema_walks_to_dict_under_schema_key():
    msg = _FakeElement(
        {
            "schema": {
                "name": "ReferenceDataResponse",
                "elements": [
                    {"name": "securityData", "type": "SecurityData"},
                    {"name": "responseError", "type": "ErrorInfo"},
                ],
            }
        }
    )
    norm = normalize_message(msg)
    schema = norm.data["_schema"]
    assert "schema" in schema
    assert schema["schema"]["name"] == "ReferenceDataResponse"
