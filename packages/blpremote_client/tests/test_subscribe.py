"""Tests for the M3 subscribe() client.

The SSE wire-format parsing is unit-testable without a live server.
The HTTP layer is exercised separately with a Mock httpx.Client so
we can assert request shape (URL, query params, Accept header) and
synthetic response streams.
"""

from typing import Iterator
from unittest.mock import MagicMock, patch

import httpx
import pytest

from blpremote_client.exceptions import (
    AuthenticationError,
    ConnectionError as RemoteConnectionError,
)
from blpremote_client.subscribe import _parse_sse_stream, subscribe


class TestParseSSEStream:
    """The parser turns a raw line iterator into ``{event, data}`` dicts."""

    def _frames(self, raw: str) -> list[dict]:
        # SSE lines come in with trailing newlines; iter_lines strips them.
        # Match that behaviour for the parser tests.
        return list(_parse_sse_stream(iter(raw.split("\n"))))

    def test_single_event_with_json_data(self):
        raw = 'event: subscription_data\ndata: {"price": 285.0}\n\n'
        frames = self._frames(raw)
        assert frames == [{"event": "subscription_data", "data": {"price": 285.0}}]

    def test_multiple_events_dispatch_on_blank_line(self):
        raw = (
            'event: subscribed\ndata: {"cid": "1"}\n\n'
            'event: subscription_status\ndata: {"reason": "started"}\n\n'
            'event: subscription_data\ndata: {"BID": 287}\n\n'
        )
        frames = self._frames(raw)
        assert [f["event"] for f in frames] == [
            "subscribed", "subscription_status", "subscription_data",
        ]
        assert frames[2]["data"]["BID"] == 287

    def test_data_with_leading_space_after_colon_stripped(self):
        # SSE spec allows ``data: foo`` (with the space) — strip just the one.
        raw = 'event: ping\ndata: {"ts": 1}\n\n'
        frames = self._frames(raw)
        assert frames[0]["data"] == {"ts": 1}

    def test_multiline_data_concatenated_with_newline(self):
        # Per SSE spec, multiple data: lines are joined with \n.
        raw = 'event: error\ndata: line1\ndata: line2\n\n'
        frames = self._frames(raw)
        # Not valid JSON — fall back to raw string.
        assert frames == [{"event": "error", "data": "line1\nline2"}]

    def test_comments_ignored(self):
        raw = (
            ': this is a keepalive comment\n'
            'event: ping\ndata: {}\n\n'
        )
        frames = self._frames(raw)
        assert frames == [{"event": "ping", "data": {}}]

    def test_event_name_defaults_to_message_when_missing(self):
        raw = 'data: {"x": 1}\n\n'
        frames = self._frames(raw)
        assert frames == [{"event": "message", "data": {"x": 1}}]

    def test_invalid_json_falls_back_to_raw_string(self):
        raw = 'event: subscription_data\ndata: not-json{{\n\n'
        frames = self._frames(raw)
        assert frames[0]["event"] == "subscription_data"
        assert frames[0]["data"] == "not-json{{"

    def test_empty_stream_yields_no_frames(self):
        assert self._frames("") == []
        assert self._frames("\n\n\n") == []

    def test_no_trailing_blank_line_drops_last_frame(self):
        # Per SSE spec a frame is only "dispatched" on blank line. Last
        # event without a trailing blank line is intentionally dropped —
        # surfaces incomplete buffer issues clearly rather than silently.
        raw = 'event: subscription_data\ndata: {"x": 1}'
        frames = self._frames(raw)
        assert frames == []


class _FakeStreamResponse:
    """Minimal stand-in for httpx's stream response context manager."""

    def __init__(self, status_code: int, lines: list[str], headers: dict | None = None):
        self.status_code = status_code
        self._lines = lines
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_lines(self) -> Iterator[str]:
        return iter(self._lines)

    def read(self) -> bytes:
        return "\n".join(self._lines).encode()


class _FakeHost:
    """Stub for RemoteHost used by subscribe tests."""

    def __init__(self):
        self.host = "https://fake.example"
        self._token_manager = MagicMock()

    def _get_token(self) -> str:
        return "fake-token"


class TestSubscribe:
    def _stream_lines(self, frames: list[tuple[str, str]]) -> list[str]:
        """Turn ``[(event, data), ...]`` into the line stream httpx
        would yield from a real SSE response."""
        out: list[str] = []
        for event, data in frames:
            out.append(f"event: {event}")
            out.append(f"data: {data}")
            out.append("")  # blank line dispatches the frame
        return out

    def _patched_subscribe(
        self,
        host,
        response: _FakeStreamResponse,
        **kwargs,
    ):
        """Run subscribe() with the httpx.Client.stream call patched
        to return our scripted response. Returns the list of frames
        the iterator yielded."""
        with patch("httpx.Client") as ClientCls:
            client_instance = MagicMock()
            client_instance.__enter__.return_value = client_instance
            client_instance.stream.return_value = response
            ClientCls.return_value = client_instance
            return list(subscribe(host, "AAPL US Equity", ["LAST_PRICE"], **kwargs))

    def test_yields_frames_from_stream(self):
        host = _FakeHost()
        response = _FakeStreamResponse(
            status_code=200,
            lines=self._stream_lines([
                ("subscribed", '{"cid": "cid-1"}'),
                ("subscription_status", '{"message_type": "SubscriptionStarted"}'),
                ("subscription_data", '{"fields": {"LAST_PRICE": 285.5}}'),
            ]),
        )
        frames = self._patched_subscribe(host, response)
        assert [f["event"] for f in frames] == [
            "subscribed", "subscription_status", "subscription_data",
        ]
        assert frames[2]["data"]["fields"]["LAST_PRICE"] == 285.5

    def test_filters_pings_by_default(self):
        host = _FakeHost()
        response = _FakeStreamResponse(
            status_code=200,
            lines=self._stream_lines([
                ("subscribed", "{}"),
                ("ping", '{"ts": 1}'),
                ("subscription_data", '{"fields": {"BID": 100}}'),
                ("ping", '{"ts": 2}'),
            ]),
        )
        frames = self._patched_subscribe(host, response)
        events = [f["event"] for f in frames]
        assert "ping" not in events
        assert events == ["subscribed", "subscription_data"]

    def test_with_pings_true_passes_them_through(self):
        host = _FakeHost()
        response = _FakeStreamResponse(
            status_code=200,
            lines=self._stream_lines([
                ("ping", '{"ts": 1}'),
                ("subscription_data", '{"fields": {}}'),
                ("ping", '{"ts": 2}'),
            ]),
        )
        frames = self._patched_subscribe(host, response, with_pings=True)
        assert [f["event"] for f in frames] == ["ping", "subscription_data", "ping"]

    def test_with_status_false_filters_status_events(self):
        host = _FakeHost()
        response = _FakeStreamResponse(
            status_code=200,
            lines=self._stream_lines([
                ("subscribed", "{}"),
                ("subscription_status", '{"message_type": "SubscriptionStarted"}'),
                ("subscription_data", '{"fields": {"BID": 100}}'),
            ]),
        )
        frames = self._patched_subscribe(host, response, with_status=False)
        assert [f["event"] for f in frames] == ["subscribed", "subscription_data"]

    def test_401_raises_authentication_error(self):
        host = _FakeHost()
        response = _FakeStreamResponse(status_code=401, lines=[])
        with pytest.raises(AuthenticationError):
            self._patched_subscribe(host, response)
        host._token_manager.clear_token.assert_called_once()

    def test_5xx_raises_connection_error_with_body(self):
        host = _FakeHost()
        response = _FakeStreamResponse(
            status_code=503, lines=["service unavailable"]
        )
        with pytest.raises(RemoteConnectionError, match="HTTP 503"):
            self._patched_subscribe(host, response)

    def test_connect_error_raises_connection_error(self):
        host = _FakeHost()
        with patch("httpx.Client") as ClientCls:
            client_instance = MagicMock()
            client_instance.__enter__.return_value = client_instance
            client_instance.stream.side_effect = httpx.ConnectError("refused")
            ClientCls.return_value = client_instance
            with pytest.raises(RemoteConnectionError, match="Cannot connect"):
                list(subscribe(host, "AAPL US Equity", ["LAST_PRICE"]))

    def test_url_includes_topic_and_fields(self):
        host = _FakeHost()
        response = _FakeStreamResponse(status_code=200, lines=self._stream_lines([
            ("subscribed", "{}"),
        ]))
        with patch("httpx.Client") as ClientCls:
            client_instance = MagicMock()
            client_instance.__enter__.return_value = client_instance
            client_instance.stream.return_value = response
            ClientCls.return_value = client_instance
            list(subscribe(host, "AAPL US Equity", ["LAST_PRICE", "BID"]))
            args, kwargs = client_instance.stream.call_args
            url = args[1] if len(args) > 1 else kwargs.get("url", "")
            assert "/v1/subscribe?" in url
            assert "topic=AAPL+US+Equity" in url or "topic=AAPL%20US%20Equity" in url
            assert "fields=LAST_PRICE%2CBID" in url

    def test_options_join_into_query_param(self):
        host = _FakeHost()
        response = _FakeStreamResponse(status_code=200, lines=self._stream_lines([
            ("subscribed", "{}"),
        ]))
        with patch("httpx.Client") as ClientCls:
            client_instance = MagicMock()
            client_instance.__enter__.return_value = client_instance
            client_instance.stream.return_value = response
            ClientCls.return_value = client_instance
            list(subscribe(
                host, "AAPL US Equity", ["LAST_PRICE"],
                options={"interval": "1.0"},
            ))
            args, kwargs = client_instance.stream.call_args
            url = args[1] if len(args) > 1 else kwargs.get("url", "")
            assert "options=interval%3D1.0" in url

    def test_authorization_header_present(self):
        host = _FakeHost()
        response = _FakeStreamResponse(status_code=200, lines=self._stream_lines([
            ("subscribed", "{}"),
        ]))
        with patch("httpx.Client") as ClientCls:
            client_instance = MagicMock()
            client_instance.__enter__.return_value = client_instance
            client_instance.stream.return_value = response
            ClientCls.return_value = client_instance
            list(subscribe(host, "AAPL US Equity", ["LAST_PRICE"]))
            _, kwargs = client_instance.stream.call_args
            headers = kwargs["headers"]
            assert headers["Authorization"] == "Bearer fake-token"
            assert headers["Accept"] == "text/event-stream"
