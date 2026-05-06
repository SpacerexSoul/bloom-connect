"""Tests for SchemaCache (M2 d).

Uses fake blpapi.Service shapes (numOperations / getOperation /
operation.name / requestDefinition / numResponseDefinitions /
getResponseDefinition) to drive the serialiser without a live
Bloomberg session.
"""

from __future__ import annotations

from typing import Any

import pytest

from blpremote_server.schema_cache import SchemaCache


# --- Fakes -----------------------------------------------------------


class _FakeElementDef:
    def __init__(self, name: str):
        self._name = name

    def name(self) -> str:
        return self._name


class _FakeTypeDef:
    def __init__(self, element_names: list[str]):
        self._elems = [_FakeElementDef(n) for n in element_names]

    def numElementDefinitions(self) -> int:
        return len(self._elems)

    def getElementDefinition(self, i: int) -> _FakeElementDef:
        return self._elems[i]


class _FakeSchemaDef:
    """Stand-in for blpapi's SchemaTypeDefinition wrapper."""

    def __init__(self, element_names: list[str], name: str = ""):
        self._td = _FakeTypeDef(element_names)
        self._name = name

    def typeDefinition(self) -> _FakeTypeDef:
        return self._td

    def name(self) -> str:
        return self._name


class _FakeOperation:
    def __init__(
        self,
        name: str,
        request_elements: list[str],
        response_definitions: list[tuple[str, list[str]]],
    ):
        self._name = name
        self._req = _FakeSchemaDef(request_elements)
        self._resps = [
            _FakeSchemaDef(elems, name=resp_name)
            for resp_name, elems in response_definitions
        ]

    def name(self) -> str:
        return self._name

    def requestDefinition(self) -> _FakeSchemaDef:
        return self._req

    def numResponseDefinitions(self) -> int:
        return len(self._resps)

    def getResponseDefinition(self, i: int) -> _FakeSchemaDef:
        return self._resps[i]


class _FakeService:
    def __init__(self, operations: list[_FakeOperation]):
        self._ops = operations

    def numOperations(self) -> int:
        return len(self._ops)

    def getOperation(self, i: int) -> _FakeOperation:
        return self._ops[i]


class _FakeManager:
    """Stand-in for SessionManager — only needs get_service()."""

    def __init__(self, services_by_name: dict[str, _FakeService]):
        self._svcs = services_by_name

    def get_service(self, name: str) -> _FakeService:
        if name not in self._svcs:
            raise RuntimeError(f"unknown service {name}")
        return self._svcs[name]


# --- Fixtures --------------------------------------------------------


@pytest.fixture
def refdata_service() -> _FakeService:
    return _FakeService(
        [
            _FakeOperation(
                "ReferenceDataRequest",
                request_elements=["securities", "fields", "overrides"],
                response_definitions=[
                    ("ReferenceDataResponse", ["securityData", "responseError"]),
                ],
            ),
            _FakeOperation(
                "HistoricalDataRequest",
                request_elements=[
                    "securities", "fields", "startDate", "endDate", "periodicitySelection",
                ],
                response_definitions=[
                    ("HistoricalDataResponse", ["securityData", "responseError"]),
                ],
            ),
        ]
    )


# --- Tests -----------------------------------------------------------


def test_get_returns_none_when_empty(refdata_service):
    cache = SchemaCache(_FakeManager({"//blp/refdata": refdata_service}))
    assert cache.get("//blp/refdata") is None


def test_warm_serialises_operations(refdata_service):
    cache = SchemaCache(_FakeManager({"//blp/refdata": refdata_service}))
    info, etag = cache.warm("//blp/refdata")

    assert info["service"] == "//blp/refdata"
    op_names = [op["name"] for op in info["operations"]]
    assert op_names == ["ReferenceDataRequest", "HistoricalDataRequest"]

    refdata_op = info["operations"][0]
    assert refdata_op["request_elements"] == [
        "securities", "fields", "overrides",
    ]
    assert refdata_op["response_elements"] == [
        {
            "name": "ReferenceDataResponse",
            "elements": ["securityData", "responseError"],
        }
    ]

    assert isinstance(etag, str) and len(etag) == 16


def test_get_or_warm_caches(refdata_service):
    mgr = _FakeManager({"//blp/refdata": refdata_service})
    cache = SchemaCache(mgr)

    info1, etag1 = cache.get_or_warm("//blp/refdata")
    info2, etag2 = cache.get_or_warm("//blp/refdata")
    assert info1 is info2  # same dict, no second walk
    assert etag1 == etag2


def test_etag_changes_when_content_changes(refdata_service):
    cache = SchemaCache(_FakeManager({"//blp/refdata": refdata_service}))
    _, etag1 = cache.warm("//blp/refdata")

    # Simulate content change: replace the underlying service.
    new_svc = _FakeService(
        [
            _FakeOperation(
                "NewlyAddedRequest",
                request_elements=["foo"],
                response_definitions=[("NewlyAddedResponse", ["bar"])],
            )
        ]
    )
    cache._mgr = _FakeManager({"//blp/refdata": new_svc})
    _, etag2 = cache.warm("//blp/refdata")
    assert etag1 != etag2


def test_invalidate_drops_one_entry(refdata_service):
    cache = SchemaCache(_FakeManager({"//blp/refdata": refdata_service}))
    cache.warm("//blp/refdata")
    assert cache.get("//blp/refdata") is not None
    cache.invalidate("//blp/refdata")
    assert cache.get("//blp/refdata") is None


def test_invalidate_all(refdata_service):
    cache = SchemaCache(
        _FakeManager(
            {
                "//blp/refdata": refdata_service,
                "//blp/news": _FakeService([]),
            }
        )
    )
    cache.warm("//blp/refdata")
    cache.warm("//blp/news")
    assert sorted(cache.keys()) == ["//blp/news", "//blp/refdata"]
    cache.invalidate(None)
    assert cache.keys() == []


def test_warm_handles_missing_response_definitions():
    """An operation without numResponseDefinitions returns an empty
    response_elements list rather than crashing."""

    class _OpNoResponses:
        def name(self):
            return "OneOff"

        def requestDefinition(self):
            return _FakeSchemaDef(["x"])

        # numResponseDefinitions intentionally missing

    svc = _FakeService([_OpNoResponses()])
    cache = SchemaCache(_FakeManager({"//blp/x": svc}))
    info, _ = cache.warm("//blp/x")
    assert info["operations"][0]["response_elements"] == []
