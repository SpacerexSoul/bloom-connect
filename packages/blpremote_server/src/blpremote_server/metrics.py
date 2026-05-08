"""Tiny Prometheus-compatible metrics registry.

Pure-stdlib, ~200 LOC. Avoids pulling in ``prometheus_client`` since
this server is meant to stay light. The exposition format matches
the Prometheus text protocol so any compatible scraper (Grafana
Agent, vmagent, OpenTelemetry collector) can ingest it.

Usage::

    from blpremote_server.metrics import requests_total, request_duration

    requests_total.inc(endpoint="/v1/execute", status="ok")
    request_duration.observe(0.327, endpoint="/v1/execute")

Render via :func:`render_exposition` from the ``/metrics`` endpoint.

Contention:
    Each metric carries one ``threading.Lock`` (no global registry
    lock). Counter increments under the lock are O(1) on a defaultdict
    keyed by the label tuple — fine for any sane request volume on a
    single-server deployment.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Iterable

# Bucket boundaries tuned for HTTP request latency in seconds.
# Covers sub-millisecond cache hits through ~10s historical bulk pulls.
DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
)


def _escape_label_value(s: str) -> str:
    """Escape a label value per Prometheus exposition format."""
    return s.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _render_labels(label_names: tuple[str, ...], label_values: tuple[str, ...]) -> str:
    if not label_names:
        return ""
    parts = [
        f'{n}="{_escape_label_value(str(v))}"'
        for n, v in zip(label_names, label_values)
    ]
    return "{" + ",".join(parts) + "}"


class Counter:
    """Monotonic counter — only goes up. Resets across process restarts."""

    def __init__(self, name: str, help: str, label_names: tuple[str, ...] = ()):
        self.name = name
        self.help = help
        self.label_names = label_names
        self._values: dict[tuple[str, ...], float] = defaultdict(float)
        self._lock = threading.Lock()

    def inc(self, *, value: float = 1.0, **labels: str) -> None:
        if set(labels) != set(self.label_names):
            raise ValueError(
                f"Counter {self.name} expects labels {self.label_names}, "
                f"got {tuple(labels)}"
            )
        key = tuple(str(labels[n]) for n in self.label_names)
        with self._lock:
            self._values[key] += value

    def reset(self) -> None:
        with self._lock:
            self._values.clear()

    def render(self) -> str:
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} counter"]
        with self._lock:
            if not self._values and not self.label_names:
                # Make the counter visible at zero even if never incremented —
                # otherwise scrapers can't tell "wired but quiet" from "unwired".
                lines.append(f"{self.name} 0")
            for key, val in sorted(self._values.items()):
                lines.append(f"{self.name}{_render_labels(self.label_names, key)} {val}")
        return "\n".join(lines)


class Histogram:
    """Histogram — counts observations by upper bucket boundary.

    Renders three series per label-set per scrape:
    ``<name>_bucket{le="..."}``, ``<name>_sum``, ``<name>_count``.
    """

    def __init__(
        self,
        name: str,
        help: str,
        label_names: tuple[str, ...] = (),
        buckets: Iterable[float] = DEFAULT_BUCKETS,
    ):
        self.name = name
        self.help = help
        self.label_names = label_names
        self.buckets = tuple(sorted(buckets))
        # _counts[label_key] = list of cumulative counts per bucket (last is +Inf)
        self._counts: dict[tuple[str, ...], list[int]] = defaultdict(
            lambda: [0] * (len(self.buckets) + 1)
        )
        self._sums: dict[tuple[str, ...], float] = defaultdict(float)
        self._lock = threading.Lock()

    def observe(self, value: float, **labels: str) -> None:
        if set(labels) != set(self.label_names):
            raise ValueError(
                f"Histogram {self.name} expects labels {self.label_names}, "
                f"got {tuple(labels)}"
            )
        key = tuple(str(labels[n]) for n in self.label_names)
        with self._lock:
            counts = self._counts[key]
            # Each bucket is cumulative ("le" = less-than-or-equal).
            for i, upper in enumerate(self.buckets):
                if value <= upper:
                    counts[i] += 1
            counts[-1] += 1  # +Inf bucket = total count
            self._sums[key] += value

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()
            self._sums.clear()

    def render(self) -> str:
        lines = [
            f"# HELP {self.name} {self.help}",
            f"# TYPE {self.name} histogram",
        ]
        with self._lock:
            for key in sorted(self._counts.keys() | self._sums.keys()):
                counts = self._counts.get(key, [0] * (len(self.buckets) + 1))
                total = self._sums.get(key, 0.0)
                base = self.name
                label_str_no_le = _render_labels(self.label_names, key)
                # Bucket lines.
                for i, upper in enumerate(self.buckets):
                    le_label = f'le="{upper}"'
                    if label_str_no_le:
                        full_labels = label_str_no_le[:-1] + "," + le_label + "}"
                    else:
                        full_labels = "{" + le_label + "}"
                    lines.append(f"{base}_bucket{full_labels} {counts[i]}")
                # +Inf line.
                if label_str_no_le:
                    inf_labels = label_str_no_le[:-1] + ',le="+Inf"}'
                else:
                    inf_labels = '{le="+Inf"}'
                lines.append(f"{base}_bucket{inf_labels} {counts[-1]}")
                lines.append(f"{base}_sum{label_str_no_le} {total}")
                lines.append(f"{base}_count{label_str_no_le} {counts[-1]}")
        return "\n".join(lines)


class Registry:
    def __init__(self) -> None:
        self._metrics: list = []
        self._lock = threading.Lock()

    def register(self, metric) -> None:
        with self._lock:
            self._metrics.append(metric)

    def render(self) -> str:
        with self._lock:
            metrics = list(self._metrics)
        return "\n".join(m.render() for m in metrics) + "\n"

    def reset_all(self) -> None:
        """For tests."""
        with self._lock:
            metrics = list(self._metrics)
        for m in metrics:
            m.reset()


# --- Module-level metrics ------------------------------------------------

REGISTRY = Registry()

requests_total = Counter(
    "blpremote_requests_total",
    "Total HTTP requests served, by endpoint and outcome.",
    label_names=("endpoint", "status"),
)
REGISTRY.register(requests_total)

request_duration = Histogram(
    "blpremote_request_duration_seconds",
    "HTTP request handling latency in seconds.",
    label_names=("endpoint",),
)
REGISTRY.register(request_duration)

audit_lines_total = Counter(
    "blpremote_audit_lines_total",
    "Total audit-log lines written across all sinks.",
)
REGISTRY.register(audit_lines_total)

schema_cache_hits_total = Counter(
    "blpremote_schema_cache_hits_total",
    "Schema cache lookups served from in-memory cache (no introspect).",
    label_names=("service",),
)
REGISTRY.register(schema_cache_hits_total)

schema_cache_misses_total = Counter(
    "blpremote_schema_cache_misses_total",
    "Schema cache lookups that fell through to a fresh blpapi introspect.",
    label_names=("service",),
)
REGISTRY.register(schema_cache_misses_total)

request_cache_hits_total = Counter(
    "blpremote_request_cache_hits_total",
    "Plan executions served from the in-process LRU+TTL cache (no blpapi hit).",
)
REGISTRY.register(request_cache_hits_total)

request_cache_misses_total = Counter(
    "blpremote_request_cache_misses_total",
    "Plan executions that fell through to a fresh blpapi execution.",
)
REGISTRY.register(request_cache_misses_total)

rate_limited_total = Counter(
    "blpremote_rate_limited_total",
    "Requests rejected with HTTP 429 because the user's token bucket was empty.",
    label_names=("user",),
)
REGISTRY.register(rate_limited_total)


def render_exposition() -> str:
    """Render the entire registry as Prometheus exposition text."""
    return REGISTRY.render()
