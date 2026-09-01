from __future__ import annotations

import json
from pathlib import Path

import pytest
from clocks import ScriptedClock
from pydantic import ValidationError

from pitchbot.benchmarks.cli import main
from pitchbot.benchmarks.graph_retrieval import (
    GraphRetrievalSuite,
    _run_graph_case,
    run_graph_retrieval_evaluation,
    validate_graph_retrieval_suite,
)
from pitchbot.knowledge import FactClaimStatus

SUITE_PATH = Path("evals/corpora/graph-retrieval-cases.json")


def test_reviewed_graph_retrieval_suite_covers_temporal_multilingual_slices() -> None:
    suite = validate_graph_retrieval_suite(SUITE_PATH)

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
    statuses = {claim.status for item in suite.cases for claim in item.claims}
    assert statuses == {
        FactClaimStatus.CURRENT,
        FactClaimStatus.CONFLICTING,
        FactClaimStatus.SUPERSEDED,
    }
    assert all(len({claim.session_id for claim in item.claims}) >= 2 for item in suite.cases)


def test_graph_retrieval_evaluation_gates_and_minimizes_artifacts() -> None:
    run = run_graph_retrieval_evaluation(
        SUITE_PATH,
        run_id="graph-bm25-test",
        git_revision="abcdef1",
    )

    assert run.gates_pass() is True
    assert len(run.cases) == 6
    assert {metric.name for metric in run.metrics} == {
        "graph_retrieval.excluded_claim_rate",
        "graph_retrieval.mean_recall_at_k",
        "graph_retrieval.mean_reciprocal_rank",
        "graph_retrieval.p95_latency_ms",
        "graph_retrieval.timeout_rate",
    }
    assert (
        next(
            metric.value
            for metric in run.metrics
            if metric.name == "graph_retrieval.excluded_claim_rate"
        )
        == 0
    )
    artifact = run.model_dump_json()
    assert '"query"' not in artifact
    assert '"claims"' not in artifact
    assert '"relevant_claim_ids"' not in artifact
    assert '"excluded_claim_ids"' not in artifact
    assert "catalog,search" not in artifact
    assert "प्लास्टिक plastics" not in artifact
    suite = validate_graph_retrieval_suite(SUITE_PATH)
    assert all(
        f'"{claim.claim_id}"' not in artifact for case in suite.cases for claim in case.claims
    )


def test_graph_retrieval_suite_rejects_unsafe_temporal_gold() -> None:
    payload = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    case = payload["cases"][1]
    case["relevant_claim_ids"] = ["b1"]
    case["excluded_claim_ids"] = []

    with pytest.raises(ValidationError, match="cannot be relevant"):
        GraphRetrievalSuite.model_validate(payload)

    payload = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    payload["cases"][1]["excluded_claim_ids"] = []
    with pytest.raises(ValidationError, match="must be excluded"):
        GraphRetrievalSuite.model_validate(payload)

    payload = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    payload["cases"][0]["claims"][0]["status"] = "current"
    with pytest.raises(ValidationError, match="contradicts"):
        GraphRetrievalSuite.model_validate(payload)

    payload = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    payload["cases"][0]["claims"][1]["session_id"] = "discovery"
    with pytest.raises(ValidationError, match="only one active claim"):
        GraphRetrievalSuite.model_validate(payload)


def test_graph_retrieval_suite_rejects_impossible_or_unexercised_cases() -> None:
    payload = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    payload["cases"][1]["claims"][0]["value"] = "toys"
    with pytest.raises(ValidationError, match="must change"):
        GraphRetrievalSuite.model_validate(payload)

    payload = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    payload["cases"][1]["claims"] = payload["cases"][1]["claims"][1:]
    payload["cases"][1]["excluded_claim_ids"] = []
    with pytest.raises(ValidationError, match="must exercise"):
        GraphRetrievalSuite.model_validate(payload)


def test_graph_retrieval_suite_enforces_production_bm25_limits() -> None:
    payload = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    payload["cases"][0]["query"] = "😀"
    with pytest.raises(ValidationError, match="letters or numbers"):
        GraphRetrievalSuite.model_validate(payload)

    payload = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    payload["cases"][0]["claims"][0]["value"] = "x" * 4_096
    with pytest.raises(ValidationError, match="document exceeds size"):
        GraphRetrievalSuite.model_validate(payload)


def test_graph_retrieval_suite_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text(
        '{"suite_id":"first","suite_id":"second"}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate JSON key: suite_id"):
        validate_graph_retrieval_suite(path)


def test_graph_retrieval_evaluation_timeout_has_no_partial_quality_credit() -> None:
    suite = validate_graph_retrieval_suite(SUITE_PATH)
    ticks = iter((0, 201_000_000, 202_000_000))

    result = _run_graph_case(suite.cases[0], suite, clock=lambda: next(ticks))

    assert result.status.value == "failed"
    assert result.duration_ms == 202
    assert "graph-retrieval-timeout" in result.failure_codes
    assert "graph-retrieval-miss" in result.failure_codes
    assert (
        next(
            metric.value
            for metric in result.metrics
            if metric.name == "graph_retrieval.recall_at_k"
        )
        == 0
    )


def test_graph_retrieval_evaluation_rejects_late_over_deadline_results() -> None:
    suite = validate_graph_retrieval_suite(SUITE_PATH)
    clock = ScriptedClock(0, 0, 0, 0, 0, 0, 0, 199_000_000, 201_000_000)

    result = _run_graph_case(suite.cases[0], suite, clock=clock)

    assert result.duration_ms == 201
    assert "graph-retrieval-timeout" in result.failure_codes
    assert "graph-retrieval-miss" in result.failure_codes
    assert (
        next(
            metric.value
            for metric in result.metrics
            if metric.name == "graph_retrieval.recall_at_k"
        )
        == 0
    )


def test_graph_retrieval_cli_validates_and_writes_minimized_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifact = tmp_path / "graph-retrieval-run.json"

    assert main(["validate-graph-retrieval-suite", str(SUITE_PATH)]) == 0
    assert "validated 6 graph retrieval cases" in capsys.readouterr().out
    assert (
        main(
            [
                "run-graph-retrieval",
                str(SUITE_PATH),
                str(artifact),
                "--run-id",
                "graph-bm25-cli",
                "--git-revision",
                "abcdef1",
            ]
        )
        == 0
    )
    assert "artifact-gates=pass" in capsys.readouterr().out
    assert main(["validate-evaluation", str(artifact)]) == 0
