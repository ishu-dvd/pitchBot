from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from clocks import ScriptedClock
from pydantic import ValidationError

from pitchbot.benchmarks.cli import main
from pitchbot.benchmarks.gates import gates_pass
from pitchbot.benchmarks.graph_retrieval import (
    GraphRetrievalSuite,
    _build_source,
    _projection_fidelity,
    _run_graph_case,
    _StaticKnowledgeSource,
    run_graph_retrieval_evaluation,
    validate_graph_retrieval_suite,
)
from pitchbot.knowledge import FactClaimStatus, TemporalKnowledgeGraphBuilder

SUITE_PATH = Path("evals/corpora/graph-retrieval-cases.json")


def test_reviewed_graph_retrieval_suite_covers_temporal_multilingual_slices() -> None:
    suite = validate_graph_retrieval_suite(SUITE_PATH)

    assert len(suite.cases) == 7
    assert {item.language.value for item in suite.cases} == {"en", "hi", "mixed"}
    assert {item.industry for item in suite.cases} == {
        "apparel",
        "books",
        "electronics",
        "food",
        "import-export",
        "plastics",
        "toys",
    }
    assert len({item.persona for item in suite.cases}) == 7
    assert any(claim.confirmed_by_customer for item in suite.cases for claim in item.claims)
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

    assert gates_pass(run) is True
    assert gates_pass(run, validate_graph_retrieval_suite(SUITE_PATH).gate_spec()) is True
    assert len(run.cases) == 7
    assert {metric.name for metric in run.metrics} == {
        "graph_retrieval.excluded_claim_rate",
        "graph_retrieval.mean_projection_fidelity",
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

    payload = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    payload["cases"][0]["claims"][2]["confirmed_by_customer"] = True
    with pytest.raises(ValidationError, match="customer confirmed"):
        GraphRetrievalSuite.model_validate(payload)


def test_graph_retrieval_cases_are_projected_by_production_code() -> None:
    suite = validate_graph_retrieval_suite(SUITE_PATH)
    case = next(item for item in suite.cases if item.case_id == "hi-electronics-confirmed-revision")
    snapshot, labels_by_fact_id, expected = _build_source(case)
    graph = TemporalKnowledgeGraphBuilder(_StaticKnowledgeSource(snapshot)).build(snapshot.lead_id)
    derived = {labels_by_fact_id[claim.fact.fact_id]: claim for claim in graph.claims}

    assert derived["g1"].status is FactClaimStatus.SUPERSEDED
    assert derived["g2"].status is FactClaimStatus.CURRENT
    assert derived["g2"].confirmed_by_customer is True
    assert derived["g2"].confirmed_by_revision_id == expected["g2"].confirmed_by_revision_id
    assert derived["g2"].confirmed_at == expected["g2"].confirmed_at
    assert derived["g4"].confirmed_by_customer is False
    assert _projection_fidelity(graph, labels_by_fact_id, expected) == 1.0


@pytest.mark.parametrize(
    "mutation",
    [
        {"confirmed_by_customer": False, "confirmed_by_revision_id": None, "confirmed_at": None},
        {"confirmed_by_revision_id": uuid4()},
        {"confirmed_at": datetime(2030, 1, 1, tzinfo=UTC)},
    ],
)
def test_projection_fidelity_detects_confirmation_drift(mutation: dict[str, object]) -> None:
    suite = validate_graph_retrieval_suite(SUITE_PATH)
    case = next(item for item in suite.cases if item.case_id == "en-toys-direct-supersession")
    snapshot, labels_by_fact_id, expected = _build_source(case)
    graph = TemporalKnowledgeGraphBuilder(_StaticKnowledgeSource(snapshot)).build(snapshot.lead_id)
    drifted = graph.model_copy(
        update={
            "claims": tuple(
                claim.model_copy(update=mutation) if claim.confirmed_by_customer else claim
                for claim in graph.claims
            )
        }
    )

    assert any(claim.confirmed_by_customer for claim in graph.claims)
    assert _projection_fidelity(drifted, labels_by_fact_id, expected) < 1.0


def test_projection_fidelity_detects_validity_interval_drift() -> None:
    suite = validate_graph_retrieval_suite(SUITE_PATH)
    case = next(item for item in suite.cases if item.case_id == "en-toys-direct-supersession")
    snapshot, labels_by_fact_id, expected = _build_source(case)
    graph = TemporalKnowledgeGraphBuilder(_StaticKnowledgeSource(snapshot)).build(snapshot.lead_id)
    drifted = graph.model_copy(
        update={
            "claims": tuple(
                claim.model_copy(update={"superseded_by_fact_id": uuid4()})
                if claim.status is FactClaimStatus.SUPERSEDED
                else claim
                for claim in graph.claims
            )
        }
    )

    assert _projection_fidelity(drifted, labels_by_fact_id, expected) < 1.0


def test_projection_fidelity_detects_relation_and_payload_drift() -> None:
    suite = validate_graph_retrieval_suite(SUITE_PATH)
    case = next(item for item in suite.cases if item.case_id == "en-toys-direct-supersession")
    snapshot, labels_by_fact_id, expected = _build_source(case)
    graph = TemporalKnowledgeGraphBuilder(_StaticKnowledgeSource(snapshot)).build(snapshot.lead_id)

    without_relations = graph.model_copy(update={"relations": graph.relations[:-1]})
    assert _projection_fidelity(without_relations, labels_by_fact_id, expected) == 0.0

    drifted_payload = graph.model_copy(
        update={
            "claims": tuple(
                claim.model_copy(update={"fact": claim.fact.model_copy(update={"confidence": 0.5})})
                for claim in graph.claims
            )
        }
    )
    assert _projection_fidelity(drifted_payload, labels_by_fact_id, expected) == 0.0


def test_graph_retrieval_suite_rejects_impossible_or_unexercised_cases() -> None:
    payload = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    payload["cases"][1]["claims"][0]["value"] = "toys"
    with pytest.raises(ValidationError, match="must change"):
        GraphRetrievalSuite.model_validate(payload)

    payload = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    payload["cases"] = [item for item in payload["cases"] if not item.get("excluded_claim_ids")]
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
    assert "validated 7 graph retrieval cases" in capsys.readouterr().out
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
