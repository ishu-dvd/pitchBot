from __future__ import annotations

import hashlib
import json
from collections import deque
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime

from pitchbot.adapters.contracts import (
    ActionResult,
    ArtifactAdapter,
    AudioChunk,
    ModelAdapter,
    ObjectStorageAdapter,
    ResearchAdapter,
    ResearchResult,
    SchedulerAdapter,
    SpeechToTextAdapter,
    StructuredCompletion,
    SynthesizedAudioChunk,
    TelephonyAdapter,
    TextToSpeechAdapter,
    TranscriptChunk,
    VoiceActivity,
    VoiceActivityDetector,
    WhatsAppAdapter,
)
from pitchbot.adapters.errors import (
    AdapterError,
    IdempotencyConflictError,
    MockCapacityError,
    PermanentAdapterError,
)
from pitchbot.domain import JsonValue, LanguageCode


class ScriptedOutcomes[T]:
    def __init__(self, outcomes: list[T | AdapterError] | None = None) -> None:
        self._outcomes = deque(outcomes or [])

    def next_or(self, default: T) -> T:
        if not self._outcomes:
            return default
        outcome = self._outcomes.popleft()
        if isinstance(outcome, AdapterError):
            raise outcome
        return outcome


@dataclass(frozen=True, slots=True)
class RecordedAction:
    operation: str
    idempotency_key: str
    payload: Mapping[str, JsonValue]


class MockVoiceActivityDetector(VoiceActivityDetector):
    """Deterministic stand-in for an acoustic detector.

    Opus is variable bitrate, so a silent 20 ms frame encodes far smaller than a spoken
    one. Classifying on encoded frame size is therefore a defensible placeholder, but it
    is a byte-size heuristic and not an acoustic model: it is here so endpointing,
    barge-in, and their bounds can be developed and tested before a real detector is
    benchmarked and selected.
    """

    def __init__(
        self,
        *,
        speech_threshold_bytes: int = 512,
        decisions: list[bool] | None = None,
        error: AdapterError | None = None,
    ) -> None:
        if speech_threshold_bytes < 1:
            raise ValueError("speech_threshold_bytes must be positive")
        self._speech_threshold_bytes = speech_threshold_bytes
        self._decisions = deque(decisions or [])
        self._error = error
        self.frames_seen = 0

    def detect(self, frame: AudioChunk) -> VoiceActivity:
        if self._error is not None:
            raise self._error
        self.frames_seen += 1
        if self._decisions:
            is_speech = self._decisions.popleft()
            confidence = 1.0
        else:
            is_speech = len(frame.data) >= self._speech_threshold_bytes
            confidence = 0.5
        return VoiceActivity(
            is_speech=is_speech,
            confidence=confidence,
            sequence=frame.sequence,
        )


class MockSpeechToTextAdapter(SpeechToTextAdapter):
    def __init__(
        self,
        transcripts: list[TranscriptChunk] | None = None,
        error: AdapterError | None = None,
        max_audio_chunks: int = 1_000,
    ) -> None:
        self._transcripts = transcripts or []
        self._error = error
        self._max_audio_chunks = max_audio_chunks
        self.received_audio: list[tuple[int, int]] = []

    async def transcribe(
        self,
        audio: AsyncIterator[AudioChunk],
    ) -> AsyncIterator[TranscriptChunk]:
        async for chunk in audio:
            if len(self.received_audio) >= self._max_audio_chunks:
                raise MockCapacityError("Mock STT audio history capacity exceeded")
            self.received_audio.append((chunk.sequence, len(chunk.data)))
        if self._error is not None:
            raise self._error
        for transcript in self._transcripts:
            yield transcript


class MockTextToSpeechAdapter(TextToSpeechAdapter):
    def __init__(
        self,
        chunks: list[SynthesizedAudioChunk] | None = None,
        error: AdapterError | None = None,
        max_requests: int = 1_000,
    ) -> None:
        self._chunks = chunks
        self._error = error
        self._max_requests = max_requests
        self.requests: list[tuple[int, LanguageCode]] = []

    async def synthesize(
        self,
        text: str,
        language: LanguageCode,
    ) -> AsyncIterator[SynthesizedAudioChunk]:
        if len(self.requests) >= self._max_requests:
            raise MockCapacityError("Mock TTS request history capacity exceeded")
        self.requests.append((len(text), language))
        if self._error is not None:
            raise self._error
        chunks = self._chunks or [
            SynthesizedAudioChunk(data=text.encode(), sequence=0, is_final=True)
        ]
        for chunk in chunks:
            yield chunk


class MockModelAdapter(ModelAdapter):
    def __init__(
        self,
        outcomes: list[StructuredCompletion | AdapterError] | None = None,
        max_requests: int = 1_000,
    ) -> None:
        self._outcomes = ScriptedOutcomes(outcomes)
        self._max_requests = max_requests
        self.requests: list[tuple[int, str]] = []

    async def complete_structured(
        self,
        instruction: str,
        schema_name: str,
    ) -> StructuredCompletion:
        if len(self.requests) >= self._max_requests:
            raise MockCapacityError("Mock model request history capacity exceeded")
        self.requests.append((len(instruction), schema_name))
        default = StructuredCompletion(value={}, model_version="mock-v1")
        return self._outcomes.next_or(default)


class _IdempotentActions:
    def __init__(
        self,
        failures: list[AdapterError | None] | None = None,
        *,
        max_actions: int = 1_000,
    ) -> None:
        self.actions: list[RecordedAction] = []
        self._results: dict[str, ActionResult] = {}
        self._fingerprints: dict[str, str] = {}
        self._failures = deque(failures or [])
        self._max_actions = max_actions

    def clear_operations(self, idempotency_key_prefix: str) -> None:
        keys = tuple(key for key in self._results if key.startswith(idempotency_key_prefix))
        for key in keys:
            self._results.pop(key, None)
            self._fingerprints.pop(key, None)
        self.actions[:] = [
            action
            for action in self.actions
            if not action.idempotency_key.startswith(idempotency_key_prefix)
        ]

    def record(
        self,
        operation: str,
        idempotency_key: str,
        payload: Mapping[str, JsonValue],
        *,
        recorded_payload: Mapping[str, JsonValue] | None = None,
    ) -> ActionResult:
        if not idempotency_key.strip():
            raise PermanentAdapterError("idempotency_key must not be empty")
        previous = self._results.get(idempotency_key)
        if previous is not None:
            fingerprint = self._fingerprint(operation, payload)
            if self._fingerprints[idempotency_key] != fingerprint:
                raise IdempotencyConflictError(
                    f"Idempotency key {idempotency_key!r} was reused with different input"
                )
            return previous

        if self._failures:
            failure = self._failures.popleft()
            if failure is not None:
                raise failure
        if len(self.actions) >= self._max_actions:
            raise MockCapacityError("Mock action history capacity exceeded")

        fingerprint = self._fingerprint(operation, payload)
        result = ActionResult(
            idempotency_key=idempotency_key,
            status="simulated",
            provider_reference=f"mock:{operation}:{len(self.actions) + 1}",
        )
        self.actions.append(
            RecordedAction(
                operation=operation,
                idempotency_key=idempotency_key,
                payload=dict(recorded_payload or {}),
            )
        )
        self._results[idempotency_key] = result
        self._fingerprints[idempotency_key] = fingerprint
        return result

    @staticmethod
    def _fingerprint(operation: str, payload: Mapping[str, JsonValue]) -> str:
        canonical = json.dumps(
            {"operation": operation, "payload": payload},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(canonical).hexdigest()


class MockTelephonyAdapter(_IdempotentActions, TelephonyAdapter):
    async def dial(self, contact_ref: str, idempotency_key: str) -> ActionResult:
        return self.record(
            "dial",
            idempotency_key,
            {"contact_ref": contact_ref},
            recorded_payload={"contact_ref": "[REDACTED]"},
        )


class MockWhatsAppAdapter(_IdempotentActions, WhatsAppAdapter):
    async def send_message(
        self,
        contact_ref: str,
        message: str,
        idempotency_key: str,
    ) -> ActionResult:
        return self.record(
            "send-message",
            idempotency_key,
            {"contact_ref": contact_ref, "message": message},
            recorded_payload={
                "contact_ref": "[REDACTED]",
                "message_length": len(message),
            },
        )


class MockSchedulerAdapter(_IdempotentActions, SchedulerAdapter):
    def __init__(
        self,
        failures: list[AdapterError | None] | None = None,
        *,
        max_actions: int = 1_000,
    ) -> None:
        super().__init__(failures, max_actions=max_actions)
        self.jobs: dict[str, tuple[datetime, Mapping[str, JsonValue]]] = {}

    async def schedule(
        self,
        job_key: str,
        run_at: datetime,
        payload: Mapping[str, JsonValue],
        idempotency_key: str,
    ) -> ActionResult:
        if run_at.tzinfo is None:
            raise PermanentAdapterError("run_at must be timezone-aware")
        fingerprint_payload = dict(payload)
        fingerprint_payload["job_key"] = job_key
        fingerprint_payload["run_at"] = run_at.isoformat()
        result = self.record(
            "schedule",
            idempotency_key,
            fingerprint_payload,
            recorded_payload={"job_key": job_key, "run_at": run_at.isoformat()},
        )
        self.jobs[job_key] = (run_at, dict(payload))
        return result

    async def cancel(self, job_key: str, idempotency_key: str) -> ActionResult:
        result = self.record("cancel", idempotency_key, {"job_key": job_key})
        self.jobs.pop(job_key, None)
        return result


class MockResearchAdapter(ResearchAdapter):
    def __init__(
        self,
        pages: Mapping[str, ResearchResult] | None = None,
        *,
        max_requests: int = 1_000,
    ) -> None:
        self._pages = dict(pages or {})
        self._max_requests = max_requests
        self.request_count = 0

    async def fetch(self, url: str) -> ResearchResult:
        if self.request_count >= self._max_requests:
            raise MockCapacityError("Mock research request capacity exceeded")
        self.request_count += 1
        try:
            return self._pages[url]
        except KeyError as error:
            raise PermanentAdapterError(f"No scripted research result for {url}") from error


class MockArtifactAdapter(_IdempotentActions, ArtifactAdapter):
    async def create(
        self,
        artifact_key: str,
        payload: Mapping[str, JsonValue],
        idempotency_key: str,
    ) -> ActionResult:
        return self.record(
            "create-artifact",
            idempotency_key,
            {"artifact_key": artifact_key, "payload": dict(payload)},
            recorded_payload={"artifact_key": artifact_key, "field_count": len(payload)},
        )


class MockObjectStorageAdapter(_IdempotentActions, ObjectStorageAdapter):
    def __init__(
        self,
        failures: list[AdapterError | None] | None = None,
        *,
        max_actions: int = 1_000,
    ) -> None:
        super().__init__(failures, max_actions=max_actions)
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put(
        self,
        object_key: str,
        data: bytes,
        media_type: str,
        idempotency_key: str,
    ) -> ActionResult:
        result = self.record(
            "put-object",
            idempotency_key,
            {
                "content_sha256": hashlib.sha256(data).hexdigest(),
                "media_type": media_type,
                "object_key": object_key,
                "size_bytes": len(data),
            },
            recorded_payload={
                "media_type": media_type,
                "object_key": object_key,
                "size_bytes": len(data),
            },
        )
        self.objects[object_key] = (data, media_type)
        return result

    async def get(self, object_key: str) -> bytes | None:
        stored = self.objects.get(object_key)
        return stored[0] if stored is not None else None
