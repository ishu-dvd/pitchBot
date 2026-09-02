from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

MAX_FRAME_DURATION_MS = 2_000
MAX_UTTERANCE_MS = 60_000
MAX_SILENCE_MS = 5_000
MAX_AGENT_FLOOR_MS = 120_000


class TurnTakingState(StrEnum):
    """Who currently holds the floor."""

    IDLE = "idle"
    LISTENING = "listening"
    AGENT_SPEAKING = "agent-speaking"


class EndpointReason(StrEnum):
    """Why an utterance was closed."""

    SILENCE = "silence"
    MAX_DURATION = "max-duration"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class SpeechFrame:
    """A frame that has already been classified and whose audio has been released.

    Only the byte count is retained. The frame payload is never stored, so a frame can
    describe what was heard without keeping any of it.
    """

    sequence: int
    byte_count: int
    duration_ms: int
    is_speech: bool
    captured_at: datetime

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("sequence must not be negative")
        if self.byte_count < 0:
            raise ValueError("byte_count must not be negative")
        if not 1 <= self.duration_ms <= MAX_FRAME_DURATION_MS:
            raise ValueError(f"duration_ms must be between 1 and {MAX_FRAME_DURATION_MS}")
        if self.captured_at.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class SpeechSegment:
    """A completed buyer utterance, described only by counts and durations."""

    frame_count: int
    speech_ms: int
    silence_ms: int
    byte_count: int
    reason: EndpointReason
    started_at: datetime
    ended_at: datetime

    @property
    def duration_ms(self) -> int:
        return self.speech_ms + self.silence_ms


@dataclass(frozen=True, slots=True)
class BargeIn:
    """The buyer started speaking while the agent held the floor."""

    at_sequence: int
    speech_ms: int
    detected_at: datetime
