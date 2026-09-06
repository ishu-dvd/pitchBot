from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from pitchbot.domain import ActionType, ContactPolicy, LanguageCode, LeadTemperature


class ActionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AuthorizationStatus(StrEnum):
    APPROVED = "approved"
    BLOCKED = "blocked"


class CallbackAgenda(StrEnum):
    WEBSITE_DISCOVERY = "website-discovery"
    REQUIREMENTS_REVIEW = "requirements-review"
    PROPOSAL_REVIEW = "proposal-review"


class BlockReason(StrEnum):
    DISCLOSURE_MISSING = "disclosure-missing"
    CONSENT_MISSING = "consent-missing"
    OUTREACH_NOT_ALLOWED = "outreach-not-allowed"
    NOT_ALLOWLISTED = "not-allowlisted"
    DND_NOT_PASSED = "dnd-not-passed"
    CALLING_HOURS_NOT_PASSED = "calling-hours-not-passed"
    OPTED_OUT = "opted-out"
    CONVERSATION_NOT_ELIGIBLE = "conversation-not-eligible"
    CLASSIFICATION_REVIEW = "classification-review"
    CLASSIFICATION_INELIGIBLE = "classification-ineligible"
    QUOTA_EXCEEDED = "quota-exceeded"
    CALLBACK_TIME_INVALID = "callback-time-invalid"
    POLICY_CHANGED = "policy-changed"


class ActionAuthorizationContext(ActionModel):
    disclosure_delivered: bool = False
    consent_granted: bool = False
    contact_policy: ContactPolicy = Field(default_factory=ContactPolicy)
    temperature: LeadTemperature = LeadTemperature.REVIEW_NEEDED
    conversation_disposition: str = Field(min_length=1, max_length=50)
    used_actions: int = Field(default=0, ge=0)
    max_actions: int = Field(default=3, ge=1, le=20)


class ActionDecision(ActionModel):
    status: AuthorizationStatus
    action_type: ActionType
    reasons: tuple[BlockReason, ...] = ()
    decided_at: AwareDatetime


class FollowUpSummary(ActionModel):
    lead_id: UUID
    language: LanguageCode
    business_type: str | None = Field(default=None, max_length=50)
    requested_features: tuple[str, ...] = Field(default=(), max_length=10)
    budget_summary: str | None = Field(default=None, max_length=100)
    timeline_summary: str | None = Field(default=None, max_length=100)
    next_steps: tuple[str, ...] = Field(default=(), max_length=5)


class CallbackRequest(ActionModel):
    lead_id: UUID
    callback_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    run_at: AwareDatetime
    timezone: str = Field(min_length=1, max_length=100)
    agenda: CallbackAgenda = CallbackAgenda.WEBSITE_DISCOVERY
    idempotency_key: str = Field(min_length=1, max_length=200)


class CallbackStatus(StrEnum):
    SCHEDULED = "scheduled"
    CANCELLATION_PENDING = "cancellation-pending"
    CANCELLATION_REQUIRED = "cancellation-required"
    CANCELED = "canceled"
    DISPATCHED = "dispatched"
    BLOCKED = "blocked"


class CallbackRecord(ActionModel):
    request: CallbackRequest
    status: CallbackStatus
    provider_reference: str | None = Field(default=None, max_length=300)
    block_reasons: tuple[BlockReason, ...] = ()
    updated_at: AwareDatetime


class DeckIndustry(StrEnum):
    APPAREL = "apparel"
    TOYS = "toys"
    BOOKS = "books"
    FOOD = "food"
    IMPORT_EXPORT = "import-export"
    PLASTICS = "plastics"


class DeckRequest(ActionModel):
    lead_id: UUID
    deck_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    industry: DeckIndustry
    language: LanguageCode
    requested_features: tuple[str, ...] = Field(default=(), max_length=10)
    # What the buyer actually said, after `build_follow_up` has minimised it - an
    # allowlisted business type and pattern-matched budget and timing. Bounded here as
    # well as there so a caller assembling a request by hand cannot widen what a deck may
    # carry. Until PR 54 a deck received none of this and every buyer got the same slides.
    budget_summary: str | None = Field(default=None, max_length=100)
    timeline_summary: str | None = Field(default=None, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def reject_unknown_language(self) -> DeckRequest:
        if self.language is LanguageCode.UNKNOWN:
            raise ValueError("Deck language must be explicit")
        return self


class DeckSlide(ActionModel):
    title: str = Field(min_length=1, max_length=100)
    bullets: tuple[str, ...] = Field(min_length=1, max_length=6)


class DeckPreview(ActionModel):
    deck_id: str
    industry: DeckIndustry
    language: LanguageCode
    title: str = Field(min_length=1, max_length=150)
    slides: tuple[DeckSlide, ...] = Field(min_length=1, max_length=10)
    format: str = "structured-preview-v1"
    generated_at: AwareDatetime


class ActionPreviewResult(ActionModel):
    decision: ActionDecision
    label: str = Field(min_length=1, max_length=300)
    executed: bool = False
    provider_reference: str | None = Field(default=None, max_length=300)
    callback: CallbackRecord | None = None
    deck: DeckPreview | None = None
