from __future__ import annotations

import itertools
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from clocks import SteppingClock
from pydantic import ValidationError

from pitchbot.adapters.contracts import AudioChunk, VoiceActivity, VoiceActivityDetector
from pitchbot.adapters.errors import PermanentAdapterError
from pitchbot.adapters.mocks import MockVoiceActivityDetector
from pitchbot.benchmarks.audio import (
    ClipSpec,
    SegmentKind,
    SegmentSpec,
    generate_clip,
    is_speech_kind,
)
from pitchbot.benchmarks.cli import main
from pitchbot.benchmarks.metrics import Interval, vad_precision_recall_f1
from pitchbot.benchmarks.models import EvaluationMetric, MetricDirection
from pitchbot.benchmarks.speech import (
    VadSuite,
    run_speech_evaluation,
    speech_gates_pass,
    validate_speech_suite,
)

SUITE_PATH = Path("evals/corpora/vad-cases.json")


class _ConstantDetector(VoiceActivityDetector):
    def __init__(self, value: bool) -> None:
        self._value = value

    def detect(self, frame: AudioChunk) -> VoiceActivity:
        return VoiceActivity(is_speech=self._value, confidence=1.0, sequence=frame.sequence)


class _ErrorDetector(VoiceActivityDetector):
    def detect(self, frame: AudioChunk) -> VoiceActivity:
        raise PermanentAdapterError("detector unavailable")


def _hash_for(case: dict[str, Any], frame_ms: int, sample_rate_hz: int) -> str:
    spec = ClipSpec(
        seed=case["seed"],
        segments=tuple(
            SegmentSpec(SegmentKind(segment["kind"]), segment["duration_ms"])
            for segment in case["segments"]
        ),
        sample_rate_hz=sample_rate_hz,
        frame_ms=frame_ms,
    )
    return generate_clip(spec).sha256


def _case(
    *,
    case_id: str,
    language: str,
    vertical: str,
    condition: str,
    persona: str,
    seed: int,
    pairs: list[tuple[SegmentKind, int]],
    tags: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "language": language,
        "vertical": vertical,
        "condition": condition,
        "persona": persona,
        "seed": seed,
        "segments": [{"kind": kind.value, "duration_ms": duration} for kind, duration in pairs],
        "tags": list(tags),
    }


def _write_suite(
    tmp_path: Path,
    cases: list[dict[str, Any]],
    *,
    frame_ms: int = 20,
    sample_rate_hz: int = 16_000,
    speech_threshold_bytes: int = 512,
    min_f1: float = 0.85,
    fill_hashes: bool = True,
) -> Path:
    prepared: list[dict[str, Any]] = []
    for case in cases:
        item = dict(case)
        if fill_hashes:
            item["audio_sha256"] = _hash_for(item, frame_ms, sample_rate_hz)
        prepared.append(item)
    suite = {
        "suite_id": "fixture-vad",
        "version": "1",
        "corpus_id": "fixture-corpus",
        "corpus_version": "1",
        "frame_ms": frame_ms,
        "sample_rate_hz": sample_rate_hz,
        "speech_threshold_bytes": speech_threshold_bytes,
        "min_f1": min_f1,
        "cases": prepared,
    }
    path = tmp_path / "vad-fixture.json"
    path.write_text(json.dumps(suite), encoding="utf-8")
    return path


def _clear() -> list[tuple[SegmentKind, int]]:
    return [
        (SegmentKind.SILENCE, 100),
        (SegmentKind.SPEECH, 300),
        (SegmentKind.SILENCE, 100),
    ]


def _metric(run_metrics: Sequence[EvaluationMetric], name: str) -> float:
    return next(metric.value for metric in run_metrics if metric.name == name)


def test_reviewed_vad_suite_covers_structural_language_and_vertical_slices() -> None:
    suite = validate_speech_suite(SUITE_PATH)

    assert len(suite.cases) == 8
    assert {case.language.value for case in suite.cases} == {"en", "hi", "mixed"}
    assert {case.vertical for case in suite.cases} == {
        "apparel",
        "books",
        "food",
        "import-export",
        "plastics",
        "toys",
    }
    assert {case.condition for case in suite.cases} == {
        "background-noise",
        "barge-in",
        "clear",
        "crosstalk",
        "inter-word-pause",
        "leading-silence",
        "long-silence",
        "noise-burst",
    }
    # Every reviewed case carries real speech to detect and at least one non-speech region.
    for case in suite.cases:
        kinds = [segment.kind for segment in case.segments]
        assert any(is_speech_kind(kind) for kind in kinds)
        assert any(not is_speech_kind(kind) for kind in kinds)


def test_validate_speech_suite_verifies_regenerated_hashes(tmp_path: Path) -> None:
    path = _write_suite(
        tmp_path,
        [
            _case(
                case_id="en-apparel-clear",
                language="en",
                vertical="apparel",
                condition="clear",
                persona="buyer",
                seed=42,
                pairs=_clear(),
            )
        ],
    )
    assert validate_speech_suite(path).cases[0].case_id == "en-apparel-clear"

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["cases"][0]["audio_sha256"] = "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="synthetic audio checksum mismatch"):
        validate_speech_suite(path)


def test_run_speech_scores_the_reviewed_corpus_and_gates_pass() -> None:
    run = run_speech_evaluation(SUITE_PATH, run_id="vad-test", git_revision="abcdef1")

    assert run.gates_pass() is True
    assert len(run.cases) == 8
    assert all(case.status.value == "passed" for case in run.cases)
    metric_names = {metric.name for metric in run.metrics}
    assert {
        "speech.vad_mean_f1",
        "speech.vad_min_f1",
        "speech.vad_f1.lang.en",
        "speech.vad_f1.lang.hi",
        "speech.vad_f1.lang.mixed",
        "speech.vad_f1.cond.noise-burst",
        "speech.vad_f1.vert.import-export",
        "speech.mean_real_time_factor",
        "speech.peak_python_kib",
    } <= metric_names
    # Structural-only: nothing STT/TTS is ever emitted.
    assert all("wer" not in name and "cer" not in name for name in metric_names)
    assert all("tts" not in name and "naturalness" not in name for name in metric_names)


def test_run_speech_artifact_is_labeled_structural_and_omits_generator_inputs() -> None:
    run = run_speech_evaluation(SUITE_PATH, run_id="vad-test", git_revision="abcdef1")

    assert run.suite_id == "pitchbot-vad-structural"
    assert run.corpus_id == "synthetic-vad-structural"
    artifact = run.model_dump_json()
    assert '"seed"' not in artifact
    assert '"segments"' not in artifact
    assert '"audio_sha256"' not in artifact
    assert len(run.corpus_manifest_sha256) == 64


def test_bad_detector_fails_the_gate() -> None:
    run = run_speech_evaluation(
        SUITE_PATH,
        run_id="vad-bad",
        git_revision="abcdef1",
        detector_factory=lambda: _ConstantDetector(False),
    )

    assert run.gates_pass() is False
    assert all(case.status.value == "failed" for case in run.cases)
    assert all(_metric(case.metrics, "speech.vad_f1") == 0.0 for case in run.cases)
    assert all("vad-f1-below-threshold" in case.failure_codes for case in run.cases)


def test_speech_gate_is_suite_aware_unlike_the_shared_fail_open_gate() -> None:
    suite = validate_speech_suite(SUITE_PATH)
    run = run_speech_evaluation(SUITE_PATH, run_id="vad-gate", git_revision="abcdef1")

    assert speech_gates_pass(run, suite) is True

    # Strip the run down to a single unrelated passing metric and drop every case metric.
    stripped = run.model_copy(
        update={
            "metrics": (
                EvaluationMetric(
                    name="unrelated.pass",
                    value=1.0,
                    unit="ratio",
                    direction=MetricDirection.AT_LEAST,
                    threshold=0.0,
                ),
            ),
            "cases": tuple(case.model_copy(update={"metrics": ()}) for case in run.cases),
        }
    )

    # The shared, non-suite-aware gate is fail-open: it passes on an unrelated metric.
    assert stripped.gates_pass() is True
    # run-speech's suite-aware gate fails closed because the required VAD metrics are absent.
    assert speech_gates_pass(stripped, suite) is False


def test_speech_gate_rejects_a_failed_case_and_a_foreign_run() -> None:
    suite = validate_speech_suite(SUITE_PATH)
    run = run_speech_evaluation(
        SUITE_PATH,
        run_id="vad-bad",
        git_revision="abcdef1",
        detector_factory=lambda: _ConstantDetector(False),
    )
    assert speech_gates_pass(run, suite) is False

    good = run_speech_evaluation(SUITE_PATH, run_id="vad-ok", git_revision="abcdef1")
    foreign = good.model_copy(update={"suite_id": "someone-elses-suite"})
    assert speech_gates_pass(foreign, suite) is False


def test_always_speech_detector_fails_cases_that_contain_silence(tmp_path: Path) -> None:
    path = _write_suite(
        tmp_path,
        [
            _case(
                case_id="mostly-silence",
                language="hi",
                vertical="toys",
                condition="long-silence",
                persona="owner",
                seed=9,
                pairs=[
                    (SegmentKind.SPEECH, 100),
                    (SegmentKind.SILENCE, 600),
                    (SegmentKind.SPEECH, 100),
                ],
            )
        ],
    )
    run = run_speech_evaluation(
        path,
        run_id="vad-loud",
        git_revision="abcdef1",
        detector_factory=lambda: _ConstantDetector(True),
    )

    assert run.gates_pass() is False
    assert run.cases[0].status.value == "failed"
    assert _metric(run.cases[0].metrics, "speech.vad_recall") == pytest.approx(1.0)
    assert _metric(run.cases[0].metrics, "speech.vad_precision") < 0.85


def test_per_slice_metric_isolates_a_single_language_regression(tmp_path: Path) -> None:
    cases = [
        _case(
            case_id="en-first",
            language="en",
            vertical="apparel",
            condition="clear",
            persona="buyer",
            seed=1,
            pairs=_clear(),
        ),
        _case(
            case_id="hi-regressed",
            language="hi",
            vertical="toys",
            condition="clear",
            persona="owner",
            seed=2,
            pairs=_clear(),
        ),
        _case(
            case_id="en-second",
            language="en",
            vertical="books",
            condition="clear",
            persona="reseller",
            seed=3,
            pairs=_clear(),
        ),
    ]
    path = _write_suite(tmp_path, cases)
    counter = itertools.count()

    def factory() -> VoiceActivityDetector:
        index = next(counter)
        if index == 1:  # only the Hindi case gets a broken detector
            return _ConstantDetector(False)
        return MockVoiceActivityDetector(speech_threshold_bytes=512)

    run = run_speech_evaluation(
        path, run_id="vad-slice", git_revision="abcdef1", detector_factory=factory
    )

    assert run.gates_pass() is False
    assert _metric(run.metrics, "speech.vad_f1.lang.hi") == 0.0
    assert _metric(run.metrics, "speech.vad_f1.lang.en") == pytest.approx(1.0)
    statuses = {case.case_id: case.status.value for case in run.cases}
    assert statuses == {"en-first": "passed", "hi-regressed": "failed", "en-second": "passed"}


def test_real_time_factor_gate_rejects_a_too_heavy_candidate(tmp_path: Path) -> None:
    path = _write_suite(
        tmp_path,
        [
            _case(
                case_id="clear",
                language="en",
                vertical="apparel",
                condition="clear",
                persona="buyer",
                seed=1,
                pairs=_clear(),
            )
        ],
    )
    # A deterministic surrogate for a slow detector: each case reports 2 s of processing
    # over ~0.5 s of audio, so the real-time factor is ~4 and must be rejected.
    run = run_speech_evaluation(
        path,
        run_id="vad-heavy",
        git_revision="abcdef1",
        clock=SteppingClock(2_000_000_000),
        max_real_time_factor=1.0,
    )

    assert run.gates_pass() is False
    mean_rtf = next(m for m in run.metrics if m.name == "speech.mean_real_time_factor")
    assert mean_rtf.direction.value == "at-most"
    assert mean_rtf.value > 1.0
    assert mean_rtf.meets_threshold() is False


def test_light_detector_passes_a_generous_real_time_factor_gate(tmp_path: Path) -> None:
    path = _write_suite(
        tmp_path,
        [
            _case(
                case_id="clear",
                language="en",
                vertical="apparel",
                condition="clear",
                persona="buyer",
                seed=1,
                pairs=_clear(),
            )
        ],
    )
    run = run_speech_evaluation(
        path,
        run_id="vad-light",
        git_revision="abcdef1",
        clock=SteppingClock(0),
        max_real_time_factor=1.0,
    )

    assert run.gates_pass() is True
    assert _metric(run.metrics, "speech.mean_real_time_factor") == 0.0


def test_degenerate_all_silence_case_scores_as_correct_silence(tmp_path: Path) -> None:
    path = _write_suite(
        tmp_path,
        [
            _case(
                case_id="all-silence",
                language="en",
                vertical="apparel",
                condition="long-silence",
                persona="buyer",
                seed=1,
                pairs=[(SegmentKind.SILENCE, 400)],
            )
        ],
    )
    run = run_speech_evaluation(path, run_id="vad-silence", git_revision="abcdef1")

    assert run.gates_pass() is True
    assert run.cases[0].status.value == "passed"
    assert _metric(run.cases[0].metrics, "speech.vad_f1") == 1.0


def test_detector_error_marks_the_case_as_error(tmp_path: Path) -> None:
    path = _write_suite(
        tmp_path,
        [
            _case(
                case_id="clear",
                language="en",
                vertical="apparel",
                condition="clear",
                persona="buyer",
                seed=1,
                pairs=_clear(),
            )
        ],
    )
    run = run_speech_evaluation(
        path,
        run_id="vad-error",
        git_revision="abcdef1",
        detector_factory=_ErrorDetector,
    )

    assert run.gates_pass() is False
    assert run.cases[0].status.value == "error"
    assert "vad-detector-error" in run.cases[0].failure_codes


def test_adding_a_vertical_slice_is_a_data_only_edit(tmp_path: Path) -> None:
    payload = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    new_case = _case(
        case_id="en-electronics-clear",
        language="en",
        vertical="electronics",
        condition="clear",
        persona="buyer",
        seed=2024,
        pairs=_clear(),
    )
    new_case["audio_sha256"] = _hash_for(new_case, payload["frame_ms"], payload["sample_rate_hz"])
    payload["cases"].append(new_case)
    path = tmp_path / "vad-extended.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    run = run_speech_evaluation(path, run_id="vad-extended", git_revision="abcdef1")

    assert run.gates_pass() is True
    assert _metric(run.metrics, "speech.vad_f1.vert.electronics") == pytest.approx(1.0)
    assert any(case.case_id == "en-electronics-clear" for case in run.cases)


def test_vad_metric_matches_hand_worked_overlap_cases() -> None:
    # Perfect overlap.
    assert vad_precision_recall_f1([Interval(0, 2)], [Interval(0, 2)]) == (1.0, 1.0, 1.0)
    # Half overlap: 1 s shared of a 2 s reference and a 2 s prediction.
    assert vad_precision_recall_f1([Interval(0, 2)], [Interval(1, 3)]) == (0.5, 0.5, 0.5)
    # Missed all speech: prediction empty -> recall 0.
    assert vad_precision_recall_f1([Interval(0, 2)], []) == (0.0, 0.0, 0.0)
    # False positive on pure silence: reference empty -> precision 0.
    assert vad_precision_recall_f1([], [Interval(0, 2)]) == (0.0, 0.0, 0.0)
    # Correct all-silence: both empty -> perfect.
    assert vad_precision_recall_f1([], []) == (1.0, 1.0, 1.0)
    # Prediction covers reference plus extra: recall 1, precision 2/3.
    precision, recall, f1 = vad_precision_recall_f1([Interval(1, 2)], [Interval(0, 3)])
    assert recall == 1.0
    assert precision == pytest.approx(1 / 3)
    assert f1 == pytest.approx(0.5)


def test_speech_suite_rejects_structurally_invalid_manifests(tmp_path: Path) -> None:
    payload = json.loads(
        _write_suite(
            tmp_path,
            [
                _case(
                    case_id="clear",
                    language="en",
                    vertical="apparel",
                    condition="clear",
                    persona="buyer",
                    seed=1,
                    pairs=_clear(),
                )
            ],
        ).read_text(encoding="utf-8")
    )

    empty = dict(payload, cases=[])
    with pytest.raises(ValidationError):
        VadSuite.model_validate(empty)

    unaligned = json.loads(json.dumps(payload))
    unaligned["cases"][0]["segments"][0]["duration_ms"] = 30
    with pytest.raises(ValidationError, match="multiple of frame_ms"):
        VadSuite.model_validate(unaligned)

    duplicate = json.loads(json.dumps(payload))
    duplicate["cases"].append(json.loads(json.dumps(duplicate["cases"][0])))
    with pytest.raises(ValidationError, match="unique"):
        VadSuite.model_validate(duplicate)


def test_speech_cli_validates_runs_and_renders(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifact = tmp_path / "vad-run.json"
    report = tmp_path / "vad-report.html"

    assert main(["validate-speech-suite", str(SUITE_PATH)]) == 0
    assert "validated 8 vad cases" in capsys.readouterr().out
    assert (
        main(
            [
                "run-speech",
                str(SUITE_PATH),
                str(artifact),
                "--run-id",
                "vad-cli-run",
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
    assert "speech.vad_mean_f1" in report.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        main(
            [
                "run-speech",
                str(SUITE_PATH),
                str(artifact),
                "--run-id",
                "vad-cli-run",
                "--git-revision",
                "abcdef1",
            ]
        )


def test_speech_cli_rejects_a_corrupted_corpus(tmp_path: Path) -> None:
    payload = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    payload["cases"][0]["audio_sha256"] = "0" * 64
    path = tmp_path / "vad-corrupt.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="checksum mismatch"):
        main(["validate-speech-suite", str(path)])


def test_run_speech_cli_exit_code_reflects_the_gate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _case(
        case_id="en-apparel-clear",
        language="en",
        vertical="apparel",
        condition="clear",
        persona="buyer",
        seed=1,
        pairs=_clear(),
    )
    passing = _write_suite(tmp_path, [dict(case)])
    passing_artifact = tmp_path / "vad-pass.json"
    assert (
        main(
            [
                "run-speech",
                str(passing),
                str(passing_artifact),
                "--run-id",
                "vad-pass",
                "--git-revision",
                "abcdef1",
            ]
        )
        == 0
    )
    assert "artifact-gates=pass" in capsys.readouterr().out

    # A mis-tuned threshold above the 600-byte voiced frame size: the mock never detects
    # speech, so the gate must fail and the process must exit non-zero - not print "fail"
    # and return 0 like the shared runners currently do.
    failing = _write_suite(tmp_path, [dict(case)], speech_threshold_bytes=1000)
    failing_artifact = tmp_path / "vad-fail.json"
    assert (
        main(
            [
                "run-speech",
                str(failing),
                str(failing_artifact),
                "--run-id",
                "vad-fail",
                "--git-revision",
                "abcdef1",
            ]
        )
        == 1
    )
    assert "artifact-gates=fail" in capsys.readouterr().out
    # The failing run is still a well-formed, completed artifact.
    assert main(["validate-evaluation", str(failing_artifact)]) == 0
