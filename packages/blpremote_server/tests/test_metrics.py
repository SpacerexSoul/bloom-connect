"""Tests for the in-process metrics registry."""

from __future__ import annotations

import re

import pytest

from blpremote_server.metrics import (
    Counter,
    Histogram,
    Registry,
    REGISTRY,
    audit_lines_total,
    render_exposition,
    request_duration,
    requests_total,
    schema_cache_hits_total,
    schema_cache_misses_total,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    REGISTRY.reset_all()
    yield
    REGISTRY.reset_all()


class TestCounter:
    def test_unlabeled_counter_renders_zero_when_unused(self):
        c = Counter("c", "help")
        text = c.render()
        assert "# TYPE c counter" in text
        assert "c 0" in text

    def test_increment_unlabeled(self):
        c = Counter("c", "help")
        c.inc()
        c.inc(value=4)
        assert "c 5.0" in c.render()

    def test_increment_with_labels(self):
        c = Counter("requests", "help", label_names=("endpoint", "status"))
        c.inc(endpoint="/v1/execute", status="ok")
        c.inc(endpoint="/v1/execute", status="ok")
        c.inc(endpoint="/v1/execute", status="error")
        text = c.render()
        assert 'requests{endpoint="/v1/execute",status="ok"} 2.0' in text
        assert 'requests{endpoint="/v1/execute",status="error"} 1.0' in text

    def test_label_set_mismatch_raises(self):
        c = Counter("c", "help", label_names=("a",))
        with pytest.raises(ValueError, match="expects labels"):
            c.inc(b="oops")

    def test_label_value_with_quotes_escapes(self):
        c = Counter("c", "help", label_names=("path",))
        c.inc(path='weird"path')
        # Escaped \" inside the label value, not a raw quote.
        assert 'path="weird\\"path"' in c.render()


class TestHistogram:
    def test_observation_bumps_correct_buckets(self):
        h = Histogram(
            "h", "help", label_names=("endpoint",), buckets=(0.1, 1.0, 10.0),
        )
        h.observe(0.05, endpoint="/x")  # under 0.1 — all 3 buckets + +Inf
        h.observe(0.5, endpoint="/x")   # under 1.0 — 1.0, 10.0, +Inf
        h.observe(20.0, endpoint="/x")  # over 10.0 — only +Inf
        text = h.render()
        # Bucket 0.1 should count 1 (only the 0.05).
        assert re.search(r'h_bucket\{endpoint="/x",le="0.1"\} 1', text)
        # Bucket 1.0 should count 2 (0.05 + 0.5).
        assert re.search(r'h_bucket\{endpoint="/x",le="1.0"\} 2', text)
        # Bucket 10.0 should count 2.
        assert re.search(r'h_bucket\{endpoint="/x",le="10.0"\} 2', text)
        # +Inf bucket = total count = 3.
        assert re.search(r'h_bucket\{endpoint="/x",le="\+Inf"\} 3', text)
        # Sum + count series.
        assert re.search(r'h_sum\{endpoint="/x"\} 20\.55', text)
        assert re.search(r'h_count\{endpoint="/x"\} 3', text)

    def test_no_observations_renders_empty(self):
        h = Histogram("h", "help", label_names=("endpoint",))
        text = h.render()
        # Type comment present, but no series for endpoints we never saw.
        assert "# TYPE h histogram" in text
        assert "h_bucket" not in text

    def test_observation_label_mismatch_raises(self):
        h = Histogram("h", "help", label_names=("endpoint",))
        with pytest.raises(ValueError, match="expects labels"):
            h.observe(1.0, oops="x")


class TestRegistryRender:
    def test_render_includes_all_default_metrics(self):
        text = render_exposition()
        # Even with zero observations, the counter HELP/TYPE comments
        # should make the metric discoverable.
        assert "blpremote_requests_total" in text
        assert "blpremote_request_duration_seconds" in text
        assert "blpremote_audit_lines_total" in text
        assert "blpremote_schema_cache_hits_total" in text
        assert "blpremote_schema_cache_misses_total" in text

    def test_counters_actually_increment_via_module_singletons(self):
        requests_total.inc(endpoint="/v1/execute", status="ok")
        audit_lines_total.inc()
        schema_cache_hits_total.inc(service="//blp/refdata")
        schema_cache_misses_total.inc(service="//blp/news")
        request_duration.observe(0.327, endpoint="/v1/execute")

        text = render_exposition()
        assert 'blpremote_requests_total{endpoint="/v1/execute",status="ok"} 1.0' in text
        assert "blpremote_audit_lines_total 1.0" in text
        assert 'blpremote_schema_cache_hits_total{service="//blp/refdata"} 1.0' in text
        assert 'blpremote_schema_cache_misses_total{service="//blp/news"} 1.0' in text
        assert 'blpremote_request_duration_seconds_bucket{endpoint="/v1/execute",le="1.0"} 1' in text
        assert 'blpremote_request_duration_seconds_count{endpoint="/v1/execute"} 1' in text

    def test_render_ends_with_newline(self):
        # Prometheus requires the exposition end with a newline.
        assert render_exposition().endswith("\n")
