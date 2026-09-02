"""Suite-aware, fail-closed gate evaluation for evaluation artifacts.

``EvaluationRun`` is a transport contract: it can faithfully represent a run that is
missing every metric its suite exists to measure, and it must be able to, because a
failed run is still a run. Deciding whether such an artifact *passes* therefore needs
something the artifact cannot supply about itself - the suite's own declaration of what
a complete result contains. Without that declaration a gate can only observe that some
metric happens to pass, which is not a gate at all.

A suite declares that shape once, as an :class:`EvaluationGateSpec`. The specs are keyed
by ``suite_id`` in :data:`SUITE_GATE_SPECS` so artifact-only callers - ``validate-evaluation``
and ``render-evaluation``, which are handed a run and never see a suite manifest - can
still gate it. The runners, which do hold the manifest they just loaded, call
:meth:`EvaluationGateSpec.for_suite` to narrow the same declaration with the corpus, the
exact case set, and any per-slice metrics that only the manifest knows about. One
declaration, two levels of sharpness, no second mechanism.

An artifact whose ``suite_id`` has no reviewed spec cannot be checked against anything,
so it fails closed rather than reporting a pass nobody verified.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from math import isclose
from types import MappingProxyType

from pitchbot.benchmarks.models import (
    EvaluationCaseResult,
    EvaluationCaseStatus,
    EvaluationRun,
    EvaluationRunStatus,
)

# A run-level fold and this re-fold sum the same doubles in a different order, so exact
# equality would reject honest artifacts. Anything above float noise is a real
# disagreement between an aggregate and the cases it claims to summarize.
_AGGREGATE_RELATIVE_TOLERANCE = 1e-9
_AGGREGATE_ABSOLUTE_TOLERANCE = 1e-12

# Failure reasons are machine-readable labels, so a caller printing them into a terminal
# or an HTML report never has to render an unbounded list.
MAX_REPORTED_FAILURES = 5


class AggregateKind(StrEnum):
    MEAN = "mean"
    MIN = "min"
    MAX = "max"
    P95 = "p95"


@dataclass(frozen=True, slots=True)
class MetricFoldRule:
    """A run metric that must equal a fold of one per-case metric.

    ``case_ids`` restricts the fold to a slice of the run; ``None`` means every case.
    """

    run_metric: str
    case_metric: str
    kind: AggregateKind
    case_ids: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class FailureRateRule:
    """A run metric that must equal the share of cases carrying one failure code."""

    run_metric: str
    failure_code: str


@dataclass(frozen=True, slots=True)
class EvaluationGateSpec:
    """What a complete, gateable artifact for one suite must contain."""

    suite_id: str
    required_run_metrics: frozenset[str]
    required_case_metrics: frozenset[str]
    folds: tuple[MetricFoldRule, ...] = ()
    failure_rates: tuple[FailureRateRule, ...] = ()
    corpus_id: str | None = None
    required_case_ids: frozenset[str] | None = None

    def for_suite(
        self,
        *,
        suite_id: str,
        corpus_id: str,
        case_ids: frozenset[str],
        extra_run_metrics: frozenset[str] = frozenset(),
        extra_folds: tuple[MetricFoldRule, ...] = (),
    ) -> EvaluationGateSpec:
        """Sharpen this declaration with detail only a loaded suite manifest knows."""

        return replace(
            self,
            suite_id=suite_id,
            corpus_id=corpus_id,
            required_case_ids=case_ids,
            required_run_metrics=self.required_run_metrics | extra_run_metrics,
            folds=self.folds + extra_folds,
        )


@dataclass(frozen=True, slots=True)
class EvaluationGateReport:
    """Why an artifact passed or failed, in bounded machine-readable labels."""

    passed: bool
    failures: tuple[str, ...]

    def summary(self, *, limit: int = MAX_REPORTED_FAILURES) -> str:
        if not self.failures:
            return ""
        shown = self.failures[:limit]
        remaining = len(self.failures) - len(shown)
        rendered = ",".join(shown)
        return f"{rendered},+{remaining}-more" if remaining else rendered


def _case_metric_value(case: EvaluationCaseResult, name: str) -> float | None:
    return next((metric.value for metric in case.metrics if metric.name == name), None)


def _fold(values: Sequence[float], kind: AggregateKind) -> float:
    if kind is AggregateKind.MEAN:
        return sum(values) / len(values)
    if kind is AggregateKind.MIN:
        return min(values)
    if kind is AggregateKind.MAX:
        return max(values)
    # Nearest-rank p95, matching how every runner computes its own p95 metric.
    ordered = sorted(values)
    return ordered[max(0, (95 * len(ordered) + 99) // 100 - 1)]


def _agrees(actual: float, expected: float) -> bool:
    return isclose(
        actual,
        expected,
        rel_tol=_AGGREGATE_RELATIVE_TOLERANCE,
        abs_tol=_AGGREGATE_ABSOLUTE_TOLERANCE,
    )


def _fold_failures(run: EvaluationRun, spec: EvaluationGateSpec) -> list[str]:
    run_metrics = {metric.name: metric.value for metric in run.metrics}
    failures: list[str] = []
    for rule in spec.folds:
        reported = run_metrics.get(rule.run_metric)
        if reported is None:
            # Absence is already reported as a missing required metric when the suite
            # requires it; an optional folded metric simply has nothing to check.
            continue
        selected = [
            case for case in run.cases if rule.case_ids is None or case.case_id in rule.case_ids
        ]
        values = [_case_metric_value(case, rule.case_metric) for case in selected]
        if not values or any(value is None for value in values):
            failures.append(f"aggregate-unverifiable:{rule.run_metric}")
            continue
        expected = _fold([value for value in values if value is not None], rule.kind)
        if not _agrees(reported, expected):
            failures.append(f"aggregate-inconsistent:{rule.run_metric}")
    for rate_rule in spec.failure_rates:
        reported = run_metrics.get(rate_rule.run_metric)
        if reported is None:
            continue
        if not run.cases:
            failures.append(f"aggregate-unverifiable:{rate_rule.run_metric}")
            continue
        observed = sum(rate_rule.failure_code in case.failure_codes for case in run.cases) / len(
            run.cases
        )
        if not _agrees(reported, observed):
            failures.append(f"aggregate-inconsistent:{rate_rule.run_metric}")
    return failures


def evaluate_gates(
    run: EvaluationRun,
    spec: EvaluationGateSpec | None = None,
) -> EvaluationGateReport:
    """Gate ``run`` against ``spec``, or against the reviewed spec for its ``suite_id``."""

    resolved = spec if spec is not None else SUITE_GATE_SPECS.get(run.suite_id)
    if resolved is None:
        return EvaluationGateReport(passed=False, failures=(f"unknown-suite:{run.suite_id}",))

    failures: list[str] = []
    if run.status is not EvaluationRunStatus.COMPLETED:
        failures.append(f"run-not-completed:{run.status.value}")
    if not run.cases:
        failures.append("no-case-results")
    if run.suite_id != resolved.suite_id:
        failures.append(f"suite-mismatch:{run.suite_id}")
    if resolved.corpus_id is not None and run.corpus_id != resolved.corpus_id:
        failures.append(f"corpus-mismatch:{run.corpus_id}")
    if (
        resolved.required_case_ids is not None
        and {case.case_id for case in run.cases} != resolved.required_case_ids
    ):
        failures.append("case-set-mismatch")

    present_run_metrics = {metric.name for metric in run.metrics}
    failures.extend(
        f"missing-run-metric:{name}"
        for name in sorted(resolved.required_run_metrics - present_run_metrics)
    )
    for case in run.cases:
        if case.status is not EvaluationCaseStatus.PASSED:
            failures.append(f"case-not-passed:{case.case_id}")
        present_case_metrics = {metric.name for metric in case.metrics}
        failures.extend(
            f"missing-case-metric:{case.case_id}.{name}"
            for name in sorted(resolved.required_case_metrics - present_case_metrics)
        )

    failures.extend(_fold_failures(run, resolved))

    all_metrics = (*run.metrics, *(metric for case in run.cases for metric in case.metrics))
    gating = [
        (metric.name, result)
        for metric in all_metrics
        if (result := metric.meets_threshold()) is not None
    ]
    if not gating:
        failures.append("no-gating-metric")
    failures.extend(f"gate-below-threshold:{name}" for name, result in gating if not result)
    return EvaluationGateReport(passed=not failures, failures=tuple(failures))


def gates_pass(run: EvaluationRun, spec: EvaluationGateSpec | None = None) -> bool:
    """Whether ``run`` is a complete, self-consistent, fully passing artifact."""

    return evaluate_gates(run, spec).passed


RETRIEVAL_GATE_SPEC = EvaluationGateSpec(
    suite_id="pitchbot-bm25-baseline",
    corpus_id="synthetic-structured-facts",
    required_run_metrics=frozenset(
        {
            "retrieval.mean_recall_at_k",
            "retrieval.mean_reciprocal_rank",
            "retrieval.timeout_rate",
            "retrieval.p95_latency_ms",
        }
    ),
    required_case_metrics=frozenset(
        {
            "retrieval.recall_at_k",
            "retrieval.reciprocal_rank",
            "retrieval.latency_ms",
        }
    ),
    folds=(
        MetricFoldRule(
            "retrieval.mean_recall_at_k",
            "retrieval.recall_at_k",
            AggregateKind.MEAN,
        ),
        MetricFoldRule(
            "retrieval.mean_reciprocal_rank",
            "retrieval.reciprocal_rank",
            AggregateKind.MEAN,
        ),
        MetricFoldRule(
            "retrieval.p95_latency_ms",
            "retrieval.latency_ms",
            AggregateKind.P95,
        ),
    ),
    failure_rates=(FailureRateRule("retrieval.timeout_rate", "retrieval-timeout"),),
)

GRAPH_RETRIEVAL_GATE_SPEC = EvaluationGateSpec(
    suite_id="pitchbot-graph-retrieval",
    corpus_id="synthetic-temporal-claims",
    required_run_metrics=frozenset(
        {
            "graph_retrieval.mean_recall_at_k",
            "graph_retrieval.mean_reciprocal_rank",
            "graph_retrieval.excluded_claim_rate",
            "graph_retrieval.timeout_rate",
            "graph_retrieval.p95_latency_ms",
            "graph_retrieval.mean_projection_fidelity",
        }
    ),
    required_case_metrics=frozenset(
        {
            "graph_retrieval.recall_at_k",
            "graph_retrieval.reciprocal_rank",
            "graph_retrieval.excluded_claim_rate",
            "graph_retrieval.projection_fidelity",
            "graph_retrieval.latency_ms",
        }
    ),
    folds=(
        MetricFoldRule(
            "graph_retrieval.mean_recall_at_k",
            "graph_retrieval.recall_at_k",
            AggregateKind.MEAN,
        ),
        MetricFoldRule(
            "graph_retrieval.mean_reciprocal_rank",
            "graph_retrieval.reciprocal_rank",
            AggregateKind.MEAN,
        ),
        MetricFoldRule(
            "graph_retrieval.excluded_claim_rate",
            "graph_retrieval.excluded_claim_rate",
            AggregateKind.MEAN,
        ),
        MetricFoldRule(
            "graph_retrieval.mean_projection_fidelity",
            "graph_retrieval.projection_fidelity",
            AggregateKind.MEAN,
        ),
        MetricFoldRule(
            "graph_retrieval.p95_latency_ms",
            "graph_retrieval.latency_ms",
            AggregateKind.P95,
        ),
    ),
    failure_rates=(FailureRateRule("graph_retrieval.timeout_rate", "graph-retrieval-timeout"),),
)

SPEECH_GATE_SPEC = EvaluationGateSpec(
    suite_id="pitchbot-vad-structural",
    corpus_id="synthetic-vad-structural",
    required_run_metrics=frozenset(
        {
            "speech.vad_mean_f1",
            "speech.vad_min_f1",
            "speech.mean_real_time_factor",
            "speech.p95_real_time_factor",
            "speech.peak_python_kib",
        }
    ),
    required_case_metrics=frozenset(
        {
            "speech.vad_f1",
            "speech.vad_precision",
            "speech.vad_recall",
            "speech.real_time_factor",
        }
    ),
    folds=(
        MetricFoldRule("speech.vad_mean_f1", "speech.vad_f1", AggregateKind.MEAN),
        MetricFoldRule("speech.vad_min_f1", "speech.vad_f1", AggregateKind.MIN),
        MetricFoldRule(
            "speech.mean_real_time_factor",
            "speech.real_time_factor",
            AggregateKind.MEAN,
        ),
        MetricFoldRule(
            "speech.p95_real_time_factor",
            "speech.real_time_factor",
            AggregateKind.P95,
        ),
    ),
)

SUITE_GATE_SPECS: Mapping[str, EvaluationGateSpec] = MappingProxyType(
    {
        spec.suite_id: spec
        for spec in (RETRIEVAL_GATE_SPEC, GRAPH_RETRIEVAL_GATE_SPEC, SPEECH_GATE_SPEC)
    }
)


__all__ = [
    "GRAPH_RETRIEVAL_GATE_SPEC",
    "MAX_REPORTED_FAILURES",
    "RETRIEVAL_GATE_SPEC",
    "SPEECH_GATE_SPEC",
    "SUITE_GATE_SPECS",
    "AggregateKind",
    "EvaluationGateReport",
    "EvaluationGateSpec",
    "FailureRateRule",
    "MetricFoldRule",
    "evaluate_gates",
    "gates_pass",
]
