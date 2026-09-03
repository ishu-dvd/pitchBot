"""Tests for the optional py-webrtcvad adapter and the real-PCM benchmark path.

Two properties are load-bearing here and are asserted directly rather than assumed.

*The dependency is optional.* Every test in this file either runs with ``webrtcvad``
absent or is skipped by :data:`requires_webrtcvad`, and the module-level import of
``pitchbot.adapters.webrtc_vad`` is itself part of the assertion - it must succeed in an
environment that has never seen the extension.

*Nothing is downloaded.* No test reaches the network. The WebRTC detector carries no model
weights, so a test that constructs it is offline by construction; there is no cache to
warm and nothing to fetch.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pitchbot.adapters.contracts import AudioChunk, VoiceActivity, VoiceActivityDetector
from pitchbot.adapters.errors import PermanentAdapterError
from pitchbot.adapters.webrtc_vad import (
    ALGORITHM,
    DECISION_CONFIDENCE,
    INSTALL_HINT,
    LICENSE,
    MODEL_WEIGHTS,
    PROVIDER_ID,
    SUPPORTED_FRAME_MS,
    SUPPORTED_SAMPLE_RATES_HZ,
    WEBRTC_VAD_AVAILABLE,
    WebRtcVadMode,
    WebRtcVoiceActivityDetector,
    frame_duration_ms,
    installed_distribution,
    require_webrtcvad,
)
from pitchbot.benchmarks.audio import ClipSpec, SegmentKind, SegmentSpec, generate_clip
from pitchbot.benchmarks.cli import main
from pitchbot.benchmarks.gates import evaluate_gates
from pitchbot.benchmarks.speech import (
    DetectorProfile,
    VadFrameSource,
    load_speech_suite,
    mock_detector_profile,
    run_speech_evaluation,
    speech_gates_pass,
    webrtc_detector_profile,
)

requires_webrtcvad = pytest.mark.skipif(
    not WEBRTC_VAD_AVAILABLE,
    reason=f"webrtcvad is not installed; install the optional extra: {INSTALL_HINT}",
)

CORPUS = Path("evals/corpora/vad-cases.json")
_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def _pcm_chunk(
    samples: list[int],
    *,
    sequence: int = 0,
    sample_rate_hz: int = 16_000,
) -> AudioChunk:
    return AudioChunk(
        data=struct.pack(f"<{len(samples)}h", *samples),
        captured_at=_EPOCH,
        sequence=sequence,
        sample_rate_hz=sample_rate_hz,
    )


class _ConstantDetector(VoiceActivityDetector):
    """A detector that records the frames it saw, so frame *source* can be asserted."""

    def __init__(self, is_speech: bool) -> None:
        self._is_speech = is_speech
        self.frame_lengths: list[int] = []

    def detect(self, frame: AudioChunk) -> VoiceActivity:
        self.frame_lengths.append(len(frame.data))
        return VoiceActivity(
            is_speech=self._is_speech,
            confidence=0.5,
            sequence=frame.sequence,
        )


# --------------------------------------------------------------------------------------
# The dependency is optional.
# --------------------------------------------------------------------------------------


def test_adapter_module_imports_without_the_optional_extension() -> None:
    """Importing the adapter must never require the extension, so callers can probe.

    This is the property that keeps ``webrtcvad`` out of the core import graph: the module
    resolves availability at import and exposes it, rather than raising.
    """

    assert isinstance(WEBRTC_VAD_AVAILABLE, bool)
    assert PROVIDER_ID == "py-webrtcvad"
    assert ALGORITHM == "webrtc-gmm-vad"
    assert MODEL_WEIGHTS == "none"
    assert "MIT" in LICENSE and "BSD-3-Clause" in LICENSE


def test_core_package_import_graph_excludes_the_optional_provider() -> None:
    """``pitchbot.adapters`` must not re-export the provider, or absence becomes fatal."""

    import pitchbot.adapters as adapters

    assert "WebRtcVoiceActivityDetector" not in adapters.__all__
    assert not hasattr(adapters, "webrtcvad")


def test_provider_is_declared_as_an_extra_and_never_as_a_runtime_dependency() -> None:
    """The dependency must be opt-in. A runtime requirement would break the north star."""

    import tomllib

    manifest = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    project = manifest["project"]
    runtime = " ".join(project["dependencies"])
    assert "webrtcvad" not in runtime
    extras = project["optional-dependencies"]
    assert any("webrtcvad-wheels" in entry for entry in extras["webrtc-vad"])
    assert not any("webrtcvad" in entry for entry in extras["dev"])


def test_missing_dependency_fails_with_the_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Absence is a permanent adapter error naming the extra, not an ``ImportError``."""

    monkeypatch.setattr("pitchbot.adapters.webrtc_vad._MODULE", None)
    with pytest.raises(PermanentAdapterError) as error:
        require_webrtcvad()
    assert INSTALL_HINT in str(error.value)
    with pytest.raises(PermanentAdapterError):
        WebRtcVoiceActivityDetector()


def test_profile_builder_reports_not_installed_without_the_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A profile can be described with the extension absent; only the factory needs it."""

    monkeypatch.setattr("pitchbot.adapters.webrtc_vad.installed_distribution", lambda: None)
    suite = load_speech_suite(CORPUS)
    profile = webrtc_detector_profile(suite, mode=2)
    configuration = profile.as_configuration()
    assert configuration["package_version"] == "not-installed"
    assert configuration["frame_source"] == VadFrameSource.PCM.value


# --------------------------------------------------------------------------------------
# Frame constraints imposed by the real library.
# --------------------------------------------------------------------------------------


def test_frame_duration_helper_matches_the_corpus_frame() -> None:
    assert frame_duration_ms(640, 16_000) == pytest.approx(20.0)
    assert frame_duration_ms(320, 16_000) == pytest.approx(10.0)
    assert SUPPORTED_FRAME_MS == (10, 20, 30)
    assert SUPPORTED_SAMPLE_RATES_HZ == (8_000, 16_000, 32_000, 48_000)


@requires_webrtcvad
@pytest.mark.parametrize("sample_rate_hz", [11_025, 22_050, 44_100])
def test_unsupported_sample_rate_is_refused_rather_than_resampled(sample_rate_hz: int) -> None:
    """Resampling would change what is being measured, so it is refused instead."""

    with pytest.raises(PermanentAdapterError) as error:
        WebRtcVoiceActivityDetector(sample_rate_hz=sample_rate_hz)
    assert str(sample_rate_hz) in str(error.value)


@requires_webrtcvad
def test_unsupported_mode_is_refused() -> None:
    with pytest.raises(PermanentAdapterError):
        WebRtcVoiceActivityDetector(mode=4)


@requires_webrtcvad
def test_frame_carrying_a_different_sample_rate_is_refused() -> None:
    detector = WebRtcVoiceActivityDetector(sample_rate_hz=16_000)
    with pytest.raises(PermanentAdapterError) as error:
        detector.detect(_pcm_chunk([0] * 320, sample_rate_hz=8_000))
    assert "8000" in str(error.value)


@requires_webrtcvad
@pytest.mark.parametrize("sample_count", [0, 1, 100, 319, 321, 640])
def test_frame_of_the_wrong_duration_is_refused(sample_count: int) -> None:
    """10/20/30 ms are the only durations WebRTC accepts; anything else is invalid input."""

    detector = WebRtcVoiceActivityDetector(sample_rate_hz=16_000)
    with pytest.raises(PermanentAdapterError) as error:
        detector.detect(_pcm_chunk([0] * sample_count))
    assert "bytes of mono 16-bit PCM" in str(error.value)


@requires_webrtcvad
def test_encoded_length_proxy_frames_are_refused_as_input() -> None:
    """The generator's byte-length proxy frames are not PCM, and must not be scored as if.

    This is the incompatibility that makes ``VadFrameSource`` necessary: a truncated
    variable-bitrate stand-in has no fixed duration, so feeding it to an acoustic detector
    is invalid input rather than a lower score.
    """

    clip = generate_clip(
        ClipSpec(seed=7, segments=(SegmentSpec(SegmentKind.SPEECH, 100),), frame_ms=20)
    )
    detector = WebRtcVoiceActivityDetector()
    proxy_frame = clip.frames[0]
    assert len(proxy_frame) != len(clip.pcm_frames[0])
    with pytest.raises(PermanentAdapterError):
        detector.detect(
            AudioChunk(data=proxy_frame, captured_at=_EPOCH, sequence=0, sample_rate_hz=16_000)
        )


# --------------------------------------------------------------------------------------
# Detection behaviour with the dependency present.
# --------------------------------------------------------------------------------------


@requires_webrtcvad
@pytest.mark.parametrize("frame_ms", [10, 20, 30])
def test_detects_energetic_audio_and_rejects_digital_silence(frame_ms: int) -> None:
    samples_per_frame = 16_000 * frame_ms // 1_000
    clip = generate_clip(
        ClipSpec(
            seed=11,
            segments=(
                SegmentSpec(SegmentKind.SPEECH, frame_ms * 10),
                SegmentSpec(SegmentKind.SILENCE, frame_ms * 10),
            ),
            frame_ms=frame_ms,
        )
    )
    detector = WebRtcVoiceActivityDetector(mode=WebRtcVadMode.AGGRESSIVE)
    decisions = [
        detector.detect(
            AudioChunk(data=frame, captured_at=_EPOCH, sequence=index, sample_rate_hz=16_000)
        )
        for index, frame in enumerate(clip.pcm_frames)
    ]
    assert len(decisions) == 20
    assert all(len(frame) == samples_per_frame * 2 for frame in clip.pcm_frames)
    assert detector.frames_seen == 20
    assert all(item.is_speech for item in decisions[:10])
    # WebRTC applies a speech-tail hangover by design, so only the settled tail is asserted.
    assert not decisions[-1].is_speech


@requires_webrtcvad
def test_confidence_is_a_fixed_constant_and_carries_no_information() -> None:
    """webrtcvad exposes no posterior, so a varying confidence would be fabricated."""

    clip = generate_clip(
        ClipSpec(
            seed=13,
            segments=(
                SegmentSpec(SegmentKind.SPEECH, 100),
                SegmentSpec(SegmentKind.SILENCE, 100),
            ),
            frame_ms=20,
        )
    )
    detector = WebRtcVoiceActivityDetector()
    confidences = {
        detector.detect(
            AudioChunk(data=frame, captured_at=_EPOCH, sequence=index, sample_rate_hz=16_000)
        ).confidence
        for index, frame in enumerate(clip.pcm_frames)
    }
    assert confidences == {DECISION_CONFIDENCE}


@requires_webrtcvad
def test_sequence_is_preserved_from_the_frame() -> None:
    detector = WebRtcVoiceActivityDetector()
    activity = detector.detect(_pcm_chunk([0] * 320, sequence=41))
    assert activity.sequence == 41


@requires_webrtcvad
def test_provenance_reports_the_installed_distribution_and_reviewed_license() -> None:
    detector = WebRtcVoiceActivityDetector(mode=2)
    provenance = detector.provenance()
    distribution = installed_distribution()
    assert distribution is not None
    package, version = distribution
    assert package in {"webrtcvad-wheels", "webrtcvad"}
    assert provenance.package == package
    assert provenance.package_version == version
    assert provenance.license == LICENSE
    assert provenance.model_weights == "none"
    assert provenance.mode == 2


@requires_webrtcvad
def test_detector_satisfies_the_unchanged_contract() -> None:
    detector: VoiceActivityDetector = WebRtcVoiceActivityDetector()
    assert isinstance(detector.detect(_pcm_chunk([0] * 320)), VoiceActivity)


# --------------------------------------------------------------------------------------
# Runner integration. These do not need the extension.
# --------------------------------------------------------------------------------------


def test_pcm_frames_are_a_slice_of_the_hashed_audio() -> None:
    """The PCM view must be the same bytes the committed ``audio_sha256`` describes."""

    clip = generate_clip(
        ClipSpec(
            seed=17,
            segments=(
                SegmentSpec(SegmentKind.SPEECH, 60),
                SegmentSpec(SegmentKind.SILENCE, 40),
            ),
            frame_ms=20,
        )
    )
    assert b"".join(clip.pcm_frames) == clip.pcm
    assert len(clip.pcm_frames) == len(clip.frames)
    assert {len(frame) for frame in clip.pcm_frames} == {640}


def _profile_with(frame_source: VadFrameSource, detector: _ConstantDetector) -> DetectorProfile:
    return DetectorProfile(
        detector_id="probe",
        algorithm="probe",
        package="probe",
        package_version="1",
        license="probe",
        model_weights="none",
        frame_source=frame_source,
        factory=lambda: detector,
    )


@pytest.mark.parametrize(
    ("frame_source", "expected_lengths"),
    [(VadFrameSource.PCM, {640}), (VadFrameSource.ENCODED_LENGTH_PROXY, {48, 600})],
)
def test_frame_source_selects_what_the_detector_receives(
    frame_source: VadFrameSource,
    expected_lengths: set[int],
    tmp_path: Path,
) -> None:
    detector = _ConstantDetector(True)
    run_speech_evaluation(
        CORPUS,
        run_id="frame-source",
        git_revision="abcdef1",
        detector_profile=_profile_with(frame_source, detector),
    )
    assert expected_lengths <= set(detector.frame_lengths)


def test_detector_identity_changes_the_configuration_hash() -> None:
    """Two detectors on one corpus must not produce indistinguishable artifacts.

    Before this change the hashed configuration named the mock unconditionally, so a run
    of a real provider carried a configuration digest claiming the placeholder.
    """

    suite = load_speech_suite(CORPUS)
    mock_run = run_speech_evaluation(
        CORPUS,
        run_id="hash-mock",
        git_revision="abcdef1",
        detector_profile=mock_detector_profile(suite),
    )
    probe_run = run_speech_evaluation(
        CORPUS,
        run_id="hash-probe",
        git_revision="abcdef1",
        detector_profile=_profile_with(VadFrameSource.PCM, _ConstantDetector(True)),
    )
    assert mock_run.configuration_sha256 != probe_run.configuration_sha256


def test_supplying_both_a_profile_and_a_factory_is_rejected() -> None:
    suite = load_speech_suite(CORPUS)
    with pytest.raises(ValueError):
        run_speech_evaluation(
            CORPUS,
            run_id="both",
            git_revision="abcdef1",
            detector_factory=lambda: _ConstantDetector(True),
            detector_profile=mock_detector_profile(suite),
        )


def test_unlabelled_detector_factory_is_profiled_as_custom() -> None:
    """A detector with no provenance must not inherit the mock's identity in the hash."""

    suite = load_speech_suite(CORPUS)
    mock_run = run_speech_evaluation(
        CORPUS,
        run_id="profile-mock",
        git_revision="abcdef1",
        detector_profile=mock_detector_profile(suite),
    )
    custom_run = run_speech_evaluation(
        CORPUS,
        run_id="profile-custom",
        git_revision="abcdef1",
        detector_factory=lambda: _ConstantDetector(True),
    )
    assert mock_run.configuration_sha256 != custom_run.configuration_sha256


def test_a_detector_that_calls_everything_speech_fails_the_gate() -> None:
    """The gate must still be able to fail for a bad configuration on the real PCM path."""

    suite = load_speech_suite(CORPUS)
    run = run_speech_evaluation(
        CORPUS,
        run_id="all-speech",
        git_revision="abcdef1",
        detector_profile=_profile_with(VadFrameSource.PCM, _ConstantDetector(True)),
    )
    assert not speech_gates_pass(run, suite)
    report = evaluate_gates(run, suite.gate_spec())
    assert any(reason.startswith("gate-below-threshold:") for reason in report.failures)


def test_real_time_factor_is_measured_with_a_clock_fine_enough_to_see_it() -> None:
    """A 15.6 ms-resolution clock reports zero for a sub-millisecond detector.

    That silently empties ``--max-rtf``, which is the only gate that can reject a
    candidate too heavy for the target box, so the runner measures on ``perf_counter``.
    """

    from time import get_clock_info

    assert get_clock_info("perf_counter").monotonic
    assert get_clock_info("perf_counter").resolution <= 1e-6
    run = run_speech_evaluation(
        CORPUS,
        run_id="rtf-resolution",
        git_revision="abcdef1",
        detector_profile=_profile_with(VadFrameSource.PCM, _ConstantDetector(True)),
    )
    per_case = [
        metric.value
        for case in run.cases
        for metric in case.metrics
        if metric.name == "speech.real_time_factor"
    ]
    assert all(value > 0.0 for value in per_case)


# --------------------------------------------------------------------------------------
# CLI.
# --------------------------------------------------------------------------------------


def test_cli_defaults_to_the_mock_and_still_passes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "vad.json"
    exit_code = main(
        [
            "run-speech",
            str(CORPUS),
            str(output),
            "--run-id",
            "cli-default",
            "--git-revision",
            "abcdef1",
        ]
    )
    captured = capsys.readouterr().out
    assert exit_code == 0
    assert "detector=mock-voice-activity-detector" in captured
    assert "frame-source=encoded-length-proxy" in captured
    assert "artifact-gates=pass" in captured


@requires_webrtcvad
def test_cli_webrtc_prints_full_provenance_and_reports_the_gate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The exact package version and license must be readable, not only hashed."""

    output = tmp_path / "vad-webrtc.json"
    exit_code = main(
        [
            "run-speech",
            str(CORPUS),
            str(output),
            "--run-id",
            "cli-webrtc",
            "--git-revision",
            "abcdef1",
            "--detector",
            "webrtc",
            "--webrtc-mode",
            "3",
        ]
    )
    captured = capsys.readouterr().out
    distribution = installed_distribution()
    assert distribution is not None
    assert f"package={distribution[0]}=={distribution[1]}" in captured
    assert "license=MIT AND BSD-3-Clause" in captured
    assert "frame-source=pcm" in captured
    assert '"mode": 3' in captured
    # The measured finding: this corpus rejects the real detector. If that ever changes,
    # this test must be revisited deliberately rather than silently.
    assert exit_code == 1
    assert "artifact-gates=fail" in captured
    artifact = json.loads(output.read_text())
    assert artifact["status"] == "completed"
    assert len(artifact["cases"]) == 8


@requires_webrtcvad
def test_measured_webrtc_recall_is_perfect_and_precision_is_what_fails() -> None:
    """Pin the shape of the finding, not just its verdict.

    Every false decision is a false *positive*: the detector never misses speech on this
    corpus, and loses only on WebRTC's speech-tail hangover over digitally-silent regions.
    A future change that made this a recall problem would be a different finding.
    """

    suite = load_speech_suite(CORPUS)
    run = run_speech_evaluation(
        CORPUS,
        run_id="webrtc-shape",
        git_revision="abcdef1",
        detector_profile=webrtc_detector_profile(suite, mode=3),
    )
    recalls = [
        metric.value
        for case in run.cases
        for metric in case.metrics
        if metric.name == "speech.vad_recall"
    ]
    precisions = [
        metric.value
        for case in run.cases
        for metric in case.metrics
        if metric.name == "speech.vad_precision"
    ]
    assert recalls == [pytest.approx(1.0)] * len(run.cases)
    assert min(precisions) < 1.0
    assert not speech_gates_pass(run, suite)


@requires_webrtcvad
def test_measured_webrtc_is_cheap_enough_for_the_target_box() -> None:
    """The one axis on which this candidate is unambiguously excellent."""

    suite = load_speech_suite(CORPUS)
    run = run_speech_evaluation(
        CORPUS,
        run_id="webrtc-cost",
        git_revision="abcdef1",
        detector_profile=webrtc_detector_profile(suite, mode=2),
        max_real_time_factor=0.5,
    )
    rtf = next(metric for metric in run.metrics if metric.name == "speech.p95_real_time_factor")
    assert rtf.threshold == 0.5
    assert rtf.meets_threshold() is True


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No test here may reach the network; the detector has no weights to fetch."""

    import socket

    def _refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access is not permitted in these tests")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield
