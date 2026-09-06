"""Giving the turn back when the decoder will not finish.

Measured 2026-09-06 (`probe_per_language_model.py`, `_flail.py`), on the shipped
`small`/int8 configuration and a **supported** language:

======================  =========  ==========
clip                    audio      median
======================  =========  ==========
English, 8 sentences    3.1-6.3 s  1,855 ms
Hindi, 5.8 s            5.8 s      2,491 ms
Hindi, **3.2 s**        3.2 s      **11,455 ms**
======================  =========  ==========

The same 3.2 s Hindi clip has been observed at **28,656 ms**. It is not a runaway output -
one segment, 40 characters, compression ratio 1.24 - the decoder simply searched. And
because cost is nearly flat in audio length (16.1 s of speech costs 2,245 ms), the
`max_audio_seconds` bound that already existed could never have caught it: the input was
never large, only slow.

Nothing else bounded it. The socket's receive loop waits inside ``push``, so an unbounded
transcription does not merely delay the reply - it makes the agent **deaf**, unable to
notice a barge-in, for however long the decoder takes. Twenty-eight seconds is 140x the
~200 ms gap a person leaves between turns.

These tests pin what the deadline is and, just as importantly, what it is not: it recovers
the **turn**, not the CPU. ``asyncio.to_thread`` cannot be interrupted, so the worker keeps
decoding until it finishes on its own.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import pytest

from pitchbot.adapters.contracts import AudioChunk, TranscriptChunk
from pitchbot.adapters.mocks import MockVoiceActivityDetector
from pitchbot.domain import LanguageCode
from pitchbot.speech import DEFAULT_TRANSCRIBE_TIMEOUT_MS
from pitchbot.speech.microphone import FRAME_MS
from pitchbot.speech.pipeline import SpeechTurnPipeline, UtteranceOutcome, UtteranceResult

# 30 ms of 16 kHz mono 16-bit PCM - the only frame length the real detector accepts, and
# the length whose duration the pipeline derives rather than assumes.
SPEECH = b"\x40" * 960
# Short enough that the byte-size mock calls it silence, as every turn-taking test does.
SILENCE = b"\x00" * 16


class SlowTranscriber:
    """Holds the decode open, the way a flailing Whisper search does.

    The wait is deliberately ``to_thread`` and not ``asyncio.sleep``: the real adapter
    calls ``model.transcribe`` in a worker thread, and the difference is the whole point
    of the last test in this file. A cancellable sleep would model a decoder that stops
    when asked, which is exactly what this one cannot do.
    """

    def __init__(self, delay_s: float) -> None:
        self.delay_s = delay_s
        self.started = 0
        self.finished = 0

    def _decode(self) -> None:
        time.sleep(self.delay_s)
        self.finished += 1

    async def transcribe(self, audio):  # type: ignore[no-untyped-def]
        async for _chunk in audio:
            pass
        self.started += 1
        await asyncio.to_thread(self._decode)
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


def _pipeline(transcriber: object, *, timeout_ms: float) -> SpeechTurnPipeline:
    return SpeechTurnPipeline(
        detector=MockVoiceActivityDetector(speech_threshold_bytes=64),
        transcriber=transcriber,  # type: ignore[arg-type]
        language=LanguageCode.ENGLISH,
        frame_duration_ms=FRAME_MS,
        transcribe_timeout_ms=timeout_ms,
    )


async def _one_utterance(pipeline: SpeechTurnPipeline, *, start: int = 0) -> UtteranceResult:
    # 10 frames clears min_speech_ms (200 ms); 40 clears end_silence_ms (700 ms).
    for index in range(10):
        await pipeline.push(_chunk(start + index, SPEECH))
    for index in range(40):
        result = await pipeline.push(_chunk(start + 10 + index, SILENCE))
        if result.utterance is not None:
            return result.utterance
    raise AssertionError("the endpointer never closed the utterance")


@pytest.mark.asyncio
async def test_a_decoder_that_will_not_finish_gives_the_turn_back() -> None:
    pipeline = _pipeline(SlowTranscriber(5.0), timeout_ms=40)

    started = asyncio.get_running_loop().time()
    utterance = await _one_utterance(pipeline)
    elapsed = (asyncio.get_running_loop().time() - started) * 1000

    assert utterance.outcome is UtteranceOutcome.TRANSCRIPTION_TIMED_OUT
    assert elapsed < 1_000, "the pipeline waited for the decoder instead of the deadline"


@pytest.mark.asyncio
async def test_a_timeout_is_not_reported_as_a_broken_transcriber() -> None:
    """`transcriber-unavailable` means "there is no transcriber", which is a different fault.

    Reporting a slow decode as a missing component would send an operator looking for a
    configuration problem that does not exist, and would hide the only fact that matters:
    the model was working, and working is what took too long.
    """

    pipeline = _pipeline(SlowTranscriber(5.0), timeout_ms=40)

    utterance = await _one_utterance(pipeline)

    assert utterance.outcome is not UtteranceOutcome.TRANSCRIBER_UNAVAILABLE
    assert utterance.outcome is not UtteranceOutcome.LANGUAGE_UNSUPPORTED
    assert utterance.is_turn is False
    assert utterance.text is None


@pytest.mark.asyncio
async def test_the_conversation_survives_a_timeout() -> None:
    """One lost utterance, not a lost call. The next thing the buyer says must work."""

    transcriber = SlowTranscriber(1.0)
    pipeline = _pipeline(transcriber, timeout_ms=40)
    first = await _one_utterance(pipeline)

    transcriber.delay_s = 0.0
    second = await _one_utterance(pipeline, start=100)

    assert first.outcome is UtteranceOutcome.TRANSCRIPTION_TIMED_OUT
    assert second.outcome is UtteranceOutcome.TRANSCRIBED
    assert second.text == "We sell toys online."


@pytest.mark.asyncio
async def test_a_healthy_transcription_is_untouched() -> None:
    """The deadline must be invisible in the case that is not pathological."""

    pipeline = _pipeline(SlowTranscriber(0.0), timeout_ms=DEFAULT_TRANSCRIBE_TIMEOUT_MS)

    utterance = await _one_utterance(pipeline)

    assert utterance.outcome is UtteranceOutcome.TRANSCRIBED
    assert utterance.text == "We sell toys online."


@pytest.mark.asyncio
async def test_zero_waits_forever_for_callers_that_want_that() -> None:
    """Batch callers may submit 120 s of audio, which legitimately costs four windows."""

    transcriber = SlowTranscriber(0.2)
    pipeline = _pipeline(transcriber, timeout_ms=0)

    utterance = await _one_utterance(pipeline)

    assert utterance.outcome is UtteranceOutcome.TRANSCRIBED
    assert transcriber.finished == 1


def test_a_negative_deadline_is_refused() -> None:
    with pytest.raises(ValueError, match="transcribe_timeout_ms"):
        _pipeline(SlowTranscriber(0.0), timeout_ms=-1)


@pytest.mark.asyncio
async def test_the_deadline_frees_the_turn_and_not_the_cpu() -> None:
    """Stated as a test because it is the one thing a reader could reasonably get wrong.

    `asyncio.wait_for` cancels the *await*. The decode itself runs in a thread that Python
    cannot interrupt, so it goes on burning CPU and will finish later. That is why the
    default deadline is generous rather than aggressive - every timeout leaves a worker
    competing with whatever runs next.
    """

    transcriber = SlowTranscriber(0.3)
    pipeline = _pipeline(transcriber, timeout_ms=40)

    utterance = await _one_utterance(pipeline)
    assert utterance.outcome is UtteranceOutcome.TRANSCRIPTION_TIMED_OUT
    assert transcriber.started == 1
    assert transcriber.finished == 0, "should have timed out before the decode completed"

    await asyncio.sleep(0.5)
    assert transcriber.finished == 1, "the abandoned decode kept running, as documented"
