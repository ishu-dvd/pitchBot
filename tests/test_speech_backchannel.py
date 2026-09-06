"""Filling a four-and-a-half-second silence without saying anything the agent will regret.

Measured 2026-09-04, the wait between a buyer finishing a sentence and the reply being
audible is ~4.5 s, of which transcription is 3,982 ms of 4,507 ms in English. That is long
enough that a person assumes the line dropped, which is why this exists.

Two things about it are easy to get wrong and are what most of these tests are for.

**The filler is chosen before anyone knows what was said.** It has to start when the
endpointer closes the utterance, because that is where the 4.5 s begins - so at the moment
of choosing, the transcript does not exist. Every candidate therefore has to be safe
against *whatever* the buyer just said. "Ok" is not: if the untranscribed sentence was
*"so you'll do it for fifty thousand?"*, the agent has agreed out loud to a number nobody
quoted. Receipt, never assent.

**It must never cost a turn.** A filler is a courtesy. If it raises, overruns, or leaves
the microphone muted, it has taken something away from the conversation it was added to
improve, so the failure paths are tested as carefully as the happy one.
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
from pitchbot.speech.backchannel import (
    MIN_WORK_MS,
    Backchannel,
    BackchannelPhrases,
    backchannel_languages,
)
from pitchbot.speech.microphone import FRAME_BYTES, FRAME_MS
from pitchbot.speech.pipeline import SpeechTurnPipeline

SPEECH = b"\x40" * FRAME_BYTES
SILENCE = b"\x00" * 16

# Words that claim agreement rather than attention, in every language and register this
# project speaks. A filler is spoken before the buyer's sentence has been transcribed, so
# any of these would be assent to an unknown proposition.
ASSENT = {
    "ok",
    "okay",
    "yes",
    "yeah",
    "sure",
    "agreed",
    "done",
    "haan",
    "haan ji",
    "theek hai",
    "ji haan",
    "हाँ",
    "जी हाँ",
    "ठीक है",
    "అవును",
    "సరే అలాగే",
}


# --------------------------------------------------------------------------------------
# The policy
# --------------------------------------------------------------------------------------


def test_nothing_is_said_before_a_person_would_say_anything() -> None:
    """A beat of silence after someone stops talking is normal turn-taking, not a gap."""

    backchannel = Backchannel()
    assert backchannel.due(0, LanguageCode.ENGLISH) is None
    assert backchannel.due(backchannel.first_after_ms - 1, LanguageCode.ENGLISH) is None
    assert backchannel.due(backchannel.first_after_ms, LanguageCode.ENGLISH) is not None


def test_a_long_wait_earns_a_second_and_longer_phrase() -> None:
    """The second one acknowledges the wait rather than repeating the acknowledgement.

    Saying "hmm" twice sounds like a stuck recording; "one moment" is what a person says
    when they know they are taking a while.
    """

    backchannel = Backchannel()
    first = backchannel.due(backchannel.first_after_ms, LanguageCode.ENGLISH)
    assert backchannel.due(backchannel.first_after_ms + 10, LanguageCode.ENGLISH) is None

    second = backchannel.due(backchannel.second_after_ms, LanguageCode.ENGLISH)
    assert second is not None
    assert second != first


def test_it_stops_after_two_even_if_the_wait_never_ends() -> None:
    """Measured headroom would allow four. Two is where attentive becomes anxious."""

    backchannel = Backchannel()
    said = [
        backchannel.due(backchannel.second_after_ms * (index + 1), LanguageCode.ENGLISH)
        for index in range(6)
    ]
    assert len([phrase for phrase in said if phrase]) == backchannel.max_per_turn


def test_consecutive_turns_do_not_repeat_the_same_token() -> None:
    """Identical every turn is what makes a backchannel sound synthetic.

    Rotation is a cursor rather than a random pick so a test can assert it; the cursor
    survives `begin_turn` for exactly this reason.
    """

    backchannel = Backchannel()
    said = []
    for _ in range(3):
        backchannel.begin_turn()
        said.append(backchannel.due(backchannel.first_after_ms, LanguageCode.ENGLISH))
    assert len(set(said)) == len(said)


def test_a_language_with_no_phrases_stays_silent() -> None:
    backchannel = Backchannel()
    assert backchannel.due(10_000, LanguageCode.UNKNOWN) is None


@pytest.mark.parametrize("language", sorted(backchannel_languages()))
def test_no_filler_ever_claims_agreement(language: LanguageCode) -> None:
    """The rule the whole module is built around, asserted rather than trusted.

    A filler is spoken before the buyer's sentence has been transcribed. If the sentence
    was a price proposal, an agreeing filler has committed the agent to a number nobody
    quoted - in a sales call, out loud, on the record. "Theek hai" and "ok" are natural
    and are excluded for exactly this reason.
    """

    from pitchbot.speech.backchannel import _PHRASES  # noqa: PLC2701

    phrases = _PHRASES[language]
    for phrase in (*phrases.brief, *phrases.patient):
        stripped = phrase.strip().strip(".।!").lower()
        assert stripped not in ASSENT, f"{phrase!r} reads as agreement, not acknowledgement"


@pytest.mark.parametrize("language", sorted(backchannel_languages()))
def test_every_filler_is_short(language: LanguageCode) -> None:
    """Long is worse than silent: it eats the gap it was meant to hide.

    Measured, the longest candidate here is 1.07 s spoken against a 4.5 s gap. A filler
    that outlasts the wait delays the reply instead of covering it.
    """

    from pitchbot.speech.backchannel import _PHRASES  # noqa: PLC2701

    phrases = _PHRASES[language]
    for phrase in (*phrases.brief, *phrases.patient):
        assert len(phrase.split()) <= 3, f"{phrase!r} is a sentence, not a backchannel"


def test_a_language_must_offer_both_lengths() -> None:
    with pytest.raises(ValueError, match="brief and a patient"):
        BackchannelPhrases(brief=(), patient=("One moment.",))


def test_thresholds_must_increase() -> None:
    with pytest.raises(ValueError, match="thresholds must increase"):
        Backchannel(first_after_ms=900, second_after_ms=400)


# --------------------------------------------------------------------------------------
# Wired to a listener
# --------------------------------------------------------------------------------------


class SlowTranscriber(MockSpeechToTextAdapter):
    """A transcriber that takes long enough for the silence to be worth filling."""

    def __init__(self, chunks: list[TranscriptChunk], delay_s: float) -> None:
        super().__init__(chunks)
        self._delay_s = delay_s

    async def transcribe(self, audio: Any) -> Any:
        await asyncio.sleep(self._delay_s)
        async for chunk in super().transcribe(audio):
            yield chunk


class RecordingMicrophone:
    """Yields frames and records exactly when it was muted, and by how much."""

    def __init__(self, payloads: list[bytes]) -> None:
        self._payloads = payloads
        self.paused = False
        self.pause_calls = 0
        self.resume_calls = 0
        self.stopped = False

    async def frames(self) -> Any:
        for index, payload in enumerate(self._payloads, start=1):
            yield AudioChunk(
                data=payload,
                captured_at=datetime.now(UTC),
                sequence=index,
                sample_rate_hz=16_000,
            )
            await asyncio.sleep(0)

    def pause(self) -> None:
        self.paused = True
        self.pause_calls += 1

    def resume(self) -> None:
        self.paused = False
        self.resume_calls += 1

    async def stop(self) -> None:
        self.stopped = True


def build(
    *,
    delay_s: float,
    said: list[str],
    first_after_ms: int = 20,
    second_after_ms: int | None = None,
    fail: bool = False,
    language: LanguageCode = LanguageCode.ENGLISH,
) -> tuple[Listener, RecordingMicrophone]:
    microphone = RecordingMicrophone([SPEECH] * 10 + [SILENCE] * 60)

    async def say(text: str) -> None:
        said.append(text)
        if fail:
            raise RuntimeError("playback device disappeared")
        await asyncio.sleep(0.01)

    listener = Listener(
        microphone,
        None,
        backchannel=Backchannel(
            first_after_ms=first_after_ms,
            second_after_ms=second_after_ms if second_after_ms is not None else first_after_ms * 3,
        ),
        say=say,
        language=language,
    )
    pipeline = SpeechTurnPipeline(
        detector=MockVoiceActivityDetector(speech_threshold_bytes=64),
        transcriber=SlowTranscriber(
            [
                TranscriptChunk(
                    text="We sell toys online.",
                    language=LanguageCode.ENGLISH,
                    confidence=0.9,
                    is_final=True,
                    sequence=1,
                )
            ],
            delay_s,
        ),
        language=LanguageCode.ENGLISH,
        frame_duration_ms=FRAME_MS,
        on_thinking=listener.start_thinking,
    )
    listener.attach(pipeline)
    return listener, microphone


@pytest.mark.asyncio
async def test_a_slow_transcription_is_covered_rather_than_left_silent() -> None:
    said: list[str] = []
    listener, microphone = build(delay_s=0.20, said=said)

    heard = await asyncio.wait_for(listener.next_turn(), timeout=5)

    assert heard is not None
    assert heard.text == "We sell toys online."
    assert said, "a 200 ms wait should have been filled"
    # The microphone is muted for the filler and handed back afterwards, so the buyer is
    # never recorded talking over the agent and is never left talking to a muted device.
    assert microphone.pause_calls == microphone.resume_calls == len(said)
    assert microphone.paused is False


@pytest.mark.asyncio
async def test_a_fast_turn_is_not_padded() -> None:
    """The filler hides latency; it must never add any.

    A transcriber that answers immediately leaves nothing to cover, and speaking anyway
    would delay the reply by the length of the courtesy.
    """

    said: list[str] = []
    listener, _ = build(delay_s=0.0, said=said, first_after_ms=2_000)

    heard = await asyncio.wait_for(listener.next_turn(), timeout=5)

    assert heard is not None
    assert said == []


@pytest.mark.asyncio
async def test_a_very_long_wait_earns_a_second_filler() -> None:
    said: list[str] = []
    listener, _ = build(delay_s=0.35, said=said, first_after_ms=20, second_after_ms=80)

    await asyncio.wait_for(listener.next_turn(), timeout=5)

    assert len(said) == 2
    assert said[0] != said[1]


@pytest.mark.asyncio
async def test_a_filler_that_cannot_be_played_does_not_cost_the_turn() -> None:
    """A courtesy that fails must not take the conversation with it.

    The microphone in particular has to come back: leaving it muted would end the call
    silently, with the buyer talking to a device that stopped listening.
    """

    said: list[str] = []
    listener, microphone = build(delay_s=0.20, said=said, fail=True)

    heard = await asyncio.wait_for(listener.next_turn(), timeout=5)

    assert heard is not None
    assert heard.text == "We sell toys online."
    assert microphone.paused is False
    assert microphone.resume_calls == microphone.pause_calls


@pytest.mark.asyncio
async def test_the_filler_follows_a_language_switch() -> None:
    """Thinking out loud in the language the conversation left is worse than silence."""

    said: list[str] = []
    listener, _ = build(delay_s=0.20, said=said, language=LanguageCode.ENGLISH)
    listener.set_language(LanguageCode.MIXED)

    await asyncio.wait_for(listener.next_turn(), timeout=5)

    from pitchbot.speech.backchannel import _PHRASES  # noqa: PLC2701

    hinglish = {*_PHRASES[LanguageCode.MIXED].brief, *_PHRASES[LanguageCode.MIXED].patient}
    assert said
    assert set(said) <= hinglish


@pytest.mark.asyncio
async def test_closing_the_listener_stops_it_thinking() -> None:
    """A conversation that ends mid-thought must not leave a task talking to nobody."""

    said: list[str] = []
    listener, microphone = build(delay_s=0.20, said=said, first_after_ms=5_000)

    await asyncio.wait_for(listener.next_turn(), timeout=5)
    await listener.close()

    assert microphone.stopped
    assert said == []


# --------------------------------------------------------------------------------------
# Two clocks: the buyer's silence, and our own work
# --------------------------------------------------------------------------------------


def test_silence_already_spent_endpointing_counts_towards_the_threshold() -> None:
    """The bug: a "700 ms" first filler was measured landing at 1,420 ms.

    `FIRST_AFTER_MS` is documented as a beat since the buyer stopped, but it was counted
    from the moment the endpointer *noticed* - which is `end_silence_ms` later. Both halves
    of that sentence were true and they were not the same instant.
    """

    policy = Backchannel()

    # No silence to credit - a typed turn - so the wait is the threshold, unchanged.
    assert policy.work_target_ms(policy.first_after_ms, 0.0) == float(policy.first_after_ms)

    # 720 ms already spent means only the remainder is owed, floored by the work deadband.
    assert policy.work_target_ms(policy.first_after_ms, 720.0) == float(policy.work_floor_ms)


def test_the_work_deadband_never_becomes_the_binding_constraint() -> None:
    """A floor on our work must not silently override a deliberately short beat.

    Capping it at `first_after_ms` keeps every zero-silence path byte-identical to before,
    so the change can only affect the path it was written for.
    """

    assert Backchannel().work_floor_ms == MIN_WORK_MS
    assert Backchannel(first_after_ms=10, second_after_ms=20).work_floor_ms == 10
    assert Backchannel(first_after_ms=10, second_after_ms=20).work_target_ms(10, 0.0) == 10.0


def test_a_reply_that_is_nearly_ready_is_not_padded_with_a_filler() -> None:
    """Why the deadband exists at all.

    Once endpoint silence is credited, every spoken turn clears the beat the instant we
    learn there is work - including one whose reply is milliseconds away. A filler is not
    free to abandon: the reply waits for it rather than chopping it, so filling there would
    extend the wait by the filler's own length rather than covering it.
    """

    policy = Backchannel()
    target = policy.work_target_ms(policy.first_after_ms, 720.0)

    assert target > 0, "a filler that starts at zero work can never be cancelled by a reply"
    assert policy.due(720.0 + target, LanguageCode.ENGLISH) is not None


def test_the_second_filler_still_lands_after_a_normal_reply() -> None:
    """Crediting silence moves both thresholds earlier, and one of them must not move.

    The whole spoken turn is ~2,587 ms. Left at the 2,500 it read before, the second filler
    would have started 87 ms *before* the reply was ready - and a filler that begins just
    before the reply delays it. 3,200 keeps the position it effectively had.
    """

    policy = Backchannel()
    spoken_at = 720.0 + policy.work_target_ms(policy.second_after_ms, 720.0)

    assert spoken_at > 2_587


@pytest.mark.asyncio
async def test_the_pipeline_tells_the_filler_how_long_the_buyer_has_been_quiet() -> None:
    """The wiring, end to end: without it the two clocks are still one clock.

    Asserted against the endpointer's own threshold rather than a literal, because the
    point is that it reports the silence that actually elapsed - not a constant either
    side happens to agree on.
    """

    offsets: list[float] = []
    pipeline = SpeechTurnPipeline(
        detector=MockVoiceActivityDetector(speech_threshold_bytes=64),
        transcriber=MockSpeechToTextAdapter(),
        language=LanguageCode.ENGLISH,
        frame_duration_ms=FRAME_MS,
        on_thinking=offsets.append,
    )

    sequence = 0
    for _ in range(10):
        await pipeline.push(_chunk(sequence, SPEECH))
        sequence += 1
    for _ in range(60):
        result = await pipeline.push(_chunk(sequence, b"\x00" * 16))
        sequence += 1
        if result.utterance is not None:
            break

    assert offsets, "the filler was never told there was work"
    assert offsets[0] >= pipeline.turn_taking.config.end_silence_ms


def _chunk(sequence: int, payload: bytes) -> AudioChunk:
    return AudioChunk(
        data=payload,
        captured_at=datetime.now(UTC),
        sequence=sequence,
        sample_rate_hz=16_000,
    )
