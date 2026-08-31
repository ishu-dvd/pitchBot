from __future__ import annotations

import math
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, model_validator

from pitchbot.domain import JsonValue, LanguageCode


class BenchmarkModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BenchmarkKind(StrEnum):
    VAD = "vad"
    STT = "stt"
    TTS = "tts"
    MODEL = "model"


class EvaluationRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class EvaluationCaseStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


class MetricDirection(StrEnum):
    AT_MOST = "at-most"
    AT_LEAST = "at-least"
    INFORMATIONAL = "informational"


type EvaluationLabel = Annotated[
    str,
    Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$"),
]


class CorpusAvailability(StrEnum):
    PLANNED = "planned"
    GENERATED = "generated"
    AVAILABLE = "available"


class SourceType(StrEnum):
    SYNTHETIC = "synthetic"
    LICENSED = "licensed"
    CONSENTED = "consented"


class Candidate(BenchmarkModel):
    candidate_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]+$")
    kinds: tuple[BenchmarkKind, ...] = Field(min_length=1)
    repository_url: HttpUrl
    repository_license: str
    repository_license_verified_at: AwareDatetime
    model_or_voice_license_required: bool = True
    notes: str = ""


class CandidateRegistry(BenchmarkModel):
    registry_version: str
    reviewed_at: AwareDatetime
    candidates: tuple[Candidate, ...] = Field(min_length=1)


class CorpusItem(BenchmarkModel):
    item_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]+$")
    language: LanguageCode
    source_type: SourceType
    availability: CorpusAvailability
    reference_text: str = Field(min_length=1)
    tags: tuple[str, ...] = ()
    audio_path: str | None = None
    audio_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    license_or_consent_ref: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_audio_evidence_when_available(self) -> CorpusItem:
        has_audio_evidence = self.audio_path is not None or self.audio_sha256 is not None
        if self.availability in {
            CorpusAvailability.GENERATED,
            CorpusAvailability.AVAILABLE,
        } and (self.audio_path is None or self.audio_sha256 is None):
            raise ValueError("generated or available audio requires path and sha256")
        if self.availability is CorpusAvailability.PLANNED and has_audio_evidence:
            raise ValueError("planned audio cannot include unverified path or checksum")
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("corpus tags must be unique")
        if self.source_type is not SourceType.SYNTHETIC and not self.license_or_consent_ref.strip():
            raise ValueError("licensed or consented data requires provenance")
        return self


class CorpusManifest(BenchmarkModel):
    manifest_id: str
    version: str
    purpose: str
    items: tuple[CorpusItem, ...] = Field(min_length=1)


class HardwareProfile(BenchmarkModel):
    operating_system: str
    architecture: str
    python_version: str
    processor: str
    logical_cpu_count: int | None = Field(default=None, ge=1)
    accelerator: str | None = None
    memory_note: str | None = None


class EvaluationMetric(BenchmarkModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra={
            "allOf": [
                {
                    "if": {
                        "properties": {"direction": {"const": "informational"}},
                        "required": ["direction"],
                    },
                    "then": {"properties": {"threshold": {"type": "null"}}},
                    "else": {
                        "properties": {"threshold": {"type": "number"}},
                        "required": ["threshold"],
                    },
                }
            ]
        },
    )

    name: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,63}$")
    value: float
    unit: str = Field(pattern=r"^[a-z][a-z0-9_/%.-]{0,31}$")
    direction: MetricDirection
    threshold: float | None = None

    @model_validator(mode="after")
    def validate_threshold(self) -> EvaluationMetric:
        if not math.isfinite(self.value):
            raise ValueError("evaluation metric value must be finite")
        if self.threshold is not None and not math.isfinite(self.threshold):
            raise ValueError("evaluation metric threshold must be finite")
        if self.direction is MetricDirection.INFORMATIONAL:
            if self.threshold is not None:
                raise ValueError("informational metrics cannot define a threshold")
        elif self.threshold is None:
            raise ValueError("gating metrics require a threshold")
        return self

    def meets_threshold(self) -> bool | None:
        if self.direction is MetricDirection.INFORMATIONAL:
            return None
        assert self.threshold is not None
        if self.direction is MetricDirection.AT_MOST:
            return self.value <= self.threshold
        return self.value >= self.threshold


class EvaluationCaseResult(BenchmarkModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra={
            "allOf": [
                {
                    "if": {
                        "properties": {"status": {"const": "passed"}},
                        "required": ["status"],
                    },
                    "then": {"properties": {"failure_codes": {"maxItems": 0}}},
                    "else": {
                        "properties": {"failure_codes": {"minItems": 1}},
                        "required": ["failure_codes"],
                    },
                }
            ]
        },
    )

    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,127}$")
    status: EvaluationCaseStatus
    language: LanguageCode
    industry: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")
    persona: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")
    duration_ms: float = Field(ge=0)
    tags: tuple[EvaluationLabel, ...] = Field(default=(), max_length=32)
    metrics: tuple[EvaluationMetric, ...] = Field(default=(), max_length=128)
    failure_codes: tuple[EvaluationLabel, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def validate_case_result(self) -> EvaluationCaseResult:
        if not math.isfinite(self.duration_ms):
            raise ValueError("case duration must be finite")
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("evaluation case tags must be unique")
        metric_names = [metric.name for metric in self.metrics]
        if len(metric_names) != len(set(metric_names)):
            raise ValueError("evaluation case metric names must be unique")
        if self.status is EvaluationCaseStatus.PASSED and self.failure_codes:
            raise ValueError("passed evaluation cases cannot contain failure codes")
        if self.status is not EvaluationCaseStatus.PASSED and not self.failure_codes:
            raise ValueError("failed evaluation cases require a failure code")
        return self


class EvaluationHardwareProfile(BenchmarkModel):
    operating_system: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._+@()/-]{0,127}$")
    architecture: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._+@()/-]{0,63}$")
    python_version: str = Field(pattern=r"^[0-9][0-9a-z.+-]{0,31}$")
    processor: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._+@()/-]{0,127}$")
    logical_cpu_count: int | None = Field(default=None, ge=1, le=4_096)
    physical_memory_bytes: int | None = Field(default=None, ge=1, le=2**63 - 1)
    accelerator: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._+@()/-]{0,127}$",
    )


class EvaluationRun(BenchmarkModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        json_schema_extra={
            "allOf": [
                {
                    "if": {
                        "properties": {"status": {"const": "running"}},
                        "required": ["status"],
                    },
                    "then": {
                        "properties": {
                            "completed_at": {"type": "null"},
                            "run_failure_code": {"type": "null"},
                        }
                    },
                },
                {
                    "if": {
                        "properties": {"status": {"const": "completed"}},
                        "required": ["status"],
                    },
                    "then": {
                        "properties": {
                            "cases": {"minItems": 1},
                            "completed_at": {"type": "string", "format": "date-time"},
                            "run_failure_code": {"type": "null"},
                        },
                        "required": ["cases", "completed_at"],
                    },
                },
                {
                    "if": {
                        "properties": {"status": {"const": "failed"}},
                        "required": ["status"],
                    },
                    "then": {
                        "properties": {
                            "completed_at": {"type": "string", "format": "date-time"},
                            "run_failure_code": {
                                "type": "string",
                                "pattern": "^[a-z0-9][a-z0-9._-]{1,63}$",
                            },
                        },
                        "required": ["completed_at", "run_failure_code"],
                    },
                },
            ]
        },
    )

    evaluation_schema_version: Literal["1"]
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,127}$")
    status: EvaluationRunStatus
    git_revision: str = Field(pattern=r"^[a-f0-9]{7,64}$")
    suite_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,127}$")
    suite_version: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    suite_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    corpus_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,127}$")
    corpus_version: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    corpus_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    configuration_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    hardware: EvaluationHardwareProfile
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    metrics: tuple[EvaluationMetric, ...] = Field(default=(), max_length=256)
    cases: tuple[EvaluationCaseResult, ...] = Field(default=(), max_length=2_000)
    run_failure_code: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$",
    )

    @model_validator(mode="after")
    def validate_run(self) -> EvaluationRun:
        placeholders = {"unknown", "pending", "placeholder", "noassertion"}
        if self.git_revision.strip().casefold() in placeholders:
            raise ValueError("git_revision must identify an exact revision")
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        metric_names = [metric.name for metric in self.metrics]
        if len(metric_names) != len(set(metric_names)):
            raise ValueError("evaluation run metric names must be unique")
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("evaluation case identifiers must be unique")
        if self.status is EvaluationRunStatus.RUNNING:
            if self.completed_at is not None or self.run_failure_code is not None:
                raise ValueError("running evaluations cannot contain completion fields")
        elif self.completed_at is None:
            raise ValueError("finished evaluations require completed_at")
        if self.status is EvaluationRunStatus.COMPLETED:
            if not self.cases:
                raise ValueError("completed evaluations require case results")
            if self.run_failure_code is not None:
                raise ValueError("completed evaluations cannot contain a run failure code")
        if self.status is EvaluationRunStatus.FAILED and self.run_failure_code is None:
            raise ValueError("failed evaluations require a run failure code")
        return self

    def gates_pass(self) -> bool:
        if self.status is not EvaluationRunStatus.COMPLETED or not self.cases:
            return False
        all_metrics = (*self.metrics, *(metric for case in self.cases for metric in case.metrics))
        gating_results = [
            result for metric in all_metrics if (result := metric.meets_threshold()) is not None
        ]
        return (
            bool(gating_results)
            and all(case.status is EvaluationCaseStatus.PASSED for case in self.cases)
            and all(gating_results)
        )


class BenchmarkResult(BenchmarkModel):
    result_schema_version: Literal["1"] = "1"
    measurement_source: Literal["measured"] = "measured"
    run_id: str
    kind: BenchmarkKind
    candidate_id: str
    candidate_revision: str = Field(min_length=7)
    model_or_voice_id: str
    model_or_voice_license: str
    corpus_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    hardware: HardwareProfile
    configuration: dict[str, JsonValue]
    metrics: dict[str, float]
    measured_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def reject_placeholder_or_invalid_measurement(self) -> BenchmarkResult:
        placeholders = {"", "unknown", "pending", "placeholder", "noassertion"}
        if self.candidate_revision.strip().casefold() in placeholders:
            raise ValueError("candidate_revision must identify an exact revision")
        if self.model_or_voice_license.strip().casefold() in placeholders:
            raise ValueError("model_or_voice_license must be verified")
        if not self.metrics:
            raise ValueError("measured result requires metrics")
        if any(not key.strip() or not math.isfinite(value) for key, value in self.metrics.items()):
            raise ValueError("metric names must be non-empty and values finite")
        return self
