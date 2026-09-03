"""Build speech providers from configuration, deny-by-default and fail-closed.

PR 33 and PR 34 added real text-to-speech and speech-to-text adapters behind the existing
contracts, but nothing in the running application could construct one: ``_build_service``
never passed ``speech_detector`` or ``speech_transcriber``, so both adapters were reachable
only from tests. This module is the missing link, and it is deliberately the *only* place
that turns configuration into a provider.

Two rules govern it.

**Deny by default.** With no configuration the result is exactly what shipped before those
adapters existed: the byte-size mock detector, and **no transcriber at all**, so a spoken
utterance is reported as ``transcriber-unavailable`` rather than invented. ADR-0004 has not
been satisfied for any provider, so enabling one is a deliberate local act, never a default.

**A configured provider that cannot be built is a startup error.** If an operator names
``faster-whisper`` and the extra is absent, this raises rather than quietly handing back
``None``. Silently degrading would leave them believing speech works when every utterance
is being dropped - which is strictly worse than refusing to start, and is exactly the class
of inert-configuration problem PR 29 removed elsewhere in this codebase.

The optional adapter modules import cleanly whether or not their extras are installed, so
importing this module is always safe; only *constructing* a provider can fail.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol, runtime_checkable

from pitchbot.adapters.contracts import SpeechToTextAdapter, VoiceActivityDetector
from pitchbot.adapters.errors import PermanentAdapterError
from pitchbot.adapters.faster_whisper_stt import (
    FASTER_WHISPER_AVAILABLE,
    FasterWhisperSpeechToTextAdapter,
)
from pitchbot.adapters.faster_whisper_stt import INSTALL_HINT as WHISPER_INSTALL_HINT
from pitchbot.adapters.mocks import MockVoiceActivityDetector
from pitchbot.adapters.webrtc_vad import INSTALL_HINT as WEBRTC_INSTALL_HINT
from pitchbot.adapters.webrtc_vad import (
    WEBRTC_VAD_AVAILABLE,
    WebRtcVoiceActivityDetector,
)
from pitchbot.config import Settings
from pitchbot.domain import LanguageCode


class VadProvider(StrEnum):
    MOCK = "mock"
    WEBRTC = "webrtc"


class SttProvider(StrEnum):
    NONE = "none"
    FASTER_WHISPER = "faster-whisper"


MOCK_VAD_ID: Final[str] = "mock-voice-activity-detector"
NO_TRANSCRIBER_ID: Final[str] = "none"


@dataclass(frozen=True, slots=True)
class SpeechProviders:
    """What was actually built, and what it is - for logging and diagnostics.

    ``detector`` is always present because endpointing and barge-in must work with or
    without a model. ``transcriber`` is ``None`` when no speech-to-text provider is
    configured, which is the default and is not an error.
    """

    detector: VoiceActivityDetector
    transcriber: SpeechToTextAdapter | None
    detector_id: str
    transcriber_id: str

    @property
    def can_transcribe(self) -> bool:
        return self.transcriber is not None


@runtime_checkable
class Preloadable(Protocol):
    """A provider that can load its weights ahead of first use."""

    async def preload(self) -> None: ...


async def preload_speech_providers(providers: SpeechProviders) -> None:
    """Load model weights at startup instead of during the first buyer utterance.

    Measured end to end: with a lazily-loaded transcriber the first spoken turn reported
    ``transcribe_ms`` of **5,384 ms** for 3.4 s of speech, of which roughly three seconds
    was model construction rather than decoding. Constructing the model holds the GIL, so
    that cost is not merely slow - it stalls the event loop, including the audio socket
    that barge-in depends on, for whichever caller happens to arrive first.

    This is a no-op when no provider defines ``preload``, and when no transcriber is
    configured at all, which is the default.
    """

    transcriber = providers.transcriber
    if isinstance(transcriber, Preloadable):
        await transcriber.preload()


def _stt_language(value: str) -> LanguageCode | None:
    """Empty means auto-detect; anything else is a declared language."""

    return LanguageCode(value) if value else None


def build_voice_activity_detector(settings: Settings) -> tuple[VoiceActivityDetector, str]:
    """The configured detector, or a startup error naming what is missing."""

    provider = VadProvider(settings.speech_vad_provider)
    if provider is VadProvider.MOCK:
        return MockVoiceActivityDetector(), MOCK_VAD_ID
    if not WEBRTC_VAD_AVAILABLE:
        raise PermanentAdapterError(
            f"speech_vad_provider={provider.value!r} is configured but the optional "
            f"dependency is not installed. Install it with: {WEBRTC_INSTALL_HINT}. "
            "Refusing to fall back to the mock detector, because a silent downgrade "
            "would hide that the configured detector is not running."
        )
    detector = WebRtcVoiceActivityDetector(
        mode=settings.speech_vad_mode,
        sample_rate_hz=settings.speech_vad_sample_rate_hz,
    )
    return detector, detector.provenance().provider_id


def build_speech_to_text(settings: Settings) -> tuple[SpeechToTextAdapter | None, str]:
    """The configured transcriber, ``None`` when none is configured, or a startup error."""

    provider = SttProvider(settings.speech_stt_provider)
    if provider is SttProvider.NONE:
        return None, NO_TRANSCRIBER_ID
    if not FASTER_WHISPER_AVAILABLE:
        raise PermanentAdapterError(
            f"speech_stt_provider={provider.value!r} is configured but the optional "
            f"dependency is not installed. Install it with: {WHISPER_INSTALL_HINT}. "
            "Refusing to fall back to no transcriber, because every spoken utterance "
            "would then be silently reported as transcriber-unavailable."
        )
    adapter = FasterWhisperSpeechToTextAdapter(
        model_size=settings.speech_stt_model,
        device=settings.speech_stt_device,
        compute_type=settings.speech_stt_compute_type,
        beam_size=settings.speech_stt_beam_size,
        download_root=settings.speech_stt_download_root or None,
        allow_download=settings.speech_stt_allow_download,
        language=_stt_language(settings.speech_stt_language),
    )
    return adapter, f"{adapter.provenance().provider_id}:{adapter.model_size}"


def build_speech_providers(settings: Settings) -> SpeechProviders:
    """Both providers, built together so a misconfiguration fails once at startup."""

    detector, detector_id = build_voice_activity_detector(settings)
    transcriber, transcriber_id = build_speech_to_text(settings)
    return SpeechProviders(
        detector=detector,
        transcriber=transcriber,
        detector_id=detector_id,
        transcriber_id=transcriber_id,
    )


__all__ = [
    "MOCK_VAD_ID",
    "NO_TRANSCRIBER_ID",
    "Preloadable",
    "SpeechProviders",
    "SttProvider",
    "VadProvider",
    "build_speech_providers",
    "build_speech_to_text",
    "build_voice_activity_detector",
    "preload_speech_providers",
]
