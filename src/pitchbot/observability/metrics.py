"""Counters and histograms, so "what is p95 turn latency in production" has an answer.

It did not before. The turn path measured `transcribe_ms` and `engine_ms`, sent them to the
one browser that asked, and forgot them - which meant every latency claim in this repository
came from a probe script run by hand on one machine, and `docs/BENCHMARKS.md` correctly
refused to publish the shipped numbers as benchmark results.

Dependency-free and in-process, for the same reason the rate limiter is: `prometheus_client`
brings a registry, a multiprocess mode and an exposition server to express what is a few
hundred lines here, and this repository consistently prefers a small verified implementation
to a dependency it would then have to keep optional.

**Label cardinality is bounded by construction.** Labels come from closed sets - a language, a
stage name, an outcome - and never from a session id, a lead reference or anything else a
caller chooses. An unbounded label space is how a metrics registry becomes the memory leak it
was added to detect, which is the same failure the rate limiter avoids by keying on
credentials rather than client addresses. :meth:`MetricsRegistry.counter` enforces this by
refusing a series once the configured ceiling is reached, rather than growing quietly.

Exposition is Prometheus text format. Not because anything here depends on Prometheus, but
because it is the one format every scraper already reads, and emitting it costs a `join`.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Mapping, Sequence
from typing import Final

MAX_SERIES: Final[int] = 2_000
"""Ceiling on distinct label combinations, across all metrics.

Reached only by a bug - every label in this codebase comes from a closed set - so hitting it
means a caller has started labelling by something unbounded, and the useful behaviour is to
stop recording rather than to keep allocating.
"""

DEFAULT_DURATION_BUCKETS_MS: Final[tuple[float, ...]] = (
    50.0,
    100.0,
    250.0,
    500.0,
    1_000.0,
    2_000.0,
    3_000.0,
    5_000.0,
    10_000.0,
    30_000.0,
)
"""Bucket edges chosen from what this system actually does, not from a template.

The interesting region is 2-5 s, where a spoken turn lives. 30 s exists because Telugu
transcription was measured at 37.7 s: a bucket set that topped out at 10 s would have hidden
the worst number in the project inside `+Inf`.
"""


def _key(name: str, labels: Mapping[str, str] | None) -> tuple[str, tuple[tuple[str, str], ...]]:
    return name, tuple(sorted((labels or {}).items()))


def _render_labels(
    labels: tuple[tuple[str, str], ...],
    extra: tuple[str, str] | None = None,
) -> str:
    pairs = list(labels)
    if extra is not None:
        pairs.append(extra)
    if not pairs:
        return ""
    rendered = ",".join(f'{name}="{_escape(value)}"' for name, value in pairs)
    return "{" + rendered + "}"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class Counter:
    """A monotonically increasing count."""

    __slots__ = ("_value", "_lock")

    def __init__(self) -> None:
        self._value = 0.0
        self._lock = threading.Lock()

    def increment(self, amount: float = 1.0) -> None:
        if amount < 0:
            raise ValueError("a counter cannot decrease")
        with self._lock:
            self._value += amount

    @property
    def value(self) -> float:
        return self._value


class Histogram:
    """Cumulative buckets, a sum and a count - enough to compute a quantile."""

    __slots__ = ("_buckets", "_counts", "_sum", "_count", "_lock")

    def __init__(self, buckets: Sequence[float]) -> None:
        ordered = tuple(sorted(float(edge) for edge in buckets))
        if not ordered:
            raise ValueError("a histogram needs at least one bucket")
        self._buckets = ordered
        self._counts = [0 for _ in ordered]
        self._sum = 0.0
        self._count = 0
        self._lock = threading.Lock()

    def observe(self, value: float) -> None:
        if math.isnan(value):
            # A NaN would poison the sum forever and silently corrupt every later average.
            return
        with self._lock:
            self._sum += value
            self._count += 1
            for index, edge in enumerate(self._buckets):
                if value <= edge:
                    self._counts[index] += 1

    @property
    def count(self) -> int:
        return self._count

    @property
    def sum(self) -> float:
        return self._sum

    def quantile(self, fraction: float) -> float:
        """Bucket-interpolated quantile, for tests and for a human-readable summary.

        Honest about its resolution: the answer is the upper edge of the bucket the quantile
        falls in, so it is an upper bound rather than an estimate that looks more precise
        than the data supports.
        """

        if not 0.0 < fraction <= 1.0:
            raise ValueError("fraction must be in (0, 1]")
        if self._count == 0:
            return math.nan
        target = fraction * self._count
        for index, edge in enumerate(self._buckets):
            if self._counts[index] >= target:
                return edge
        return math.inf


class MetricsRegistry:
    """Named counters and histograms, addressed by name plus labels."""

    def __init__(self, *, max_series: int = MAX_SERIES) -> None:
        if max_series < 1:
            raise ValueError("max_series must be at least 1")
        self._max_series = max_series
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], Counter] = {}
        self._histograms: dict[tuple[str, tuple[tuple[str, str], ...]], Histogram] = {}
        self._help: dict[str, str] = {}
        self._dropped_series = 0
        self._lock = threading.Lock()

    @property
    def dropped_series(self) -> int:
        """Series refused because the ceiling was reached. Non-zero means a labelling bug."""

        return self._dropped_series

    def describe(self, name: str, help_text: str) -> None:
        self._help[name] = help_text

    def counter(self, name: str, labels: Mapping[str, str] | None = None) -> Counter:
        key = _key(name, labels)
        with self._lock:
            existing = self._counters.get(key)
            if existing is not None:
                return existing
            if len(self._counters) + len(self._histograms) >= self._max_series:
                self._dropped_series += 1
                return Counter()  # unregistered, so it is recorded nowhere and leaks nothing
            created = Counter()
            self._counters[key] = created
            return created

    def histogram(
        self,
        name: str,
        labels: Mapping[str, str] | None = None,
        *,
        buckets: Sequence[float] = DEFAULT_DURATION_BUCKETS_MS,
    ) -> Histogram:
        key = _key(name, labels)
        with self._lock:
            existing = self._histograms.get(key)
            if existing is not None:
                return existing
            if len(self._counters) + len(self._histograms) >= self._max_series:
                self._dropped_series += 1
                return Histogram(buckets)
            created = Histogram(buckets)
            self._histograms[key] = created
            return created

    def render(self) -> str:
        """Prometheus text exposition."""

        lines: list[str] = []
        with self._lock:
            counters = sorted(self._counters.items())
            histograms = sorted(self._histograms.items())
            help_text = dict(self._help)

        emitted: set[str] = set()
        for (name, labels), counter in counters:
            if name not in emitted:
                if name in help_text:
                    lines.append(f"# HELP {name} {help_text[name]}")
                lines.append(f"# TYPE {name} counter")
                emitted.add(name)
            lines.append(f"{name}{_render_labels(labels)} {counter.value:g}")

        for (name, labels), histogram in histograms:
            if name not in emitted:
                if name in help_text:
                    lines.append(f"# HELP {name} {help_text[name]}")
                lines.append(f"# TYPE {name} histogram")
                emitted.add(name)
            cumulative = 0
            for index, edge in enumerate(histogram._buckets):  # noqa: SLF001 - same module
                cumulative = histogram._counts[index]  # noqa: SLF001
                rendered = _render_labels(labels, ("le", f"{edge:g}"))
                lines.append(f"{name}_bucket{rendered} {cumulative}")
            lines.append(f"{name}_bucket{_render_labels(labels, ('le', '+Inf'))} {histogram.count}")
            lines.append(f"{name}_sum{_render_labels(labels)} {histogram.sum:g}")
            lines.append(f"{name}_count{_render_labels(labels)} {histogram.count}")

        lines.append("# TYPE pitchbot_metrics_dropped_series_total counter")
        lines.append(f"pitchbot_metrics_dropped_series_total {self._dropped_series}")
        return "\n".join(lines) + "\n"


__all__ = [
    "DEFAULT_DURATION_BUCKETS_MS",
    "MAX_SERIES",
    "Counter",
    "Histogram",
    "MetricsRegistry",
]
