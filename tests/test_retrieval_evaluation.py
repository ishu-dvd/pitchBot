from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pitchbot.benchmarks.cli import main
from pitchbot.benchmarks.retrieval import (
    RetrievalSuite,
    _run_case,
    run_retrieval_evaluation,
    validate_retrieval_suite,
)

SUITE_PATH = Path("evals/corpora/retrieval-cases.json")


def test_reviewed_retrieval_suite_is_multilingual_and_industry_diverse() -> None:
    suite = validate_retrieval_suite(SUITE_PATH)

    assert len(suite.cases) == 6
    assert {item.language.value for item in suite.cases} == {"en", "hi", "mixed"}
    assert {item.industry for item in suite.cases} == {
        "apparel",
        "books",
        "food",
        "import-export",
        "plastics",
        "toys",
    }
    assert len({item.persona for item in suite.cases}) == 6


def test_retrieval_evaluation_emits_only_minimized_metrics() -> None:
    run = run_retrieval_evaluation(
        SUITE_PATH,
        run_id="bm25-test-run",
        git_revision="abcdef1",
    )

    assert run.gates_pass() is True
    assert len(run.cases) == 6
    assert {metric.name for metric in run.metrics} == {
        "retrieval.mean_recall_at_k",
        "retrieval.mean_reciprocal_rank",
        "retrieval.p95_latency_ms",
        "retrieval.timeout_rate",
    }
    artifact = run.model_dump_json()
    suite = validate_retrieval_suite(SUITE_PATH)
    assert all(item.query not in artifact for item in suite.cases)
    assert '"query"' not in artifact
    assert '"documents"' not in artifact
    assert '"relevant_document_ids"' not in artifact
    assert "catalog,payments" not in artifact
    assert "certificates,enquiry" not in artifact
    assert "बजट 50000" not in artifact


def test_retrieval_suite_rejects_missing_gold_document() -> None:
    payload = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    payload["cases"][0]["relevant_document_ids"] = ["missing"]

    with pytest.raises(ValidationError, match="must exist"):
        RetrievalSuite.model_validate(payload)


def test_retrieval_evaluation_deadline_includes_index_construction() -> None:
    suite = validate_retrieval_suite(SUITE_PATH)
    ticks = iter((0, 200_000_000, 201_000_000))

    result = _run_case(suite.cases[0], suite, clock=lambda: next(ticks))

    assert result.status.value == "failed"
    assert "retrieval-timeout" in result.failure_codes
    assert result.duration_ms == 201


def test_retrieval_cli_validates_runs_and_renders_existing_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifact = tmp_path / "retrieval-run.json"
    report = tmp_path / "retrieval-report.html"

    assert main(["validate-retrieval-suite", str(SUITE_PATH)]) == 0
    assert "validated 6 retrieval cases" in capsys.readouterr().out
    assert (
        main(
            [
                "run-retrieval",
                str(SUITE_PATH),
                str(artifact),
                "--run-id",
                "bm25-cli-run",
                "--git-revision",
                "abcdef1",
            ]
        )
        == 0
    )
    assert "artifact-gates=pass" in capsys.readouterr().out
    assert main(["validate-evaluation", str(artifact)]) == 0
    capsys.readouterr()
    assert main(["render-evaluation", str(artifact), str(report)]) == 0
    assert "retrieval.mean_recall_at_k" in report.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        main(
            [
                "run-retrieval",
                str(SUITE_PATH),
                str(artifact),
                "--run-id",
                "bm25-cli-run",
                "--git-revision",
                "abcdef1",
            ]
        )
