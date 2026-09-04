from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    language_switched: bool = False
    """Whether this turn changed the language the conversation is being held in.

    ``language`` alone is not enough to act on: a caller has to re-point a voice and a
    transcriber, and doing that on every turn would reload models needlessly, while doing
    it never is the bug this exists to fix. This says which turn it was.
    """

    disposition: ConversationDisposition
    phase: ConversationPhase
    safety_signals: tuple[SafetySignal, ...] = Field(default=(), max_length=16)
    facts: tuple[RequirementFact, ...] = Field(default=(), max_length=10_000)
    revisions: tuple[RequirementRevision, ...] = Field(default=(), max_length=10_000)
    evidence: tuple[IntentEvidence, ...] = Field(default=(), max_length=10_000)
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


class ConversationStateCheckpoint(ConversationModel):
    checkpoint_schema_version: Literal["1", "2"]
    """``"2"`` adds the language-switching fields. Both are accepted for reading.

    A ``"1"`` checkpoint restores with the language fields at their defaults, which is
    exactly right: it was written by a build that could not switch language, so it had
    none to preserve. Writing ``"2"`` means an older build rejects it loudly on the
    version literal rather than silently dropping a language the buyer had asked for.
    """

    lead_id: UUID
    max_turns: int = Field(ge=1, le=10_000)
    max_facts: int = Field(ge=1, le=10_000)
    max_evidence: int = Field(ge=1, le=10_000)
    max_classifications: int = Field(ge=1, le=10_000)
    max_goal_changes: int = Field(ge=1, le=10_000)
    digest_key_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    phase: ConversationPhase
    turn_count: int = Field(ge=0)
    abuse_redirected: bool = False
    stopped: bool = False
    recent_turn_digests: tuple[str, ...] = Field(default=(), max_length=20)
    facts: tuple[RequirementFact, ...] = Field(default=(), max_length=10_000)
    evidence: tuple[IntentEvidence, ...] = Field(default=(), max_length=10_000)
    classifications: tuple[Classification, ...] = Field(default=(), max_length=10_000)
    goal_change_count: int = Field(default=0, ge=0)
    language: LanguageCode = LanguageCode.UNKNOWN
    declared_language: LanguageCode = LanguageCode.UNKNOWN
    pending_language: LanguageCode | None = None
    pending_language_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_checkpoint(self) -> ConversationStateCheckpoint:
        if self.turn_count > self.max_turns:
            raise ValueError("checkpoint turn count exceeds capacity")
        if len(self.recent_turn_digests) > min(self.max_turns, 20):
            raise ValueError("checkpoint recent-turn history exceeds capacity")
        if any(
            len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest)
            for digest in self.recent_turn_digests
        ):
            raise ValueError("checkpoint recent turns must be SHA-256 digests")
        if len(self.facts) > self.max_facts:
            raise ValueError("checkpoint facts exceed capacity")
        if len(self.evidence) > self.max_evidence:
            raise ValueError("checkpoint evidence exceeds capacity")
        if len(self.classifications) > self.max_classifications:
            raise ValueError("checkpoint classifications exceed capacity")
        if len({fact.key for fact in self.facts}) != len(self.facts):
            raise ValueError("checkpoint fact keys must be unique")
        nested_lead_ids = {
            *(fact.lead_id for fact in self.facts),
            *(item.lead_id for item in self.evidence),
            *(item.lead_id for item in self.classifications),
        }
        if nested_lead_ids - {self.lead_id}:
            raise ValueError("checkpoint records must belong to its lead")
        if self.stopped is not (self.phase is ConversationPhase.CLOSED):
            raise ValueError("checkpoint closed phase and stopped state must agree")
        if (self.pending_language is None) is not (self.pending_language_count == 0):
            # A count without a candidate would restore hysteresis that can never
            # complete; a candidate without a count would restore one that switches on
            # the next turn regardless of what is said. Both are silent, so both are
            # rejected here rather than discovered mid-conversation.
            raise ValueError("checkpoint pending language and its count must agree")
        return self
