from __future__ import annotations

import math
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, model_validator

from pitchbot.domain import JsonValue, LanguageCode


class BenchmarkModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BenchmarkKind(StrEnum):
    VAD = "vad"
    STT = "stt"
    TTS = "tts"
    MODEL = "model"


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
