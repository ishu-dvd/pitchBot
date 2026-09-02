"""How a suite declares what a complete artifact contains, and that the runners honour it.

The declaration in ``pitchbot.benchmarks.gates`` is the only thing standing between an
artifact and a pass, so it has to stay tied to what the runners actually emit. These tests
run each reviewed suite and check the emitted artifact against its own registered spec: if a
runner renames or drops a metric, the spec stops matching and this fails rather than the
gate silently ceasing to check that metric.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pitchbot.benchmarks.gates import (
    GRAPH_RETRIEVAL_GATE_SPEC,
    RETRIEVAL_GATE_SPEC,
    SPEECH_GATE_SPEC,
    SUITE_GATE_SPECS,
    AggregateKind,
    EvaluationGateSpec,
    MetricFoldRule,
    evaluate_gates,
    gates_pass,
)
from pitchbot.benchmarks.graph_retrieval import (
    run_graph_retrieval_evaluation,
    validate_graph_retrieval_suite,
)
from pitchbot.benchmarks.models import EvaluationRun
from pitchbot.benchmarks.retrieval import run_retrieval_evaluation, validate_retrieval_suite
from pitchbot.benchmarks.speech import (
    load_speech_suite,
    run_speech_evaluation,
    speech_gates_pass,
)

RETRIEVAL_SUITE = Path("evals/corpora/retrieval-cases.json")
GRAPH_SUITE = Path("evals/corpora/graph-retrieval-cases.json")
SPEECH_SUITE = Path("evals/corpora/vad-cases.json")


def _reviewed_runs() -> list[tuple[EvaluationRun, EvaluationGateSpec]]:
    retrieval = validate_retrieval_suite(RETRIEVAL_SUITE)
    graph = validate_graph_retrieval_suite(GRAPH_SUITE)
    speech = load_speech_suite(SPEECH_SUITE)
    return [
        (
            run_retrieval_evaluation(RETRIEVAL_SUITE, run_id="spec-r", git_revision="abcdef1"),
            retrieval.gate_spec(),
        ),
        (
            run_graph_retrieval_evaluation(GRAPH_SUITE, run_id="spec-g", git_revision="abcdef1"),
            graph.gate_spec(),
        ),
        (
            run_speech_evaluation(SPEECH_SUITE, run_id="spec-s", git_revision="abcdef1"),
            speech.gate_spec(),
        ),
    ]


def test_every_shipped_suite_manifest_has_a_reviewed_gate_spec() -> None:
    manifest_suite_ids = {
        json.loads(path.read_text(encoding="utf-8"))["suite_id"]
        for path in (RETRIEVAL_SUITE, GRAPH_SUITE, SPEECH_SUITE)
    }

    assert manifest_suite_ids == set(SUITE_GATE_SPECS)
    assert {RETRIEVAL_GATE_SPEC, GRAPH_RETRIEVAL_GATE_SPEC, SPEECH_GATE_SPEC} == set(
        SUITE_GATE_SPECS.values()
    )


def test_each_runner_emits_everything_its_spec_requires() -> None:
    for run, spec in _reviewed_runs():
        report = evaluate_gates(run, spec)
        assert report.passed, (run.suite_id, report.failures)
        # Same verdict through the registry, which is the path artifact-only callers use.
        assert gates_pass(run) is True, run.suite_id


def test_an_unreviewed_suite_cannot_pass_a_gate_nobody_declared() -> None:
    run, _ = _reviewed_runs()[0]
    stranger = run.model_copy(update={"suite_id": "nobody-reviewed-this"})

    report = evaluate_gates(stranger)

    assert report.passed is False
    assert report.failures == ("unknown-suite:nobody-reviewed-this",)


def test_a_narrowed_spec_rejects_a_run_from_a_different_case_set() -> None:
    run, spec = _reviewed_runs()[0]
    assert spec.required_case_ids is not None

    narrowed = spec.for_suite(
        suite_id=spec.suite_id,
        corpus_id=spec.corpus_id or run.corpus_id,
        case_ids=spec.required_case_ids | {"a-case-that-was-never-run"},
    )

    assert gates_pass(run, narrowed) is False
    assert "case-set-mismatch" in evaluate_gates(run, narrowed).failures


def test_the_speech_spec_derives_one_gate_per_data_declared_slice() -> None:
    suite = load_speech_suite(SPEECH_SUITE)

    spec = suite.gate_spec()
    slice_metrics = {
        name for name in spec.required_run_metrics if name.startswith("speech.vad_f1.")
    }

    assert {f"speech.vad_f1.lang.{case.language.value}" for case in suite.cases} <= slice_metrics
    assert {f"speech.vad_f1.vert.{case.vertical}" for case in suite.cases} <= slice_metrics
    assert {f"speech.vad_f1.cond.{case.condition}" for case in suite.cases} <= slice_metrics
    # Every declared slice metric is also folded against the cases in that slice.
    assert slice_metrics <= {fold.run_metric for fold in spec.folds}


@pytest.mark.parametrize(
    ("values", "kind", "expected"),
    [
        ([0.0, 1.0, 2.0], AggregateKind.MEAN, 1.0),
        ([0.5, 0.25, 2.0], AggregateKind.MIN, 0.25),
        ([0.5, 0.25, 2.0], AggregateKind.MAX, 2.0),
        ([1.0], AggregateKind.P95, 1.0),
        # Nearest-rank p95 over 20 values is the 19th smallest, matching the runners.
        ([float(index) for index in range(20)], AggregateKind.P95, 18.0),
    ],
)
def test_fold_matches_the_arithmetic_the_runners_use(
    values: list[float],
    kind: AggregateKind,
    expected: float,
) -> None:
    from pitchbot.benchmarks.gates import _fold

    assert _fold(values, kind) == expected


def test_speech_gate_keeps_every_fail_closed_property_after_sharing_the_helper() -> None:
    """run-speech must not have lost anything by reusing the repaired shared gate."""

    suite = load_speech_suite(SPEECH_SUITE)
    run = run_speech_evaluation(SPEECH_SUITE, run_id="retention", git_revision="abcdef1")
    assert speech_gates_pass(run, suite) is True

    metric_names = {metric.name for metric in run.metrics}
    for absent in sorted(SPEECH_GATE_SPEC.required_run_metrics):
        stripped = run.model_copy(
            update={"metrics": tuple(metric for metric in run.metrics if metric.name != absent)}
        )
        assert absent in metric_names
        assert speech_gates_pass(stripped, suite) is False, absent

    foreign = run.model_copy(update={"suite_id": "someone-elses-suite"})
    assert speech_gates_pass(foreign, suite) is False

    other_corpus = run.model_copy(update={"corpus_id": "someone-elses-corpus"})
    assert speech_gates_pass(other_corpus, suite) is False

    short = run.model_copy(update={"cases": run.cases[:-1]})
    assert speech_gates_pass(short, suite) is False

    # New since PR 27: an aggregate that disagrees with its cases is rejected even though it
    # still clears its own threshold, which the hand-rolled PR 24 gate could not see.
    forged = run.model_copy(
        update={
            "metrics": tuple(
                metric.model_copy(update={"value": 0.9})
                if metric.name == "speech.vad_min_f1"
                else metric
                for metric in run.metrics
            )
        }
    )
    forged_min = next(m for m in forged.metrics if m.name == "speech.vad_min_f1")
    assert forged_min.meets_threshold() is True
    assert speech_gates_pass(forged, suite) is False
    assert (
        "aggregate-inconsistent:speech.vad_min_f1"
        in evaluate_gates(forged, suite.gate_spec()).failures
    )


def test_failure_rate_aggregates_are_checked_against_the_case_failure_codes() -> None:
    run, spec = _reviewed_runs()[0]
    assert gates_pass(run, spec) is True

    # No case timed out, so any non-zero rate is a claim the cases do not support.
    lying = run.model_copy(
        update={
            "metrics": tuple(
                metric.model_copy(update={"value": 0.5})
                if metric.name == "retrieval.timeout_rate"
                else metric
                for metric in run.metrics
            )
        }
    )

    assert gates_pass(lying, spec) is False
    assert "aggregate-inconsistent:retrieval.timeout_rate" in evaluate_gates(lying, spec).failures


def test_a_fold_over_a_missing_case_metric_is_unverifiable_not_ignored() -> None:
    run, spec = _reviewed_runs()[0]
    without_case_metrics = run.model_copy(
        update={"cases": tuple(case.model_copy(update={"metrics": ()}) for case in run.cases)}
    )

    failures = evaluate_gates(without_case_metrics, spec).failures

    assert "aggregate-unverifiable:retrieval.mean_recall_at_k" in failures
    assert gates_pass(without_case_metrics, spec) is False


def test_slice_folds_only_summarize_their_own_slice() -> None:
    suite = load_speech_suite(SPEECH_SUITE)
    spec = suite.gate_spec()

    english_cases = frozenset(case.case_id for case in suite.cases if case.language.value == "en")
    fold = next(item for item in spec.folds if item.run_metric == "speech.vad_f1.lang.en")

    assert fold == MetricFoldRule(
        "speech.vad_f1.lang.en",
        "speech.vad_f1",
        AggregateKind.MEAN,
        case_ids=english_cases,
    )
    assert english_cases < frozenset(case.case_id for case in suite.cases)
