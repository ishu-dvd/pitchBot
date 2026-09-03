from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


def utc_now() -> datetime:
    return datetime.now(UTC)


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LanguageCode(StrEnum):
    ENGLISH = "en"
    HINDI = "hi"
    TELUGU = "te"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class LeadTemperature(StrEnum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"
    REVIEW_NEEDED = "review-needed"


class SessionStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"


class PrivacyOperationType(StrEnum):
    ANONYMIZED = "anonymized"
    HARD_DELETED = "hard-deleted"


class ActionType(StrEnum):
    WHATSAPP_PREVIEW = "whatsapp-preview"
    CALLBACK_SCHEDULE = "callback-schedule"
    ARTIFACT_PREVIEW = "artifact-preview"
    END_CONTACT = "end-contact"


class ActionStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    BLOCKED = "blocked"
    EXECUTED = "executed"
    FAILED = "failed"


class ContactPolicy(DomainModel):
    outreach_allowed: bool = False
    allowlisted: bool = False
    dnd_check_passed: bool = False
    calling_hours_check_passed: bool = False
    opted_out: bool = False


class Lead(DomainModel):
    lead_id: UUID = Field(default_factory=uuid4)
    display_name: str = Field(min_length=1, max_length=200)
    language_preference: LanguageCode = LanguageCode.UNKNOWN
    contact_policy: ContactPolicy = Field(default_factory=ContactPolicy)
    created_at: AwareDatetime = Field(default_factory=utc_now)


class CallSession(DomainModel):
    session_id: UUID = Field(default_factory=uuid4)
    lead_id: UUID
    status: SessionStatus = SessionStatus.PLANNED
    language: LanguageCode = LanguageCode.UNKNOWN
    started_at: AwareDatetime | None = None
    ended_at: AwareDatetime | None = None


class Turn(DomainModel):
    turn_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    speaker: str = Field(min_length=1, max_length=50)
    started_at: AwareDatetime = Field(default_factory=utc_now)
    ended_at: AwareDatetime | None = None


class TranscriptSpan(DomainModel):
    span_id: UUID = Field(default_factory=uuid4)
    turn_id: UUID
    text: str = Field(min_length=1)
    language: LanguageCode = LanguageCode.UNKNOWN
    confidence: float = Field(ge=0.0, le=1.0)
    captured_at: AwareDatetime = Field(default_factory=utc_now)


class RequirementFact(DomainModel):
    fact_id: UUID = Field(default_factory=uuid4)
    lead_id: UUID
    key: str = Field(min_length=1, max_length=100)
    value: JsonValue
    source_span_ids: tuple[UUID, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0)
    captured_at: AwareDatetime = Field(default_factory=utc_now)


class RequirementRevision(DomainModel):
    revision_id: UUID = Field(default_factory=uuid4)
    lead_id: UUID
    key: str = Field(min_length=1, max_length=100)
    previous_fact_id: UUID | None = None
    replacement_fact_id: UUID
    confirmed_by_customer: bool = False
    reason: str = Field(min_length=1, max_length=500)
    revised_at: AwareDatetime = Field(default_factory=utc_now)


class IntentEvidence(DomainModel):
    evidence_id: UUID = Field(default_factory=uuid4)
    lead_id: UUID
    dimension: str = Field(min_length=1, max_length=100)
    weight: float = Field(ge=-1.0, le=1.0)
    reason: str = Field(min_length=1, max_length=500)
    source_span_ids: tuple[UUID, ...] = ()
    captured_at: AwareDatetime = Field(default_factory=utc_now)


class Classification(DomainModel):
    classification_id: UUID = Field(default_factory=uuid4)
    lead_id: UUID
    temperature: LeadTemperature
    score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: tuple[UUID, ...] = ()
    counter_evidence_ids: tuple[UUID, ...] = ()
    rule_version: str = Field(min_length=1, max_length=100)
    model_version: str | None = Field(default=None, max_length=200)
    classified_at: AwareDatetime = Field(default_factory=utc_now)


class FollowUp(DomainModel):
    follow_up_id: UUID = Field(default_factory=uuid4)
    lead_id: UUID
    summary: str = Field(min_length=1)
    open_questions: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    created_at: AwareDatetime = Field(default_factory=utc_now)


class Schedule(DomainModel):
    schedule_id: UUID = Field(default_factory=uuid4)
    lead_id: UUID
    scheduled_for: AwareDatetime
    timezone: str = Field(min_length=1, max_length=100)
    agenda: str = Field(min_length=1, max_length=1000)
    status: str = Field(default="pending", min_length=1, max_length=50)


class ActionProposal(DomainModel):
    proposal_id: UUID = Field(default_factory=uuid4)
    lead_id: UUID
    action_type: ActionType
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=1000)
    status: ActionStatus = ActionStatus.PROPOSED
    proposed_at: AwareDatetime = Field(default_factory=utc_now)


class ActionExecution(DomainModel):
    execution_id: UUID = Field(default_factory=uuid4)
    proposal_id: UUID
    status: ActionStatus
    idempotency_key: str = Field(min_length=1, max_length=200)
    detail: str = Field(max_length=1000)
    executed_at: AwareDatetime = Field(default_factory=utc_now)


class Consent(DomainModel):
    consent_id: UUID = Field(default_factory=uuid4)
    lead_id: UUID
    purpose: str = Field(min_length=1, max_length=200)
    granted: bool
    source: str = Field(min_length=1, max_length=100)
    recorded_at: AwareDatetime = Field(default_factory=utc_now)


class PrivacyOperation(DomainModel):
    operation_id: UUID = Field(default_factory=uuid4)
    aggregate_id: UUID
    operation: PrivacyOperationType
    affected_event_count: int = Field(ge=0)
    occurred_at: AwareDatetime = Field(default_factory=utc_now)


class OptOut(DomainModel):
    opt_out_id: UUID = Field(default_factory=uuid4)
    lead_id: UUID
    channel: str = Field(min_length=1, max_length=50)
    reason: str = Field(min_length=1, max_length=500)
    recorded_at: AwareDatetime = Field(default_factory=utc_now)


class Artifact(DomainModel):
    artifact_id: UUID = Field(default_factory=uuid4)
    lead_id: UUID
    artifact_type: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    location: str = Field(min_length=1, max_length=1000)
    source_fact_ids: tuple[UUID, ...] = ()
    created_at: AwareDatetime = Field(default_factory=utc_now)


class Citation(DomainModel):
    citation_id: UUID = Field(default_factory=uuid4)
    source_name: str = Field(min_length=1, max_length=300)
    source_url: HttpUrl | None = None
    note: str = Field(min_length=1, max_length=1000)
    accessed_at: AwareDatetime = Field(default_factory=utc_now)


class ConversationStrategy(DomainModel):
    strategy_id: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=50)
    supported_languages: tuple[LanguageCode, ...]
    eligible_contexts: tuple[str, ...] = ()
    opening_template: str = Field(min_length=1)
    continuation_rules: tuple[str, ...] = ()
    abandonment_rules: tuple[str, ...] = ()
    prohibited_inferences: tuple[str, ...] = ()
    risk_level: str = Field(min_length=1, max_length=50)
    citation_ids: tuple[UUID, ...] = ()


class StrategyExperiment(DomainModel):
    experiment_id: UUID = Field(default_factory=uuid4)
    strategy_id: str = Field(min_length=1, max_length=100)
    scenario_id: str = Field(min_length=1, max_length=100)
    metric_name: str = Field(min_length=1, max_length=100)
    metric_value: float
    evaluated_at: AwareDatetime = Field(default_factory=utc_now)


class AuditEvent(DomainModel):
    event_id: UUID = Field(default_factory=uuid4)
    aggregate_id: UUID
    aggregate_type: str = Field(min_length=1, max_length=100)
    aggregate_version: int = Field(ge=1)
    event_type: str = Field(min_length=1, max_length=150)
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    occurred_at: AwareDatetime = Field(default_factory=utc_now)
