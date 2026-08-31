from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from pitchbot.benchmarks.cli import main
from pitchbot.benchmarks.evaluation import render_evaluation_report, validate_evaluation_run
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


def _evaluation_run(
    *,
    metric_value: float = 150,
    case_status: EvaluationCaseStatus = EvaluationCaseStatus.PASSED,
    failure_codes: tuple[str, ...] = (),
    industry: str = "apparel",
) -> EvaluationRun:
    started_at = datetime(2026, 9, 1, tzinfo=UTC)
    return EvaluationRun(
        run_id="local-eval-1",
        status=EvaluationRunStatus.COMPLETED,
        git_revision="1234567",
        suite_id="realtime-conversation",
        suite_version="1.0",
        suite_manifest_sha256="c" * 64,
        corpus_id="conversation-cases",
        corpus_version="1.0",
        corpus_manifest_sha256="a" * 64,
        configuration_sha256="b" * 64,
        evaluation_schema_version="1",
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
                name="latency.reply_p95",
                value=metric_value,
                unit="ms",
                direction=MetricDirection.AT_MOST,
                threshold=200,
            ),
        ),
        cases=(
            EvaluationCaseResult(
                case_id="case-1",
                status=case_status,
                language=LanguageCode.ENGLISH,
                industry=industry,
                persona="direct-buyer",
                duration_ms=100,
                failure_codes=failure_codes,
            ),
        ),
    )


def test_evaluation_gate_status_requires_passing_cases_and_gates() -> None:
    assert _evaluation_run().gates_pass()
    assert not _evaluation_run(metric_value=201).gates_pass()
    assert not _evaluation_run(
        case_status=EvaluationCaseStatus.FAILED,
        failure_codes=("wrong-disposition",),
    ).gates_pass()


def test_evaluation_contract_rejects_inconsistent_or_unbounded_results() -> None:
    with pytest.raises(ValidationError, match="failed evaluation cases require"):
        _evaluation_run(case_status=EvaluationCaseStatus.ERROR)
    with pytest.raises(ValidationError, match="gating metrics require"):
        EvaluationMetric(
            name="latency.reply_p95",
            value=1,
            unit="ms",
            direction=MetricDirection.AT_MOST,
        )
    with pytest.raises(ValidationError, match="evaluation_schema_version"):
        EvaluationRun.model_validate(
            _evaluation_run().model_dump(exclude={"evaluation_schema_version"})
        )
    with pytest.raises(ValidationError, match="running evaluations"):
        _evaluation_run().model_copy(
            update={
                "status": EvaluationRunStatus.RUNNING,
            }
        ).model_validate(_evaluation_run().model_dump() | {"status": "running"})


def test_report_escapes_labels_and_contains_no_script() -> None:
    run = _evaluation_run().model_copy(update={"suite_id": "<img src=x onerror=alert(1)>"})
    report = render_evaluation_report(run)

    assert "&lt;img src=x onerror=alert(1)&gt;" in report
    assert "<img" not in report
    assert "<script" not in report
    assert "default-src 'none'" in report


def test_cli_validates_and_atomically_renders_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifact = tmp_path / "run.json"
    artifact.write_text(_evaluation_run().model_dump_json(indent=2), encoding="utf-8")
    report = tmp_path / "report.html"

    assert main(["validate-evaluation", str(artifact)]) == 0
    assert "artifact-gates=pass" in capsys.readouterr().out
    assert main(["render-evaluation", str(artifact), str(report)]) == 0
    assert report.read_text(encoding="utf-8").startswith("<!doctype html>")
    with pytest.raises(FileExistsError):
        main(["render-evaluation", str(artifact), str(report)])
    assert main(["render-evaluation", str(artifact), str(report), "--force"]) == 0


def test_report_cannot_overwrite_input_artifact_or_alias(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "run.json"
    original = _evaluation_run().model_dump_json(indent=2)
    artifact.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError, match="different files"):
        main(["render-evaluation", str(artifact), str(artifact), "--force"])
    assert artifact.read_text(encoding="utf-8") == original

    alias = tmp_path / "run-alias.json"
    os.link(artifact, alias)
    with pytest.raises(ValueError, match="different files"):
        main(["render-evaluation", str(artifact), str(alias), "--force"])
    assert artifact.read_text(encoding="utf-8") == original


def test_evaluation_loader_rejects_nonstandard_constants(tmp_path: Path) -> None:
    artifact = tmp_path / "run.json"
    artifact.write_text(json.dumps(_evaluation_run().model_dump(mode="json")), encoding="utf-8")
    content = artifact.read_text(encoding="utf-8").replace('"value": 150.0', '"value": NaN')
    artifact.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="invalid JSON constant"):
        validate_evaluation_run(artifact)


def test_committed_evaluation_schema_matches_contract_and_excludes_raw_content() -> None:
    schema = json.loads(Path("evals/schemas/evaluation-run-v1.json").read_text(encoding="utf-8"))

    assert schema == EvaluationRun.model_json_schema()
    assert "evaluation_schema_version" in schema["required"]
    assert len(schema["allOf"]) == 3
    assert len(schema["$defs"]["EvaluationMetric"]["allOf"]) == 1
    case_schema = schema["$defs"]["EvaluationCaseResult"]
    assert case_schema["allOf"][0]["else"]["required"] == ["failure_codes"]
    assert case_schema["properties"]["tags"]["items"]["$ref"].endswith("/EvaluationLabel")
    assert case_schema["properties"]["failure_codes"]["items"]["$ref"].endswith("/EvaluationLabel")
    assert schema["$defs"]["EvaluationLabel"]["pattern"].endswith("{0,63}$")
    assert (
        schema["$defs"]["EvaluationHardwareProfile"]["properties"]["physical_memory_bytes"][
            "anyOf"
        ][0]["maximum"]
        == 2**63 - 1
    )
    serialized = json.dumps(schema).casefold()
    assert '"transcript"' not in serialized
    assert '"prompt"' not in serialized
    assert '"audio"' not in serialized
    assert '"contact"' not in serialized
    assert '"memory_note"' not in serialized
