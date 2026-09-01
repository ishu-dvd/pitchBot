from __future__ import annotations

import json
from collections import defaultdict
from uuid import UUID

from pitchbot.conversation import (
    ConversationJournal,
    JournaledConversationFact,
    JournaledConversationRevision,
    JournalHistoryUnavailableError,
    LeadKnowledgeSourceSnapshot,
)
from pitchbot.knowledge.models import (
    FactClaimStatus,
    KnowledgeNodeType,
    KnowledgeRelation,
    KnowledgeRelationType,
    LeadKnowledgeGraph,
    TemporalFactClaim,
)
from pitchbot.retrieval import RetrievalDeadline, RetrievalDeadlineExceededError


class KnowledgeGraphDeadlineExceededError(RetrievalDeadlineExceededError):
    """Graph construction stopped on an exhausted budget after reading its source."""

    def __init__(self, lead_id: UUID, aggregate_version: int) -> None:
        super().__init__("knowledge graph construction deadline exceeded")
        self.lead_id = lead_id
        self.aggregate_version = aggregate_version


class TemporalKnowledgeGraphBuilder:
    def __init__(
        self,
        journal: ConversationJournal,
        *,
        max_sessions: int = 1_000,
        max_claims: int = 1_000,
        max_revisions: int | None = None,
        max_relations: int = 3_000,
    ) -> None:
        resolved_max_revisions = max_claims if max_revisions is None else max_revisions
        if min(max_sessions, max_claims, resolved_max_revisions, max_relations) < 1:
            raise ValueError("knowledge graph capacities must be positive")
        if (
            max_sessions > 1_000
            or max_claims > 10_000
            or resolved_max_revisions > 10_000
            or max_relations > 30_000
        ):
            raise ValueError("knowledge graph capacities exceed safe limits")
        self._journal = journal
        self._max_sessions = max_sessions
        self._max_claims = max_claims
        self._max_revisions = resolved_max_revisions
        self._max_relations = max_relations

    def build(
        self,
        lead_id: UUID,
        *,
        deadline: RetrievalDeadline | None = None,
    ) -> LeadKnowledgeGraph:
        source = self._journal.knowledge_source(
            lead_id,
            max_sessions=self._max_sessions,
            max_facts=self._max_claims,
            max_revisions=self._max_revisions,
        )
        try:
            return self._project(source, lead_id, deadline)
        except RetrievalDeadlineExceededError:
            raise KnowledgeGraphDeadlineExceededError(
                lead_id,
                source.aggregate_version,
            ) from None

    def _project(
        self,
        source: LeadKnowledgeSourceSnapshot,
        lead_id: UUID,
        deadline: RetrievalDeadline | None,
    ) -> LeadKnowledgeGraph:
        if deadline is not None:
            deadline.check()
        revisions_by_previous = {
            item.revision.previous_fact_id: item
            for item in source.revisions
            if item.revision.previous_fact_id is not None
        }
        active_by_key: dict[str, list[JournaledConversationFact]] = defaultdict(list)
        for item in source.facts:
            if item.fact.fact_id not in revisions_by_previous:
                active_by_key[item.fact.key].append(item)
        conflicting_ids = {
            item.fact.fact_id
            for claims in active_by_key.values()
            if len({_canonical_value(item.fact.value) for item in claims}) > 1
            for item in claims
        }
        confirmed_ids = {
            item.revision.replacement_fact_id
            for item in source.revisions
            if item.revision.confirmed_by_customer
        }
        facts = source.facts if deadline is None else deadline.guard(source.facts)
        claims = tuple(
            self._claim(
                item,
                revisions_by_previous.get(item.fact.fact_id),
                conflicting=item.fact.fact_id in conflicting_ids,
                confirmed=item.fact.fact_id in confirmed_ids,
            )
            for item in facts
        )
        relations = self._relations(source.lead_id, source.session_ids, claims, deadline)
        if len(relations) > self._max_relations:
            raise JournalHistoryUnavailableError("knowledge graph relation capacity reached")
        if deadline is not None:
            deadline.check()
        graph = LeadKnowledgeGraph(
            lead_id=lead_id,
            aggregate_version=source.aggregate_version,
            session_ids=source.session_ids,
            claims=claims,
            relations=relations,
        )
        self._journal.validate_knowledge_source(source)
        return graph

    def validate(self, graph: LeadKnowledgeGraph) -> None:
        self.validate_version(graph.lead_id, graph.aggregate_version)

    def validate_version(self, lead_id: UUID, aggregate_version: int) -> None:
        self._journal.validate_knowledge_version(lead_id, aggregate_version)

    @staticmethod
    def _claim(
        item: JournaledConversationFact,
        revision: JournaledConversationRevision | None,
        *,
        conflicting: bool,
        confirmed: bool,
    ) -> TemporalFactClaim:
        if revision is not None:
            return TemporalFactClaim(
                fact=item.fact,
                status=FactClaimStatus.SUPERSEDED,
                session_id=item.session_id,
                language=item.language,
                valid_from_version=item.aggregate_version,
                valid_from=item.occurred_at,
                valid_to_version=revision.aggregate_version,
                valid_to=revision.occurred_at,
                superseded_by_fact_id=revision.revision.replacement_fact_id,
                confirmed_by_customer=False,
            )
        return TemporalFactClaim(
            fact=item.fact,
            status=(FactClaimStatus.CONFLICTING if conflicting else FactClaimStatus.CURRENT),
            session_id=item.session_id,
            language=item.language,
            valid_from_version=item.aggregate_version,
            valid_from=item.occurred_at,
            confirmed_by_customer=confirmed,
        )

    @staticmethod
    def _relations(
        lead_id: UUID,
        session_ids: tuple[UUID, ...],
        claims: tuple[TemporalFactClaim, ...],
        deadline: RetrievalDeadline | None = None,
    ) -> tuple[KnowledgeRelation, ...]:
        relations = [
            KnowledgeRelation(
                source_type=KnowledgeNodeType.LEAD,
                source_id=lead_id,
                relation=KnowledgeRelationType.HAS_SESSION,
                target_type=KnowledgeNodeType.SESSION,
                target_id=session_id,
            )
            for session_id in session_ids
        ]
        for claim in claims if deadline is None else deadline.guard(claims):
            relations.append(
                KnowledgeRelation(
                    source_type=KnowledgeNodeType.SESSION,
                    source_id=claim.session_id,
                    relation=KnowledgeRelationType.OBSERVED_FACT,
                    target_type=KnowledgeNodeType.FACT,
                    target_id=claim.fact.fact_id,
                )
            )
            if claim.superseded_by_fact_id is not None:
                relations.append(
                    KnowledgeRelation(
                        source_type=KnowledgeNodeType.FACT,
                        source_id=claim.fact.fact_id,
                        relation=KnowledgeRelationType.SUPERSEDED_BY,
                        target_type=KnowledgeNodeType.FACT,
                        target_id=claim.superseded_by_fact_id,
                    )
                )
        return tuple(relations)


def _canonical_value(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
