"""Tests for the M5(B) per-user token-bucket rate limiter."""

from __future__ import annotations

import time

import pytest

from blpremote_server import rate_limit
from blpremote_server.config import settings
from blpremote_server.rate_limit import TokenBucket, check_rate_limit


@pytest.fixture(autouse=True)
def _isolate_rate_limit_settings():
    saved = (settings.rate_limit_per_minute, settings.rate_limit_burst)
    rate_limit.reset_for_tests()
    yield
    settings.rate_limit_per_minute, settings.rate_limit_burst = saved
    rate_limit.reset_for_tests()


class TestTokenBucket:
    def test_acquire_succeeds_until_bucket_empty(self):
        b = TokenBucket(capacity=3, refill_per_second=0.001)
        assert b.acquire() is True
        assert b.acquire() is True
        assert b.acquire() is True
        assert b.acquire() is False  # bucket drained

    def test_refill_restores_tokens_over_time(self):
        b = TokenBucket(capacity=2, refill_per_second=10.0)  # 10 tok/s
        # Anchor `now` to the bucket's internal clock so the test
        # math doesn't fight the monotonic construction time.
        t0 = b._last_refill
        b.acquire(now=t0)         # 1 left
        b.acquire(now=t0)         # 0 left
        assert b.acquire(now=t0) is False
        # 0.15s later: refill = 0.15 * 10 = 1.5 tokens. One acquire ok.
        assert b.acquire(now=t0 + 0.15) is True
        # Still 0.5 tokens — next acquire fails until more time passes.
        assert b.acquire(now=t0 + 0.15) is False

    def test_capacity_is_a_hard_ceiling(self):
        b = TokenBucket(capacity=2, refill_per_second=100.0)
        t0 = b._last_refill
        # A long virtual wait — bucket caps at capacity, doesn't
        # accumulate beyond it.
        assert b.acquire(now=t0 + 100.0) is True
        assert b.acquire(now=t0 + 100.0) is True
        assert b.acquire(now=t0 + 100.0) is False  # cap=2 enforced

    def test_init_rejects_nonpositive(self):
        with pytest.raises(ValueError):
            TokenBucket(capacity=0, refill_per_second=1.0)
        with pytest.raises(ValueError):
            TokenBucket(capacity=1, refill_per_second=0.0)


class TestPerUserCheck:
    def test_distinct_users_have_distinct_buckets(self):
        settings.rate_limit_per_minute = 60
        settings.rate_limit_burst = 1  # tiny — easy to drain
        rate_limit.reset_for_tests()

        # mac drains its bucket in one call; win still has its own.
        assert check_rate_limit("mac") is True
        assert check_rate_limit("mac") is False
        assert check_rate_limit("win") is True

    def test_zero_per_minute_disables_limiter(self):
        settings.rate_limit_per_minute = 0
        settings.rate_limit_burst = 1
        rate_limit.reset_for_tests()
        for _ in range(100):
            assert check_rate_limit("mac") is True

    def test_zero_burst_disables_limiter(self):
        settings.rate_limit_per_minute = 60
        settings.rate_limit_burst = 0
        rate_limit.reset_for_tests()
        for _ in range(100):
            assert check_rate_limit("mac") is True

    def test_settings_change_rebuilds_bucket(self):
        settings.rate_limit_per_minute = 60
        settings.rate_limit_burst = 1
        rate_limit.reset_for_tests()
        assert check_rate_limit("mac") is True
        assert check_rate_limit("mac") is False  # drained

        # Bump capacity — limiter notices on next call and rebuilds.
        settings.rate_limit_burst = 5
        # The new bucket starts full at capacity=5, so the next 5 succeed.
        for _ in range(5):
            assert check_rate_limit("mac") is True
        assert check_rate_limit("mac") is False

    def test_burst_absorbs_parallel_fan_out(self):
        settings.rate_limit_per_minute = 60
        settings.rate_limit_burst = 10
        rate_limit.reset_for_tests()
        # 10 immediate calls: all succeed (burst).
        for _ in range(10):
            assert check_rate_limit("mac") is True
        # 11th: throttled.
        assert check_rate_limit("mac") is False
