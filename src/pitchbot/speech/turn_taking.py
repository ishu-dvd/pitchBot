from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from pitchbot.speech.models import (
    MAX_AGENT_FLOOR_MS,
    MAX_FRAME_DURATION_MS,
    MAX_SILENCE_MS,
    MAX_UTTERANCE_MS,
    BargeIn,
    EndpointReason,
    SpeechFrame,
    SpeechSegment,
    TurnTakingState,
)


@dataclass(frozen=True, slots=True)
class TurnTakingConfig:
    """Thresholds that decide when the agent may speak.

    These are the numbers a buyer actually feels. ``end_silence_ms`` is the dominant
    term in perceived responsiveness: too low and the agent interrupts a buyer who is
    still thinking, too high and every reply feels sluggish. It is configuration rather
    than a constant so it can be tuned against measurements once a real detector is
    benchmarked.
    """

    min_speech_ms: int = 200
    end_silence_ms: int = 700
    max_utterance_ms: int = 20_000
    barge_in_speech_ms: int = 300
    agent_floor_ms: int = 30_000

    def __post_init__(self) -> None:
        if not 1 <= self.min_speech_ms <= MAX_UTTERANCE_MS:
            raise ValueError(f"min_speech_ms must be between 1 and {MAX_UTTERANCE_MS}")
        if not 1 <= self.end_silence_ms <= MAX_SILENCE_MS:
            raise ValueError(f"end_silence_ms must be between 1 and {MAX_SILENCE_MS}")
        if not 1 <= self.max_utterance_ms <= MAX_UTTERANCE_MS:
            raise ValueError(f"max_utterance_ms must be between 1 and {MAX_UTTERANCE_MS}")
        if not 1 <= self.barge_in_speech_ms <= MAX_UTTERANCE_MS:
            raise ValueError(f"barge_in_speech_ms must be between 1 and {MAX_UTTERANCE_MS}")
        if not 1 <= self.agent_floor_ms <= MAX_AGENT_FLOOR_MS:
            raise ValueError(f"agent_floor_ms must be between 1 and {MAX_AGENT_FLOOR_MS}")
        if self.min_speech_ms > self.max_utterance_ms:
            raise ValueError("min_speech_ms must not exceed max_utterance_ms")


@dataclass(frozen=True, slots=True)
class TurnTakingDecision:
    """What the caller should do after one frame."""

    state: TurnTakingState
    segment: SpeechSegment | None = None
    barge_in: BargeIn | None = None
    capture: bool = False
    """The frame belongs to an open or provisional utterance and may be buffered."""

    discarded: bool = False
    """An utterance was abandoned without a segment; anything buffered for it is stale."""


class TurnTaking:
    """Decides when the buyer has finished speaking and when they interrupt.

    The machine holds counters only. It never accumulates frames, so a stream that never
    goes silent costs a fixed amount of memory and is closed by ``max_utterance_ms``
    rather than growing without bound.
    """

    def __init__(self, config: TurnTakingConfig | None = None) -> None:
        self._config = config or TurnTakingConfig()
        self._state = TurnTakingState.IDLE
        self._speech_ms = 0
        self._silence_ms = 0
        self._trailing_silence_ms = 0
        self._frame_count = 0
        self._byte_count = 0
        self._started_at: datetime | None = None
        self._last_at: datetime | None = None
        self._barge_in_speech_ms = 0
        self._agent_speech_elapsed_ms = 0

    @property
    def state(self) -> TurnTakingState:
        return self._state

    @property
    def config(self) -> TurnTakingConfig:
        return self._config

    def agent_started_speaking(self) -> None:
        """The agent now holds the floor, so buyer speech counts as an interruption."""

        self._reset_utterance()
        self._state = TurnTakingState.AGENT_SPEAKING
        self._barge_in_speech_ms = 0
        self._agent_speech_elapsed_ms = 0

    def agent_stopped_speaking(self) -> None:
        if self._state is TurnTakingState.AGENT_SPEAKING:
            self._release_floor()

    def observe(self, frame: SpeechFrame) -> TurnTakingDecision:
        if self._state is TurnTakingState.AGENT_SPEAKING:
            return self._observe_while_agent_speaks(frame)
        return self._observe_while_listening(frame)

    def stop(self) -> SpeechSegment | None:
        """Close any open utterance, for example when the buyer stops the microphone."""

        if self._state is not TurnTakingState.LISTENING:
            self._reset_utterance()
            if self._state is not TurnTakingState.AGENT_SPEAKING:
                self._state = TurnTakingState.IDLE
            return None
        if self._speech_ms < self._config.min_speech_ms:
            self._reset_utterance()
            self._state = TurnTakingState.IDLE
            return None
        return self._close(EndpointReason.STOPPED)

    def _observe_while_agent_speaks(self, frame: SpeechFrame) -> TurnTakingDecision:
        self._agent_speech_elapsed_ms += frame.duration_ms
        if self._agent_speech_elapsed_ms >= self._config.agent_floor_ms:
            # The client never reported that playback finished. Reclaiming the floor
            # keeps one lost notification from muting the buyer for the rest of the call.
            stale = self._barge_in_speech_ms > 0
            self._release_floor()
            decision = self._observe_while_listening(frame)
            return replace(decision, discarded=True) if stale else decision
        if not frame.is_speech:
            if self._barge_in_speech_ms == 0:
                return TurnTakingDecision(state=self._state)
            # The run broke off before it qualified as an interruption, so whatever was
            # captured for it must not be carried into a later utterance.
            self._barge_in_speech_ms = 0
            self._reset_utterance()
            return TurnTakingDecision(state=self._state, discarded=True)
        # The interrupting run is accumulated into the utterance from its first frame, so
        # the buyer's opening words survive the wait for the barge-in threshold.
        if self._barge_in_speech_ms == 0:
            self._start(frame)
        else:
            self._accumulate(frame)
        self._barge_in_speech_ms += frame.duration_ms
        if self._barge_in_speech_ms < self._config.barge_in_speech_ms:
            return TurnTakingDecision(state=self._state, capture=True)
        barge_in = BargeIn(
            at_sequence=frame.sequence,
            speech_ms=self._barge_in_speech_ms,
            detected_at=frame.captured_at,
        )
        self._barge_in_speech_ms = 0
        self._agent_speech_elapsed_ms = 0
        self._state = TurnTakingState.LISTENING
        return TurnTakingDecision(state=self._state, barge_in=barge_in, capture=True)

    def _observe_while_listening(self, frame: SpeechFrame) -> TurnTakingDecision:
        if self._state is TurnTakingState.IDLE:
            if not frame.is_speech:
                return TurnTakingDecision(state=self._state)
            self._state = TurnTakingState.LISTENING
            self._start(frame)
            return TurnTakingDecision(state=self._state, capture=True)

        self._accumulate(frame)
        if self._speech_ms + self._silence_ms >= self._config.max_utterance_ms:
            # A stream that never falls silent must still yield the floor.
            if self._speech_ms < self._config.min_speech_ms:
                self._reset_utterance()
                self._state = TurnTakingState.IDLE
                return TurnTakingDecision(state=self._state, discarded=True)
            return TurnTakingDecision(
                state=TurnTakingState.IDLE,
                segment=self._close(EndpointReason.MAX_DURATION),
                capture=True,
            )
        if self._trailing_silence_ms < self._config.end_silence_ms:
            return TurnTakingDecision(state=self._state, capture=True)
        if self._speech_ms < self._config.min_speech_ms:
            # Too short to be an utterance. Treat it as noise and keep the floor open.
            self._reset_utterance()
            self._state = TurnTakingState.IDLE
            return TurnTakingDecision(state=self._state, discarded=True)
        return TurnTakingDecision(
            state=TurnTakingState.IDLE,
            segment=self._close(EndpointReason.SILENCE),
            capture=True,
        )

    def _start(self, frame: SpeechFrame) -> None:
        self._started_at = frame.captured_at
        self._accumulate(frame)

    def _accumulate(self, frame: SpeechFrame) -> None:
        self._frame_count += 1
        self._byte_count += frame.byte_count
        self._last_at = frame.captured_at
        if frame.is_speech:
            self._speech_ms += frame.duration_ms
            self._trailing_silence_ms = 0
        else:
            self._silence_ms += frame.duration_ms
            self._trailing_silence_ms += frame.duration_ms

    def _close(self, reason: EndpointReason) -> SpeechSegment:
        started_at = self._started_at
        ended_at = self._last_at
        assert started_at is not None and ended_at is not None
        segment = SpeechSegment(
            frame_count=self._frame_count,
            speech_ms=self._speech_ms,
            silence_ms=self._silence_ms,
            byte_count=self._byte_count,
            reason=reason,
            started_at=started_at,
            ended_at=ended_at,
            trailing_silence_ms=self._trailing_silence_ms,
        )
        self._reset_utterance()
        self._state = TurnTakingState.IDLE
        return segment

    def _release_floor(self) -> None:
        self._state = TurnTakingState.IDLE
        self._barge_in_speech_ms = 0
        self._agent_speech_elapsed_ms = 0
        self._reset_utterance()

    def _reset_utterance(self) -> None:
        self._speech_ms = 0
        self._silence_ms = 0
        self._trailing_silence_ms = 0
        self._frame_count = 0
        self._byte_count = 0
        self._started_at = None
        self._last_at = None


__all__ = [
    "MAX_FRAME_DURATION_MS",
    "TurnTaking",
    "TurnTakingConfig",
    "TurnTakingDecision",
]
