from __future__ import annotations

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from pitchbot.actions import ActionPreviewResult, DeckIndustry
from pitchbot.conversation import (
    ConversationDisposition,
    ConversationPhase,
    SafetySignal,
)
from pitchbot.domain import ContactPolicy, JsonValue, LanguageCode, LeadTemperature
from pitchbot.knowledge import FactClaimStatus


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


class ResumeSessionRequest(SimulatorModel):
    lead_ref: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class RecalledClaim(SimulatorModel):
    """A prior claim about this lead, surfaced for context only.

    Recall is never authoritative: it cannot change the reply, the extracted facts,
    or the classification. Fact and span identifiers are deliberately omitted so the
    browser never receives journal provenance handles.
    """

    rank: int = Field(ge=1)
    key: str = Field(min_length=1, max_length=100)
    value: JsonValue
    status: FactClaimStatus
    language: LanguageCode
    session_id: UUID
    observed_at: AwareDatetime
    confirmed_by_customer: bool
    from_current_session: bool


class TurnRecall(SimulatorModel):
    """Best-effort recall of what this lead said before, under its own budget."""

    aggregate_version: int = Field(ge=1)
    duration_ms: float = Field(ge=0)
    indexed_claim_count: int = Field(ge=0)
    timed_out: bool
    claims: list[RecalledClaim] = Field(default_factory=list)


class TurnResponse(SimulatorModel):
    session_id: UUID
    reply: str
    preview: ActionPreviewResult | None
    disposition: ConversationDisposition
    phase: ConversationPhase
    temperature: str
    safety_signals: list[SafetySignal] = Field(default_factory=list)
    repeated_turn: bool = False
    # ``None`` means recall was not attempted for this turn (safety signal, closed
    # conversation, durable replay, or no durable journal), not that nothing matched.
    recall: TurnRecall | None = None
    events: list[SimulatorEvent]


class LeadHistoryResponse(SimulatorModel):
    lead_ref: str
    events: list[SimulatorEvent]


class DurableRequirementFact(SimulatorModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    key: str
    value: JsonValue
    confidence: float
    captured_at: AwareDatetime


class DurableRequirementRevision(SimulatorModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    key: str
    confirmed_by_customer: bool
    reason: str
    revised_at: AwareDatetime


class DurableIntentEvidence(SimulatorModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    dimension: str
    weight: float
    reason: str
    captured_at: AwareDatetime


class DurableClassification(SimulatorModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    temperature: LeadTemperature
    score: float
    confidence: float
    rule_version: str
    model_version: str | None
    classified_at: AwareDatetime


class DurableConversationResult(SimulatorModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    reply: str
    language: LanguageCode
    disposition: ConversationDisposition
    phase: ConversationPhase
    safety_signals: tuple[SafetySignal, ...]
    facts: tuple[DurableRequirementFact, ...]
    revisions: tuple[DurableRequirementRevision, ...]
    evidence: tuple[DurableIntentEvidence, ...]
    classification: DurableClassification
    repeated_turn: bool
    turn_count: int


class DurableHistoryTurn(SimulatorModel):
    aggregate_version: int = Field(ge=1)
    occurred_at: AwareDatetime
    result: DurableConversationResult


class DurableHistoryResponse(SimulatorModel):
    session_id: UUID
    turns: list[DurableHistoryTurn]


class AudioMetadata(SimulatorModel):
    byte_count: int = Field(ge=0, le=262_144)
    media_type: str = Field(min_length=1, max_length=100)
    captured_at: AwareDatetime
