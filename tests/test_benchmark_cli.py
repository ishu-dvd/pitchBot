import json
from pathlib import Path

import pytest

from pitchbot.benchmarks.cli import main


def test_cli_validates_repository_manifests(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate-candidates", "benchmarks/candidates.json"]) == 0
    assert "validated 8 candidates" in capsys.readouterr().out

    assert main(["validate-corpus", "evals/corpora/speech-cases.json"]) == 0
    output = capsys.readouterr().out
    assert "validated 12 items" in output
    assert "canonical_sha256=" in output


def test_cli_scores_transcript_and_reports_environment(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["score-transcript", "--reference", "Hello", "--hypothesis", "hello"]) == 0
    assert json.loads(capsys.readouterr().out) == {"cer": 0.0, "wer": 0.0}

    assert main(["environment"]) == 0
    environment = json.loads(capsys.readouterr().out)
    assert environment["python_version"]
    assert environment["operating_system"]


def test_cli_emits_evaluation_schema(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["evaluation-schema"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert schema["title"] == "EvaluationRun"
    assert schema["properties"]["evaluation_schema_version"]["const"] == "1"

    output = tmp_path / "nested" / "schema.json"
    assert main(["evaluation-schema", "--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8")) == schema
    with pytest.raises(FileExistsError):
        main(["evaluation-schema", "--output", str(output)])
    assert main(["evaluation-schema", "--output", str(output), "--force"]) == 0


def test_manifest_files_do_not_contain_phone_numbers() -> None:
    content = Path("evals/corpora/speech-cases.json").read_text(encoding="utf-8")
    assert "+91" not in content
