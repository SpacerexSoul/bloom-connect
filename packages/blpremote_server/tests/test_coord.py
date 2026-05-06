"""Tests for the coord (cross-machine messaging) module."""

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
