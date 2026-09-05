"""Deciding the language while the buyer is still talking.

The shipped default (`speech_stt_language = ""`) auto-detects, which makes Whisper run its
encoder twice: once to identify the language, once to decode it. Measured 2026-09-05 on a
7.17 s English clip, that is 3,857 ms against 2,235 ms when the language is already known -
1,622 ms of the 3,982 ms an English utterance currently spends being transcribed.

Detection cost does not shrink with less audio (Whisper pads every clip to a 30 s window,
measured flat at 1,631-1,758 ms for every prefix tried), so the only way to win is to start
it *sooner*, overlapping the buyer's own speech.

Two properties matter more than the saving and are what most of these tests are for.

**A wrong language is not a worse transcript, it is a fluent invention.** Hindi audio
decoded under a forced `en` came back as "We run a retail shop and our budget is 50,000
rupees" at confidence 0.616 - higher than the *correct* Telugu transcript scored (0.316).
So the hint is gated by a probability floor, and falling below it must cost nothing but
the optimisation.

**A detection outlives nothing.** Barge-in, a discarded utterance and the agent taking the
floor all destroy the audio the detection was computed from, so the detection must go with
it rather than be applied to whatever is said next.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from pitchbot.adapters.contracts import AudioChunk, TranscriptChunk
from pitchbot.adapters.faster_whisper_stt import DetectedLanguage
from pitchbot.adapters.mocks import MockVoiceActivityDetector
from pitchbot.domain import LanguageCode
from pitchbot.speech.pipeline import (
    EarlyDetectingTranscriber,
    SpeechTurnPipeline,
    UtteranceResult,
)

FRAME_MS = 250
# 250 ms of 16 kHz mono 16-bit PCM.
FRAME_BYTES = 16_000 * 2 * FRAME_MS // 1000
SPEECH = b"\x40" * FRAME_BYTES
SILENCE = b"\x00" * 16


class RecordingTranscriber:
    """A transcriber that records how it was called, and can stall its detection."""

    def __init__(self, *, detected: DetectedLanguage | None, detect_delay_s: float = 0.0):
        self.detected = detected
        self.detect_delay_s = detect_delay_s
        self.detect_calls: list[int] = []
        self.hints: list[object | None] = []
        self.detect_cancelled = 0

    async def detect_prefix_language(self, payload: bytes) -> object | None:
        self.detect_calls.append(len(payload))
        try:
            if self.detect_delay_s:
                await asyncio.sleep(self.detect_delay_s)
        except asyncio.CancelledError:
            self.detect_cancelled += 1
            raise
        return self.detected

    async def transcribe(self, audio, *, language_hint: object | None = None):  # type: ignore[no-untyped-def]
        async for _chunk in audio:
            pass
        self.hints.append(language_hint)
        yield TranscriptChunk(
            text="We sell toys online.",
            language=LanguageCode.ENGLISH,
            confidence=0.9,
            is_final=True,
            sequence=0,
        )


class PlainTranscriber:
    """A transcriber with no early detection at all - it must still work untouched."""

    def __init__(self) -> None:
        self.calls = 0

    async def transcribe(self, audio):  # type: ignore[no-untyped-def]
        async for _chunk in audio:
            pass
        self.calls += 1
        yield TranscriptChunk(
            text="We sell toys online.",
            language=LanguageCode.ENGLISH,
            confidence=0.9,
            is_final=True,
            sequence=0,
        )


def _chunk(sequence: int, data: bytes) -> AudioChunk:
    return AudioChunk(
        sequence=sequence,
        data=data,
        sample_rate_hz=16_000,
        captured_at=datetime.now(UTC),
    )


def _pipeline(transcriber: object, *, early_seconds: float) -> SpeechTurnPipeline:
    return SpeechTurnPipeline(
        detector=MockVoiceActivityDetector(speech_threshold_bytes=64),
        transcriber=transcriber,  # type: ignore[arg-type]
        language=LanguageCode.ENGLISH,
        frame_duration_ms=FRAME_MS,
        early_detection_seconds=early_seconds,
    )


async def _speak(pipeline: SpeechTurnPipeline, frames: int, *, start: int = 0) -> None:
    for index in range(frames):
        await pipeline.push(_chunk(start + index, SPEECH))


async def _finish(pipeline: SpeechTurnPipeline, *, start: int) -> UtteranceResult | None:
    """Push silence until the endpointer closes the utterance."""

    for index in range(12):
        result = await pipeline.push(_chunk(start + index, SILENCE))
        if result.utterance is not None:
            return result.utterance
    return None


def test_protocol_matches_a_transcriber_that_can_detect_early() -> None:
    assert isinstance(RecordingTranscriber(detected=None), EarlyDetectingTranscriber)


def test_protocol_does_not_match_a_plain_transcriber() -> None:
    assert not isinstance(PlainTranscriber(), EarlyDetectingTranscriber)


def test_negative_prefix_is_refused() -> None:
    with pytest.raises(ValueError, match="early_detection_seconds must not be negative"):
        _pipeline(PlainTranscriber(), early_seconds=-1.0)


@pytest.mark.asyncio
async def test_detection_fires_once_the_prefix_is_buffered() -> None:
    transcriber = RecordingTranscriber(
        detected=DetectedLanguage(language=LanguageCode.ENGLISH, probability=0.95)
    )
    pipeline = _pipeline(transcriber, early_seconds=0.5)
    await _speak(pipeline, 3)  # 750 ms, past the 500 ms threshold
    await asyncio.sleep(0)
    assert transcriber.detect_calls, "detection should have started during speech"
    assert transcriber.detect_calls[0] >= FRAME_BYTES * 2


@pytest.mark.asyncio
async def test_detection_does_not_fire_before_the_prefix_is_reached() -> None:
    transcriber = RecordingTranscriber(detected=None)
    pipeline = _pipeline(transcriber, early_seconds=5.0)
    await _speak(pipeline, 3)
    await asyncio.sleep(0)
    assert transcriber.detect_calls == []


@pytest.mark.asyncio
async def test_detection_fires_at_most_once_per_utterance() -> None:
    """Re-running it as more audio arrives would spend the CPU again for the same answer."""

    transcriber = RecordingTranscriber(
        detected=DetectedLanguage(language=LanguageCode.ENGLISH, probability=0.95)
    )
    pipeline = _pipeline(transcriber, early_seconds=0.5)
    await _speak(pipeline, 8)
    await asyncio.sleep(0)
    assert len(transcriber.detect_calls) == 1


@pytest.mark.asyncio
async def test_zero_seconds_disables_the_mechanism_entirely() -> None:
    transcriber = RecordingTranscriber(detected=None)
    pipeline = _pipeline(transcriber, early_seconds=0.0)
    await _speak(pipeline, 8)
    await asyncio.sleep(0)
    assert transcriber.detect_calls == []


@pytest.mark.asyncio
async def test_a_finished_detection_reaches_transcription_as_a_hint() -> None:
    hint = DetectedLanguage(language=LanguageCode.ENGLISH, probability=0.95)
    transcriber = RecordingTranscriber(detected=hint)
    pipeline = _pipeline(transcriber, early_seconds=0.5)
    await _speak(pipeline, 4)
    await asyncio.sleep(0.01)  # let the detection task complete
    utterance = await _finish(pipeline, start=100)
    assert utterance is not None
    assert transcriber.hints == [hint]


@pytest.mark.asyncio
async def test_a_landed_detection_reports_how_long_it_took() -> None:
    """The duration is data on the result; the router is what turns it into a metric.

    Without it, the one thing an operator cannot see is whether this feature is working:
    a detection that never lands leaves transcription paying the auto-detect cost the
    feature exists to remove, and looks identical to one that lands.
    """

    transcriber = RecordingTranscriber(
        detected=DetectedLanguage(language=LanguageCode.ENGLISH, probability=0.95),
        detect_delay_s=0.01,
    )
    pipeline = _pipeline(transcriber, early_seconds=0.5)
    await _speak(pipeline, 4)
    await asyncio.sleep(0.05)  # let the detection task complete
    utterance = await _finish(pipeline, start=100)
    assert utterance is not None
    assert utterance.detect_language_ms is not None
    assert utterance.detect_language_ms >= 10.0


@pytest.mark.asyncio
async def test_an_abandoned_detection_reports_no_duration() -> None:
    """It contributed nothing, so reporting a time would make wasted work look productive."""

    transcriber = RecordingTranscriber(
        detected=DetectedLanguage(language=LanguageCode.ENGLISH, probability=0.95),
        detect_delay_s=30.0,
    )
    pipeline = _pipeline(transcriber, early_seconds=0.5)
    await _speak(pipeline, 4)
    await asyncio.sleep(0)
    utterance = await _finish(pipeline, start=100)
    assert utterance is not None
    assert utterance.detect_language_ms is None


@pytest.mark.asyncio
async def test_a_detection_that_never_ran_reports_no_duration() -> None:
    transcriber = PlainTranscriber()
    pipeline = _pipeline(transcriber, early_seconds=0.5)
    await _speak(pipeline, 4)
    utterance = await _finish(pipeline, start=100)
    assert utterance is not None
    assert utterance.detect_language_ms is None


@pytest.mark.asyncio
async def test_an_unfinished_detection_is_abandoned_rather_than_waited_for() -> None:
    """The utterance has already ended, so waiting would add to the very gap this shrinks."""

    transcriber = RecordingTranscriber(
        detected=DetectedLanguage(language=LanguageCode.ENGLISH, probability=0.95),
        detect_delay_s=30.0,
    )
    pipeline = _pipeline(transcriber, early_seconds=0.5)
    await _speak(pipeline, 4)
    await asyncio.sleep(0)  # let the detection actually begin before it is abandoned
    assert transcriber.detect_calls, "detection should be in flight"
    utterance = await _finish(pipeline, start=100)
    assert utterance is not None
    assert transcriber.hints == [None]
    await asyncio.sleep(0)  # let the cancellation reach the coroutine
    assert transcriber.detect_cancelled == 1


@pytest.mark.asyncio
async def test_a_detection_that_never_started_costs_nothing() -> None:
    """A short utterance can cancel the task before its coroutine is ever entered.

    That is the cheapest possible outcome and must not be mistaken for a failure: no
    inference ran, no hint exists, and the utterance transcribes exactly as it always did.
    """

    transcriber = RecordingTranscriber(
        detected=DetectedLanguage(language=LanguageCode.ENGLISH, probability=0.95),
        detect_delay_s=30.0,
    )
    pipeline = _pipeline(transcriber, early_seconds=0.5)
    await _speak(pipeline, 4)
    utterance = await _finish(pipeline, start=100)
    assert utterance is not None
    assert transcriber.detect_calls == []
    assert transcriber.hints == [None]


@pytest.mark.asyncio
async def test_a_plain_transcriber_is_never_asked_and_still_transcribes() -> None:
    transcriber = PlainTranscriber()
    pipeline = _pipeline(transcriber, early_seconds=0.5)
    await _speak(pipeline, 4)
    utterance = await _finish(pipeline, start=100)
    assert utterance is not None
    assert utterance.text == "We sell toys online."
    assert transcriber.calls == 1


@pytest.mark.asyncio
async def test_the_agent_taking_the_floor_cancels_an_in_flight_detection() -> None:
    """The audio it was computed from is discarded, so the answer must be too."""

    transcriber = RecordingTranscriber(
        detected=DetectedLanguage(language=LanguageCode.ENGLISH, probability=0.95),
        detect_delay_s=30.0,
    )
    pipeline = _pipeline(transcriber, early_seconds=0.5)
    await _speak(pipeline, 4)
    await asyncio.sleep(0)
    pipeline.agent_started_speaking()
    await asyncio.sleep(0)
    assert transcriber.detect_cancelled == 1


@pytest.mark.asyncio
async def test_detection_restarts_for_the_next_utterance() -> None:
    """The once-per-utterance latch must not become once-per-session."""

    transcriber = RecordingTranscriber(
        detected=DetectedLanguage(language=LanguageCode.ENGLISH, probability=0.95)
    )
    pipeline = _pipeline(transcriber, early_seconds=0.5)
    await _speak(pipeline, 4)
    await asyncio.sleep(0.01)
    await _finish(pipeline, start=100)
    await _speak(pipeline, 4, start=200)
    await asyncio.sleep(0.01)
    assert len(transcriber.detect_calls) == 2
