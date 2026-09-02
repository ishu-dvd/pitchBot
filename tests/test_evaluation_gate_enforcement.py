"""The evaluation gate is a build control, so these tests assert on exit codes.

Every test here drives the shipped command surface rather than the gate internals, because
the defect this covers was not that the gate computed the wrong answer - it was that nothing
downstream acted on the answer. A gate whose result never reaches the process exit code
cannot fail a build, and CI could therefore not detect a failing suite at all.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pitchbot.benchmarks.cli import main
from pitchbot.benchmarks.evaluation import validate_evaluation_run
from pitchbot.benchmarks.models import (
    EvaluationCaseResult,
    EvaluationCaseStatus,
    EvaluationHardwareProfile,
    EvaluationMetric,
    EvaluationRun,
    EvaluationRunStatus,
    MetricDirection,
)
from pitchbot.domain import LanguageCode

RETRIEVAL_SUITE = Path("evals/corpora/retrieval-cases.json")
GRAPH_SUITE = Path("evals/corpora/graph-retrieval-cases.json")
SPEECH_SUITE = Path("evals/corpora/vad-cases.json")

# No document in the reviewed corpus contains this term, so BM25 returns nothing and the
# case misses its gold document. It is a scoring failure, not an infrastructure failure.
_UNMATCHABLE_QUERY = "zzzqqq unmatchable token"


def _fail_open_artifact(*, suite_id: str) -> EvaluationRun:
    """The exact artifact the audit used to prove the shared gate was fail-open.

    A completed run, one passed case carrying no metrics whatsoever, and a single unrelated
    passing metric. Nothing the suite exists to measure is present.
    """

    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    return EvaluationRun(
        evaluation_schema_version="1",
        run_id="fail-open-probe",
        status=EvaluationRunStatus.COMPLETED,
        git_revision="1234567",
        suite_id=suite_id,
        suite_version="1.0",
        suite_manifest_sha256="c" * 64,
        corpus_id="synthetic-structured-facts",
        corpus_version="1.0",
        corpus_manifest_sha256="a" * 64,
        configuration_sha256="b" * 64,
        hardware=EvaluationHardwareProfile(
            operating_system="test",
            architecture="test",
            python_version="3.12",
            processor="test",
        ),
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=1),
        metrics=(
            EvaluationMetric(
                name="unrelated.pass",
                value=1.0,
                unit="ratio",
                direction=MetricDirection.AT_LEAST,
                threshold=0.0,
            ),
        ),
        cases=(
            EvaluationCaseResult(
                case_id="case-1",
                status=EvaluationCaseStatus.PASSED,
                language=LanguageCode.ENGLISH,
                industry="apparel",
                persona="direct-buyer",
                duration_ms=100,
            ),
        ),
    )


def _write(path: Path, run: EvaluationRun) -> Path:
    path.write_text(f"{run.model_dump_json(indent=2)}\n", encoding="utf-8")
    return path


def _suite_with_unmatchable_first_case(source: Path, destination: Path) -> Path:
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["cases"][0]["query"] = _UNMATCHABLE_QUERY
    destination.write_text(json.dumps(payload), encoding="utf-8")
    return destination


@pytest.mark.parametrize("suite_id", ["pitchbot-bm25-baseline", "some-unreviewed-suite"])
def test_the_fail_open_artifact_the_audit_reproduced_is_now_rejected(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    suite_id: str,
) -> None:
    artifact = _write(tmp_path / f"{suite_id}.json", _fail_open_artifact(suite_id=suite_id))

    # It is a structurally valid, completed run - the contract is not what was broken.
    assert validate_evaluation_run(artifact).status is EvaluationRunStatus.COMPLETED
    assert main(["validate-evaluation", str(artifact)]) == 1
    output = capsys.readouterr().out
    assert "artifact-gates=fail" in output
    assert "artifact-gates=pass" not in output


def test_a_reviewed_suite_rejects_the_fail_open_artifact_by_name(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The rejection is about missing metrics, not merely about an unrecognised suite id."""

    artifact = _write(
        tmp_path / "named.json",
        _fail_open_artifact(suite_id="pitchbot-bm25-baseline"),
    )

    assert main(["validate-evaluation", str(artifact)]) == 1
    output = capsys.readouterr().out
    assert "missing-run-metric:retrieval.mean_recall_at_k" in output
    assert "missing-run-metric:retrieval.timeout_rate" in output


def test_validate_evaluation_exit_code_follows_the_gate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifact = tmp_path / "retrieval-run.json"
    assert (
        main(
            [
                "run-retrieval",
                str(RETRIEVAL_SUITE),
                str(artifact),
                "--run-id",
                "gate-exit-pass",
                "--git-revision",
                "abcdef1",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["validate-evaluation", str(artifact)]) == 0
    assert "artifact-gates=pass" in capsys.readouterr().out

    # Drop one gating metric below its threshold and nothing else.
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    for metric in payload["metrics"]:
        if metric["name"] == "retrieval.mean_reciprocal_rank":
            metric["value"] = 0.1
    regressed = tmp_path / "regressed.json"
    regressed.write_text(json.dumps(payload), encoding="utf-8")

    assert main(["validate-evaluation", str(regressed)]) == 1
    assert "gate-below-threshold:retrieval.mean_reciprocal_rank" in capsys.readouterr().out


def test_aggregate_inconsistent_with_the_cases_is_rejected(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An aggregate that disagrees with its own cases fails even when no threshold trips.

    ``retrieval.p95_latency_ms`` is informational, so a threshold check can never see this
    edit. Only comparing the aggregate against the per-case results it claims to summarize
    catches an artifact whose run-level numbers were not produced by its own cases.
    """

    artifact = tmp_path / "retrieval-run.json"
    assert (
        main(
            [
                "run-retrieval",
                str(RETRIEVAL_SUITE),
                str(artifact),
                "--run-id",
                "aggregate-probe",
                "--git-revision",
                "abcdef1",
            ]
        )
        == 0
    )
    capsys.readouterr()

    payload = json.loads(artifact.read_text(encoding="utf-8"))
    for metric in payload["metrics"]:
        if metric["name"] == "retrieval.p95_latency_ms":
            metric["value"] = 999.0
        if metric["name"] == "retrieval.mean_reciprocal_rank":
            # Still above its 0.75 threshold, so only the fold can reject it.
            metric["value"] = 0.8
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(payload), encoding="utf-8")

    assert main(["validate-evaluation", str(tampered)]) == 1
    output = capsys.readouterr().out
    assert "aggregate-inconsistent:retrieval.p95_latency_ms" in output
    assert "aggregate-inconsistent:retrieval.mean_reciprocal_rank" in output


def test_run_retrieval_exit_code_is_non_zero_when_the_gate_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    failing_suite = _suite_with_unmatchable_first_case(
        RETRIEVAL_SUITE, tmp_path / "retrieval-failing.json"
    )
    artifact = tmp_path / "retrieval-failing-run.json"

    assert (
        main(
            [
                "run-retrieval",
                str(failing_suite),
                str(artifact),
                "--run-id",
                "retrieval-regression",
                "--git-revision",
                "abcdef1",
            ]
        )
        == 1
    )
    assert "artifact-gates=fail" in capsys.readouterr().out
    # The artifact is still written, so the regression can be inspected and reported.
    assert validate_evaluation_run(artifact).status is EvaluationRunStatus.COMPLETED


def test_run_graph_retrieval_exit_code_is_non_zero_when_the_gate_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    failing_suite = _suite_with_unmatchable_first_case(GRAPH_SUITE, tmp_path / "graph-failing.json")
    artifact = tmp_path / "graph-failing-run.json"

    assert (
        main(
            [
                "run-graph-retrieval",
                str(failing_suite),
                str(artifact),
                "--run-id",
                "graph-regression",
                "--git-revision",
                "abcdef1",
            ]
        )
        == 1
    )
    assert "artifact-gates=fail" in capsys.readouterr().out
    assert validate_evaluation_run(artifact).status is EvaluationRunStatus.COMPLETED


def test_reviewed_suites_still_pass_and_exit_zero(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Tightening the gate must not have quietly turned a green suite red."""

    commands = (
        ("run-retrieval", RETRIEVAL_SUITE, "retrieval"),
        ("run-graph-retrieval", GRAPH_SUITE, "graph"),
        ("run-speech", SPEECH_SUITE, "speech"),
    )
    for command, suite, label in commands:
        artifact = tmp_path / f"{label}.json"
        assert (
            main(
                [
                    command,
                    str(suite),
                    str(artifact),
                    "--run-id",
                    f"{label}-green",
                    "--git-revision",
                    "abcdef1",
                ]
            )
            == 0
        ), label
        assert "artifact-gates=pass" in capsys.readouterr().out
        assert main(["validate-evaluation", str(artifact)]) == 0, label
        capsys.readouterr()
