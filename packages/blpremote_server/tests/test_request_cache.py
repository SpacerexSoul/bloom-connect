"""Tests for the LRU+TTL request cache."""

from __future__ import annotations

import time

import pytest

from blpremote_server import request_cache
from blpremote_server.config import settings
from blpremote_server.models import (
    AppendOp,
    AuthToken,
    CollectResponseOp,
    CreateRequestOp,
    ErrorDetail,
    ExecutionPlan,
    ExecutionResult,
    OpenServiceOp,
    SendRequestOp,
    StartSessionOp,
)


def _refdata_plan(securities: list[str], fields: list[str]) -> ExecutionPlan:
    ops = [
        StartSessionOp(),
        OpenServiceOp(service="//blp/refdata"),
        CreateRequestOp(service="//blp/refdata", request="ReferenceDataRequest", id="r1"),
    ]
    for s in securities:
        ops.append(AppendOp(id="r1", path="securities", value=s))
    for f in fields:
        ops.append(AppendOp(id="r1", path="fields", value=f))
    ops += [
        SendRequestOp(id="r1", correlation_id="cid"),
        CollectResponseOp(correlation_id="cid", timeout_ms=10000),
    ]
    return ExecutionPlan(auth=AuthToken(token="t"), ops=ops)


def _ok_result(plan: ExecutionPlan, data: dict) -> ExecutionResult:
    return ExecutionResult(request_id=str(plan.request_id), status="ok", data=data)


@pytest.fixture(autouse=True)
def _isolate_cache_settings():
    saved_ttl = settings.request_cache_ttl_s
    saved_max = settings.request_cache_max_entries
    request_cache.reset_for_tests()
    yield
    settings.request_cache_ttl_s = saved_ttl
    settings.request_cache_max_entries = saved_max
    request_cache.reset_for_tests()


class TestCacheBasics:
    def test_put_then_get_returns_same_result(self):
        c = request_cache.RequestCache(max_entries=10, ttl_s=60.0)
        plan = _refdata_plan(["AAPL US Equity"], ["PX_LAST"])
        result = _ok_result(plan, {"AAPL US Equity": {"PX_LAST": 285.0}})
        c.put(plan, result)
        cached = c.get(plan)
        assert cached is not None
        assert cached.data == {"AAPL US Equity": {"PX_LAST": 285.0}}

    def test_get_on_empty_cache_returns_none(self):
        c = request_cache.RequestCache(max_entries=10, ttl_s=60.0)
        plan = _refdata_plan(["AAPL US Equity"], ["PX_LAST"])
        assert c.get(plan) is None

    def test_different_plans_do_not_collide(self):
        c = request_cache.RequestCache(max_entries=10, ttl_s=60.0)
        a = _refdata_plan(["AAPL US Equity"], ["PX_LAST"])
        b = _refdata_plan(["MSFT US Equity"], ["PX_LAST"])
        c.put(a, _ok_result(a, {"AAPL US Equity": {"PX_LAST": 285}}))
        c.put(b, _ok_result(b, {"MSFT US Equity": {"PX_LAST": 410}}))
        assert c.get(a).data == {"AAPL US Equity": {"PX_LAST": 285}}
        assert c.get(b).data == {"MSFT US Equity": {"PX_LAST": 410}}

    def test_identical_ir_with_different_request_ids_collides(self):
        """ir_hash excludes request_id by design — two callers with
        the same IR get the same cache hit (intentional dedup)."""
        c = request_cache.RequestCache(max_entries=10, ttl_s=60.0)
        a = _refdata_plan(["AAPL US Equity"], ["PX_LAST"])
        b = _refdata_plan(["AAPL US Equity"], ["PX_LAST"])
        # a and b have different request_ids but identical IR.
        assert a.request_id != b.request_id
        c.put(a, _ok_result(a, {"AAPL US Equity": {"PX_LAST": 285}}))
        cached = c.get(b)
        assert cached is not None
        assert cached.data == {"AAPL US Equity": {"PX_LAST": 285}}


class TestCacheTTL:
    def test_entry_expires_after_ttl(self):
        c = request_cache.RequestCache(max_entries=10, ttl_s=0.05)
        plan = _refdata_plan(["AAPL US Equity"], ["PX_LAST"])
        c.put(plan, _ok_result(plan, {"AAPL US Equity": {"PX_LAST": 285}}))
        assert c.get(plan) is not None
        time.sleep(0.07)
        assert c.get(plan) is None

    def test_ttl_zero_means_caching_disabled(self):
        c = request_cache.RequestCache(max_entries=10, ttl_s=0.0)
        plan = _refdata_plan(["AAPL US Equity"], ["PX_LAST"])
        c.put(plan, _ok_result(plan, {"AAPL US Equity": {"PX_LAST": 285}}))
        # put() short-circuits when ttl_s <= 0; nothing was stored.
        assert c.get(plan) is None
        assert len(c) == 0


class TestCacheLRU:
    def test_lru_evicts_oldest_when_full(self):
        c = request_cache.RequestCache(max_entries=2, ttl_s=60.0)
        a = _refdata_plan(["AAPL US Equity"], ["PX_LAST"])
        b = _refdata_plan(["MSFT US Equity"], ["PX_LAST"])
        d = _refdata_plan(["GOOG US Equity"], ["PX_LAST"])
        c.put(a, _ok_result(a, {}))
        c.put(b, _ok_result(b, {}))
        c.put(d, _ok_result(d, {}))  # evicts 'a' (oldest)
        assert c.get(a) is None
        assert c.get(b) is not None
        assert c.get(d) is not None

    def test_get_bumps_lru_position(self):
        c = request_cache.RequestCache(max_entries=2, ttl_s=60.0)
        a = _refdata_plan(["AAPL US Equity"], ["PX_LAST"])
        b = _refdata_plan(["MSFT US Equity"], ["PX_LAST"])
        d = _refdata_plan(["GOOG US Equity"], ["PX_LAST"])
        c.put(a, _ok_result(a, {}))
        c.put(b, _ok_result(b, {}))
        c.get(a)             # bump 'a' to most-recently-used
        c.put(d, _ok_result(d, {}))  # evicts 'b' now, not 'a'
        assert c.get(a) is not None
        assert c.get(b) is None
        assert c.get(d) is not None


class TestCacheStatusGate:
    def test_partial_status_not_cached(self):
        c = request_cache.RequestCache(max_entries=10, ttl_s=60.0)
        plan = _refdata_plan(["AAPL US Equity"], ["PX_LAST", "BAD"])
        result = ExecutionResult(
            request_id=str(plan.request_id),
            status="partial",
            data={"AAPL US Equity": {"PX_LAST": 285}},
            errors=[ErrorDetail(code="BLP_FIELD_BAD_FLD", message="...", field="BAD")],
        )
        c.put(plan, result)
        assert c.get(plan) is None
        assert len(c) == 0

    def test_error_status_not_cached(self):
        c = request_cache.RequestCache(max_entries=10, ttl_s=60.0)
        plan = _refdata_plan(["AAPL US Equity"], ["PX_LAST"])
        result = ExecutionResult(
            request_id=str(plan.request_id), status="error",
            errors=[ErrorDetail(code="BLP_TIMEOUT", message="...")],
        )
        c.put(plan, result)
        assert c.get(plan) is None


class TestSingleton:
    def test_get_request_cache_returns_none_when_disabled(self):
        settings.request_cache_ttl_s = 0
        request_cache.reset_for_tests()
        assert request_cache.get_request_cache() is None

    def test_get_request_cache_lazy_builds_when_enabled(self):
        settings.request_cache_ttl_s = 5.0
        settings.request_cache_max_entries = 100
        request_cache.reset_for_tests()
        c = request_cache.get_request_cache()
        assert c is not None
        assert c.ttl_s == 5.0
        assert c.max_entries == 100
        # Same instance on second call.
        assert request_cache.get_request_cache() is c

    def test_init_rejects_nonpositive_max_entries(self):
        with pytest.raises(ValueError, match="max_entries"):
            request_cache.RequestCache(max_entries=0, ttl_s=60.0)
