"""Declining a language rather than transcribing it into text nobody should act on.

Measured 2026-09-05 (`probe_telugu_loop.py`): Whisper `small` on Telugu returns nonsense in
every decoder configuration tried - the reference "మేము రిటైల్ షాప్ నడుపుతాము" came back as
"మరింరIsn claiming the jammals from the charity sponsor" - and takes **37,533 ms** to do it.
`no_repeat_ngram_size=3` cuts that to 2,216 ms, a 16.9x speedup, but the same probe run on
English showed it rewriting a buyer who legitimately repeated themselves ("50,000 rupees"
became "50 thousand rupees"), so it cannot be applied globally to buy the saving.

What is left is honest refusal, which this project already does when no transcriber is
configured at all. These tests pin the two properties that make refusal safe: it happens only
on a *confident* identification, and it is never reported as a fault.
"""

from __future__ import annotations

import array
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from pitchbot.adapters.contracts import AudioChunk
from pitchbot.adapters.errors import AdapterError, UnsupportedLanguageError
from pitchbot.adapters.faster_whisper_stt import (
    FASTER_WHISPER_AVAILABLE,
    DetectedLanguage,
    FasterWhisperSpeechToTextAdapter,
)
from pitchbot.domain import LanguageCode

requires_faster_whisper = pytest.mark.skipif(
    not FASTER_WHISPER_AVAILABLE, reason="faster-whisper is not installed"
)

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


class _FakeSegment:
    def __init__(self, text: str) -> None:
        self.text = text
        self.start = 0.0
        self.end = 1.0
        self.avg_logprob = -0.1
        self.no_speech_prob = 0.01
        self.compression_ratio = 1.0


class _FakeInfo:
    language = "te"
    language_probability = 1.0


class _FakeModel:
    def __init__(self) -> None:
        self.transcribe_calls = 0

    def transcribe(self, samples: object, **kwargs: object) -> tuple[object, _FakeInfo]:
        self.transcribe_calls += 1
        return iter([_FakeSegment("nonsense")]), _FakeInfo()

    def detect_language(self, samples: object) -> tuple[str, float, None]:
        return ("te", 0.93, None)


async def _stream(payload: bytes) -> AsyncIterator[AudioChunk]:
    yield AudioChunk(data=payload, captured_at=_EPOCH, sequence=0, sample_rate_hz=16_000)


def _pcm(count: int) -> bytes:
    return array.array("h", [0] * count).tobytes()


def _adapter(**kwargs: object) -> FasterWhisperSpeechToTextAdapter:
    options: dict[str, object] = {"model_size": "small"}
    options.update(kwargs)
    return FasterWhisperSpeechToTextAdapter(**options)  # type: ignore[arg-type]


@requires_faster_whisper
@pytest.mark.asyncio
async def test_a_confident_unsupported_language_is_declined_before_the_expensive_call() -> None:
    """The whole point: 37,533 ms of shared CPU is not spent producing untrustworthy text."""

    adapter = _adapter(unsupported_languages=[LanguageCode.TELUGU])
    model = _FakeModel()
    adapter._model = model  # noqa: SLF001
    hint = DetectedLanguage(language=LanguageCode.TELUGU, probability=0.93)

    with pytest.raises(UnsupportedLanguageError) as raised:
        async for _chunk in adapter.transcribe(_stream(_pcm(16_000)), language_hint=hint):
            pass

    assert raised.value.language == "te"
    assert model.transcribe_calls == 0, "the model must not have been asked to decode"


@requires_faster_whisper
@pytest.mark.asyncio
async def test_an_unconfident_guess_still_transcribes() -> None:
    """Refusing on a hunch would silence a buyer the model might well have understood."""

    adapter = _adapter(
        unsupported_languages=[LanguageCode.TELUGU], early_detection_min_probability=0.7
    )
    model = _FakeModel()
    adapter._model = model  # noqa: SLF001
    hint = DetectedLanguage(language=LanguageCode.TELUGU, probability=0.42)

    chunks = [
        chunk async for chunk in adapter.transcribe(_stream(_pcm(16_000)), language_hint=hint)
    ]
    assert model.transcribe_calls == 1
    assert chunks


@requires_faster_whisper
@pytest.mark.asyncio
async def test_no_hint_at_all_still_transcribes() -> None:
    adapter = _adapter(unsupported_languages=[LanguageCode.TELUGU])
    model = _FakeModel()
    adapter._model = model  # noqa: SLF001

    chunks = [chunk async for chunk in adapter.transcribe(_stream(_pcm(16_000)))]
    assert model.transcribe_calls == 1
    assert chunks


@requires_faster_whisper
@pytest.mark.asyncio
async def test_a_supported_language_is_unaffected() -> None:
    adapter = _adapter(unsupported_languages=[LanguageCode.TELUGU])
    model = _FakeModel()
    adapter._model = model  # noqa: SLF001
    hint = DetectedLanguage(language=LanguageCode.ENGLISH, probability=0.95)

    chunks = [
        chunk async for chunk in adapter.transcribe(_stream(_pcm(16_000)), language_hint=hint)
    ]
    assert model.transcribe_calls == 1
    assert chunks


@requires_faster_whisper
@pytest.mark.asyncio
async def test_an_empty_unsupported_set_restores_the_previous_behaviour() -> None:
    adapter = _adapter(unsupported_languages=[])
    model = _FakeModel()
    adapter._model = model  # noqa: SLF001
    hint = DetectedLanguage(language=LanguageCode.TELUGU, probability=0.99)

    chunks = [
        chunk async for chunk in adapter.transcribe(_stream(_pcm(16_000)), language_hint=hint)
    ]
    assert model.transcribe_calls == 1
    assert chunks


def test_the_decline_is_not_an_adapter_error() -> None:
    """Nothing failed. Classifying it as a fault would log a warning during correct
    operation and tell the caller the transcriber is unavailable when it is working."""

    assert not issubclass(UnsupportedLanguageError, AdapterError)
    assert issubclass(UnsupportedLanguageError, RuntimeError)


def test_the_decline_names_the_language_it_heard() -> None:
    error = UnsupportedLanguageError("te")
    assert error.language == "te"
    assert "te" in str(error)


# --------------------------------------------------------------------------------------
# Pipeline level: a decline is an outcome, not a failure
# --------------------------------------------------------------------------------------


class _DecliningTranscriber:
    """Identifies Telugu confidently, then declines to transcribe it."""

    def __init__(self) -> None:
        self.transcribe_calls = 0

    async def detect_prefix_language(self, payload: bytes) -> object | None:
        return DetectedLanguage(language=LanguageCode.TELUGU, probability=0.93)

    async def transcribe(self, audio, *, language_hint: object | None = None):  # type: ignore[no-untyped-def]
        async for _chunk in audio:
            pass
        self.transcribe_calls += 1
        if language_hint is not None:
            raise UnsupportedLanguageError("te")
        yield  # pragma: no cover - unreachable, kept so this stays an async generator


@pytest.mark.asyncio
async def test_pipeline_reports_a_decline_as_its_own_outcome() -> None:
    """`transcriber-unavailable` would be a lie about a component that is working fine."""

    from pitchbot.adapters.mocks import MockVoiceActivityDetector
    from pitchbot.speech.pipeline import SpeechTurnPipeline, UtteranceOutcome

    frame_ms = 30
    frame_bytes = 16_000 * 2 * frame_ms // 1000
    transcriber = _DecliningTranscriber()
    pipeline = SpeechTurnPipeline(
        detector=MockVoiceActivityDetector(speech_threshold_bytes=64),
        transcriber=transcriber,
        language=LanguageCode.TELUGU,
        frame_duration_ms=frame_ms,
        early_detection_seconds=0.05,
    )

    sequence = 0
    for _ in range(8):
        await pipeline.push(
            AudioChunk(
                data=b"\x40" * frame_bytes,
                captured_at=_EPOCH,
                sequence=sequence,
                sample_rate_hz=16_000,
            )
        )
        sequence += 1
    import asyncio

    await asyncio.sleep(0)  # let the detection finish so a hint exists

    outcome = None
    for _ in range(40):
        result = await pipeline.push(
            AudioChunk(
                data=b"\x00" * 16,
                captured_at=_EPOCH,
                sequence=sequence,
                sample_rate_hz=16_000,
            )
        )
        sequence += 1
        if result.utterance is not None:
            outcome = result.utterance.outcome
            break

    assert outcome is UtteranceOutcome.LANGUAGE_UNSUPPORTED
