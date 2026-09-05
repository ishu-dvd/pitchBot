from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from pitchbot.adapters import AudioChunk, TranscriptChunk, VoiceActivity
from pitchbot.adapters.errors import PermanentAdapterError
from pitchbot.adapters.mocks import MockSpeechToTextAdapter, MockVoiceActivityDetector
from pitchbot.domain import LanguageCode
from pitchbot.speech import (
    EndpointReason,
    SpeechFrame,
    SpeechTurnPipeline,
    TurnTaking,
    TurnTakingConfig,
    TurnTakingState,
    UtteranceOutcome,
)

START = datetime(2026, 1, 1, tzinfo=UTC)
FRAME_MS = 100


def frame(index: int, *, is_speech: bool, byte_count: int = 1_024) -> SpeechFrame:
    return SpeechFrame(
        sequence=index,
        byte_count=byte_count,
        duration_ms=FRAME_MS,
        is_speech=is_speech,
        captured_at=START + timedelta(milliseconds=index * FRAME_MS),
    )


def config(**overrides: int) -> TurnTakingConfig:
    values: dict[str, int] = {
        "min_speech_ms": 200,
        "end_silence_ms": 300,
        "max_utterance_ms": 2_000,
        "barge_in_speech_ms": 200,
    }
    values.update(overrides)
    return TurnTakingConfig(**values)


def state_of(machine: TurnTaking) -> TurnTakingState:
    """Read the state without letting a type checker narrow it across asserts."""

    return machine.state


def drive(machine: TurnTaking, pattern: str) -> list[object]:
    """Feed a pattern where ``s`` is a speech frame and ``.`` is a silent one."""

    decisions: list[object] = []
    for index, symbol in enumerate(pattern):
        decisions.append(machine.observe(frame(index, is_speech=symbol == "s")))
    return decisions


def test_silence_before_speech_never_opens_an_utterance() -> None:
    machine = TurnTaking(config())

    decisions = drive(machine, "....")

    assert state_of(machine) is TurnTakingState.IDLE
    assert all(decision.segment is None for decision in decisions)  # type: ignore[attr-defined]


def test_an_utterance_closes_only_after_the_configured_end_silence() -> None:
    machine = TurnTaking(config(end_silence_ms=300))

    # Three speech frames, then silence. The first two silent frames are inside the
    # buyer's own pause and must not end the turn.
    decisions = drive(machine, "sss..")
    assert all(decision.segment is None for decision in decisions)  # type: ignore[attr-defined]
    assert state_of(machine) is TurnTakingState.LISTENING

    final = machine.observe(frame(5, is_speech=False))

    assert final.segment is not None
    assert final.segment.reason is EndpointReason.SILENCE
    assert final.segment.speech_ms == 300
    assert final.segment.silence_ms == 300
    assert state_of(machine) is TurnTakingState.IDLE


def test_a_short_noise_burst_is_discarded_instead_of_becoming_a_turn() -> None:
    machine = TurnTaking(config(min_speech_ms=300))

    decisions = drive(machine, "s...")

    assert all(decision.segment is None for decision in decisions)  # type: ignore[attr-defined]
    assert state_of(machine) is TurnTakingState.IDLE


def test_a_stream_that_never_falls_silent_still_yields_the_floor() -> None:
    machine = TurnTaking(config(max_utterance_ms=500))

    decisions = drive(machine, "sssss")

    segments = [decision.segment for decision in decisions if decision.segment]  # type: ignore[attr-defined]
    assert len(segments) == 1
    assert segments[0].reason is EndpointReason.MAX_DURATION
    assert state_of(machine) is TurnTakingState.IDLE


def test_buyer_speech_while_the_agent_talks_is_a_barge_in_that_keeps_the_words() -> None:
    machine = TurnTaking(config(barge_in_speech_ms=200))
    machine.agent_started_speaking()
    assert state_of(machine) is TurnTakingState.AGENT_SPEAKING

    first = machine.observe(frame(0, is_speech=True))
    assert first.barge_in is None
    assert state_of(machine) is TurnTakingState.AGENT_SPEAKING

    second = machine.observe(frame(1, is_speech=True))

    assert second.barge_in is not None
    assert second.barge_in.speech_ms == 200
    # The interrupting speech opens the next utterance instead of being thrown away.
    assert state_of(machine) is TurnTakingState.LISTENING

    closing = drive(machine, "s...")
    segments = [decision.segment for decision in closing if decision.segment]  # type: ignore[attr-defined]
    assert len(segments) == 1
    # 2 frames of interrupting speech before the threshold plus 1 after it. Counting
    # only the triggering frame would silently amputate the start of every interruption.
    assert segments[0].speech_ms == 3 * FRAME_MS
    assert segments[0].frame_count == 6


def test_isolated_noise_while_the_agent_talks_is_not_a_barge_in() -> None:
    machine = TurnTaking(config(barge_in_speech_ms=300))
    machine.agent_started_speaking()

    decisions = [
        machine.observe(frame(0, is_speech=True)),
        machine.observe(frame(1, is_speech=False)),
        machine.observe(frame(2, is_speech=True)),
        machine.observe(frame(3, is_speech=False)),
    ]

    assert all(decision.barge_in is None for decision in decisions)
    assert state_of(machine) is TurnTakingState.AGENT_SPEAKING


def test_stopping_closes_a_long_enough_utterance_and_drops_a_short_one() -> None:
    machine = TurnTaking(config(min_speech_ms=200))
    drive(machine, "sss")

    segment = machine.stop()

    assert segment is not None
    assert segment.reason is EndpointReason.STOPPED
    assert state_of(machine) is TurnTakingState.IDLE

    short = TurnTaking(config(min_speech_ms=500))
    drive(short, "s")
    assert short.stop() is None


def test_turn_taking_config_rejects_impossible_thresholds() -> None:
    with pytest.raises(ValueError, match="end_silence_ms"):
        TurnTakingConfig(end_silence_ms=0)
    with pytest.raises(ValueError, match="max_utterance_ms"):
        TurnTakingConfig(max_utterance_ms=0)
    with pytest.raises(ValueError, match="min_speech_ms must not exceed"):
        TurnTakingConfig(min_speech_ms=5_000, max_utterance_ms=1_000)


class _ScriptedTranscriber(MockSpeechToTextAdapter):
    def __init__(self, transcripts: list[TranscriptChunk]) -> None:
        super().__init__(transcripts=transcripts)


class _RecordingTranscriber(MockSpeechToTextAdapter):
    """Records exactly which audio chunks were handed to transcription."""

    def __init__(self) -> None:
        super().__init__(transcripts=[])
        self.sequences: list[int] = []

    async def transcribe(
        self,
        audio: AsyncIterator[AudioChunk],
    ) -> AsyncIterator[TranscriptChunk]:
        async for item in audio:
            self.sequences.append(item.sequence)
        return
        yield  # pragma: no cover - unreachable, keeps this an async generator


class _FailingAfterDetector(MockVoiceActivityDetector):
    """Classifies ``healthy_frames`` normally, then fails the way a real model can.

    A wrapper around an acoustic model breaks partway through a call — the device is
    lost, the model is unloaded, a frame is malformed — so the dangerous case is a fault
    that starts while an utterance is already open, not one that is failing from the
    first frame.
    """

    def __init__(self, *, decisions: list[bool], healthy_frames: int, error: Exception) -> None:
        super().__init__(decisions=decisions)
        self._healthy_frames = healthy_frames
        self._failure = error

    def detect(self, frame: AudioChunk) -> VoiceActivity:
        if self.frames_seen >= self._healthy_frames:
            raise self._failure
        return super().detect(frame)


def chunk(index: int, *, size: int = 1_024) -> AudioChunk:
    return AudioChunk(
        data=b"x" * size,
        captured_at=START + timedelta(milliseconds=index * FRAME_MS),
        sequence=index,
    )


def pipeline(
    *,
    decisions: list[bool],
    transcripts: list[TranscriptChunk] | None = None,
    transcriber: MockSpeechToTextAdapter | None = None,
    max_utterance_bytes: int = 2 * 1024 * 1024,
    min_confidence: float = 0.3,
) -> SpeechTurnPipeline:
    return SpeechTurnPipeline(
        detector=MockVoiceActivityDetector(decisions=list(decisions)),
        transcriber=transcriber or _ScriptedTranscriber(transcripts or []),
        language=LanguageCode.ENGLISH,
        config=config(),
        frame_duration_ms=FRAME_MS,
        max_utterance_bytes=max_utterance_bytes,
        min_confidence=min_confidence,
    )


async def run(engine: SpeechTurnPipeline, count: int, *, size: int = 1_024) -> list[object]:
    return [await engine.push(chunk(index, size=size)) for index in range(count)]


# --------------------------------------------------------------------------------------
# A frame's duration is measured, not assumed
# --------------------------------------------------------------------------------------

PCM_30MS_BYTES = 960  # 30 ms of 16 kHz mono 16-bit PCM, the shape microphone.py captures


def measuring_pipeline(*, decisions: list[bool], frame_duration_ms: int) -> SpeechTurnPipeline:
    return SpeechTurnPipeline(
        detector=MockVoiceActivityDetector(decisions=list(decisions)),
        transcriber=_ScriptedTranscriber([]),
        language=LanguageCode.ENGLISH,
        config=TurnTakingConfig(
            min_speech_ms=200,
            end_silence_ms=700,
            max_utterance_ms=20_000,
            barge_in_speech_ms=300,
        ),
        frame_duration_ms=frame_duration_ms,
    )


@pytest.mark.asyncio
async def test_pcm_frames_are_timed_by_their_own_length_not_the_configured_default() -> None:
    """The bug that cut buyers off mid-sentence.

    `frame_duration_ms` defaults to 250 because the browser calls `MediaRecorder.start(250)`,
    and `create_speech_pipeline` never overrode it. A 30 ms microphone frame was therefore
    counted as 250 ms - 8.3x - so `max_utterance_ms` fired after 80 frames, which is 2.4 s of
    real speech rather than 20 s. Measured live, 8.4 s of continuous English became four
    utterances, each answered as a separate remark.

    100 frames is well past that 80-frame cliff and nowhere near 20 s of real audio.
    """

    engine = measuring_pipeline(decisions=[True] * 100, frame_duration_ms=250)
    results = await run(engine, 100, size=PCM_30MS_BYTES)

    assert all(getattr(item, "utterance", None) is None for item in results), (
        "3 s of continuous speech must not be endpointed by a 20 s maximum"
    )


@pytest.mark.asyncio
async def test_a_frame_whose_length_is_not_pcm_keeps_the_configured_duration() -> None:
    """An encoded frame's byte count says nothing about how long it lasts.

    `MediaRecorder` sends WebM/Opus, and the benchmark sources send length proxies. Neither
    can be measured, so both must keep using the value the caller configured.
    """

    # 1 KiB is 32 ms of PCM - not a frame length WebRTC accepts - so it is not treated as
    # measurable, and the configured 250 ms stands. 80 x 250 ms reaches the 20 s maximum.
    engine = measuring_pipeline(decisions=[True] * 80, frame_duration_ms=250)
    results = await run(engine, 80, size=1_024)

    assert any(getattr(item, "utterance", None) is not None for item in results), (
        "an unmeasurable frame must still be counted at the configured duration"
    )


@pytest.mark.asyncio
async def test_trailing_silence_closes_a_pcm_utterance_at_its_real_duration() -> None:
    """`end_silence_ms` is a sum of frame durations too, so it was 8.3x short as well."""

    # 20 frames of speech, then silence. At 30 ms a 700 ms threshold needs 24 silent frames;
    # 20 of them must not be enough.
    engine = measuring_pipeline(decisions=[True] * 20 + [False] * 20, frame_duration_ms=250)
    results = await run(engine, 40, size=PCM_30MS_BYTES)
    assert all(getattr(item, "utterance", None) is None for item in results)

    # Four more silent frames crosses 700 ms of real silence and closes it.
    engine = measuring_pipeline(decisions=[True] * 20 + [False] * 24, frame_duration_ms=250)
    results = await run(engine, 44, size=PCM_30MS_BYTES)
    assert any(getattr(item, "utterance", None) is not None for item in results)


@pytest.mark.asyncio
async def test_an_endpointed_utterance_is_transcribed_once() -> None:
    transcript = TranscriptChunk(
        text="  We sell apparel online.  ",
        language=LanguageCode.ENGLISH,
        confidence=0.9,
        is_final=True,
        sequence=0,
    )
    engine = pipeline(decisions=[True] * 3 + [False] * 3, transcripts=[transcript])

    results = await run(engine, 6)

    utterances = [result.utterance for result in results if result.utterance]  # type: ignore[attr-defined]
    assert len(utterances) == 1
    assert utterances[0].outcome is UtteranceOutcome.TRANSCRIBED
    assert utterances[0].text == "We sell apparel online."
    assert utterances[0].is_turn is True


@pytest.mark.asyncio
async def test_a_final_transcript_wins_over_a_partial_one() -> None:
    engine = pipeline(
        decisions=[True] * 3 + [False] * 3,
        transcripts=[
            TranscriptChunk(
                text="we sell",
                language=LanguageCode.ENGLISH,
                confidence=0.9,
                is_final=False,
                sequence=0,
            ),
            TranscriptChunk(
                text="we sell apparel",
                language=LanguageCode.ENGLISH,
                confidence=0.9,
                is_final=True,
                sequence=1,
            ),
        ],
    )

    results = await run(engine, 6)

    utterance = next(result.utterance for result in results if result.utterance)  # type: ignore[attr-defined]
    assert utterance.text == "we sell apparel"


@pytest.mark.asyncio
async def test_the_pipeline_never_invents_buyer_speech() -> None:
    silent = pipeline(decisions=[True] * 3 + [False] * 3, transcripts=[])
    results = await run(silent, 6)
    utterance = next(result.utterance for result in results if result.utterance)  # type: ignore[attr-defined]
    assert utterance.outcome is UtteranceOutcome.NO_SPEECH_RECOGNIZED
    assert utterance.text is None
    assert utterance.is_turn is False

    unsure = pipeline(
        decisions=[True] * 3 + [False] * 3,
        transcripts=[
            TranscriptChunk(
                text="maybe something",
                language=LanguageCode.ENGLISH,
                confidence=0.1,
                is_final=True,
                sequence=0,
            )
        ],
    )
    results = await run(unsure, 6)
    utterance = next(result.utterance for result in results if result.utterance)  # type: ignore[attr-defined]
    assert utterance.outcome is UtteranceOutcome.LOW_CONFIDENCE
    assert utterance.text is None


@pytest.mark.asyncio
async def test_a_failing_transcriber_loses_one_utterance_and_not_the_call() -> None:
    engine = pipeline(
        decisions=[True] * 3 + [False] * 3 + [True] * 3 + [False] * 3,
        transcriber=MockSpeechToTextAdapter(error=PermanentAdapterError("model missing")),
    )

    results = await run(engine, 12)

    utterances = [result.utterance for result in results if result.utterance]  # type: ignore[attr-defined]
    assert len(utterances) == 2
    assert all(
        utterance.outcome is UtteranceOutcome.TRANSCRIBER_UNAVAILABLE for utterance in utterances
    )


@pytest.mark.asyncio
async def test_an_oversize_utterance_is_dropped_rather_than_truncated() -> None:
    engine = pipeline(
        decisions=[True] * 3 + [False] * 3,
        transcripts=[
            TranscriptChunk(
                text="should never be used",
                language=LanguageCode.ENGLISH,
                confidence=0.9,
                is_final=True,
                sequence=0,
            )
        ],
        max_utterance_bytes=1_500,
    )

    results = await run(engine, 6, size=1_024)

    utterance = next(result.utterance for result in results if result.utterance)  # type: ignore[attr-defined]
    assert utterance.outcome is UtteranceOutcome.OVERSIZE
    assert utterance.text is None


@pytest.mark.asyncio
async def test_a_failing_detector_does_not_end_the_call() -> None:
    engine = SpeechTurnPipeline(
        detector=MockVoiceActivityDetector(error=PermanentAdapterError("detector down")),
        transcriber=_ScriptedTranscriber([]),
        language=LanguageCode.ENGLISH,
        config=config(),
        frame_duration_ms=FRAME_MS,
    )

    results = await run(engine, 4)

    assert all(result.utterance is None for result in results)  # type: ignore[attr-defined]
    assert all(result.barge_in is None for result in results)  # type: ignore[attr-defined]
    assert engine.turn_taking.state is TurnTakingState.IDLE


@pytest.mark.asyncio
async def test_a_detector_that_fails_mid_utterance_still_yields_the_floor() -> None:
    transcriber = _RecordingTranscriber()
    engine = SpeechTurnPipeline(
        detector=_FailingAfterDetector(
            decisions=[True] * 3,
            healthy_frames=3,
            error=PermanentAdapterError("detector unloaded"),
        ),
        transcriber=transcriber,
        language=LanguageCode.ENGLISH,
        config=config(end_silence_ms=300),
        frame_duration_ms=FRAME_MS,
    )

    results = [await engine.push(chunk(index)) for index in range(3)]
    assert state_of(engine.turn_taking) is TurnTakingState.LISTENING

    # The detector now fails for every remaining frame. A dropped frame never reaches the
    # machine, so trailing silence would stall and the buyer would hold the floor for the
    # rest of the call with their audio pinned in memory.
    results += [await engine.push(chunk(index)) for index in range(3, 23)]

    utterances = [result.utterance for result in results if result.utterance]
    assert len(utterances) == 1
    assert utterances[0].segment.reason is EndpointReason.SILENCE
    assert utterances[0].dropped_frames == 3
    assert state_of(engine.turn_taking) is TurnTakingState.IDLE
    assert transcriber.sequences == [0, 1, 2, 3, 4, 5]
    assert engine._buffer == []
    assert engine._buffered_bytes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [RuntimeError("device lost"), ValueError("bad frame size")],
)
async def test_a_detector_raising_outside_the_adapter_hierarchy_does_not_kill_the_call(
    failure: Exception,
) -> None:
    engine = SpeechTurnPipeline(
        detector=_FailingAfterDetector(
            decisions=[True] * 2,
            healthy_frames=2,
            error=failure,
        ),
        transcriber=_ScriptedTranscriber([]),
        language=LanguageCode.ENGLISH,
        config=config(end_silence_ms=300),
        frame_duration_ms=FRAME_MS,
    )

    # A model wrapper is not obliged to raise AdapterError. Letting one escape here would
    # unwind the audio socket, which only handles a disconnect, and drop the call.
    results = await run(engine, 8)

    utterances = [result.utterance for result in results if result.utterance]  # type: ignore[attr-defined]
    assert len(utterances) == 1
    assert engine.turn_taking.state is TurnTakingState.IDLE


@pytest.mark.asyncio
async def test_audio_is_released_after_every_utterance() -> None:
    engine = pipeline(decisions=[True] * 3 + [False] * 3, transcripts=[])

    await run(engine, 6)

    assert engine._buffer == []
    assert engine._buffered_bytes == 0


@pytest.mark.asyncio
async def test_barge_in_releases_the_agent_turn_audio_and_starts_a_new_utterance() -> None:
    engine = pipeline(decisions=[True, True, True, True, False, False, False], transcripts=[])
    engine.agent_started_speaking()

    results = await run(engine, 7)

    barge_ins = [result.barge_in for result in results if result.barge_in]  # type: ignore[attr-defined]
    assert len(barge_ins) == 1
    utterances = [result.utterance for result in results if result.utterance]  # type: ignore[attr-defined]
    assert len(utterances) == 1


@pytest.mark.asyncio
async def test_stopping_the_microphone_closes_an_open_utterance() -> None:
    engine = pipeline(
        decisions=[True] * 3,
        transcripts=[
            TranscriptChunk(
                text="we are still talking",
                language=LanguageCode.ENGLISH,
                confidence=0.9,
                is_final=True,
                sequence=0,
            )
        ],
    )
    await run(engine, 3)

    utterance = await engine.stop()

    assert utterance is not None
    assert utterance.segment.reason is EndpointReason.STOPPED
    assert utterance.text == "we are still talking"
    assert await engine.stop() is None


def test_pipeline_rejects_impossible_bounds() -> None:
    with pytest.raises(ValueError, match="max_utterance_bytes"):
        pipeline(decisions=[], max_utterance_bytes=0)
    with pytest.raises(ValueError, match="min_confidence"):
        pipeline(decisions=[], min_confidence=1.5)


def test_the_mock_detector_classifies_by_encoded_frame_size() -> None:
    detector = MockVoiceActivityDetector(speech_threshold_bytes=512)

    assert detector.detect(chunk(0, size=1_024)).is_speech is True
    assert detector.detect(chunk(1, size=64)).is_speech is False
    assert detector.frames_seen == 2


def test_speech_frames_reject_unbounded_or_naive_input() -> None:
    with pytest.raises(ValueError, match="duration_ms"):
        SpeechFrame(
            sequence=0,
            byte_count=1,
            duration_ms=10_000,
            is_speech=True,
            captured_at=START,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        SpeechFrame(
            sequence=0,
            byte_count=1,
            duration_ms=100,
            is_speech=True,
            captured_at=datetime(2026, 1, 1),
        )


@pytest.mark.asyncio
async def test_the_transcriber_receives_only_the_current_utterance() -> None:
    transcriber = MockSpeechToTextAdapter(
        transcripts=[
            TranscriptChunk(
                text="first",
                language=LanguageCode.ENGLISH,
                confidence=0.9,
                is_final=True,
                sequence=0,
            )
        ]
    )
    engine = pipeline(
        decisions=[False, False, True, True, True, False, False, False],
        transcriber=transcriber,
    )

    await run(engine, 8)

    # The two leading silent frames precede the utterance and are never sent.
    assert [sequence for sequence, _ in transcriber.received_audio] == [2, 3, 4, 5, 6, 7]


@pytest.mark.asyncio
async def test_without_a_transcriber_utterances_close_but_no_audio_is_held() -> None:
    engine = SpeechTurnPipeline(
        detector=MockVoiceActivityDetector(decisions=[True] * 3 + [False] * 3),
        transcriber=None,
        language=LanguageCode.ENGLISH,
        config=config(),
        frame_duration_ms=FRAME_MS,
    )
    assert engine.can_transcribe is False

    results = await run(engine, 6)

    utterances = [result.utterance for result in results if result.utterance]  # type: ignore[attr-defined]
    assert len(utterances) == 1
    assert utterances[0].outcome is UtteranceOutcome.TRANSCRIBER_UNAVAILABLE
    assert utterances[0].text is None
    assert utterances[0].is_turn is False
    # Audio that can never be transcribed is never held, even mid-utterance.
    assert engine._buffer == []
    assert engine._buffered_bytes == 0


def test_a_broken_off_interruption_discards_its_audio_instead_of_carrying_it_forward() -> None:
    machine = TurnTaking(config(barge_in_speech_ms=300))
    machine.agent_started_speaking()

    partial = machine.observe(frame(0, is_speech=True))
    broken = machine.observe(frame(1, is_speech=False))

    assert partial.capture is True
    assert broken.discarded is True
    assert state_of(machine) is TurnTakingState.AGENT_SPEAKING


def test_the_agent_floor_is_reclaimed_when_playback_is_never_reported_finished() -> None:
    machine = TurnTaking(config(agent_floor_ms=2 * FRAME_MS))
    machine.agent_started_speaking()

    machine.observe(frame(0, is_speech=False))
    reclaimed = machine.observe(frame(1, is_speech=False))

    assert reclaimed.state is TurnTakingState.IDLE
    # A lost playback notification must not mute the buyer for the rest of the call.
    decisions = drive(machine, "ss...")
    segments = [decision.segment for decision in decisions if decision.segment]  # type: ignore[attr-defined]
    assert len(segments) == 1


@pytest.mark.asyncio
async def test_a_discarded_burst_is_not_prepended_to_the_next_utterance() -> None:
    transcriber = _RecordingTranscriber()
    engine = pipeline(
        # One sub-threshold burst, then a real utterance.
        decisions=[True, False, False, False, True, True, True, False, False, False],
        transcriber=transcriber,
    )

    await run(engine, 10)

    assert transcriber.sequences == [4, 5, 6, 7, 8, 9]
    assert engine._buffer == []
    assert engine._buffered_bytes == 0


@pytest.mark.asyncio
async def test_repeated_noise_bursts_do_not_latch_the_oversize_cap() -> None:
    transcript = TranscriptChunk(
        text="Send me the price list.",
        language=LanguageCode.ENGLISH,
        confidence=0.9,
        is_final=True,
        sequence=0,
    )
    burst = [True, False, False, False]
    engine = pipeline(
        decisions=burst * 4 + [True, True, True, False, False, False],
        transcripts=[transcript],
        max_utterance_bytes=8 * 1_024,
    )

    results = await run(engine, 22)

    utterances = [result.utterance for result in results if result.utterance]  # type: ignore[attr-defined]
    assert len(utterances) == 1
    # Leaked burst audio used to fill the cap and fail every later utterance closed.
    assert utterances[0].outcome is UtteranceOutcome.TRANSCRIBED


@pytest.mark.asyncio
async def test_a_control_frame_abandon_drops_the_barge_in_audio() -> None:
    transcriber = _RecordingTranscriber()
    engine = pipeline(
        decisions=[True] + [True] * 3 + [False] * 3,
        transcriber=transcriber,
    )
    engine.agent_started_speaking()

    # One sub-threshold interruption, abandoned by the browser reporting that playback
    # finished rather than by a later frame. Nothing else can discard it: the machine
    # never sees another frame in AGENT_SPEAKING, so no discarded decision is produced.
    await engine.push(chunk(0))
    assert engine._buffered_bytes > 0
    engine.agent_stopped_speaking()

    assert engine.turn_taking.state is TurnTakingState.IDLE
    assert engine._buffer == []
    assert engine._buffered_bytes == 0

    for index in range(1, 7):
        await engine.push(chunk(index))

    # Sequence 0 was spoken over the agent and abandoned. Carrying it forward attributes
    # it to a later, unrelated turn.
    assert transcriber.sequences == [1, 2, 3, 4, 5, 6]


@pytest.mark.asyncio
async def test_a_stray_playback_report_does_not_truncate_an_open_utterance() -> None:
    transcriber = _RecordingTranscriber()
    engine = pipeline(decisions=[True] * 3 + [False] * 3, transcriber=transcriber)

    await engine.push(chunk(0))
    assert engine.turn_taking.state is TurnTakingState.LISTENING

    # The browser may report playback finished at any time, including while the buyer
    # already holds the floor. The machine treats that as a no-op, so the buffer must
    # survive it; dropping it would transcribe half a sentence as the whole turn.
    engine.agent_stopped_speaking()
    assert engine._buffered_bytes > 0

    for index in range(1, 6):
        await engine.push(chunk(index))

    assert transcriber.sequences == [0, 1, 2, 3, 4, 5]
