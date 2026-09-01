from __future__ import annotations

from collections.abc import Callable
from time import monotonic_ns
from uuid import UUID

from pitchbot.knowledge.graph import TemporalKnowledgeGraphBuilder
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
    RetrievalScope,
    validate_bm25_request,
)


class LeadKnowledgeBm25Retriever:
    def __init__(
        self,
        graph_builder: TemporalKnowledgeGraphBuilder,
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
        started = self._clock()
        graph = self._graph_builder.build(lead_id)
        claims_by_id = {
            claim.fact.fact_id: claim
            for claim in graph.claims
            if claim.status is not FactClaimStatus.SUPERSEDED
        }
        index = Bm25Index(
            (self._document(claim) for claim in claims_by_id.values()),
            scope=RetrievalScope.LEAD,
        )
        elapsed_ns = max(0, self._clock() - started)
        remaining_ms = deadline_ms - elapsed_ns // 1_000_000
        if remaining_ms < 1:
            self._graph_builder.validate(graph)
            return self._timeout_response(graph, started)
        ranked, _, timed_out = index.search(
            query,
            top_k=top_k,
            deadline_ms=int(remaining_ms),
            clock=self._clock,
        )
        if timed_out:
            self._graph_builder.validate(graph)
            return self._timeout_response(graph, started)
        self._graph_builder.validate(graph)
        if self._clock() - started >= deadline_ms * 1_000_000:
            return self._timeout_response(graph, started)
        return LeadKnowledgeRetrievalResponse(
            lead_id=lead_id,
            aggregate_version=graph.aggregate_version,
            duration_ms=max(0.0, (self._clock() - started) / 1_000_000),
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

    def _timeout_response(
        self,
        graph: LeadKnowledgeGraph,
        started: int,
    ) -> LeadKnowledgeRetrievalResponse:
        return LeadKnowledgeRetrievalResponse(
            lead_id=graph.lead_id,
            aggregate_version=graph.aggregate_version,
            duration_ms=max(0.0, (self._clock() - started) / 1_000_000),
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
