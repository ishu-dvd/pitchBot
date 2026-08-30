from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pitchbot.adapters.contracts import (
    ActionResult,
    ArtifactAdapter,
    AudioChunk,
    ModelAdapter,
    ObjectStorageAdapter,
    ResearchAdapter,
    SchedulerAdapter,
    SpeechToTextAdapter,
    TelephonyAdapter,
    TextToSpeechAdapter,
    TranscriptChunk,
    WhatsAppAdapter,
)
from pitchbot.adapters.mocks import (
    MockArtifactAdapter,
    MockModelAdapter,
    MockObjectStorageAdapter,
    MockResearchAdapter,
    MockSchedulerAdapter,
    MockSpeechToTextAdapter,
    MockTelephonyAdapter,
    MockTextToSpeechAdapter,
    MockWhatsAppAdapter,
)
from pitchbot.domain import LanguageCode


def test_mocks_satisfy_provider_contracts_statically() -> None:
    stt: SpeechToTextAdapter = MockSpeechToTextAdapter()
    tts: TextToSpeechAdapter = MockTextToSpeechAdapter()
    model: ModelAdapter = MockModelAdapter()
    telephony: TelephonyAdapter = MockTelephonyAdapter()
    whatsapp: WhatsAppAdapter = MockWhatsAppAdapter()
    scheduler: SchedulerAdapter = MockSchedulerAdapter()
    research: ResearchAdapter = MockResearchAdapter()
    artifact: ArtifactAdapter = MockArtifactAdapter()
    storage: ObjectStorageAdapter = MockObjectStorageAdapter()

    assert all(
        adapter is not None
        for adapter in (
            stt,
            tts,
            model,
            telephony,
            whatsapp,
            scheduler,
            research,
            artifact,
            storage,
        )
    )


def test_transport_contracts_reject_invalid_boundary_values() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        AudioChunk(data=b"", captured_at=datetime(2026, 1, 1), sequence=0)
    with pytest.raises(ValueError, match="sample_rate_hz"):
        AudioChunk(
            data=b"",
            captured_at=datetime.now(UTC),
            sequence=0,
            sample_rate_hz=0,
        )
    with pytest.raises(ValueError, match="confidence"):
        TranscriptChunk(
            text="invalid",
            language=LanguageCode.ENGLISH,
            confidence=1.1,
            is_final=True,
            sequence=0,
        )
    with pytest.raises(ValueError, match="idempotency_key"):
        ActionResult(idempotency_key=" ", status="simulated")
