from __future__ import annotations

from collections.abc import Callable
from time import monotonic_ns
from typing import Protocol
from uuid import UUID

from pitchbot.knowledge.graph import KnowledgeGraphDeadlineExceededError
from pitchbot.knowledge.models import (
    FactClaimStatus,
    LeadKnowledgeGraph,
    LeadKnowledgeRetrievalResponse,
    RankedKnowledgeClaim,
    TemporalFactClaim,
)
from pitchbot.retrieval import (
    MAX_DEADLINE_MS,
    Bm25Index,
    FactProvenance,
    LexicalDocument,
    RetrievalDeadline,
    RetrievalDeadlineExceededError,
    RetrievalScope,
    validate_bm25_request,
)


class LeadKnowledgeGraphSource(Protocol):
    def build(
        self,
        lead_id: UUID,
        *,
        deadline: RetrievalDeadline | None = None,
    ) -> LeadKnowledgeGraph: ...

    def validate(self, graph: LeadKnowledgeGraph) -> None: ...

    def validate_version(self, lead_id: UUID, aggregate_version: int) -> None: ...


class LeadKnowledgeBm25Retriever:
    def __init__(
        self,
        graph_builder: LeadKnowledgeGraphSource,
        *,
        clock: Callable[[], int] = monotonic_ns,
    ) -> None:
        self._graph_builder = graph_builder
        self._clock = clock

    def search(
        self,
        lead_id: UUID,
        query: str,
        *,
        top_k: int = 5,
        deadline_ms: int = MAX_DEADLINE_MS,
    ) -> LeadKnowledgeRetrievalResponse:
        validate_bm25_request(query, top_k, deadline_ms)
        budget = RetrievalDeadline.start(deadline_ms, clock=self._clock)
        try:
            graph = self._graph_builder.build(lead_id, deadline=budget)
        except KnowledgeGraphDeadlineExceededError as exceeded:
            self._graph_builder.validate_version(lead_id, exceeded.aggregate_version)
            return self._timeout_response(lead_id, exceeded.aggregate_version, budget)
        claims_by_id = {
            claim.fact.fact_id: claim
            for claim in graph.claims
            if claim.status is not FactClaimStatus.SUPERSEDED
        }
        try:
            index = Bm25Index(
                (self._document(claim) for claim in claims_by_id.values()),
                scope=RetrievalScope.LEAD,
                deadline=budget,
            )
        except RetrievalDeadlineExceededError:
            self._graph_builder.validate(graph)
            return self._timeout_response(graph.lead_id, graph.aggregate_version, budget)
        ranked, _, timed_out = index.search(
            query,
            top_k=top_k,
            deadline_ms=deadline_ms,
            deadline=budget,
        )
        self._graph_builder.validate(graph)
        if timed_out or budget.expired():
            return self._timeout_response(graph.lead_id, graph.aggregate_version, budget)
        return LeadKnowledgeRetrievalResponse(
            lead_id=lead_id,
            aggregate_version=graph.aggregate_version,
            duration_ms=budget.elapsed_ms(),
            indexed_claim_count=index.document_count,
            timed_out=False,
            results=tuple(
                RankedKnowledgeClaim(
                    rank=item.rank,
                    score=item.score,
                    matched_terms=item.matched_terms,
                    claim=claims_by_id[item.document.provenance.fact_id],
                )
                for item in ranked
            ),
        )

    @staticmethod
    def _timeout_response(
        lead_id: UUID,
        aggregate_version: int,
        budget: RetrievalDeadline,
    ) -> LeadKnowledgeRetrievalResponse:
        return LeadKnowledgeRetrievalResponse(
            lead_id=lead_id,
            aggregate_version=aggregate_version,
            duration_ms=budget.elapsed_ms(),
            indexed_claim_count=0,
            timed_out=True,
            results=(),
        )

    @staticmethod
    def _document(claim: TemporalFactClaim) -> LexicalDocument:
        return LexicalDocument(
            key=claim.fact.key,
            value=claim.fact.value,
            language=claim.language,
            provenance=FactProvenance(
                lead_id=claim.fact.lead_id,
                session_id=claim.session_id,
                aggregate_version=claim.valid_from_version,
                fact_id=claim.fact.fact_id,
                source_span_ids=claim.fact.source_span_ids,
                occurred_at=claim.valid_from,
            ),
        )
