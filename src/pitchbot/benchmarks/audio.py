"""Deterministic, dependency-free synthetic audio for voice-activity benchmarks.

The generator emits, from a seed, a real 16-bit PCM WAV plus a stream of byte frames
carrying exact ground-truth speech/silence intervals. It never synthesises intelligible
speech: it only reproduces the *structure* a voice-activity detector must recover
(speech vs silence, onsets, pauses, and bursts). Intelligible speech would require a TTS
model, which ADR-0004 has not authorised, so this module deliberately produces structure
only and cannot be used to measure STT or TTS.

Two properties matter for scoring:

* The WAV/PCM bytes are bit-for-bit reproducible across runs and platforms - the samples
  come from Python's platform-independent Mersenne-Twister PRNG and are packed
  little-endian - so the SHA-256 of the WAV is a real, verifiable corpus hash.
* Each emitted frame's byte length grows with its energy, mirroring a variable-bitrate
  codec where a silent 20 ms frame encodes far smaller than a spoken one. This is exactly
  the premise the shipped ``MockVoiceActivityDetector`` byte-size heuristic models, so the
  generated frames are something that detector can be meaningfully scored against.
"""

from __future__ import annotations

import hashlib
import io
import math
import struct
import wave
from dataclasses import dataclass
from enum import StrEnum
from random import Random

from pitchbot.benchmarks.metrics import Interval

GENERATOR_VERSION = "vad-synthetic-v1"
DEFAULT_SAMPLE_RATE_HZ = 16_000
DEFAULT_FRAME_MS = 20

_SAMPLE_WIDTH_BYTES = 2
_INT16_MIN = -32_768
_INT16_MAX = 32_767

# Variable-bitrate proxy: the encoded frame length rises with root-mean-square amplitude,
# so quiet frames stay well under and voiced frames well over a byte-size detector's
# default 512-byte threshold. Encoded length never exceeds the raw PCM frame, as a real
# codec never inflates content.
_MIN_FRAME_BYTES = 48
_MAX_FRAME_BYTES = 600
_VOICED_RMS_REFERENCE = 1_500.0


class SegmentKind(StrEnum):
    """The acoustic regime of one labeled region of a clip."""

    SPEECH = "speech"
    SILENCE = "silence"
    BACKGROUND_NOISE = "background-noise"
    NOISE_BURST = "noise-burst"
    CROSSTALK = "crosstalk"


# Peak amplitude and ground-truth speech label per regime. Voiced regimes sit far above the
# byte-size threshold; non-voiced regimes (including short bursts) far below it, so the
# placeholder detector rejects a burst as non-speech exactly as the ground truth requires.
_KIND_AMPLITUDE: dict[SegmentKind, int] = {
    SegmentKind.SPEECH: 8_000,
    SegmentKind.CROSSTALK: 8_000,
    SegmentKind.SILENCE: 0,
    SegmentKind.BACKGROUND_NOISE: 200,
    SegmentKind.NOISE_BURST: 300,
}
_SPEECH_KINDS = frozenset({SegmentKind.SPEECH, SegmentKind.CROSSTALK})


def is_speech_kind(kind: SegmentKind) -> bool:
    return kind in _SPEECH_KINDS


@dataclass(frozen=True, slots=True)
class SegmentSpec:
    """One contiguous region of a synthetic clip."""

    kind: SegmentKind
    duration_ms: int

    def __post_init__(self) -> None:
        if self.duration_ms <= 0:
            raise ValueError("segment duration must be positive")


@dataclass(frozen=True, slots=True)
class ClipSpec:
    """A fully deterministic description of a synthetic clip."""

    seed: int
    segments: tuple[SegmentSpec, ...]
    sample_rate_hz: int = DEFAULT_SAMPLE_RATE_HZ
    frame_ms: int = DEFAULT_FRAME_MS

    def __post_init__(self) -> None:
        if not self.segments:
            raise ValueError("clip requires at least one segment")
        if self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if self.frame_ms <= 0:
            raise ValueError("frame_ms must be positive")
        if (self.sample_rate_hz * self.frame_ms) % 1_000 != 0:
            raise ValueError("frame must contain a whole number of samples")
        for segment in self.segments:
            if segment.duration_ms % self.frame_ms != 0:
                raise ValueError("segment duration must be a multiple of frame_ms")

    @property
    def samples_per_frame(self) -> int:
        return self.sample_rate_hz * self.frame_ms // 1_000


@dataclass(frozen=True, slots=True)
class SyntheticClip:
    """A generated clip: raw PCM, its WAV encoding, detection frames, and ground truth."""

    spec: ClipSpec
    pcm: bytes
    wav: bytes
    frames: tuple[bytes, ...]
    frame_is_speech: tuple[bool, ...]
    sha256: str

    @property
    def audio_seconds(self) -> float:
        return len(self.frames) * self.spec.frame_ms / 1_000

    @property
    def truth_intervals(self) -> tuple[Interval, ...]:
        return frames_to_intervals(self.frame_is_speech, self.spec.frame_ms)


def frames_to_intervals(flags: tuple[bool, ...], frame_ms: int) -> tuple[Interval, ...]:
    """Merge contiguous speech frames into second-based intervals for the VAD metric."""

    frame_seconds = frame_ms / 1_000
    intervals: list[Interval] = []
    start: int | None = None
    for index, flag in enumerate(flags):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            intervals.append(Interval(start * frame_seconds, index * frame_seconds))
            start = None
    if start is not None:
        intervals.append(Interval(start * frame_seconds, len(flags) * frame_seconds))
    return tuple(intervals)


def _clamp(value: int) -> int:
    return max(_INT16_MIN, min(_INT16_MAX, value))


def _frame_samples(rng: Random, kind: SegmentKind, amplitude: int, count: int) -> list[int]:
    if kind is SegmentKind.SILENCE or amplitude == 0:
        return [0] * count
    if kind is SegmentKind.CROSSTALK:
        half = amplitude // 2
        return [_clamp(rng.randint(-half, half) + rng.randint(-half, half)) for _ in range(count)]
    return [rng.randint(-amplitude, amplitude) for _ in range(count)]


def _rms(samples: list[int]) -> float:
    if not samples:
        return 0.0
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))


def _encode_frame(frame_pcm: bytes, samples: list[int]) -> bytes:
    scale = min(1.0, _rms(samples) / _VOICED_RMS_REFERENCE)
    length = _MIN_FRAME_BYTES + round((_MAX_FRAME_BYTES - _MIN_FRAME_BYTES) * scale)
    length = max(1, min(length, len(frame_pcm)))
    return frame_pcm[:length]


def _pcm_to_wav(pcm: bytes, sample_rate_hz: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(_SAMPLE_WIDTH_BYTES)
        writer.setframerate(sample_rate_hz)
        writer.writeframes(pcm)
    return buffer.getvalue()


def generate_clip(spec: ClipSpec) -> SyntheticClip:
    """Generate the deterministic clip described by ``spec``."""

    samples_per_frame = spec.samples_per_frame
    rng = Random(spec.seed)
    pcm_parts: list[bytes] = []
    frames: list[bytes] = []
    frame_is_speech: list[bool] = []
    for segment in spec.segments:
        amplitude = _KIND_AMPLITUDE[segment.kind]
        speech = is_speech_kind(segment.kind)
        for _ in range(segment.duration_ms // spec.frame_ms):
            samples = _frame_samples(rng, segment.kind, amplitude, samples_per_frame)
            frame_pcm = struct.pack(f"<{samples_per_frame}h", *samples)
            pcm_parts.append(frame_pcm)
            frames.append(_encode_frame(frame_pcm, samples))
            frame_is_speech.append(speech)
    pcm = b"".join(pcm_parts)
    wav = _pcm_to_wav(pcm, spec.sample_rate_hz)
    return SyntheticClip(
        spec=spec,
        pcm=pcm,
        wav=wav,
        frames=tuple(frames),
        frame_is_speech=tuple(frame_is_speech),
        sha256=hashlib.sha256(wav).hexdigest(),
    )
