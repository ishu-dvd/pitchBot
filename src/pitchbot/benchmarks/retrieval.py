from __future__ import annotations

import hashlib
import json
import os
import platform
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic_ns
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pitchbot.benchmarks.gates import RETRIEVAL_GATE_SPEC, EvaluationGateSpec
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
from pitchbot.domain import JsonValue, LanguageCode
from pitchbot.retrieval import Bm25Index, FactProvenance, LexicalDocument, RankedFact

_EVALUATION_NAMESPACE = UUID("68f9884e-35e6-4726-af91-c456b523fe34")


class RetrievalSuiteModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RetrievalSuiteDocument(RetrievalSuiteModel):
    document_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    key: str = Field(min_length=1, max_length=100)
    value: JsonValue
    language: LanguageCode


class RetrievalSuiteCase(RetrievalSuiteModel):
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,127}$")
    language: LanguageCode
    industry: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")
    persona: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")
    query: str = Field(min_length=1, max_length=4_096)
    documents: tuple[RetrievalSuiteDocument, ...] = Field(min_length=1, max_length=100)
    relevant_document_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    tags: tuple[EvaluationLabel, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def validate_case(self) -> RetrievalSuiteCase:
        document_ids = [item.document_id for item in self.documents]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("retrieval document identifiers must be unique within a case")
        if len(self.relevant_document_ids) != len(set(self.relevant_document_ids)):
            raise ValueError("relevant retrieval document identifiers must be unique")
        if set(self.relevant_document_ids) - set(document_ids):
            raise ValueError("relevant retrieval documents must exist in the case")
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("retrieval case tags must be unique")
        return self


class RetrievalSuite(RetrievalSuiteModel):
    suite_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,127}$")
    version: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    corpus_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,127}$")
    corpus_version: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")
    top_k: int = Field(default=5, ge=1, le=20)
    deadline_ms: int = Field(default=200, ge=1, le=200)
    cases: tuple[RetrievalSuiteCase, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_suite(self) -> RetrievalSuite:
        case_ids = [item.case_id for item in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("retrieval case identifiers must be unique")
        return self

    def gate_spec(self) -> EvaluationGateSpec:
        """The reviewed retrieval gate narrowed to this suite's identity and case set."""

        return RETRIEVAL_GATE_SPEC.for_suite(
            suite_id=self.suite_id,
            corpus_id=self.corpus_id,
            case_ids=frozenset(item.case_id for item in self.cases),
        )


def validate_retrieval_suite(path: Path) -> RetrievalSuite:
    return load_json_model(path, RetrievalSuite)


def run_retrieval_evaluation(
    path: Path,
    *,
    run_id: str,
    git_revision: str,
) -> EvaluationRun:
    suite = validate_retrieval_suite(path)
    manifest_hash = canonical_manifest_sha256(path)
    configuration = json.dumps(
        {
            "algorithm": "bm25",
            "b": 0.75,
            "deadline_ms": suite.deadline_ms,
            "k1": 1.5,
            "top_k": suite.top_k,
            "tokenizer": "unicode-nfkc-category-lmn-v1",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    configuration_hash = hashlib.sha256(configuration).hexdigest()
    started_at = datetime.now(UTC)
    case_results = tuple(_run_case(item, suite) for item in suite.cases)
    completed_at = datetime.now(UTC)
    recalls = [_metric_value(item, "retrieval.recall_at_k") for item in case_results]
    reciprocal_ranks = [_metric_value(item, "retrieval.reciprocal_rank") for item in case_results]
    durations = sorted(item.duration_ms for item in case_results)
    p95_index = max(0, (95 * len(durations) + 99) // 100 - 1)
    timeout_rate = sum("retrieval-timeout" in item.failure_codes for item in case_results) / len(
        case_results
    )
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
                name="retrieval.mean_recall_at_k",
                value=sum(recalls) / len(recalls),
                unit="ratio",
                direction=MetricDirection.AT_LEAST,
                threshold=1.0,
            ),
            EvaluationMetric(
                name="retrieval.mean_reciprocal_rank",
                value=sum(reciprocal_ranks) / len(reciprocal_ranks),
                unit="ratio",
                direction=MetricDirection.AT_LEAST,
                threshold=0.75,
            ),
            EvaluationMetric(
                name="retrieval.timeout_rate",
                value=timeout_rate,
                unit="ratio",
                direction=MetricDirection.AT_MOST,
                threshold=0.0,
            ),
            EvaluationMetric(
                name="retrieval.p95_latency_ms",
                value=durations[p95_index],
                unit="ms",
                direction=MetricDirection.INFORMATIONAL,
            ),
        ),
        cases=case_results,
    )


def _run_case(
    item: RetrievalSuiteCase,
    suite: RetrievalSuite,
    *,
    clock: Callable[[], int] = monotonic_ns,
) -> EvaluationCaseResult:
    lead_id = uuid5(_EVALUATION_NAMESPACE, f"{item.case_id}:lead")
    session_id = uuid5(_EVALUATION_NAMESPACE, f"{item.case_id}:session")
    labels_by_fact_id: dict[UUID, str] = {}
    documents: list[LexicalDocument] = []
    occurred_at = datetime(2026, 1, 1, tzinfo=UTC)
    for document in item.documents:
        fact_id = uuid5(_EVALUATION_NAMESPACE, f"{item.case_id}:{document.document_id}")
        labels_by_fact_id[fact_id] = document.document_id
        documents.append(
            LexicalDocument(
                key=document.key,
                value=document.value,
                language=document.language,
                provenance=FactProvenance(
                    lead_id=lead_id,
                    session_id=session_id,
                    aggregate_version=1,
                    fact_id=fact_id,
                    source_span_ids=(),
                    occurred_at=occurred_at,
                ),
            )
        )
    started = clock()
    index = Bm25Index(documents)
    elapsed_ns = max(0, clock() - started)
    remaining_ms = suite.deadline_ms - elapsed_ns // 1_000_000
    ranked: tuple[RankedFact, ...]
    if remaining_ms < 1:
        ranked = ()
        timed_out = True
    else:
        ranked, _, timed_out = index.search(
            item.query,
            top_k=suite.top_k,
            deadline_ms=int(remaining_ms),
            clock=clock,
        )
    duration_ms = max(0.0, (clock() - started) / 1_000_000)
    if duration_ms >= suite.deadline_ms:
        ranked = ()
        timed_out = True
    ranked_ids = [labels_by_fact_id[result.document.provenance.fact_id] for result in ranked]
    relevant = set(item.relevant_document_ids)
    retrieved_relevant = relevant.intersection(ranked_ids)
    recall = len(retrieved_relevant) / len(relevant)
    first_relevant_rank = next(
        (rank for rank, document_id in enumerate(ranked_ids, start=1) if document_id in relevant),
        None,
    )
    reciprocal_rank = 1 / first_relevant_rank if first_relevant_rank is not None else 0.0
    failure_codes: list[str] = []
    if timed_out:
        failure_codes.append("retrieval-timeout")
    if recall < 1.0:
        failure_codes.append("retrieval-miss")
    if reciprocal_rank < 0.5:
        failure_codes.append("retrieval-rank")
    return EvaluationCaseResult(
        case_id=item.case_id,
        status=(EvaluationCaseStatus.PASSED if not failure_codes else EvaluationCaseStatus.FAILED),
        language=item.language,
        industry=item.industry,
        persona=item.persona,
        duration_ms=duration_ms,
        tags=item.tags,
        metrics=(
            EvaluationMetric(
                name="retrieval.recall_at_k",
                value=recall,
                unit="ratio",
                direction=MetricDirection.AT_LEAST,
                threshold=1.0,
            ),
            EvaluationMetric(
                name="retrieval.reciprocal_rank",
                value=reciprocal_rank,
                unit="ratio",
                direction=MetricDirection.AT_LEAST,
                threshold=0.5,
            ),
            EvaluationMetric(
                name="retrieval.latency_ms",
                value=duration_ms,
                unit="ms",
                direction=MetricDirection.INFORMATIONAL,
            ),
        ),
        failure_codes=tuple(failure_codes),
    )


def _metric_value(case: EvaluationCaseResult, name: str) -> float:
    return next(metric.value for metric in case.metrics if metric.name == name)


def _hardware_label(
    value: str,
    *,
    fallback: str,
    maximum_length: int = 128,
) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9 ._+@()/-]", " ", value)
    sanitized = " ".join(sanitized.split())[:maximum_length].rstrip()
    return sanitized if sanitized and sanitized[0].isalnum() else fallback
