from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime

import pytest

from pitchbot.adapters import (
    AudioChunk,
    ExternalNetworkDisabledError,
    IdempotencyConflictError,
    MockCapacityError,
    NetworkDisabledResearchAdapter,
    NetworkDisabledTelephonyAdapter,
    NetworkDisabledWhatsAppAdapter,
    StructuredCompletion,
    TranscriptChunk,
)
from pitchbot.adapters.errors import PermanentAdapterError, TransientAdapterError
from pitchbot.adapters.mocks import (
    MockArtifactAdapter,
    MockModelAdapter,
    MockObjectStorageAdapter,
    MockSchedulerAdapter,
    MockSpeechToTextAdapter,
    MockTelephonyAdapter,
    MockTextToSpeechAdapter,
    MockWhatsAppAdapter,
)
from pitchbot.domain import LanguageCode


async def audio_stream() -> AsyncIterator[AudioChunk]:
    yield AudioChunk(data=b"one", captured_at=datetime.now(UTC), sequence=1)
    yield AudioChunk(data=b"two", captured_at=datetime.now(UTC), sequence=2)


@pytest.mark.asyncio
async def test_mock_stt_replays_transcripts_after_consuming_audio() -> None:
    expected = TranscriptChunk(
        text="Namaste",
        language=LanguageCode.HINDI,
        confidence=0.9,
        is_final=True,
        sequence=1,
    )
    adapter = MockSpeechToTextAdapter([expected])

    actual = [chunk async for chunk in adapter.transcribe(audio_stream())]

    assert actual == [expected]
    assert adapter.received_audio == [(1, 3), (2, 3)]


@pytest.mark.asyncio
async def test_mock_tts_and_model_are_deterministic() -> None:
    tts = MockTextToSpeechAdapter()
    audio = [
        chunk
        async for chunk in tts.synthesize(
            "Hello",
            LanguageCode.ENGLISH,
        )
    ]
    completion = StructuredCompletion(
        value={"intent": "warm"},
        model_version="fixture-v1",
    )
    model = MockModelAdapter([completion])

    assert audio[0].data == b"Hello"
    assert await model.complete_structured("classify", "intent") == completion
    assert tts.requests == [(5, LanguageCode.ENGLISH)]
    assert model.requests == [(8, "intent")]


@pytest.mark.asyncio
async def test_action_mocks_are_idempotent() -> None:
    telephony = MockTelephonyAdapter()
    whatsapp = MockWhatsAppAdapter()
    artifacts = MockArtifactAdapter()

    first = await telephony.dial("synthetic-contact", "dial-1")
    duplicate = await telephony.dial("synthetic-contact", "dial-1")
    await whatsapp.send_message("synthetic-contact", "hello", "message-1")
    await artifacts.create("artifact-1", {"title": "Synthetic concept"}, "artifact-op-1")

    assert duplicate == first
    assert len(telephony.actions) == 1
    assert len(whatsapp.actions) == 1
    assert len(artifacts.actions) == 1
    assert telephony.actions[0].payload == {"contact_ref": "[REDACTED]"}
    assert whatsapp.actions[0].payload == {
        "contact_ref": "[REDACTED]",
        "message_length": 5,
    }


@pytest.mark.asyncio
async def test_idempotency_key_reuse_with_different_input_is_rejected() -> None:
    telephony = MockTelephonyAdapter()
    await telephony.dial("first-contact", "dial-1")

    with pytest.raises(IdempotencyConflictError, match="different input"):
        await telephony.dial("second-contact", "dial-1")


@pytest.mark.asyncio
async def test_action_mock_injects_failure_before_recording() -> None:
    adapter = MockTelephonyAdapter([TransientAdapterError("temporary")])

    with pytest.raises(TransientAdapterError, match="temporary"):
        await adapter.dial("synthetic-contact", "dial-1")

    assert adapter.actions == []
    result = await adapter.dial("synthetic-contact", "dial-1")
    assert result.status == "simulated"


@pytest.mark.asyncio
async def test_scheduler_requires_aware_time_and_cancel_is_idempotent() -> None:
    scheduler = MockSchedulerAdapter()
    aware = datetime.now(UTC)

    first = await scheduler.schedule("job-1", aware, {"agenda": "demo"}, "schedule-op-1")
    duplicate = await scheduler.schedule("job-1", aware, {"agenda": "demo"}, "schedule-op-1")
    await scheduler.cancel("job-1", "cancel-op-1")
    await scheduler.cancel("job-1", "cancel-op-1")
    await scheduler.schedule(
        "job-1",
        aware,
        {"agenda": "updated"},
        "schedule-op-2",
    )

    assert duplicate == first
    assert scheduler.jobs["job-1"][1] == {"agenda": "updated"}
    assert len(scheduler.actions) == 3

    with pytest.raises(PermanentAdapterError, match="timezone-aware"):
        await scheduler.schedule("job-2", datetime(2026, 1, 1), {}, "schedule-op-3")


@pytest.mark.asyncio
async def test_mock_object_storage_does_not_overwrite_duplicate_key() -> None:
    storage = MockObjectStorageAdapter()

    first = await storage.put("object-1", b"first", "text/plain", "put-op-1")
    duplicate = await storage.put("object-1", b"first", "text/plain", "put-op-1")

    assert duplicate == first
    assert await storage.get("object-1") == b"first"
    assert await storage.get("missing") is None

    with pytest.raises(IdempotencyConflictError, match="different input"):
        await storage.put("object-1", b"second", "text/plain", "put-op-1")


@pytest.mark.asyncio
async def test_mock_histories_are_bounded() -> None:
    telephony = MockTelephonyAdapter(max_actions=1)
    await telephony.dial("first", "dial-1")

    with pytest.raises(MockCapacityError, match="capacity"):
        await telephony.dial("second", "dial-2")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [
        lambda: NetworkDisabledTelephonyAdapter().dial("contact", "key"),
        lambda: NetworkDisabledWhatsAppAdapter().send_message("contact", "hello", "key"),
        lambda: NetworkDisabledResearchAdapter().fetch("https://example.invalid"),
    ],
)
async def test_disabled_external_adapters_fail_closed(
    operation: Callable[[], Awaitable[object]],
) -> None:
    with pytest.raises(ExternalNetworkDisabledError, match="External network is disabled"):
        await operation()
