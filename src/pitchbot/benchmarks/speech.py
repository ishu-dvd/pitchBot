"""Synthetic voice-activity (VAD) structural benchmark: suite schema and runner.

This runner is honest about a hard limit set by ADR-0004: no speech provider may be
selected without a reproducible measured result on reviewed audio. Voice-activity
detection is the one speech capability whose *structure* - speech vs silence, onsets,
pauses, bursts - can be generated deterministically without a model, so this is the only
speech dimension this harness can measure today. It runs a synthetic corpus through the
existing ``VoiceActivityDetector`` contract, scores overlap precision/recall/F1 per
language / condition / vertical slice, measures real-time factor and peak allocation, and
gates on F1. The emitted artifact is a *synthetic-VAD structural* result: it is not a
model selection, and it is emphatically not an STT or TTS measurement.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import tracemalloc
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic_ns

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pitchbot.adapters.contracts import AudioChunk, VoiceActivity, VoiceActivityDetector
from pitchbot.adapters.errors import AdapterError
from pitchbot.adapters.mocks import MockVoiceActivityDetector
from pitchbot.benchmarks.audio import (
    GENERATOR_VERSION,
    ClipSpec,
    SegmentKind,
    SegmentSpec,
    SyntheticClip,
    frames_to_intervals,
    generate_clip,
)
from pitchbot.benchmarks.manifest import canonical_manifest_sha256, load_json_model
from pitchbot.benchmarks.metrics import real_time_factor, vad_precision_recall_f1
from pitchbot.benchmarks.models import (
    EvaluationCaseResult,
    EvaluationCaseStatus,
    EvaluationHardwareProfile,
    EvaluationLabel,
    EvaluationMetric,
    EvaluationRun,
    EvaluationRunStatus,
    MetricDirection,
)
from pitchbot.domain import LanguageCode

_CAPTURE_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


class SpeechSuiteModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VadSuiteSegment(SpeechSuiteModel):
    kind: SegmentKind
    duration_ms: int = Field(ge=1, le=60_000)


class VadSuiteCase(SpeechSuiteModel):
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,127}$")
    language: LanguageCode
    vertical: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")
    condition: EvaluationLabel
    persona: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")
    seed: int = Field(ge=0, le=2**63 - 1)
    audio_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    segments: tuple[VadSuiteSegment, ...] = Field(min_length=1, max_length=64)
    tags: tuple[EvaluationLabel, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def validate_case(self) -> VadSuiteCase:
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("vad case tags must be unique")
        return self


class VadSuite(SpeechSuiteModel):
    suite_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,127}$")
    version: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    corpus_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,127}$")
    corpus_version: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    frame_ms: int = Field(default=20, ge=1, le=100)
    sample_rate_hz: int = Field(default=16_000, ge=8_000, le=48_000)
    speech_threshold_bytes: int = Field(default=512, ge=1, le=65_536)
    min_f1: float = Field(default=0.85, ge=0.0, le=1.0)
    cases: tuple[VadSuiteCase, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_suite(self) -> VadSuite:
        case_ids = [item.case_id for item in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("vad case identifiers must be unique")
        if (self.sample_rate_hz * self.frame_ms) % 1_000 != 0:
            raise ValueError("frame must contain a whole number of samples")
        for case in self.cases:
            for segment in case.segments:
                if segment.duration_ms % self.frame_ms != 0:
                    raise ValueError(
                        f"segment duration must be a multiple of frame_ms: {case.case_id}"
                    )
        return self


def _clip_spec(case: VadSuiteCase, suite: VadSuite) -> ClipSpec:
    return ClipSpec(
        seed=case.seed,
        segments=tuple(SegmentSpec(segment.kind, segment.duration_ms) for segment in case.segments),
        sample_rate_hz=suite.sample_rate_hz,
        frame_ms=suite.frame_ms,
    )


def build_case_clip(case: VadSuiteCase, suite: VadSuite) -> SyntheticClip:
    return generate_clip(_clip_spec(case, suite))


def verify_and_build_clips(suite: VadSuite) -> tuple[SyntheticClip, ...]:
    """Regenerate every case and verify its committed hash - the corpus rot guard."""

    clips: list[SyntheticClip] = []
    for case in suite.cases:
        clip = build_case_clip(case, suite)
        if clip.sha256 != case.audio_sha256:
            raise ValueError(f"synthetic audio checksum mismatch: {case.case_id}")
        clips.append(clip)
    return tuple(clips)


def load_speech_suite(path: Path) -> VadSuite:
    return load_json_model(path, VadSuite)


def validate_speech_suite(path: Path) -> VadSuite:
    suite = load_speech_suite(path)
    verify_and_build_clips(suite)
    return suite


def _default_detector_factory(suite: VadSuite) -> Callable[[], VoiceActivityDetector]:
    threshold = suite.speech_threshold_bytes
    return lambda: MockVoiceActivityDetector(speech_threshold_bytes=threshold)


def _detect_clip(
    clip: SyntheticClip,
    suite: VadSuite,
    detector: VoiceActivityDetector,
    clock: Callable[[], int],
) -> tuple[list[bool], float, bool]:
    predicted: list[bool] = []
    error = False
    started = clock()
    for index, frame in enumerate(clip.frames):
        chunk = AudioChunk(
            data=frame,
            captured_at=_CAPTURE_EPOCH,
            sequence=index,
            sample_rate_hz=suite.sample_rate_hz,
        )
        try:
            activity: VoiceActivity = detector.detect(chunk)
        except AdapterError:
            error = True
            break
        predicted.append(activity.is_speech)
    processing_seconds = max(0, clock() - started) / 1_000_000_000
    return predicted, processing_seconds, error


def _run_vad_case(
    case: VadSuiteCase,
    clip: SyntheticClip,
    suite: VadSuite,
    *,
    detector_factory: Callable[[], VoiceActivityDetector],
    clock: Callable[[], int],
) -> EvaluationCaseResult:
    detector = detector_factory()
    predicted, processing_seconds, error = _detect_clip(clip, suite, detector, clock)
    rtf = real_time_factor(processing_seconds, clip.audio_seconds)
    if error:
        precision = recall = f1 = 0.0
        status = EvaluationCaseStatus.ERROR
        failure_codes: tuple[str, ...] = ("vad-detector-error",)
    else:
        precision, recall, f1 = vad_precision_recall_f1(
            list(clip.truth_intervals),
            list(frames_to_intervals(tuple(predicted), suite.frame_ms)),
        )
        if f1 >= suite.min_f1:
            status = EvaluationCaseStatus.PASSED
            failure_codes = ()
        else:
            status = EvaluationCaseStatus.FAILED
            failure_codes = ("vad-f1-below-threshold",)
    tags = tuple(dict.fromkeys((case.condition, case.vertical, *case.tags)))
    return EvaluationCaseResult(
        case_id=case.case_id,
        status=status,
        language=case.language,
        industry=case.vertical,
        persona=case.persona,
        duration_ms=processing_seconds * 1_000,
        tags=tags,
        metrics=(
            EvaluationMetric(
                name="speech.vad_f1",
                value=f1,
                unit="ratio",
                direction=MetricDirection.AT_LEAST,
                threshold=suite.min_f1,
            ),
            EvaluationMetric(
                name="speech.vad_precision",
                value=precision,
                unit="ratio",
                direction=MetricDirection.INFORMATIONAL,
            ),
            EvaluationMetric(
                name="speech.vad_recall",
                value=recall,
                unit="ratio",
                direction=MetricDirection.INFORMATIONAL,
            ),
            EvaluationMetric(
                name="speech.real_time_factor",
                value=rtf,
                unit="ratio",
                direction=MetricDirection.INFORMATIONAL,
            ),
        ),
        failure_codes=failure_codes,
    )


def _score_cases(
    suite: VadSuite,
    clips: tuple[SyntheticClip, ...],
    detector_factory: Callable[[], VoiceActivityDetector],
    clock: Callable[[], int],
) -> tuple[float, tuple[EvaluationCaseResult, ...]]:
    already_tracing = tracemalloc.is_tracing()
    if not already_tracing:
        tracemalloc.start()
    try:
        results = tuple(
            _run_vad_case(case, clip, suite, detector_factory=detector_factory, clock=clock)
            for case, clip in zip(suite.cases, clips, strict=True)
        )
        peak_bytes = 0 if already_tracing else tracemalloc.get_traced_memory()[1]
    finally:
        if not already_tracing:
            tracemalloc.stop()
    return peak_bytes / 1_024, results


def _metric_value(case: EvaluationCaseResult, name: str) -> float:
    return next(metric.value for metric in case.metrics if metric.name == name)


def _slice_means(
    keys: list[str],
    case_results: tuple[EvaluationCaseResult, ...],
) -> dict[str, float]:
    grouped: dict[str, list[float]] = {}
    for key, case in zip(keys, case_results, strict=True):
        grouped.setdefault(key, []).append(_metric_value(case, "speech.vad_f1"))
    return {key: sum(values) / len(values) for key, values in sorted(grouped.items())}


def _slice_metrics(prefix: str, means: dict[str, float], min_f1: float) -> list[EvaluationMetric]:
    return [
        EvaluationMetric(
            name=f"{prefix}.{key}",
            value=value,
            unit="ratio",
            direction=MetricDirection.AT_LEAST,
            threshold=min_f1,
        )
        for key, value in means.items()
    ]


def _real_time_factor_metric(
    name: str,
    value: float,
    max_real_time_factor: float | None,
) -> EvaluationMetric:
    if max_real_time_factor is None:
        return EvaluationMetric(
            name=name,
            value=value,
            unit="ratio",
            direction=MetricDirection.INFORMATIONAL,
        )
    return EvaluationMetric(
        name=name,
        value=value,
        unit="ratio",
        direction=MetricDirection.AT_MOST,
        threshold=max_real_time_factor,
    )


def run_speech_evaluation(
    path: Path,
    *,
    run_id: str,
    git_revision: str,
    detector_factory: Callable[[], VoiceActivityDetector] | None = None,
    clock: Callable[[], int] = monotonic_ns,
    max_real_time_factor: float | None = None,
) -> EvaluationRun:
    suite = load_speech_suite(path)
    clips = verify_and_build_clips(suite)
    manifest_hash = canonical_manifest_sha256(path)
    factory = detector_factory or _default_detector_factory(suite)
    configuration_hash = hashlib.sha256(
        json.dumps(
            {
                "detector": "mock-voice-activity-detector",
                "frame_ms": suite.frame_ms,
                "generator_version": GENERATOR_VERSION,
                "max_real_time_factor": max_real_time_factor,
                "measurement_kind": "synthetic-vad-structural",
                "model_selection": False,
                "sample_rate_hz": suite.sample_rate_hz,
                "speech_threshold_bytes": suite.speech_threshold_bytes,
                "stt_tts": "blocked-pending-reviewed-audio",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    started_at = datetime.now(UTC)
    peak_python_kib, case_results = _score_cases(suite, clips, factory, clock)
    completed_at = datetime.now(UTC)

    f1_values = [_metric_value(item, "speech.vad_f1") for item in case_results]
    real_time_factors = sorted(
        _metric_value(item, "speech.real_time_factor") for item in case_results
    )
    p95_index = max(0, (95 * len(real_time_factors) + 99) // 100 - 1)
    language_means = _slice_means([case.language.value for case in suite.cases], case_results)
    condition_means = _slice_means([case.condition for case in suite.cases], case_results)
    vertical_means = _slice_means([case.vertical for case in suite.cases], case_results)

    metrics = (
        EvaluationMetric(
            name="speech.vad_mean_f1",
            value=sum(f1_values) / len(f1_values),
            unit="ratio",
            direction=MetricDirection.AT_LEAST,
            threshold=suite.min_f1,
        ),
        EvaluationMetric(
            name="speech.vad_min_f1",
            value=min(f1_values),
            unit="ratio",
            direction=MetricDirection.AT_LEAST,
            threshold=suite.min_f1,
        ),
        *_slice_metrics("speech.vad_f1.lang", language_means, suite.min_f1),
        *_slice_metrics("speech.vad_f1.cond", condition_means, suite.min_f1),
        *_slice_metrics("speech.vad_f1.vert", vertical_means, suite.min_f1),
        _real_time_factor_metric(
            "speech.mean_real_time_factor",
            sum(real_time_factors) / len(real_time_factors),
            max_real_time_factor,
        ),
        _real_time_factor_metric(
            "speech.p95_real_time_factor",
            real_time_factors[p95_index],
            max_real_time_factor,
        ),
        EvaluationMetric(
            name="speech.peak_python_kib",
            value=peak_python_kib,
            unit="kib",
            direction=MetricDirection.INFORMATIONAL,
        ),
    )
    return EvaluationRun(
        evaluation_schema_version="1",
        run_id=run_id,
        status=EvaluationRunStatus.COMPLETED,
        git_revision=git_revision,
        suite_id=suite.suite_id,
        suite_version=suite.version,
        suite_manifest_sha256=manifest_hash,
        corpus_id=suite.corpus_id,
        corpus_version=suite.corpus_version,
        corpus_manifest_sha256=manifest_hash,
        configuration_sha256=configuration_hash,
        hardware=EvaluationHardwareProfile(
            operating_system=_hardware_label(platform.platform(), fallback="not-reported"),
            architecture=_hardware_label(
                platform.machine(),
                fallback="not-reported",
                maximum_length=64,
            ),
            python_version=platform.python_version(),
            processor=_hardware_label(platform.processor(), fallback="not-reported"),
            logical_cpu_count=os.cpu_count(),
        ),
        started_at=started_at,
        completed_at=completed_at,
        metrics=metrics,
        cases=case_results,
    )


def _hardware_label(
    value: str,
    *,
    fallback: str,
    maximum_length: int = 128,
) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9 ._+@()/-]", " ", value)
    sanitized = " ".join(sanitized.split())[:maximum_length].rstrip()
    return sanitized if sanitized and sanitized[0].isalnum() else fallback


__all__ = [
    "SegmentKind",
    "VadSuite",
    "VadSuiteCase",
    "VadSuiteSegment",
    "build_case_clip",
    "load_speech_suite",
    "run_speech_evaluation",
    "validate_speech_suite",
    "verify_and_build_clips",
]
