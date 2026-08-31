from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pitchbot.domain import (
    Classification,
    IntentEvidence,
    LanguageCode,
    RequirementFact,
    RequirementRevision,
)


class ConversationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConversationDisposition(StrEnum):
    CONTINUE = "continue"
    REDIRECT = "redirect"
    STOP = "stop"
    REVIEW = "review"


class SafetySignal(StrEnum):
    OPT_OUT = "opt-out"
    ABUSE = "abuse"
    INTERNAL_INFO = "internal-info"
    PROMPT_INJECTION = "prompt-injection"
    EXCESSIVE_GOAL_CHANGES = "excessive-goal-changes"


class ConversationPhase(StrEnum):
    DISCOVERY = "discovery"
    QUALIFICATION = "qualification"
    NEXT_STEP = "next-step"
    CLOSED = "closed"


class ConversationResult(ConversationModel):
    reply: str = Field(min_length=1, max_length=1_000)
    language: LanguageCode
    disposition: ConversationDisposition
    phase: ConversationPhase
    safety_signals: tuple[SafetySignal, ...] = ()
    facts: tuple[RequirementFact, ...] = ()
    revisions: tuple[RequirementRevision, ...] = ()
    evidence: tuple[IntentEvidence, ...] = ()
    classification: Classification
    repeated_turn: bool = False
    turn_count: int = Field(ge=1)


class ConversationSnapshot(ConversationModel):
    lead_id: UUID
    phase: ConversationPhase
    turn_count: int = Field(ge=0)
    abuse_redirected: bool = False
    stopped: bool = False
    facts: tuple[RequirementFact, ...] = ()
    evidence: tuple[IntentEvidence, ...] = ()
    classifications: tuple[Classification, ...] = ()
