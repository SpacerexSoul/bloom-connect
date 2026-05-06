"""Tests for get_service_schema — client-side ETag caching against the
server's /v1/schema endpoint. No live server, no httpx — we inject a
fake host whose ``get_schema`` mirrors the real RemoteHost contract.
"""

from typing import Any, Optional

import pytest

import blpremote_client.data as data_module
from blpremote_client.data import get_service_schema


class _FakeSchemaHost:
    """Records calls and returns a scripted sequence of (body, etag) tuples.

    Mirrors ``RemoteHost.get_schema(service, etag=None)``:
      - body is None on 304, dict on 200
      - etag is the response ETag header (always set on 200/304)
    """

    def __init__(self, *responses: tuple[Optional[dict[str, Any]], Optional[str]]):
        self._responses = list(responses)
        self.calls: list[tuple[str, Optional[str]]] = []

    def get_schema(self, service, etag=None):
        self.calls.append((service, etag))
        if not self._responses:
            raise AssertionError(
                f"_FakeSchemaHost ran out of scripted responses on call "
                f"{len(self.calls)} for service={service!r} etag={etag!r}"
            )
        return self._responses.pop(0)


@pytest.fixture(autouse=True)
def _clear_schema_cache():
    """Process-local cache leaks between tests if not cleared."""
    data_module._SCHEMA_CACHE.clear()
    yield
    data_module._SCHEMA_CACHE.clear()


SAMPLE_SCHEMA = {
    "service": "//blp/refdata",
    "operations": [
        {"name": "ReferenceDataRequest", "request_elements": [], "response_elements": []},
        {"name": "HistoricalDataRequest", "request_elements": [], "response_elements": []},
    ],
}


class TestGetServiceSchema:
    def test_first_call_fetches_and_caches(self):
        host = _FakeSchemaHost((SAMPLE_SCHEMA, "etag-abc"))
        result = get_service_schema(host, "//blp/refdata")
        assert result == SAMPLE_SCHEMA
        assert host.calls == [("//blp/refdata", None)]
        # Cached.
        assert data_module._SCHEMA_CACHE["//blp/refdata"] == ("etag-abc", SAMPLE_SCHEMA)

    def test_second_call_sends_if_none_match_and_returns_cached_on_304(self):
        host = _FakeSchemaHost(
            (SAMPLE_SCHEMA, "etag-abc"),
            (None, "etag-abc"),  # 304
        )
        get_service_schema(host, "//blp/refdata")
        result = get_service_schema(host, "//blp/refdata")
        assert result == SAMPLE_SCHEMA
        # Second call carried the cached etag.
        assert host.calls[1] == ("//blp/refdata", "etag-abc")

    def test_etag_change_replaces_cached_entry(self):
        new_schema = {**SAMPLE_SCHEMA, "operations": SAMPLE_SCHEMA["operations"][:1]}
        host = _FakeSchemaHost(
            (SAMPLE_SCHEMA, "etag-old"),
            (new_schema, "etag-new"),
        )
        get_service_schema(host, "//blp/refdata")
        result = get_service_schema(host, "//blp/refdata")
        assert result == new_schema
        assert data_module._SCHEMA_CACHE["//blp/refdata"] == ("etag-new", new_schema)
        # Second call still sent If-None-Match with the OLD etag.
        assert host.calls[1] == ("//blp/refdata", "etag-old")

    def test_force_bypasses_cache(self):
        host = _FakeSchemaHost(
            (SAMPLE_SCHEMA, "etag-abc"),
            (SAMPLE_SCHEMA, "etag-abc"),
        )
        get_service_schema(host, "//blp/refdata")
        get_service_schema(host, "//blp/refdata", force=True)
        # Forced call sent NO If-None-Match.
        assert host.calls[1] == ("//blp/refdata", None)

    def test_distinct_services_cached_independently(self):
        refdata_schema = {"service": "//blp/refdata", "operations": [{"name": "RefDataReq"}]}
        news_schema = {"service": "//blp/news", "operations": [{"name": "NewsReq"}]}
        host = _FakeSchemaHost(
            (refdata_schema, "rd-etag"),
            (news_schema, "news-etag"),
        )
        a = get_service_schema(host, "//blp/refdata")
        b = get_service_schema(host, "//blp/news")
        assert a != b
        assert data_module._SCHEMA_CACHE["//blp/refdata"][0] == "rd-etag"
        assert data_module._SCHEMA_CACHE["//blp/news"][0] == "news-etag"

    def test_304_without_prior_cache_falls_back_to_force(self):
        """If somehow we send If-None-Match without a cached body and
        get 304 back, we shouldn't return None silently. Recovery: do
        a forced fetch."""
        # Simulate: 304 first (e.g. server has stale etag from prior process),
        # then a successful fetch on the forced retry.
        host = _FakeSchemaHost(
            (None, "etag-abc"),
            (SAMPLE_SCHEMA, "etag-abc"),
        )
        # Manually nudge cache so we can test the 304-without-cache branch:
        # we DON'T pre-seed the cache, so the path is "fresh call returns 304".
        # The implementation falls back to force=True on the recursive call.
        result = get_service_schema(host, "//blp/refdata")
        assert result == SAMPLE_SCHEMA
        # Two calls: first sent etag=None (returned 304), second forced with etag=None.
        assert host.calls == [
            ("//blp/refdata", None),
            ("//blp/refdata", None),
        ]

    def test_no_etag_in_response_skips_caching(self):
        """If the server returns 200 without an ETag header, we serve
        the body but don't cache it (next call will fetch fresh)."""
        host = _FakeSchemaHost(
            (SAMPLE_SCHEMA, None),
            (SAMPLE_SCHEMA, None),
        )
        get_service_schema(host, "//blp/refdata")
        assert "//blp/refdata" not in data_module._SCHEMA_CACHE
        get_service_schema(host, "//blp/refdata")
        # Second call also sent etag=None since cache stayed empty.
        assert host.calls[1] == ("//blp/refdata", None)
