from __future__ import annotations

from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from pitchbot.domain import JsonValue, LanguageCode


class RetrievalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FactProvenance(RetrievalModel):
    lead_id: UUID
    session_id: UUID
    aggregate_version: int = Field(ge=1)
    fact_id: UUID
    source_span_ids: tuple[UUID, ...]
    occurred_at: AwareDatetime


class LexicalDocument(RetrievalModel):
    key: str = Field(min_length=1, max_length=100)
    value: JsonValue
    language: LanguageCode
    provenance: FactProvenance


class RankedFact(RetrievalModel):
    rank: int = Field(ge=1, le=20)
    score: float = Field(gt=0)
    matched_terms: tuple[str, ...] = Field(min_length=1, max_length=64)
    document: LexicalDocument


class RetrievalResponse(RetrievalModel):
    lead_id: UUID
    session_id: UUID
    aggregate_version: int = Field(ge=1)
    duration_ms: float = Field(ge=0)
    indexed_document_count: int = Field(ge=0, le=1_000)
    timed_out: bool
    results: tuple[RankedFact, ...] = Field(max_length=20)
