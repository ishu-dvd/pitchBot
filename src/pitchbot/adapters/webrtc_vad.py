"""py-webrtcvad adapter behind the existing ``VoiceActivityDetector`` contract.

This is the first real speech provider in the repository, and it is deliberately the
smallest one that can exist: the WebRTC project's GMM voice-activity detector is a C
extension of a few hundred kilobytes with **no model weights**, so nothing is downloaded
at import time, at construction time, or at detection time, and the detector runs on CPU
with no accelerator. ADR-0002 anticipates exactly this - a real provider arriving behind
an unchanged contract - so ``detect(AudioChunk) -> VoiceActivity`` is implemented as
written and the protocol is untouched.

**The dependency is optional.** ``pitchbot`` imports, and every test that does not name
this module passes, with ``webrtcvad`` absent; this module itself also imports cleanly
without it, so callers can probe :data:`WEBRTC_VAD_AVAILABLE` instead of guarding an
``ImportError``. Only constructing the detector requires the package, and that failure is
a :class:`PermanentAdapterError` naming the extra to install. Install it with
``pip install "pitchbot[webrtc-vad]"``.

**What this detector consumes.** WebRTC's VAD accepts only mono 16-bit little-endian PCM
at 8/16/32/48 kHz in exactly 10, 20, or 30 ms frames. That is a hard constraint of the
library, not a preference, so the adapter validates it and refuses anything else rather
than resampling, padding, or truncating - any of which would silently change the signal
being measured. In particular it will refuse the *encoded-length proxy* frames that
``pitchbot.benchmarks.audio`` emits for the byte-size mock, because those are truncated
byte strings, not PCM; see ``VadFrameSource`` in ``pitchbot.benchmarks.speech``.

**Confidence is not a posterior.** ``webrtcvad`` exposes a single boolean per frame; the
underlying GMM likelihood ratio is not reachable through its public API. Reporting a
number that varied with the decision would fabricate a probability the library never
produced, so this adapter reports the constant :data:`DECISION_CONFIDENCE` for every
frame. ``VoiceActivity.confidence`` from this detector carries no information and must not
be thresholded; ``is_speech`` is the whole signal.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from enum import IntEnum
from importlib import metadata
from types import ModuleType
from typing import Final

from pitchbot.adapters.contracts import AudioChunk, VoiceActivity, VoiceActivityDetector
from pitchbot.adapters.errors import PermanentAdapterError


def _import_webrtcvad() -> ModuleType | None:
    """Import the extension if it is present, without a static dependency on it.

    ``importlib`` rather than a guarded ``import webrtcvad`` on purpose. ``webrtcvad``
    ships no type stubs and no ``py.typed`` marker, so a static import makes ``mypy``
    report a *different* diagnostic depending on whether the optional extra happens to be
    installed in the checking environment - untyped-import when present, missing-import
    when absent - and no single suppression is correct in both. Resolving the module at
    runtime types it as ``ModuleType`` in both states, so type checking is identical with
    and without the extra, which is exactly the property this module exists to guarantee.
    """

    try:
        return importlib.import_module("webrtcvad")
    except ImportError:
        return None


_MODULE: Final[ModuleType | None] = _import_webrtcvad()

WEBRTC_VAD_AVAILABLE: Final[bool] = _MODULE is not None
"""Whether the optional ``webrtcvad`` extension is importable in this environment."""

INSTALL_HINT: Final[str] = 'pip install "pitchbot[webrtc-vad]"'

PROVIDER_ID: Final[str] = "py-webrtcvad"
ALGORITHM: Final[str] = "webrtc-gmm-vad"
"""The WebRTC project's Gaussian-mixture voice-activity detector over six sub-bands."""

LICENSE: Final[str] = "MIT AND BSD-3-Clause"
"""Reviewed 2026-09-03 from the LICENSE shipped in the installed distribution.

The Python binding is MIT (Copyright (c) 2016 John Wiseman); the vendored WebRTC C code
under ``cbits/webrtc/`` is BSD-3-Clause (Copyright (c) 2011, The WebRTC project authors).
Both are permissive. ``docs/BENCHMARKS.md`` recorded GitHub's automatic detection as
``NOASSERTION`` and required a manual review; this is that review, and it was performed
against the file in the wheel rather than the repository landing page.
"""

MODEL_WEIGHTS: Final[str] = "none"
"""The GMM parameters are compiled into the C extension. There is nothing to download."""

DECISION_CONFIDENCE: Final[float] = 0.5
"""Fixed, information-free confidence. See the module docstring - it is not a posterior."""

SUPPORTED_SAMPLE_RATES_HZ: Final[tuple[int, ...]] = (8_000, 16_000, 32_000, 48_000)
SUPPORTED_FRAME_MS: Final[tuple[int, ...]] = (10, 20, 30)
_SAMPLE_WIDTH_BYTES: Final[int] = 2


class WebRtcVadMode(IntEnum):
    """WebRTC's aggressiveness setting: higher filters more non-speech, at more misses."""

    QUALITY = 0
    LOW_BITRATE = 1
    AGGRESSIVE = 2
    VERY_AGGRESSIVE = 3


@dataclass(frozen=True, slots=True)
class WebRtcVadProvenance:
    """Exact identity of what was measured, as ADR-0004 requires it to be captured."""

    provider_id: str
    package: str
    package_version: str
    algorithm: str
    license: str
    model_weights: str
    mode: int
    sample_rate_hz: int


def installed_distribution() -> tuple[str, str] | None:
    """The distribution actually providing ``webrtcvad``, and its exact version.

    Two distributions ship the same module: ``webrtcvad`` (sdist only, needs a compiler)
    and ``webrtcvad-wheels`` (prebuilt wheels). Which one is installed is part of the
    measured configuration, so it is resolved rather than assumed.
    """

    for distribution in ("webrtcvad-wheels", "webrtcvad"):
        try:
            return distribution, metadata.version(distribution)
        except metadata.PackageNotFoundError:
            continue
    return None


def require_webrtcvad() -> ModuleType:
    """The imported extension, or a permanent adapter error naming the extra."""

    if _MODULE is None:
        raise PermanentAdapterError(
            f"webrtcvad is not installed; install the optional extra with: {INSTALL_HINT}"
        )
    return _MODULE


def frame_duration_ms(byte_length: int, sample_rate_hz: int) -> float:
    """Duration of ``byte_length`` bytes of mono 16-bit PCM at ``sample_rate_hz``."""

    return byte_length / _SAMPLE_WIDTH_BYTES / sample_rate_hz * 1_000


class WebRtcVoiceActivityDetector(VoiceActivityDetector):
    """WebRTC's GMM voice-activity detector, one frame at a time.

    The instance is stateful by design: WebRTC adapts its internal noise model over the
    frames it sees, so a detector must be fed one clip's frames in order and a fresh
    instance used per clip. The benchmark runner already constructs one detector per case.
    """

    def __init__(
        self,
        *,
        mode: WebRtcVadMode | int = WebRtcVadMode.AGGRESSIVE,
        sample_rate_hz: int = 16_000,
    ) -> None:
        module = require_webrtcvad()
        resolved_mode = int(mode)
        if resolved_mode not in tuple(int(value) for value in WebRtcVadMode):
            raise PermanentAdapterError(f"webrtcvad mode must be 0-3, received {resolved_mode}")
        if sample_rate_hz not in SUPPORTED_SAMPLE_RATES_HZ:
            raise PermanentAdapterError(
                f"webrtcvad supports {SUPPORTED_SAMPLE_RATES_HZ} Hz, received {sample_rate_hz}"
            )
        self._mode = resolved_mode
        self._sample_rate_hz = sample_rate_hz
        self._expected_frame_bytes = frozenset(
            sample_rate_hz * frame_ms // 1_000 * _SAMPLE_WIDTH_BYTES
            for frame_ms in SUPPORTED_FRAME_MS
        )
        try:
            self._vad = module.Vad(resolved_mode)
        except Exception as error:  # pragma: no cover - constructor is validated above
            raise PermanentAdapterError(f"webrtcvad rejected mode {resolved_mode}") from error
        self.frames_seen = 0

    @property
    def mode(self) -> int:
        return self._mode

    @property
    def sample_rate_hz(self) -> int:
        return self._sample_rate_hz

    def provenance(self) -> WebRtcVadProvenance:
        distribution = installed_distribution()
        package, version = distribution if distribution is not None else (PROVIDER_ID, "unknown")
        return WebRtcVadProvenance(
            provider_id=PROVIDER_ID,
            package=package,
            package_version=version,
            algorithm=ALGORITHM,
            license=LICENSE,
            model_weights=MODEL_WEIGHTS,
            mode=self._mode,
            sample_rate_hz=self._sample_rate_hz,
        )

    def detect(self, frame: AudioChunk) -> VoiceActivity:
        if frame.sample_rate_hz != self._sample_rate_hz:
            raise PermanentAdapterError(
                f"detector is configured for {self._sample_rate_hz} Hz, "
                f"frame {frame.sequence} carries {frame.sample_rate_hz} Hz"
            )
        if len(frame.data) not in self._expected_frame_bytes:
            raise PermanentAdapterError(
                f"webrtcvad requires {sorted(self._expected_frame_bytes)} bytes of mono 16-bit "
                f"PCM at {self._sample_rate_hz} Hz "
                f"({', '.join(str(value) for value in SUPPORTED_FRAME_MS)} ms), "
                f"frame {frame.sequence} carries {len(frame.data)} bytes "
                f"({frame_duration_ms(len(frame.data), self._sample_rate_hz):.3f} ms)"
            )
        try:
            is_speech = bool(self._vad.is_speech(frame.data, self._sample_rate_hz))
        except Exception as error:
            raise PermanentAdapterError(
                f"webrtcvad rejected frame {frame.sequence}: {error}"
            ) from error
        self.frames_seen += 1
        return VoiceActivity(
            is_speech=is_speech,
            confidence=DECISION_CONFIDENCE,
            sequence=frame.sequence,
        )


__all__ = [
    "ALGORITHM",
    "DECISION_CONFIDENCE",
    "INSTALL_HINT",
    "LICENSE",
    "MODEL_WEIGHTS",
    "PROVIDER_ID",
    "SUPPORTED_FRAME_MS",
    "SUPPORTED_SAMPLE_RATES_HZ",
    "WEBRTC_VAD_AVAILABLE",
    "WebRtcVadMode",
    "WebRtcVadProvenance",
    "WebRtcVoiceActivityDetector",
    "frame_duration_ms",
    "installed_distribution",
    "require_webrtcvad",
]
