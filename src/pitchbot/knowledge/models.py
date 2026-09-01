from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from pitchbot.domain import LanguageCode, RequirementFact


class KnowledgeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FactClaimStatus(StrEnum):
    CURRENT = "current"
    SUPERSEDED = "superseded"
    CONFLICTING = "conflicting"


class KnowledgeNodeType(StrEnum):
    LEAD = "lead"
    SESSION = "session"
    FACT = "fact"


class KnowledgeRelationType(StrEnum):
    HAS_SESSION = "has-session"
    OBSERVED_FACT = "observed-fact"
    SUPERSEDED_BY = "superseded-by"


class TemporalFactClaim(KnowledgeModel):
    fact: RequirementFact
    status: FactClaimStatus
    session_id: UUID
    language: LanguageCode
    valid_from_version: int = Field(ge=1)
    valid_from: AwareDatetime
    valid_to_version: int | None = Field(default=None, ge=1)
    valid_to: AwareDatetime | None = None
    superseded_by_fact_id: UUID | None = None
    confirmed_by_customer: bool = False

    @model_validator(mode="after")
    def validate_interval(self) -> TemporalFactClaim:
        superseded = self.status is FactClaimStatus.SUPERSEDED
        closure_fields = (
            self.valid_to_version,
            self.valid_to,
            self.superseded_by_fact_id,
        )
        if superseded is not all(value is not None for value in closure_fields):
            raise ValueError("superseded claims require a complete validity boundary")
        if not superseded and any(value is not None for value in closure_fields):
            raise ValueError("active claims cannot have a validity boundary")
        if self.valid_to_version is not None and self.valid_to_version <= self.valid_from_version:
            raise ValueError("fact validity versions must increase")
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("fact validity time cannot decrease")
        return self


class KnowledgeRelation(KnowledgeModel):
    source_type: KnowledgeNodeType
    source_id: UUID
    relation: KnowledgeRelationType
    target_type: KnowledgeNodeType
    target_id: UUID


class LeadKnowledgeGraph(KnowledgeModel):
    lead_id: UUID
    aggregate_version: int = Field(ge=1)
    session_ids: tuple[UUID, ...] = Field(max_length=1_000)
    claims: tuple[TemporalFactClaim, ...] = Field(max_length=10_000)
    relations: tuple[KnowledgeRelation, ...] = Field(max_length=30_000)

    @model_validator(mode="after")
    def validate_graph(self) -> LeadKnowledgeGraph:
        if len(self.session_ids) != len(set(self.session_ids)):
            raise ValueError("knowledge graph session identifiers must be unique")
        fact_ids = [claim.fact.fact_id for claim in self.claims]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("knowledge graph fact identifiers must be unique")
        if any(claim.fact.lead_id != self.lead_id for claim in self.claims):
            raise ValueError("knowledge graph claims must belong to its lead")
        relation_keys = [
            (
                item.source_type,
                item.source_id,
                item.relation,
                item.target_type,
                item.target_id,
            )
            for item in self.relations
        ]
        if len(relation_keys) != len(set(relation_keys)):
            raise ValueError("knowledge graph relations must be unique")
        claims_by_id = {claim.fact.fact_id: claim for claim in self.claims}
        expected_relations = {
            (
                KnowledgeNodeType.LEAD,
                self.lead_id,
                KnowledgeRelationType.HAS_SESSION,
                KnowledgeNodeType.SESSION,
                session_id,
            )
            for session_id in self.session_ids
        }
        expected_relations.update(
            (
                KnowledgeNodeType.SESSION,
                claim.session_id,
                KnowledgeRelationType.OBSERVED_FACT,
                KnowledgeNodeType.FACT,
                claim.fact.fact_id,
            )
            for claim in self.claims
        )
        expected_relations.update(
            (
                KnowledgeNodeType.FACT,
                claim.fact.fact_id,
                KnowledgeRelationType.SUPERSEDED_BY,
                KnowledgeNodeType.FACT,
                claim.superseded_by_fact_id,
            )
            for claim in self.claims
            if claim.superseded_by_fact_id is not None
        )
        if set(relation_keys) != expected_relations:
            raise ValueError("knowledge graph relations do not match its nodes")
        if any(claim.session_id not in self.session_ids for claim in self.claims):
            raise ValueError("knowledge graph claim session is missing")
        if any(
            claim.superseded_by_fact_id not in claims_by_id
            for claim in self.claims
            if claim.superseded_by_fact_id is not None
        ):
            raise ValueError("knowledge graph supersession target is missing")
        return self


class RankedKnowledgeClaim(KnowledgeModel):
    rank: int = Field(ge=1, le=20)
    score: float = Field(gt=0)
    matched_terms: tuple[str, ...] = Field(min_length=1, max_length=64)
    claim: TemporalFactClaim


class LeadKnowledgeRetrievalResponse(KnowledgeModel):
    lead_id: UUID
    aggregate_version: int = Field(ge=1)
    duration_ms: float = Field(ge=0)
    indexed_claim_count: int = Field(ge=0, le=1_000)
    timed_out: bool
    results: tuple[RankedKnowledgeClaim, ...] = Field(max_length=20)
