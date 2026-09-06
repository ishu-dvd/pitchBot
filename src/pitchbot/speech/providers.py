"""Build speech providers from configuration, deny-by-default and fail-closed.

PR 33 and PR 34 added real text-to-speech and speech-to-text adapters behind the existing
contracts, but nothing in the running application could construct one: ``_build_service``
never passed ``speech_detector`` or ``speech_transcriber``, so both adapters were reachable
only from tests. This module is the missing link, and it is deliberately the *only* place
that turns configuration into a provider.

Two rules govern it.

**Deny by default.** With no configuration the result is exactly what shipped before those
adapters existed: the byte-size mock detector, **no transcriber at all** so a spoken
utterance is reported as ``transcriber-unavailable`` rather than invented, and **no
synthesiser**, leaving the browser to speak replies in its own voice as it always has.
ADR-0004 has not been satisfied for any provider, so enabling one is a deliberate local
act, never a default.

**A configured provider that cannot be built is a startup error.** If an operator names
``faster-whisper`` and the extra is absent, this raises rather than quietly handing back
``None``. Silently degrading would leave them believing speech works when every utterance
is being dropped - which is strictly worse than refusing to start, and is exactly the class
of inert-configuration problem PR 29 removed elsewhere in this codebase.

The optional adapter modules import cleanly whether or not their extras are installed, so
importing this module is always safe; only *constructing* a provider can fail.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from pitchbot.adapters.contracts import (
    Preloadable,
    SpeechToTextAdapter,
    TextToSpeechAdapter,
    VoiceActivityDetector,
)
from pitchbot.adapters.errors import PermanentAdapterError
from pitchbot.adapters.faster_whisper_stt import (
    FASTER_WHISPER_AVAILABLE,
    FasterWhisperSpeechToTextAdapter,
)
from pitchbot.adapters.faster_whisper_stt import INSTALL_HINT as WHISPER_INSTALL_HINT
from pitchbot.adapters.mocks import MockVoiceActivityDetector
from pitchbot.adapters.piper_tts import (
    DETERMINISTIC_SYNTHESIS,
    PIPER_AVAILABLE,
    PiperTextToSpeechAdapter,
    PiperVoiceRegistry,
    PiperVoiceSpec,
    voice_spec,
)
from pitchbot.adapters.piper_tts import INSTALL_HINT as PIPER_INSTALL_HINT
from pitchbot.adapters.routing_tts import LanguageRoutedTextToSpeech
from pitchbot.adapters.supertonic_tts import (
    SUPERTONIC_AVAILABLE,
    SUPERTONIC_INSTALL_HINT,
    SupertonicTextToSpeechAdapter,
)
from pitchbot.adapters.supertonic_tts import SUPPORTED_LANGUAGES as SUPERTONIC_LANGUAGES
from pitchbot.adapters.webrtc_vad import INSTALL_HINT as WEBRTC_INSTALL_HINT
from pitchbot.adapters.webrtc_vad import (
    WEBRTC_VAD_AVAILABLE,
    WebRtcVoiceActivityDetector,
)
from pitchbot.config import Settings
from pitchbot.domain import LanguageCode
from pitchbot.speech.turn_taking import TurnTakingConfig

logger = logging.getLogger(__name__)


class VadProvider(StrEnum):
    MOCK = "mock"
    WEBRTC = "webrtc"


class SttProvider(StrEnum):
    NONE = "none"
    FASTER_WHISPER = "faster-whisper"


class TtsProvider(StrEnum):
    NONE = "none"
    PIPER = "piper"


MOCK_VAD_ID: Final[str] = "mock-voice-activity-detector"
NO_TRANSCRIBER_ID: Final[str] = "none"
NO_SYNTHESIZER_ID: Final[str] = "none"


@dataclass(frozen=True, slots=True)
class SpeechProviders:
    """What was actually built, and what it is - for logging and diagnostics.

    ``detector`` is always present because endpointing and barge-in must work with or
    without a model. ``transcriber`` is ``None`` when no speech-to-text provider is
    configured, which is the default and is not an error. ``synthesizer`` is ``None`` when
    no text-to-speech provider is configured, which is also the default: the browser
    client falls back to its own Web Speech API voice, exactly as it did before.
    """

    detector: VoiceActivityDetector
    transcriber: SpeechToTextAdapter | None
    synthesizer: TextToSpeechAdapter | None
    detector_id: str
    transcriber_id: str
    synthesizer_id: str

    @property
    def can_transcribe(self) -> bool:
        return self.transcriber is not None

    @property
    def can_synthesize(self) -> bool:
        return self.synthesizer is not None


async def preload_speech_providers(providers: SpeechProviders) -> None:
    """Load model weights at startup instead of during the first buyer utterance.

    Measured end to end: with a lazily-loaded transcriber the first spoken turn reported
    ``transcribe_ms`` of **5,384 ms** for 3.4 s of speech, of which roughly three seconds
    was model construction rather than decoding. Constructing the model holds the GIL, so
    that cost is not merely slow - it stalls the event loop, including the audio socket
    that barge-in depends on, for whichever caller happens to arrive first.

    Synthesis has the same shape and needs the same treatment: loading a Piper voice was
    measured at 2,561 ms, against roughly 110 ms to synthesise a whole sentence through a
    voice already resident. Preloading also moves the registry's license refusal to
    startup, so a denied voice stops the server rather than one conversation.

    This is a no-op when no provider defines ``preload``, and when neither a transcriber
    nor a synthesiser is configured at all, which is the default.
    """

    for provider in (providers.transcriber, providers.synthesizer):
        if isinstance(provider, Preloadable):
            await provider.preload()


def _stt_language(value: str) -> LanguageCode | None:
    """Empty means auto-detect; anything else is a declared language."""

    return LanguageCode(value) if value else None


def _unsupported_languages(value: str) -> frozenset[LanguageCode]:
    """Languages the transcriber declines rather than transcribes badly."""

    return frozenset(
        LanguageCode(entry) for entry in (item.strip() for item in value.split(",")) if entry
    )


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
        early_detection_min_probability=settings.speech_stt_early_detection_min_probability,
        unsupported_languages=_unsupported_languages(settings.speech_stt_unsupported_languages),
    )
    return adapter, f"{adapter.provenance().provider_id}:{adapter.model_size}"


def parse_voice_map(value: str) -> dict[LanguageCode, str]:
    """``"en=en_US-joe-medium,hi=hi_IN-pratham-medium"`` to a language-to-voice mapping.

    A language named twice is refused rather than resolved by ordering, for the same
    reason the registry refuses it: whichever entry wins would be decided by the order
    someone happened to type, and the loser would be silently ignored.
    """

    voices: dict[LanguageCode, str] = {}
    for entry in (item.strip() for item in value.split(",")):
        if not entry:
            continue
        language, separator, voice_id = entry.partition("=")
        if not separator:
            raise PermanentAdapterError(
                f"speech_tts_voices entry {entry!r} must be '<language>=<voice-id>'"
            )
        code = LanguageCode(language.strip())
        if code in voices:
            raise PermanentAdapterError(
                f"language {code.value!r} is mapped twice in speech_tts_voices"
            )
        voices[code] = voice_id.strip()
    return voices


def build_text_to_speech(settings: Settings) -> tuple[TextToSpeechAdapter | None, str]:
    """The configured synthesiser, ``None`` when none is configured, or a startup error.

    Every failure here is a refusal to start: an unknown voice id, a missing voice file, a
    voice whose license forbids commercial use. None of them is worth degrading past,
    because a server that starts without the voice it was configured with is a server that
    will quietly speak in the browser's voice instead - which is the situation this
    provider exists to replace.
    """

    provider = TtsProvider(settings.speech_tts_provider)
    if provider is TtsProvider.NONE:
        return None, NO_SYNTHESIZER_ID
    # Parsed and gated before anything else is checked. A language name that does not exist
    # is a typo the operator must fix whichever optional extra happens to be missing, and
    # reporting the *other* missing dependency first would send them to fix the wrong file.
    routed_languages = _supertonic_languages(settings)
    if not PIPER_AVAILABLE:
        raise PermanentAdapterError(
            f"speech_tts_provider={provider.value!r} is configured but the optional "
            f"dependency is not installed. Install it with: {PIPER_INSTALL_HINT}. "
            "Refusing to fall back to no synthesiser, because the reply would then be "
            "spoken by the browser's own voice without anyone being told."
        )
    voice_dir = Path(settings.speech_tts_voice_dir)
    specs: list[PiperVoiceSpec] = []
    for language, voice_id in parse_voice_map(settings.speech_tts_voices).items():
        # `voice_spec` refuses a voice id with no reviewed license, which is what keeps a
        # licence decision from being made by whoever edits the .env file.
        specs.append(voice_spec(voice_id, language, voice_dir / f"{voice_id}.onnx"))
    registry = PiperVoiceRegistry(
        specs,
        allow_non_commercial=settings.speech_tts_allow_non_commercial,
    )
    for language in sorted(registry.languages):
        # The registry gates on license at `resolve`, so a denied voice would otherwise
        # surface on the first buyer turn in that language rather than at startup. Every
        # mapped language is resolved once here, which is the whole point of building
        # providers eagerly: a licence problem must stop the server, not a conversation.
        registry.resolve(language)
    adapter = PiperTextToSpeechAdapter(
        registry,
        synthesis=DETERMINISTIC_SYNTHESIS if settings.speech_tts_deterministic else None,
    )
    mapped = ",".join(sorted(spec.voice_id for spec in specs))
    if not routed_languages:
        return adapter, f"{TtsProvider.PIPER.value}:{mapped}"
    routes = _supertonic_routes(settings, routed_languages)
    routed = ",".join(sorted(language.value for language in routes))
    return (
        LanguageRoutedTextToSpeech(adapter, routes),
        f"{TtsProvider.PIPER.value}:{mapped}+supertonic:{routed}",
    )


def _supertonic_languages(settings: Settings) -> list[LanguageCode]:
    """Which languages are routed away from Piper, validated but not yet built.

    Separate from building the adapter so that a configuration mistake is reported before
    any other optional dependency is looked for.
    """

    languages: list[LanguageCode] = []
    for raw in settings.speech_tts_supertonic_languages.split(","):
        code = raw.strip()
        if not code:
            continue
        try:
            language = LanguageCode(code)
        except ValueError as error:
            raise PermanentAdapterError(
                f"speech_tts_supertonic_languages contains unknown language {code!r}"
            ) from error
        if language not in SUPERTONIC_LANGUAGES:
            # Narrower than the model's own 31 languages on purpose: an unmeasured
            # language is a claim, not a capability, and Telugu is absent from the model.
            raise PermanentAdapterError(
                f"supertonic is not offered for {code!r}; measured languages are "
                f"{sorted(item.value for item in SUPERTONIC_LANGUAGES)}"
            )
        languages.append(language)
    if languages and not SUPERTONIC_AVAILABLE:
        raise PermanentAdapterError(
            "speech_tts_supertonic_languages is set but the optional dependency is not "
            f"installed. Install it with: {SUPERTONIC_INSTALL_HINT}. Refusing to fall back "
            "to Piper, because the languages routed here are the ones Piper cannot serve "
            "under a commercial licence - falling back would silently ship a voice this "
            "project denies."
        )
    return languages


def _supertonic_routes(
    settings: Settings,
    languages: list[LanguageCode],
) -> dict[LanguageCode, TextToSpeechAdapter]:
    """One Supertonic adapter, shared by every language routed to it."""

    logger.warning(
        "Supertonic is enabled for %s. Its weights are OpenRAIL-M: Attachment A clause (e) "
        "requires that generated content be expressly and intelligibly disclaimed as "
        "machine generated. That obligation is on this deployment.",
        ",".join(language.value for language in languages),
    )
    adapter = SupertonicTextToSpeechAdapter(
        voice_style=settings.speech_tts_supertonic_voice,
        model_dir=settings.speech_tts_supertonic_model_dir or None,
        total_steps=settings.speech_tts_supertonic_steps,
        allow_download=settings.speech_tts_supertonic_allow_download,
    )
    return dict.fromkeys(languages, adapter)


def build_turn_taking(settings: Settings) -> TurnTakingConfig:
    """Turn-taking thresholds from configuration, or a startup error naming the setting.

    :class:`TurnTakingConfig` validates its own numbers, but it validates them under its
    *field* names - an operator who set ``PITCHBOT_SPEECH_TURN_END_SILENCE_MS`` to zero
    would be told ``end_silence_ms must be between 1 and ...`` and have to guess which line
    of their ``.env`` that was. The error is re-raised naming the thing they actually
    edited, which is the same courtesy every other builder here extends.
    """

    try:
        return TurnTakingConfig(
            min_speech_ms=settings.speech_turn_min_speech_ms,
            end_silence_ms=settings.speech_turn_end_silence_ms,
            max_utterance_ms=settings.speech_turn_max_utterance_ms,
            barge_in_speech_ms=settings.speech_turn_barge_in_speech_ms,
            agent_floor_ms=settings.speech_turn_agent_floor_ms,
        )
    except ValueError as error:
        raise PermanentAdapterError(
            f"turn-taking configuration is invalid: {error}. The settings are "
            "speech_turn_min_speech_ms, speech_turn_end_silence_ms, "
            "speech_turn_max_utterance_ms, speech_turn_barge_in_speech_ms and "
            "speech_turn_agent_floor_ms."
        ) from error


def build_speech_providers(settings: Settings) -> SpeechProviders:
    """All three providers, built together so a misconfiguration fails once at startup."""

    detector, detector_id = build_voice_activity_detector(settings)
    transcriber, transcriber_id = build_speech_to_text(settings)
    synthesizer, synthesizer_id = build_text_to_speech(settings)
    return SpeechProviders(
        detector=detector,
        transcriber=transcriber,
        synthesizer=synthesizer,
        detector_id=detector_id,
        transcriber_id=transcriber_id,
        synthesizer_id=synthesizer_id,
    )


__all__ = [
    "MOCK_VAD_ID",
    "NO_SYNTHESIZER_ID",
    "NO_TRANSCRIBER_ID",
    "Preloadable",
    "build_turn_taking",
    "SpeechProviders",
    "SttProvider",
    "TtsProvider",
    "VadProvider",
    "build_speech_providers",
    "build_speech_to_text",
    "build_text_to_speech",
    "build_voice_activity_detector",
    "parse_voice_map",
    "preload_speech_providers",
]
