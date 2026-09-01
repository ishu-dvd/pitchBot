from __future__ import annotations

import hashlib
import json
import os
import platform
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic_ns
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pitchbot.benchmarks.manifest import canonical_manifest_sha256, load_json_model
from pitchbot.benchmarks.models import (
    EvaluationCaseResult,
    EvaluationCaseStatus,
    EvaluationHardwareProfile,
    EvaluationLabel,
    EvaluationMetric,
    EvaluationRun,
    EvaluationRunStatus,
    MetricDirection,
)
from pitchbot.conversation import (
    JournaledConversationFact,
    JournaledConversationRevision,
    LeadKnowledgeSourceSnapshot,
)
from pitchbot.domain import JsonValue, LanguageCode, RequirementFact, RequirementRevision
from pitchbot.knowledge import (
    FactClaimStatus,
    KnowledgeNodeType,
    KnowledgeRelationType,
    LeadKnowledgeBm25Retriever,
    LeadKnowledgeGraph,
    TemporalKnowledgeGraphBuilder,
)
from pitchbot.retrieval import (
    validate_bm25_document,
    validate_bm25_request,
)

_EVALUATION_NAMESPACE = UUID("59d677c8-d97a-4d7f-a5a5-679648cfdf6d")


class GraphRetrievalSuiteModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GraphRetrievalSuiteClaim(GraphRetrievalSuiteModel):
    claim_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    session_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    key: str = Field(min_length=1, max_length=100)
    value: JsonValue
    language: LanguageCode
    status: FactClaimStatus
    superseded_by_claim_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$",
    )
    confirmed_by_customer: bool = False


class GraphRetrievalSuiteCase(GraphRetrievalSuiteModel):
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,127}$")
    language: LanguageCode
    industry: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")
    persona: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")
    query: str = Field(min_length=1, max_length=4_096)
    claims: tuple[GraphRetrievalSuiteClaim, ...] = Field(min_length=1, max_length=100)
    relevant_claim_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    excluded_claim_ids: tuple[str, ...] = Field(default=(), max_length=20)
    tags: tuple[EvaluationLabel, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def validate_case(self) -> GraphRetrievalSuiteCase:
        validate_bm25_request(self.query, 1, 1)
        claims_by_id = {claim.claim_id: claim for claim in self.claims}
        if len(claims_by_id) != len(self.claims):
            raise ValueError("graph retrieval claim identifiers must be unique within a case")
        if len({claim.session_id for claim in self.claims}) < 2:
            raise ValueError("graph retrieval cases must contain multiple sessions")
        if len(self.relevant_claim_ids) != len(set(self.relevant_claim_ids)):
            raise ValueError("relevant graph retrieval claim identifiers must be unique")
        if len(self.excluded_claim_ids) != len(set(self.excluded_claim_ids)):
            raise ValueError("excluded graph retrieval claim identifiers must be unique")
        if set(self.relevant_claim_ids) & set(self.excluded_claim_ids):
            raise ValueError("relevant and excluded graph retrieval claims must be disjoint")
        referenced = set(self.relevant_claim_ids) | set(self.excluded_claim_ids)
        if referenced - set(claims_by_id):
            raise ValueError("referenced graph retrieval claims must exist in the case")
        if any(
            claims_by_id[claim_id].status is FactClaimStatus.SUPERSEDED
            for claim_id in self.relevant_claim_ids
        ):
            raise ValueError("superseded graph retrieval claims cannot be relevant")
        if any(
            claims_by_id[claim_id].status is not FactClaimStatus.SUPERSEDED
            for claim_id in self.excluded_claim_ids
        ):
            raise ValueError("only superseded graph retrieval claims can be excluded")
        superseded_ids = {
            claim.claim_id for claim in self.claims if claim.status is FactClaimStatus.SUPERSEDED
        }
        if set(self.excluded_claim_ids) != superseded_ids:
            raise ValueError("all superseded graph retrieval claims must be excluded")
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("graph retrieval case tags must be unique")
        self._validate_temporal_claims(claims_by_id)
        for claim in self.claims:
            if claim.status is not FactClaimStatus.SUPERSEDED:
                validate_bm25_document(claim.key, claim.value)
        return self

    def _validate_temporal_claims(
        self,
        claims_by_id: dict[str, GraphRetrievalSuiteClaim],
    ) -> None:
        active_by_key: dict[str, list[GraphRetrievalSuiteClaim]] = {}
        active_session_keys: set[tuple[str, str]] = set()
        replacement_ids: set[str] = set()
        positions = {claim.claim_id: position for position, claim in enumerate(self.claims)}
        for claim in self.claims:
            target_id = claim.superseded_by_claim_id
            if claim.status is FactClaimStatus.SUPERSEDED:
                if target_id is None or target_id not in claims_by_id:
                    raise ValueError(
                        "superseded graph retrieval claims require a valid replacement"
                    )
                target = claims_by_id[target_id]
                if target.session_id != claim.session_id or target.key != claim.key:
                    raise ValueError("graph retrieval replacements must retain session and key")
                if _canonical_value(target.value) == _canonical_value(claim.value):
                    raise ValueError("graph retrieval replacements must change the claim value")
                if positions[target_id] <= positions[claim.claim_id]:
                    raise ValueError("graph retrieval replacements must follow prior claims")
                if target_id in replacement_ids:
                    raise ValueError("graph retrieval replacements cannot branch")
                replacement_ids.add(target_id)
            elif target_id is not None:
                raise ValueError("active graph retrieval claims cannot name a replacement")
            else:
                session_key = (claim.session_id, claim.key)
                if session_key in active_session_keys:
                    raise ValueError(
                        "graph retrieval sessions can have only one active claim per key"
                    )
                active_session_keys.add(session_key)
                active_by_key.setdefault(claim.key, []).append(claim)
        for claims in active_by_key.values():
            values = {
                json.dumps(claim.value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                for claim in claims
            }
            expected = FactClaimStatus.CONFLICTING if len(values) > 1 else FactClaimStatus.CURRENT
            if any(claim.status is not expected for claim in claims):
                raise ValueError("graph retrieval active claim status contradicts its values")
        unconfirmable = {
            claim.claim_id
            for claim in self.claims
            if claim.confirmed_by_customer and claim.claim_id not in replacement_ids
        }
        if unconfirmable:
            raise ValueError("only replacement graph retrieval claims can be customer confirmed")


class GraphRetrievalSuite(GraphRetrievalSuiteModel):
    suite_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,127}$")
    version: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    corpus_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,127}$")
    corpus_version: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    top_k: int = Field(default=5, ge=1, le=20)
    deadline_ms: int = Field(default=200, ge=1, le=200)
    cases: tuple[GraphRetrievalSuiteCase, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_suite(self) -> GraphRetrievalSuite:
        case_ids = [item.case_id for item in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("graph retrieval case identifiers must be unique")
        if not any(item.excluded_claim_ids for item in self.cases):
            raise ValueError("graph retrieval suites must exercise superseded claim exclusion")
        return self


class _StaticKnowledgeSource:
    def __init__(self, snapshot: LeadKnowledgeSourceSnapshot) -> None:
        self._snapshot = snapshot

    def knowledge_source(
        self,
        lead_id: UUID,
        *,
        max_sessions: int,
        max_facts: int,
        max_revisions: int,
    ) -> LeadKnowledgeSourceSnapshot:
        if lead_id != self._snapshot.lead_id:
            raise ValueError("graph retrieval evaluation lead does not match")
        if (
            len(self._snapshot.session_ids) > max_sessions
            or len(self._snapshot.facts) > max_facts
            or len(self._snapshot.revisions) > max_revisions
        ):
            raise ValueError("graph retrieval evaluation case exceeds knowledge graph capacity")
        return self._snapshot

    def validate_knowledge_source(self, snapshot: LeadKnowledgeSourceSnapshot) -> None:
        if snapshot is not self._snapshot:
            raise RuntimeError("graph retrieval evaluation source changed")

    def validate_knowledge_version(self, lead_id: UUID, aggregate_version: int) -> None:
        if (
            lead_id != self._snapshot.lead_id
            or aggregate_version != self._snapshot.aggregate_version
        ):
            raise RuntimeError("graph retrieval evaluation source changed")


def validate_graph_retrieval_suite(path: Path) -> GraphRetrievalSuite:
    return load_json_model(path, GraphRetrievalSuite)


def run_graph_retrieval_evaluation(
    path: Path,
    *,
    run_id: str,
    git_revision: str,
) -> EvaluationRun:
    suite = validate_graph_retrieval_suite(path)
    manifest_hash = canonical_manifest_sha256(path)
    configuration_hash = hashlib.sha256(
        json.dumps(
            {
                "algorithm": "bm25",
                "b": 0.75,
                "deadline_ms": suite.deadline_ms,
                "k1": 1.5,
                "scope": "lead-temporal-active",
                "top_k": suite.top_k,
                "tokenizer": "unicode-nfkc-category-lmn-v1",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    started_at = datetime.now(UTC)
    case_results = tuple(_run_graph_case(item, suite) for item in suite.cases)
    completed_at = datetime.now(UTC)
    recalls = [_metric_value(item, "graph_retrieval.recall_at_k") for item in case_results]
    reciprocal_ranks = [
        _metric_value(item, "graph_retrieval.reciprocal_rank") for item in case_results
    ]
    exclusion_rates = [
        _metric_value(item, "graph_retrieval.excluded_claim_rate") for item in case_results
    ]
    fidelities = [
        _metric_value(item, "graph_retrieval.projection_fidelity") for item in case_results
    ]
    durations = sorted(item.duration_ms for item in case_results)
    p95_index = max(0, (95 * len(durations) + 99) // 100 - 1)
    timeout_rate = sum(
        "graph-retrieval-timeout" in item.failure_codes for item in case_results
    ) / len(case_results)
    return EvaluationRun(
        evaluation_schema_version="1",
        run_id=run_id,
        status=EvaluationRunStatus.COMPLETED,
        git_revision=git_revision,
        suite_id=suite.suite_id,
        suite_version=suite.version,
        suite_manifest_sha256=manifest_hash,
        corpus_id=suite.corpus_id,
        corpus_version=suite.corpus_version,
        corpus_manifest_sha256=manifest_hash,
        configuration_sha256=configuration_hash,
        hardware=EvaluationHardwareProfile(
            operating_system=_hardware_label(platform.platform(), fallback="not-reported"),
            architecture=_hardware_label(
                platform.machine(),
                fallback="not-reported",
                maximum_length=64,
            ),
            python_version=platform.python_version(),
            processor=_hardware_label(platform.processor(), fallback="not-reported"),
            logical_cpu_count=os.cpu_count(),
        ),
        started_at=started_at,
        completed_at=completed_at,
        metrics=(
            EvaluationMetric(
                name="graph_retrieval.mean_recall_at_k",
                value=sum(recalls) / len(recalls),
                unit="ratio",
                direction=MetricDirection.AT_LEAST,
                threshold=1.0,
            ),
            EvaluationMetric(
                name="graph_retrieval.mean_reciprocal_rank",
                value=sum(reciprocal_ranks) / len(reciprocal_ranks),
                unit="ratio",
                direction=MetricDirection.AT_LEAST,
                threshold=0.75,
            ),
            EvaluationMetric(
                name="graph_retrieval.excluded_claim_rate",
                value=sum(exclusion_rates) / len(exclusion_rates),
                unit="ratio",
                direction=MetricDirection.AT_MOST,
                threshold=0.0,
            ),
            EvaluationMetric(
                name="graph_retrieval.timeout_rate",
                value=timeout_rate,
                unit="ratio",
                direction=MetricDirection.AT_MOST,
                threshold=0.0,
            ),
            EvaluationMetric(
                name="graph_retrieval.p95_latency_ms",
                value=durations[p95_index],
                unit="ms",
                direction=MetricDirection.INFORMATIONAL,
            ),
            EvaluationMetric(
                name="graph_retrieval.mean_projection_fidelity",
                value=sum(fidelities) / len(fidelities),
                unit="ratio",
                direction=MetricDirection.AT_LEAST,
                threshold=1.0,
            ),
        ),
        cases=case_results,
    )


def _run_graph_case(
    item: GraphRetrievalSuiteCase,
    suite: GraphRetrievalSuite,
    *,
    clock: Callable[[], int] = monotonic_ns,
) -> EvaluationCaseResult:
    snapshot, labels_by_fact_id, expected_claims = _build_source(item)
    builder = TemporalKnowledgeGraphBuilder(_StaticKnowledgeSource(snapshot))
    projection_fidelity = _projection_fidelity(
        builder.build(snapshot.lead_id), labels_by_fact_id, expected_claims
    )
    response = LeadKnowledgeBm25Retriever(builder, clock=clock).search(
        snapshot.lead_id,
        item.query,
        top_k=suite.top_k,
        deadline_ms=suite.deadline_ms,
    )
    timed_out = response.timed_out or response.duration_ms >= suite.deadline_ms
    ranked_ids = (
        []
        if timed_out
        else [labels_by_fact_id[result.claim.fact.fact_id] for result in response.results]
    )
    relevant = set(item.relevant_claim_ids)
    retrieved_relevant = relevant.intersection(ranked_ids)
    recall = len(retrieved_relevant) / len(relevant)
    first_relevant_rank = next(
        (rank for rank, claim_id in enumerate(ranked_ids, start=1) if claim_id in relevant),
        None,
    )
    reciprocal_rank = 1 / first_relevant_rank if first_relevant_rank is not None else 0.0
    excluded = set(item.excluded_claim_ids)
    excluded_rate = len(excluded.intersection(ranked_ids)) / max(1, len(excluded))
    failure_codes: list[str] = []
    if timed_out:
        failure_codes.append("graph-retrieval-timeout")
    if recall < 1.0:
        failure_codes.append("graph-retrieval-miss")
    if reciprocal_rank < 0.5:
        failure_codes.append("graph-retrieval-rank")
    if excluded_rate > 0:
        failure_codes.append("graph-retrieval-excluded-claim")
    if projection_fidelity < 1.0:
        failure_codes.append("graph-projection-mismatch")
    return EvaluationCaseResult(
        case_id=item.case_id,
        status=(EvaluationCaseStatus.PASSED if not failure_codes else EvaluationCaseStatus.FAILED),
        language=item.language,
        industry=item.industry,
        persona=item.persona,
        duration_ms=response.duration_ms,
        tags=item.tags,
        metrics=(
            EvaluationMetric(
                name="graph_retrieval.recall_at_k",
                value=recall,
                unit="ratio",
                direction=MetricDirection.AT_LEAST,
                threshold=1.0,
            ),
            EvaluationMetric(
                name="graph_retrieval.reciprocal_rank",
                value=reciprocal_rank,
                unit="ratio",
                direction=MetricDirection.AT_LEAST,
                threshold=0.5,
            ),
            EvaluationMetric(
                name="graph_retrieval.excluded_claim_rate",
                value=excluded_rate,
                unit="ratio",
                direction=MetricDirection.AT_MOST,
                threshold=0.0,
            ),
            EvaluationMetric(
                name="graph_retrieval.projection_fidelity",
                value=projection_fidelity,
                unit="ratio",
                direction=MetricDirection.AT_LEAST,
                threshold=1.0,
            ),
            EvaluationMetric(
                name="graph_retrieval.latency_ms",
                value=response.duration_ms,
                unit="ms",
                direction=MetricDirection.INFORMATIONAL,
            ),
        ),
        failure_codes=tuple(failure_codes),
    )


@dataclass(frozen=True, slots=True)
class _ExpectedClaim:
    """Exact projection the production builder must produce for a corpus claim."""

    key: str
    value: str
    confidence: float
    captured_at: datetime
    status: FactClaimStatus
    session_id: UUID
    language: LanguageCode
    valid_from_version: int
    valid_from: datetime
    valid_to_version: int | None
    valid_to: datetime | None
    superseded_by_fact_id: UUID | None
    confirmed_by_customer: bool
    confirmed_by_revision_id: UUID | None
    confirmed_at: datetime | None


def _expected_relations(
    lead_id: UUID,
    session_ids: Iterable[UUID],
    expected: dict[str, _ExpectedClaim],
    fact_ids: dict[str, UUID],
) -> set[tuple[str, UUID, str, str, UUID]]:
    relations = {
        (
            KnowledgeNodeType.LEAD.value,
            lead_id,
            KnowledgeRelationType.HAS_SESSION.value,
            KnowledgeNodeType.SESSION.value,
            session_id,
        )
        for session_id in session_ids
    }
    for label, gold in expected.items():
        relations.add(
            (
                KnowledgeNodeType.SESSION.value,
                gold.session_id,
                KnowledgeRelationType.OBSERVED_FACT.value,
                KnowledgeNodeType.FACT.value,
                fact_ids[label],
            )
        )
        if gold.superseded_by_fact_id is not None:
            relations.add(
                (
                    KnowledgeNodeType.FACT.value,
                    fact_ids[label],
                    KnowledgeRelationType.SUPERSEDED_BY.value,
                    KnowledgeNodeType.FACT.value,
                    gold.superseded_by_fact_id,
                )
            )
    return relations


def _derived_relations(
    graph: LeadKnowledgeGraph,
) -> set[tuple[str, UUID, str, str, UUID]]:
    return {
        (
            relation.source_type.value,
            relation.source_id,
            relation.relation.value,
            relation.target_type.value,
            relation.target_id,
        )
        for relation in graph.relations
    }


def _build_source(
    item: GraphRetrievalSuiteCase,
) -> tuple[LeadKnowledgeSourceSnapshot, dict[UUID, str], dict[str, _ExpectedClaim]]:
    lead_id = uuid5(_EVALUATION_NAMESPACE, f"{item.case_id}:lead")
    session_ids = {
        claim.session_id: uuid5(
            _EVALUATION_NAMESPACE,
            f"{item.case_id}:session:{claim.session_id}",
        )
        for claim in item.claims
    }
    fact_ids = {
        claim.claim_id: uuid5(_EVALUATION_NAMESPACE, f"{item.case_id}:claim:{claim.claim_id}")
        for claim in item.claims
    }
    revision_ids = {
        claim.claim_id: uuid5(_EVALUATION_NAMESPACE, f"{item.case_id}:revision:{claim.claim_id}")
        for claim in item.claims
    }
    epoch = datetime(2026, 1, 1, tzinfo=UTC)
    positions = {claim.claim_id: position for position, claim in enumerate(item.claims, start=1)}
    occurred_at = {
        claim_id: epoch + timedelta(seconds=position) for claim_id, position in positions.items()
    }
    facts = tuple(
        JournaledConversationFact(
            fact=RequirementFact(
                fact_id=fact_ids[claim.claim_id],
                lead_id=lead_id,
                key=claim.key,
                value=claim.value,
                confidence=1.0,
                captured_at=occurred_at[claim.claim_id],
            ),
            aggregate_version=positions[claim.claim_id],
            session_id=session_ids[claim.session_id],
            language=claim.language,
            occurred_at=occurred_at[claim.claim_id],
        )
        for claim in item.claims
    )
    claims_by_id = {claim.claim_id: claim for claim in item.claims}
    revisions = tuple(
        JournaledConversationRevision(
            revision=RequirementRevision(
                revision_id=revision_ids[claim.claim_id],
                lead_id=lead_id,
                key=claim.key,
                previous_fact_id=fact_ids[claim.claim_id],
                replacement_fact_id=fact_ids[replacement_id],
                confirmed_by_customer=claims_by_id[replacement_id].confirmed_by_customer,
                reason=f"evaluation revision for {claim.claim_id}",
                revised_at=occurred_at[replacement_id],
            ),
            aggregate_version=positions[replacement_id],
            session_id=session_ids[claim.session_id],
            occurred_at=occurred_at[replacement_id],
        )
        for claim in item.claims
        if (replacement_id := claim.superseded_by_claim_id) is not None
    )
    predecessors = {
        claim.superseded_by_claim_id: claim.claim_id
        for claim in item.claims
        if claim.superseded_by_claim_id is not None
    }
    expected = {
        claim.claim_id: _ExpectedClaim(
            key=claim.key,
            value=_canonical_value(claim.value),
            confidence=1.0,
            captured_at=occurred_at[claim.claim_id],
            status=claim.status,
            session_id=session_ids[claim.session_id],
            language=claim.language,
            valid_from_version=positions[claim.claim_id],
            valid_from=occurred_at[claim.claim_id],
            valid_to_version=(
                positions[claim.superseded_by_claim_id]
                if claim.superseded_by_claim_id is not None
                else None
            ),
            valid_to=(
                occurred_at[claim.superseded_by_claim_id]
                if claim.superseded_by_claim_id is not None
                else None
            ),
            superseded_by_fact_id=(
                fact_ids[claim.superseded_by_claim_id]
                if claim.superseded_by_claim_id is not None
                else None
            ),
            confirmed_by_customer=claim.confirmed_by_customer,
            confirmed_by_revision_id=(
                revision_ids[predecessors[claim.claim_id]] if claim.confirmed_by_customer else None
            ),
            confirmed_at=(occurred_at[claim.claim_id] if claim.confirmed_by_customer else None),
        )
        for claim in item.claims
    }
    return (
        LeadKnowledgeSourceSnapshot(
            lead_id=lead_id,
            aggregate_version=len(item.claims),
            session_ids=tuple(session_ids.values()),
            facts=facts,
            revisions=revisions,
        ),
        {fact_id: claim_id for claim_id, fact_id in fact_ids.items()},
        expected,
    )


def _projection_fidelity(
    graph: LeadKnowledgeGraph,
    labels_by_fact_id: dict[UUID, str],
    expected: dict[str, _ExpectedClaim],
) -> float:
    """Fraction of corpus claims the production projection reproduced exactly.

    Relations are produced by the same production builder, so a relation regression
    fails the whole case rather than silently scoring one.
    """

    if len(graph.claims) != len(expected):
        return 0.0
    fact_ids = {label: fact_id for fact_id, label in labels_by_fact_id.items()}
    derived_relations = _derived_relations(graph)
    if len(derived_relations) != len(graph.relations) or derived_relations != (
        _expected_relations(graph.lead_id, graph.session_ids, expected, fact_ids)
    ):
        return 0.0
    matched_labels: set[str] = set()
    for claim in graph.claims:
        label = labels_by_fact_id.get(claim.fact.fact_id)
        if label is None or label in matched_labels:
            continue
        gold = expected.get(label)
        if gold is None:
            continue
        derived = _ExpectedClaim(
            key=claim.fact.key,
            value=_canonical_value(claim.fact.value),
            confidence=claim.fact.confidence,
            captured_at=claim.fact.captured_at,
            status=claim.status,
            session_id=claim.session_id,
            language=claim.language,
            valid_from_version=claim.valid_from_version,
            valid_from=claim.valid_from,
            valid_to_version=claim.valid_to_version,
            valid_to=claim.valid_to,
            superseded_by_fact_id=claim.superseded_by_fact_id,
            confirmed_by_customer=claim.confirmed_by_customer,
            confirmed_by_revision_id=claim.confirmed_by_revision_id,
            confirmed_at=claim.confirmed_at,
        )
        if derived == gold:
            matched_labels.add(label)
    return len(matched_labels) / len(expected)


def _metric_value(case: EvaluationCaseResult, name: str) -> float:
    return next(metric.value for metric in case.metrics if metric.name == name)


def _canonical_value(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hardware_label(
    value: str,
    *,
    fallback: str,
    maximum_length: int = 128,
) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9 ._+@()/-]", " ", value)
    sanitized = " ".join(sanitized.split())[:maximum_length].rstrip()
    return sanitized if sanitized and sanitized[0].isalnum() else fallback
