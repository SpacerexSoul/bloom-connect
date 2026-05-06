"""Tests for the coord (cross-machine messaging) module."""

import threading
import time

import pytest

from blpremote_server import coord


@pytest.fixture(autouse=True)
def _clear_state():
    coord.reset()
    yield
    coord.reset()


class TestPostDrain:
    def test_post_then_drain_returns_message(self):
        coord.post(to="win", sender="mac", body="hello")
        msgs = coord.drain("win")
        assert len(msgs) == 1
        assert msgs[0]["sender"] == "mac"
        assert msgs[0]["to"] == "win"
        assert msgs[0]["body"] == "hello"
        assert msgs[0]["ts"].endswith("Z")

    def test_drain_clears_inbox(self):
        coord.post(to="win", sender="mac", body="hello")
        coord.drain("win")
        assert coord.drain("win") == []

    def test_peek_does_not_clear(self):
        coord.post(to="win", sender="mac", body="hello")
        assert len(coord.drain("win", peek=True)) == 1
        assert len(coord.drain("win", peek=True)) == 1
        assert len(coord.drain("win")) == 1
        assert coord.drain("win") == []

    def test_inboxes_are_isolated_per_user(self):
        coord.post(to="win", sender="mac", body="for win")
        coord.post(to="mac", sender="win", body="for mac")
        win_msgs = coord.drain("win")
        mac_msgs = coord.drain("mac")
        assert [m["body"] for m in win_msgs] == ["for win"]
        assert [m["body"] for m in mac_msgs] == ["for mac"]

    def test_drain_unknown_user_is_empty(self):
        assert coord.drain("nobody") == []

    def test_messages_preserve_order(self):
        for i in range(5):
            coord.post(to="win", sender="mac", body=f"msg-{i}")
        msgs = coord.drain("win")
        assert [m["body"] for m in msgs] == [f"msg-{i}" for i in range(5)]


class TestLimits:
    def test_oversized_body_raises(self):
        oversized = "x" * (coord.BODY_MAX_BYTES + 1)
        with pytest.raises(ValueError, match="exceeds"):
            coord.post(to="win", sender="mac", body=oversized)

    def test_at_size_limit_accepted(self):
        on_limit = "x" * coord.BODY_MAX_BYTES
        coord.post(to="win", sender="mac", body=on_limit)
        msgs = coord.drain("win")
        assert len(msgs) == 1

    def test_inbox_caps_at_max(self):
        for i in range(coord.INBOX_MAX + 50):
            coord.post(to="win", sender="mac", body=str(i))
        msgs = coord.drain("win")
        assert len(msgs) == coord.INBOX_MAX
        # Oldest entries dropped, newest retained
        assert msgs[-1]["body"] == str(coord.INBOX_MAX + 49)


class TestLongPoll:
    def test_wait_returns_immediately_when_inbox_has_messages(self):
        coord.post(to="win", sender="mac", body="already here")
        t0 = time.monotonic()
        msgs = coord.drain("win", wait_ms=5000)
        elapsed = time.monotonic() - t0
        assert len(msgs) == 1
        assert elapsed < 0.1  # should not have waited

    def test_wait_returns_empty_after_timeout(self):
        t0 = time.monotonic()
        msgs = coord.drain("win", wait_ms=200)
        elapsed = time.monotonic() - t0
        assert msgs == []
        assert 0.18 < elapsed < 0.5  # waited ~200ms

    def test_wait_wakes_on_post_from_other_thread(self):
        """Drain blocks on an empty inbox; a post from another thread
        wakes it within a few ms."""
        result: dict = {}

        def waiter():
            t0 = time.monotonic()
            result["msgs"] = coord.drain("win", wait_ms=5000)
            result["elapsed"] = time.monotonic() - t0

        t = threading.Thread(target=waiter, daemon=True)
        t.start()
        time.sleep(0.05)  # let the waiter park on the condition
        coord.post(to="win", sender="mac", body="ping")
        t.join(timeout=2.0)

        assert len(result["msgs"]) == 1
        assert result["msgs"][0]["body"] == "ping"
        # Wake should be near-instant after post; allow generous slack
        # for CI scheduling jitter.
        assert result["elapsed"] < 0.5

    def test_wait_zero_means_no_blocking(self):
        """wait_ms=0 (the default) keeps today's poll-and-return semantics."""
        t0 = time.monotonic()
        msgs = coord.drain("win", wait_ms=0)
        elapsed = time.monotonic() - t0
        assert msgs == []
        assert elapsed < 0.05

    def test_wait_only_wakes_intended_recipient(self):
        """A post addressed to 'mac' must not wake a 'win' waiter."""
        wake_record: dict = {}

        def waiter():
            t0 = time.monotonic()
            wake_record["msgs"] = coord.drain("win", wait_ms=300)
            wake_record["elapsed"] = time.monotonic() - t0

        t = threading.Thread(target=waiter, daemon=True)
        t.start()
        time.sleep(0.05)
        # Post to a different recipient — waiter should not return early.
        coord.post(to="mac", sender="win", body="for mac")
        t.join(timeout=1.0)

        assert wake_record["msgs"] == []
        # Should have waited the full timeout even though notify_all
        # fired (recipient mismatch).
        assert wake_record["elapsed"] >= 0.28
