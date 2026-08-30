import pytest

from pitchbot.benchmarks import (
    Interval,
    character_error_rate,
    real_time_factor,
    relative_duration_delta,
    structured_field_accuracy,
    vad_precision_recall_f1,
    word_error_rate,
)


def test_transcript_metrics_normalize_case_punctuation_and_unicode() -> None:
    assert word_error_rate("Hello, WORLD!", "hello world") == 0
    assert character_error_rate("नमस्ते।", "नमस्ते") == 0
    assert word_error_rate("budget next week", "budget this week") == pytest.approx(1 / 3)


def test_vad_metrics_measure_overlap_and_correct_silence() -> None:
    precision, recall, f1 = vad_precision_recall_f1(
        [Interval(0, 2)],
        [Interval(1, 3)],
    )
    assert precision == 0.5
    assert recall == 0.5
    assert f1 == 0.5
    assert vad_precision_recall_f1([], []) == (1.0, 1.0, 1.0)


def test_runtime_and_structured_metrics_validate_inputs() -> None:
    assert real_time_factor(0.5, 2) == 0.25
    assert relative_duration_delta(2, 2.5) == 0.25
    assert (
        structured_field_accuracy(
            {"intent": "warm", "language": "mixed"},
            {"intent": "warm", "language": "en"},
        )
        == 0.5
    )

    with pytest.raises(ValueError, match="audio_seconds"):
        real_time_factor(1, 0)
    with pytest.raises(ValueError, match="durations"):
        relative_duration_delta(0, 1)
    with pytest.raises(ValueError, match="interval"):
        Interval(float("nan"), 1)
    with pytest.raises(ValueError, match="processing_seconds"):
        real_time_factor(float("inf"), 1)
