from __future__ import annotations

import io
import wave

import pytest

from pitchbot.benchmarks.audio import (
    ClipSpec,
    SegmentKind,
    SegmentSpec,
    frames_to_intervals,
    generate_clip,
    is_speech_kind,
)
from pitchbot.benchmarks.metrics import Interval

_ALL_KINDS = (
    SegmentSpec(SegmentKind.SILENCE, 100),
    SegmentSpec(SegmentKind.SPEECH, 200),
    SegmentSpec(SegmentKind.BACKGROUND_NOISE, 100),
    SegmentSpec(SegmentKind.NOISE_BURST, 40),
    SegmentSpec(SegmentKind.CROSSTALK, 120),
    SegmentSpec(SegmentKind.SILENCE, 60),
)


def test_generator_is_bit_for_bit_reproducible_for_a_seed() -> None:
    spec = ClipSpec(seed=7, segments=_ALL_KINDS)

    first = generate_clip(spec)
    second = generate_clip(spec)

    assert first.wav == second.wav
    assert first.pcm == second.pcm
    assert first.frames == second.frames
    assert first.frame_is_speech == second.frame_is_speech
    assert first.sha256 == second.sha256


def test_generator_seed_changes_the_signal() -> None:
    segments = (SegmentSpec(SegmentKind.SPEECH, 200),)

    assert (
        generate_clip(ClipSpec(seed=1, segments=segments)).sha256
        != generate_clip(ClipSpec(seed=2, segments=segments)).sha256
    )


def test_ground_truth_intervals_match_the_emitted_structure_exactly() -> None:
    spec = ClipSpec(
        seed=3,
        segments=(
            SegmentSpec(SegmentKind.SILENCE, 40),
            SegmentSpec(SegmentKind.SPEECH, 60),
            SegmentSpec(SegmentKind.SILENCE, 40),
            SegmentSpec(SegmentKind.CROSSTALK, 40),
        ),
    )

    clip = generate_clip(spec)

    # 20 ms frames: 2 silence, 3 speech, 2 silence, 2 crosstalk (crosstalk is speech).
    assert clip.frame_is_speech == (
        False,
        False,
        True,
        True,
        True,
        False,
        False,
        True,
        True,
    )
    assert clip.truth_intervals == (
        Interval(0.04, 0.10),
        Interval(0.14, 0.18),
    )
    assert clip.audio_seconds == pytest.approx(0.18)


def test_frames_encode_energy_so_a_byte_size_detector_separates_speech() -> None:
    clip = generate_clip(ClipSpec(seed=11, segments=_ALL_KINDS))

    speech_sizes = [
        len(frame)
        for frame, speech in zip(clip.frames, clip.frame_is_speech, strict=True)
        if speech
    ]
    silence_sizes = [
        len(frame)
        for frame, speech in zip(clip.frames, clip.frame_is_speech, strict=True)
        if not speech
    ]

    # Voiced frames encode above and non-voiced frames (including the burst) below the
    # detector's default 512-byte threshold, so the shipped byte-size mock scores it.
    assert min(speech_sizes) >= 512
    assert max(silence_sizes) < 512
    assert max(silence_sizes) < min(speech_sizes)


def test_generated_audio_is_a_valid_pcm_wav() -> None:
    spec = ClipSpec(seed=5, segments=(SegmentSpec(SegmentKind.SPEECH, 200),))
    clip = generate_clip(spec)

    with wave.open(io.BytesIO(clip.wav), "rb") as reader:
        assert reader.getnchannels() == 1
        assert reader.getsampwidth() == 2
        assert reader.getframerate() == 16_000
        assert reader.getnframes() == 16_000 * 200 // 1_000
        assert reader.readframes(reader.getnframes()) == clip.pcm


def test_noise_burst_is_never_labeled_speech() -> None:
    assert is_speech_kind(SegmentKind.SPEECH)
    assert is_speech_kind(SegmentKind.CROSSTALK)
    assert not is_speech_kind(SegmentKind.NOISE_BURST)
    assert not is_speech_kind(SegmentKind.BACKGROUND_NOISE)
    assert not is_speech_kind(SegmentKind.SILENCE)


def test_frames_to_intervals_handles_boundaries() -> None:
    assert frames_to_intervals((), 20) == ()
    assert frames_to_intervals((False, False), 20) == ()
    assert frames_to_intervals((True, True, True), 20) == (Interval(0.0, 0.06),)
    assert frames_to_intervals((True, False, True), 20) == (
        Interval(0.0, 0.02),
        Interval(0.04, 0.06),
    )


def test_clip_spec_rejects_unaligned_or_empty_specifications() -> None:
    with pytest.raises(ValueError, match="at least one segment"):
        ClipSpec(seed=1, segments=())
    with pytest.raises(ValueError, match="multiple of frame_ms"):
        ClipSpec(seed=1, segments=(SegmentSpec(SegmentKind.SPEECH, 30),), frame_ms=20)
    with pytest.raises(ValueError, match="whole number of samples"):
        ClipSpec(
            seed=1,
            segments=(SegmentSpec(SegmentKind.SPEECH, 6),),
            sample_rate_hz=44_100,
            frame_ms=3,
        )
    with pytest.raises(ValueError, match="segment duration"):
        SegmentSpec(SegmentKind.SPEECH, 0)
