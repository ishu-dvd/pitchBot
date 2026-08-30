from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from pitchbot.domain import LanguageCode


class SimulatorModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PreviewAction(StrEnum):
    NONE = "none"
    WHATSAPP = "whatsapp-preview"
    CALLBACK = "callback-preview"
    ARTIFACT = "artifact-preview"


class SimulatorEventType(StrEnum):
    DISCLOSURE = "disclosure"
    BUYER_TURN = "buyer-turn"
    ASSISTANT_TURN = "assistant-turn"
    ACTION_PREVIEW = "action-preview"
    INTERRUPTION = "interruption"
    AUDIO_METADATA = "audio-metadata"
    FAILURE = "failure"


class CreateSessionRequest(SimulatorModel):
    lead_ref: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    language: LanguageCode = LanguageCode.MIXED


class TurnRequest(SimulatorModel):
    text: str = Field(min_length=1, max_length=4_000)
    language: LanguageCode
    preview_action: PreviewAction = PreviewAction.NONE
    simulated_latency_ms: int = Field(default=0, ge=0, le=3_000)
    inject_failure: bool = False


class SimulatorEvent(SimulatorModel):
    sequence: int = Field(ge=1)
    event_type: SimulatorEventType
    text: str | None = None
    language: LanguageCode = LanguageCode.UNKNOWN
    occurred_at: AwareDatetime
    metadata: dict[str, str | int | bool] = Field(default_factory=dict)


class SessionResponse(SimulatorModel):
    session_id: UUID
    lead_ref: str
    language: LanguageCode
    events: list[SimulatorEvent]


class TurnResponse(SimulatorModel):
    session_id: UUID
    reply: str
    preview: dict[str, str] | None
    events: list[SimulatorEvent]


class LeadHistoryResponse(SimulatorModel):
    lead_ref: str
    events: list[SimulatorEvent]


class AudioMetadata(SimulatorModel):
    byte_count: int = Field(ge=0, le=262_144)
    media_type: str = Field(min_length=1, max_length=100)
    captured_at: AwareDatetime
