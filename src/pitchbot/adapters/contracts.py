from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from pitchbot.domain import JsonValue, LanguageCode


@dataclass(frozen=True, slots=True)
class AudioChunk:
    data: bytes
    captured_at: datetime
    sequence: int
    sample_rate_hz: int = 16_000

    def __post_init__(self) -> None:
        if self.captured_at.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware")
        if self.sequence < 0:
            raise ValueError("sequence must not be negative")
        if self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")


@dataclass(frozen=True, slots=True)
class TranscriptChunk:
    text: str
    language: LanguageCode
    confidence: float
    is_final: bool
    sequence: int

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.sequence < 0:
            raise ValueError("sequence must not be negative")


@dataclass(frozen=True, slots=True)
class SynthesizedAudioChunk:
    data: bytes
    sequence: int
    is_final: bool
    media_type: str = "audio/pcm"
    sample_rate_hz: int = 16_000

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("sequence must not be negative")
        if self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if not self.media_type.strip():
            raise ValueError("media_type must not be empty")


@dataclass(frozen=True, slots=True)
class StructuredCompletion:
    value: Mapping[str, JsonValue]
    model_version: str


@dataclass(frozen=True, slots=True)
class ActionResult:
    idempotency_key: str
    status: str
    provider_reference: str | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.idempotency_key.strip():
            raise ValueError("idempotency_key must not be empty")
        if not self.status.strip():
            raise ValueError("status must not be empty")


@dataclass(frozen=True, slots=True)
class ResearchResult:
    source_url: str
    title: str
    text: str
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)


class SpeechToTextAdapter(Protocol):
    def transcribe(
        self,
        audio: AsyncIterator[AudioChunk],
    ) -> AsyncIterator[TranscriptChunk]: ...


class TextToSpeechAdapter(Protocol):
    def synthesize(
        self,
        text: str,
        language: LanguageCode,
    ) -> AsyncIterator[SynthesizedAudioChunk]: ...


class ModelAdapter(Protocol):
    async def complete_structured(
        self,
        instruction: str,
        schema_name: str,
    ) -> StructuredCompletion: ...


@runtime_checkable
class EphemeralOperationStore(Protocol):
    def clear_operations(self, idempotency_key_prefix: str) -> None: ...


class TelephonyAdapter(Protocol):
    async def dial(self, contact_ref: str, idempotency_key: str) -> ActionResult: ...


class WhatsAppAdapter(Protocol):
    async def send_message(
        self,
        contact_ref: str,
        message: str,
        idempotency_key: str,
    ) -> ActionResult: ...


class SchedulerAdapter(Protocol):
    async def schedule(
        self,
        job_key: str,
        run_at: datetime,
        payload: Mapping[str, JsonValue],
        idempotency_key: str,
    ) -> ActionResult: ...

    async def cancel(self, job_key: str, idempotency_key: str) -> ActionResult: ...


class ResearchAdapter(Protocol):
    async def fetch(self, url: str) -> ResearchResult: ...


class ArtifactAdapter(Protocol):
    async def create(
        self,
        artifact_key: str,
        payload: Mapping[str, JsonValue],
        idempotency_key: str,
    ) -> ActionResult: ...


class ObjectStorageAdapter(Protocol):
    async def put(
        self,
        object_key: str,
        data: bytes,
        media_type: str,
        idempotency_key: str,
    ) -> ActionResult: ...

    async def get(self, object_key: str) -> bytes | None: ...
