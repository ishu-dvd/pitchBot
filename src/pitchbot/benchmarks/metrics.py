from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass


def normalize_transcript(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    characters = [
        " " if unicodedata.category(character).startswith("P") else character
        for character in normalized
    ]
    return " ".join("".join(characters).split())


def edit_distance(reference: list[str], hypothesis: list[str]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for reference_index, reference_item in enumerate(reference, start=1):
        current = [reference_index]
        for hypothesis_index, hypothesis_item in enumerate(hypothesis, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[hypothesis_index] + 1,
                    previous[hypothesis_index - 1] + (reference_item != hypothesis_item),
                )
            )
        previous = current
    return previous[-1]


def word_error_rate(reference: str, hypothesis: str) -> float:
    reference_words = normalize_transcript(reference).split()
    hypothesis_words = normalize_transcript(hypothesis).split()
    if not reference_words:
        return 0.0 if not hypothesis_words else 1.0
    return edit_distance(reference_words, hypothesis_words) / len(reference_words)


def character_error_rate(reference: str, hypothesis: str) -> float:
    reference_characters = list(normalize_transcript(reference).replace(" ", ""))
    hypothesis_characters = list(normalize_transcript(hypothesis).replace(" ", ""))
    if not reference_characters:
        return 0.0 if not hypothesis_characters else 1.0
    return edit_distance(reference_characters, hypothesis_characters) / len(reference_characters)


def real_time_factor(processing_seconds: float, audio_seconds: float) -> float:
    if not math.isfinite(processing_seconds) or processing_seconds < 0:
        raise ValueError("processing_seconds must not be negative")
    if not math.isfinite(audio_seconds) or audio_seconds <= 0:
        raise ValueError("audio_seconds must be positive")
    return processing_seconds / audio_seconds


def structured_field_accuracy(
    reference: dict[str, object],
    hypothesis: dict[str, object],
) -> float:
    all_keys = reference.keys() | hypothesis.keys()
    if not all_keys:
        return 1.0 if not hypothesis else 0.0
    matches = sum(hypothesis.get(key) == value for key, value in reference.items())
    return matches / len(all_keys)


def relative_duration_delta(reference_seconds: float, measured_seconds: float) -> float:
    if (
        not math.isfinite(reference_seconds)
        or not math.isfinite(measured_seconds)
        or reference_seconds <= 0
        or measured_seconds < 0
    ):
        raise ValueError("durations require positive reference and non-negative measured values")
    return abs(measured_seconds - reference_seconds) / reference_seconds


@dataclass(frozen=True, slots=True)
class Interval:
    start_seconds: float
    end_seconds: float

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.start_seconds)
            or not math.isfinite(self.end_seconds)
            or self.start_seconds < 0
            or self.end_seconds <= self.start_seconds
        ):
            raise ValueError("interval must have non-negative start and positive duration")


def _merge(intervals: list[Interval]) -> list[Interval]:
    merged: list[Interval] = []
    for interval in sorted(intervals, key=lambda value: value.start_seconds):
        if not merged or interval.start_seconds > merged[-1].end_seconds:
            merged.append(interval)
        else:
            previous = merged[-1]
            merged[-1] = Interval(
                previous.start_seconds,
                max(previous.end_seconds, interval.end_seconds),
            )
    return merged


def _duration(intervals: list[Interval]) -> float:
    return sum(interval.end_seconds - interval.start_seconds for interval in intervals)


def _overlap(left: list[Interval], right: list[Interval]) -> float:
    total = 0.0
    left_index = 0
    right_index = 0
    while left_index < len(left) and right_index < len(right):
        left_value = left[left_index]
        right_value = right[right_index]
        total += max(
            0.0,
            min(left_value.end_seconds, right_value.end_seconds)
            - max(left_value.start_seconds, right_value.start_seconds),
        )
        if left_value.end_seconds <= right_value.end_seconds:
            left_index += 1
        else:
            right_index += 1
    return total


def vad_precision_recall_f1(
    reference: list[Interval],
    prediction: list[Interval],
) -> tuple[float, float, float]:
    merged_reference = _merge(reference)
    merged_prediction = _merge(prediction)
    overlap = _overlap(merged_reference, merged_prediction)
    predicted_duration = _duration(merged_prediction)
    reference_duration = _duration(merged_reference)
    if predicted_duration == 0 and reference_duration == 0:
        return 1.0, 1.0, 1.0
    precision = overlap / predicted_duration if predicted_duration else 0.0
    recall = overlap / reference_duration if reference_duration else 0.0
    if precision + recall == 0:
        return precision, recall, 0.0
    return precision, recall, 2 * precision * recall / (precision + recall)
