"""A whole spoken sales turn, from captured audio to a sold reply, with no hardware.

This is the test the voice loop was missing. Every part of it existed and was tested in
isolation - the detector classified frames, the endpointer closed utterances, the
transcriber returned text, the engine planned a reply, the synthesiser spoke it - and
nothing exercised them as one path from a microphone, because there was no microphone. The
first defect found by wiring them together was a frame-duration mismatch that no unit test
could have seen, since each component was individually correct.

The microphone is fake and the transcript is scripted, so this runs on a build agent with
no sound card. What is real is the pipeline, the engine, the planner and the turn-taking
state machine, which is where the wiring mistakes actually live.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from pitchbot.adapters.contracts import AudioChunk, TranscriptChunk
from pitchbot.adapters.mocks import MockSpeechToTextAdapter, MockVoiceActivityDetector
from pitchbot.cli.talk import Listener, speak
from pitchbot.domain import LanguageCode
from pitchbot.speech.microphone import FRAME_BYTES, FRAME_MS
from pitchbot.speech.pipeline import SpeechTurnPipeline

SPEECH = b"\x40" * FRAME_BYTES
SILENCE = b"\x00" * 16


class FakeMicrophone:
    """Yields a fixed run of frames, and records when the floor was taken from it."""

    def __init__(self, payloads: list[bytes]) -> None:
        self._payloads = payloads
        self.paused = False
        self.pause_calls = 0
        self.resume_calls = 0
        self.stopped = False
        self.delivered_while_paused = 0

    async def frames(self) -> Any:
        for index, payload in enumerate(self._payloads, start=1):
            if self.paused:
                self.delivered_while_paused += 1
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


def build_listener(payloads: list[bytes], said: str) -> tuple[Listener, FakeMicrophone]:
    microphone = FakeMicrophone(payloads)
    pipeline = SpeechTurnPipeline(
        detector=MockVoiceActivityDetector(speech_threshold_bytes=64),
        transcriber=MockSpeechToTextAdapter(
            [
                TranscriptChunk(
                    text=said,
                    language=LanguageCode.ENGLISH,
                    confidence=0.9,
                    is_final=True,
                    sequence=1,
                )
            ]
        ),
        language=LanguageCode.ENGLISH,
        frame_duration_ms=FRAME_MS,
    )
    return Listener(microphone, pipeline), microphone


@pytest.mark.asyncio
async def test_speech_then_silence_becomes_one_buyer_turn() -> None:
    """The endpointer must close on trailing silence and hand over what was said."""

    listener, _ = build_listener([SPEECH] * 10 + [SILENCE] * 60, "We sell toys online.")

    heard = await asyncio.wait_for(listener.next_turn(), timeout=5)

    assert heard == "We sell toys online."


@pytest.mark.asyncio
async def test_a_closed_microphone_ends_the_conversation() -> None:
    """Running out of audio returns ``None`` rather than blocking for ever."""

    listener, _ = build_listener([SILENCE] * 5, "never reached")

    assert await asyncio.wait_for(listener.next_turn(), timeout=5) is None


@pytest.mark.asyncio
async def test_an_unintelligible_utterance_is_reported_and_waited_past() -> None:
    """Silence must not be spent as a buyer turn, and must not look like a hang."""

    microphone = FakeMicrophone([SPEECH] * 10 + [SILENCE] * 60)
    pipeline = SpeechTurnPipeline(
        detector=MockVoiceActivityDetector(speech_threshold_bytes=64),
        # No transcriber at all, which is what a machine without the extra looks like.
        transcriber=None,
        language=LanguageCode.ENGLISH,
        frame_duration_ms=FRAME_MS,
    )
    listener = Listener(microphone, pipeline)
    skipped: list[str] = []

    result = await asyncio.wait_for(listener.next_turn(on_skip=skipped.append), timeout=5)

    assert result is None
    assert "transcriber-unavailable" in skipped


@pytest.mark.asyncio
async def test_the_microphone_is_deaf_while_the_agent_speaks() -> None:
    """Half duplex, asserted on the floor rather than on the audio.

    Without echo cancellation an open microphone hears the agent and endpoints on it, so
    the floor must be taken for the whole reply and given back afterwards.
    """

    listener, microphone = build_listener([SPEECH], "anything")
    spoken: list[str] = []

    class RecordingSpeaker:
        async def say(self, text: str) -> str | None:
            spoken.append(text)
            assert microphone.paused is True, "the microphone was open while the agent spoke"
            return None

    problem = await speak("Thanks, that helps.", RecordingSpeaker(), listener)  # type: ignore[arg-type]

    assert problem is None
    assert spoken == ["Thanks, that helps."]
    assert microphone.pause_calls == 1
    assert microphone.resume_calls == 1
    assert microphone.paused is False


@pytest.mark.asyncio
async def test_a_synthesis_failure_still_gives_the_floor_back() -> None:
    """A speaker that raises must not leave the buyer talking to a deaf program."""

    listener, microphone = build_listener([SPEECH], "anything")

    class BrokenSpeaker:
        async def say(self, text: str) -> str | None:
            raise RuntimeError("voice exploded")

    with pytest.raises(RuntimeError):
        await speak("hello", BrokenSpeaker(), listener)  # type: ignore[arg-type]

    assert microphone.paused is False
    assert microphone.resume_calls == 1


@pytest.mark.asyncio
async def test_speaking_without_a_listener_is_harmless() -> None:
    """The text path shares this helper, so a missing listener must not be a special case."""

    spoken: list[str] = []

    class RecordingSpeaker:
        async def say(self, text: str) -> str | None:
            spoken.append(text)
            return None

    assert await speak("hi", RecordingSpeaker(), None) is None  # type: ignore[arg-type]
    assert spoken == ["hi"]
    assert await speak("hi", None, None) is None


@pytest.mark.asyncio
async def test_closing_the_listener_releases_the_device() -> None:
    listener, microphone = build_listener([SILENCE], "unused")

    await listener.close()

    assert microphone.stopped is True


@pytest.mark.asyncio
async def test_a_spoken_turn_reaches_the_engine_and_is_sold_to() -> None:
    """End to end: audio in, a reply that pitches the vertical out.

    This is the assertion that would have failed before this change for a reason unrelated
    to speech - the engine heard "we sell toys" and answered with the next form field,
    because nothing connected the buyer's words to what was said back beyond slot filling.
    """

    from uuid import uuid4

    from pitchbot.conversation import ConversationEngine
    from pitchbot.conversation.planning import _PHRASES, _table

    listener, _ = build_listener([SPEECH] * 10 + [SILENCE] * 60, "We sell toys online.")
    heard = await asyncio.wait_for(listener.next_turn(), timeout=5)
    assert heard is not None

    engine = ConversationEngine()
    session_id = uuid4()
    engine.create_session(session_id)
    result = engine.process_turn(session_id, text=heard, language=LanguageCode.ENGLISH)

    expected = _PHRASES[_table(LanguageCode.ENGLISH)].pitch["toys"]  # noqa: SLF001
    assert expected in result.reply
