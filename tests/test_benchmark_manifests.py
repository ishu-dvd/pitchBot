from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from pitchbot.benchmarks.manifest import (
    canonical_manifest_sha256,
    validate_candidate_registry,
    validate_corpus_manifest,
)
from pitchbot.benchmarks.models import (
    BenchmarkKind,
    BenchmarkResult,
    HardwareProfile,
)


def test_repository_candidate_and_corpus_manifests_validate() -> None:
    candidates = validate_candidate_registry(Path("benchmarks/candidates.json"))
    corpus_path = Path("evals/corpora/speech-cases.json")
    corpus = validate_corpus_manifest(corpus_path)

    assert len(candidates.candidates) == 8
    assert len(corpus.items) == 12
    assert all(item.availability.value == "planned" for item in corpus.items)
    assert len(canonical_manifest_sha256(corpus_path)) == 64


def test_available_audio_requires_matching_checksum(tmp_path: Path) -> None:
    audio = tmp_path / "synthetic.wav"
    audio.write_bytes(b"synthetic-audio-fixture")
    digest = hashlib.sha256(audio.read_bytes()).hexdigest()
    manifest = {
        "manifest_id": "test",
        "version": "1",
        "purpose": "test",
        "items": [
            {
                "item_id": "synthetic-audio",
                "language": "en",
                "source_type": "synthetic",
                "availability": "available",
                "reference_text": "synthetic speech",
                "tags": ["test"],
                "audio_path": "synthetic.wav",
                "audio_sha256": digest,
                "license_or_consent_ref": "test-generated",
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    assert validate_corpus_manifest(path).items[0].audio_sha256 == digest
    audio.write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum mismatch"):
        validate_corpus_manifest(path)


def test_available_audio_cannot_escape_manifest_directory(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.wav"
    outside.write_bytes(b"outside")
    manifest = {
        "manifest_id": "test",
        "version": "1",
        "purpose": "test",
        "items": [
            {
                "item_id": "escaped-audio",
                "language": "en",
                "source_type": "synthetic",
                "availability": "available",
                "reference_text": "synthetic speech",
                "tags": [],
                "audio_path": "../outside.wav",
                "audio_sha256": hashlib.sha256(b"outside").hexdigest(),
                "license_or_consent_ref": "test-generated",
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="escapes"):
        validate_corpus_manifest(path)


def test_benchmark_result_cannot_claim_placeholder_measurement() -> None:
    values = {
        "measurement_source": "placeholder",
        "run_id": "run-1",
        "kind": BenchmarkKind.STT,
        "candidate_id": "candidate",
        "candidate_revision": "1234567",
        "model_or_voice_id": "model",
        "model_or_voice_license": "test-only",
        "corpus_manifest_sha256": "a" * 64,
        "hardware": HardwareProfile(
            operating_system="test",
            architecture="test",
            python_version="3.12",
            processor="test",
        ),
        "configuration": {},
        "metrics": {},
        "measured_at": datetime.now(UTC),
    }
    with pytest.raises(ValidationError):
        BenchmarkResult.model_validate(values)


@pytest.mark.parametrize(
    ("revision", "license_name", "metrics"),
    [
        ("unknown", "MIT", {"wer": 0.1}),
        ("1234567", "NOASSERTION", {"wer": 0.1}),
        ("1234567", "MIT", {}),
        ("1234567", "MIT", {"wer": float("nan")}),
    ],
)
def test_measured_result_requires_verified_finite_evidence(
    revision: str,
    license_name: str,
    metrics: dict[str, float],
) -> None:
    with pytest.raises(ValidationError):
        BenchmarkResult(
            run_id="run-1",
            kind=BenchmarkKind.STT,
            candidate_id="candidate",
            candidate_revision=revision,
            model_or_voice_id="model",
            model_or_voice_license=license_name,
            corpus_manifest_sha256="a" * 64,
            hardware=HardwareProfile(
                operating_system="test",
                architecture="test",
                python_version="3.12",
                processor="test",
            ),
            configuration={},
            metrics=metrics,
        )


def test_planned_audio_cannot_smuggle_unverified_path(tmp_path: Path) -> None:
    manifest = {
        "manifest_id": "test",
        "version": "1",
        "purpose": "test",
        "items": [
            {
                "item_id": "planned-audio",
                "language": "en",
                "source_type": "synthetic",
                "availability": "planned",
                "reference_text": "synthetic speech",
                "tags": [],
                "audio_path": "unverified.wav",
                "audio_sha256": "a" * 64,
                "license_or_consent_ref": "test-generated",
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValidationError, match="planned audio"):
        validate_corpus_manifest(path)


def test_manifest_rejects_non_standard_json_constants(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text('{"manifest_id":NaN}', encoding="utf-8")

    with pytest.raises(ValueError, match="invalid JSON constant"):
        validate_corpus_manifest(path)
