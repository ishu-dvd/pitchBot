"""Counters, histograms, and the cardinality ceiling that keeps them from leaking."""

from __future__ import annotations

import math

import pytest

from pitchbot.observability.metrics import (
    DEFAULT_DURATION_BUCKETS_MS,
    Counter,
    Histogram,
    MetricsRegistry,
)
from pitchbot.observability.turn_metrics import (
    TURN_STAGE_MS,
    TurnStage,
    record_stage,
)


def test_counter_accumulates() -> None:
    counter = Counter()
    counter.increment()
    counter.increment(4)
    assert counter.value == 5


def test_counter_refuses_to_decrease() -> None:
    with pytest.raises(ValueError, match="cannot decrease"):
        Counter().increment(-1)


def test_histogram_needs_buckets() -> None:
    with pytest.raises(ValueError, match="at least one bucket"):
        Histogram([])


def test_histogram_counts_are_cumulative() -> None:
    histogram = Histogram([10, 100, 1000])
    for value in (5, 50, 500, 5000):
        histogram.observe(value)
    assert histogram.count == 4
    assert histogram.sum == 5555
    # 5 <= 10; 5 and 50 <= 100; 5, 50 and 500 <= 1000; 5000 exceeds every edge.
    assert histogram._counts == [1, 2, 3]  # noqa: SLF001 - asserting the wire shape


def test_histogram_quantile_is_an_upper_bound() -> None:
    histogram = Histogram([10, 100, 1000])
    for value in (5, 5, 5, 900):
        histogram.observe(value)
    assert histogram.quantile(0.5) == 10
    assert histogram.quantile(1.0) == 1000


def test_quantile_of_nothing_is_not_a_number_rather_than_zero() -> None:
    """Zero would read as 'instant'. NaN reads as 'no data', which is the truth."""

    assert math.isnan(Histogram([10]).quantile(0.5))


def test_a_nan_observation_is_ignored_rather_than_poisoning_the_sum() -> None:
    histogram = Histogram([10])
    histogram.observe(5)
    histogram.observe(float("nan"))
    assert histogram.count == 1
    assert histogram.sum == 5


def test_buckets_reach_the_worst_number_this_project_has_measured() -> None:
    """Telugu transcription was 37.7 s; a 10 s ceiling would have hidden it in +Inf."""

    assert max(DEFAULT_DURATION_BUCKETS_MS) >= 30_000


def test_same_name_and_labels_return_the_same_series() -> None:
    registry = MetricsRegistry()
    first = registry.counter("turns", {"language": "en"})
    second = registry.counter("turns", {"language": "en"})
    assert first is second


def test_label_order_does_not_create_a_second_series() -> None:
    registry = MetricsRegistry()
    first = registry.counter("turns", {"a": "1", "b": "2"})
    second = registry.counter("turns", {"b": "2", "a": "1"})
    assert first is second


def test_cardinality_ceiling_stops_recording_rather_than_growing() -> None:
    """An unbounded label space is how a metrics registry becomes a memory leak."""

    registry = MetricsRegistry(max_series=3)
    for index in range(10):
        registry.counter("leaky", {"session": str(index)}).increment()
    assert registry.dropped_series == 7
    rendered = registry.render()
    assert rendered.count("leaky{") == 3


def test_dropped_series_are_reported_so_the_bug_is_visible() -> None:
    registry = MetricsRegistry(max_series=1)
    registry.counter("a", {"x": "1"})
    registry.counter("b", {"x": "2"})
    assert "pitchbot_metrics_dropped_series_total 1" in registry.render()


def test_max_series_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_series must be at least 1"):
        MetricsRegistry(max_series=0)


def test_render_emits_prometheus_text_format() -> None:
    registry = MetricsRegistry()
    registry.describe("pitchbot_turns_total", "Turns completed.")
    registry.counter("pitchbot_turns_total", {"language": "en"}).increment(3)
    registry.histogram("pitchbot_stage_ms", {"stage": "transcribe"}, buckets=[100, 1000]).observe(
        250
    )
    rendered = registry.render()

    assert "# HELP pitchbot_turns_total Turns completed." in rendered
    assert "# TYPE pitchbot_turns_total counter" in rendered
    assert 'pitchbot_turns_total{language="en"} 3' in rendered
    assert "# TYPE pitchbot_stage_ms histogram" in rendered
    assert 'pitchbot_stage_ms_bucket{stage="transcribe",le="1000"} 1' in rendered
    assert 'pitchbot_stage_ms_bucket{stage="transcribe",le="+Inf"} 1' in rendered
    assert 'pitchbot_stage_ms_sum{stage="transcribe"} 250' in rendered
    assert 'pitchbot_stage_ms_count{stage="transcribe"} 1' in rendered


def test_label_values_are_escaped() -> None:
    registry = MetricsRegistry()
    registry.counter("weird", {"label": 'a"b\\c'}).increment()
    assert 'a\\"b\\\\c' in registry.render()


def test_turn_stages_are_recorded_under_a_bounded_label_set() -> None:
    record_stage(TurnStage.TRANSCRIBE, 2407.0, language="en")
    from pitchbot.observability.turn_metrics import registry as turn_registry

    rendered = turn_registry.render()
    assert TURN_STAGE_MS in rendered
    assert 'stage="transcribe"' in rendered


def test_every_stage_name_is_a_closed_vocabulary_value() -> None:
    assert {str(stage) for stage in TurnStage} == {
        "detect_language",
        "transcribe",
        "plan",
        "synthesize",
        "total",
    }
