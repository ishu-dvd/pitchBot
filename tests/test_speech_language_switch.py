"""A spoken conversation that changes language has to re-point the transcriber too.

The reply language and the voice are visible when they are wrong; the transcriber is not.
A Whisper decoder forced to the language the call opened in does not fail on speech in
another one - it returns fluent, confident text in the language it was told to expect
(measured 2026-09-04: Hindi audio forced to ``en`` came back as *"Our shop and our budget
is Rs. 50,000."*, labelled ``en`` at probability 1.00). Nothing downstream can tell that
apart from the buyer having said it.

So these tests cover the two halves of the fix that have no other alarm:

* the pipeline's ``language`` actually reaches the transcriber - it was previously stored
  and never read, so a caller who re-pointed the pipeline changed nothing and had no way
  to discover that;
* an adapter that cannot be re-pointed is tolerated rather than crashed on, because a
  transcriber built around a fixed language is a legitimate implementation and the
  pipeline is explicitly built to run with no transcriber at all.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from pitchbot.adapters.contracts import AudioChunk, TranscriptChunk
from pitchbot.adapters.mocks import MockSpeechToTextAdapter, MockVoiceActivityDetector
from pitchbot.cli.talk import Listener
from pitchbot.domain import LanguageCode
from pitchbot.speech.microphone import FRAME_BYTES, FRAME_MS
from pitchbot.speech.pipeline import RetunableTranscriber, SpeechTurnPipeline

SPEECH = b"\x40" * FRAME_BYTES


class RetunableStub(MockSpeechToTextAdapter):
    """A transcriber that records every language it is re-pointed to."""

    def __init__(self, chunks: list[TranscriptChunk]) -> None:
        super().__init__(chunks)
        self.languages: list[LanguageCode | None] = []

    def set_language(self, language: LanguageCode | None) -> None:
        self.languages.append(language)


class FixedLanguageStub(MockSpeechToTextAdapter):
    """A transcriber with no ``set_language``, which must not be an error."""


def _pipeline(transcriber: Any) -> SpeechTurnPipeline:
    return SpeechTurnPipeline(
        detector=MockVoiceActivityDetector(speech_threshold_bytes=64),
        transcriber=transcriber,
        language=LanguageCode.ENGLISH,
        frame_duration_ms=FRAME_MS,
    )


def test_the_pipeline_language_reaches_the_transcriber() -> None:
    """The regression this exists for: the field was assigned and never read.

    The constructor accepted a language, promised by its own signature to transcribe in
    it, and passed it nowhere - so every caller that set it was silently ignored.
    """

    transcriber = RetunableStub([])
    pipeline = _pipeline(transcriber)
    assert pipeline.language is LanguageCode.ENGLISH

    pipeline.set_language(LanguageCode.TELUGU)

    assert pipeline.language is LanguageCode.TELUGU
    assert transcriber.languages == [LanguageCode.TELUGU]


def test_a_transcriber_that_cannot_be_re_pointed_is_left_alone() -> None:
    pipeline = _pipeline(FixedLanguageStub([]))
    pipeline.set_language(LanguageCode.HINDI)
    assert pipeline.language is LanguageCode.HINDI


def test_a_pipeline_with_no_transcriber_can_still_be_re_pointed() -> None:
    """Endpointing works without a transcriber, so re-pointing must not require one."""

    pipeline = _pipeline(None)
    pipeline.set_language(LanguageCode.HINDI)
    assert pipeline.language is LanguageCode.HINDI


def test_the_retunable_protocol_recognises_only_what_it_should() -> None:
    assert isinstance(RetunableStub([]), RetunableTranscriber)
    assert not isinstance(FixedLanguageStub([]), RetunableTranscriber)


def test_the_listener_re_points_the_pipeline() -> None:
    """The CLI's one call site for following a switch on the audio path."""

    transcriber = RetunableStub([])
    listener = Listener(_FakeMicrophone(), _pipeline(transcriber))
    listener.set_language(LanguageCode.HINDI)
    assert transcriber.languages == [LanguageCode.HINDI]


class _FakeMicrophone:
    def __init__(self) -> None:
        self.paused = False

    async def frames(self) -> Any:  # pragma: no cover - never iterated here
        yield AudioChunk(
            data=SPEECH,
            captured_at=datetime.now(UTC),
            sequence=1,
            sample_rate_hz=16_000,
        )
        await asyncio.sleep(0)

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    async def stop(self) -> None:
        return None


# --------------------------------------------------------------------------------------
# The adapter itself, without loading a model
# --------------------------------------------------------------------------------------


def test_a_forced_language_is_reported_and_an_expected_one_is_not() -> None:
    """Only a language that was actually imposed may be reported as fact.

    Reporting an expectation that was not imposed would hide the single thing auto-detect
    exists to reveal - that the buyer has started speaking something else - and would do
    it while looking entirely healthy.
    """

    from pitchbot.adapters.faster_whisper_stt import FasterWhisperSpeechToTextAdapter

    forced = FasterWhisperSpeechToTextAdapter(language=LanguageCode.ENGLISH)
    assert forced._resolve_language("hi", 0.99) is LanguageCode.ENGLISH  # noqa: SLF001

    expecting = FasterWhisperSpeechToTextAdapter(
        language=LanguageCode.ENGLISH, force_language=False
    )
    assert expecting._resolve_language("hi", 0.99) is LanguageCode.HINDI  # noqa: SLF001


def test_an_unconfident_detection_is_unknown_rather_than_guessed() -> None:
    """Whisper returns a language for anything, including silence."""

    from pitchbot.adapters.faster_whisper_stt import FasterWhisperSpeechToTextAdapter

    adapter = FasterWhisperSpeechToTextAdapter(force_language=False)
    assert adapter._resolve_language("en", 0.1) is LanguageCode.UNKNOWN  # noqa: SLF001


def test_the_expected_language_moves_without_reloading_the_model() -> None:
    """Rebuilding the adapter would cost a measured 3.55 s model load mid-turn."""

    from pitchbot.adapters.faster_whisper_stt import FasterWhisperSpeechToTextAdapter

    adapter = FasterWhisperSpeechToTextAdapter(language=LanguageCode.ENGLISH, force_language=False)
    adapter._model = object()  # noqa: SLF001
    loaded = adapter._model  # noqa: SLF001

    adapter.set_language(LanguageCode.TELUGU)

    assert adapter.language is LanguageCode.TELUGU
    assert adapter._model is loaded  # noqa: SLF001


@pytest.mark.parametrize("forced", [True, False])
def test_telugu_script_repair_follows_the_expectation_not_the_forcing(forced: bool) -> None:
    """Repair is gated on what the conversation expects, which is not a guess.

    An expectation is either the caller's declaration or a language confirmed across
    consecutive turns; either is stronger evidence than one utterance's label. Tying
    repair to ``force_language`` would switch it off exactly when a switching conversation
    turned forcing off, which is when Telugu most needs it.
    """

    from pitchbot.adapters.faster_whisper_stt import FasterWhisperSpeechToTextAdapter

    adapter = FasterWhisperSpeechToTextAdapter(language=LanguageCode.TELUGU, force_language=forced)
    devanagari = "हमारी दुकान"
    assert adapter._repair_script(devanagari) != devanagari  # noqa: SLF001
