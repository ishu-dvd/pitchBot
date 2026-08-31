from __future__ import annotations

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from pitchbot.actions import ActionPreviewResult, DeckIndustry
from pitchbot.conversation import ConversationDisposition, ConversationPhase, SafetySignal
from pitchbot.domain import ContactPolicy, LanguageCode


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
    CONVERSATION_OUTCOME = "conversation-outcome"


class CreateSessionRequest(SimulatorModel):
    lead_ref: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    language: LanguageCode = LanguageCode.MIXED
    preview_consent_granted: bool = False
    contact_policy: ContactPolicy = Field(default_factory=ContactPolicy)


class TurnRequest(SimulatorModel):
    operation_id: UUID = Field(default_factory=uuid4)
    text: str = Field(min_length=1, max_length=4_000)
    language: LanguageCode
    preview_action: PreviewAction = PreviewAction.NONE
    callback_delay_minutes: int = Field(default=5, ge=1, le=10_080)
    deck_industry: DeckIndustry = DeckIndustry.APPAREL
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
    preview: ActionPreviewResult | None
    disposition: ConversationDisposition
    phase: ConversationPhase
    temperature: str
    safety_signals: list[SafetySignal] = Field(default_factory=list)
    repeated_turn: bool = False
    events: list[SimulatorEvent]


class LeadHistoryResponse(SimulatorModel):
    lead_ref: str
    events: list[SimulatorEvent]


class AudioMetadata(SimulatorModel):
    byte_count: int = Field(ge=0, le=262_144)
    media_type: str = Field(min_length=1, max_length=100)
    captured_at: AwareDatetime
