"""Tests for building speech providers from configuration.

The property under test is that this is the *only* place configuration becomes a provider,
and that it behaves in two specific ways:

*Deny by default.* With no configuration the result is exactly what shipped before the
real adapters existed - the byte-size mock detector, no transcriber at all so a spoken
utterance is reported as ``transcriber-unavailable`` rather than invented, and no
synthesiser so the browser keeps speaking replies in its own voice.

*A configured provider that cannot be built is a startup error.* Naming a provider whose
optional extra is absent raises rather than silently handing back the mock or ``None``.
That is asserted by forcing the availability flags off, so it holds regardless of which
extras happen to be installed in the test environment.
"""

from __future__ import annotations

import pytest

from pitchbot.adapters.errors import PermanentAdapterError
from pitchbot.adapters.faster_whisper_stt import FasterWhisperSpeechToTextAdapter
from pitchbot.adapters.mocks import MockVoiceActivityDetector
from pitchbot.adapters.piper_tts import DETERMINISTIC_SYNTHESIS, PiperTextToSpeechAdapter
from pitchbot.adapters.routing_tts import LanguageRoutedTextToSpeech
from pitchbot.adapters.supertonic_tts import SupertonicTextToSpeechAdapter
from pitchbot.adapters.webrtc_vad import WebRtcVoiceActivityDetector
from pitchbot.config import Settings
from pitchbot.domain import LanguageCode
from pitchbot.speech.providers import (
    MOCK_VAD_ID,
    NO_SYNTHESIZER_ID,
    NO_TRANSCRIBER_ID,
    SpeechProviders,
    SttProvider,
    TtsProvider,
    VadProvider,
    build_speech_providers,
    build_speech_to_text,
    build_text_to_speech,
    build_voice_activity_detector,
    parse_voice_map,
    preload_speech_providers,
)

_PROVIDERS = "pitchbot.speech.providers"

requires_piper = pytest.mark.skipif(
    not __import__("pitchbot.adapters.piper_tts", fromlist=["x"]).PIPER_AVAILABLE,
    reason="piper-tts is not installed",
)


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


def _voice_settings(**overrides: object) -> Settings:
    """A minimally valid Piper configuration; the voice file need not exist to build."""

    defaults: dict[str, object] = {
        "speech_tts_provider": "piper",
        "speech_tts_voice_dir": "/voices",
        "speech_tts_voices": "en=en_US-joe-medium",
    }
    return _settings(**{**defaults, **overrides})


# --------------------------------------------------------------------------------------
# Deny by default
# --------------------------------------------------------------------------------------


def test_default_configuration_is_the_pre_adapter_behaviour() -> None:
    """No provider has satisfied ADR-0004, so none may be a default."""

    providers = build_speech_providers(_settings())

    assert isinstance(providers.detector, MockVoiceActivityDetector)
    assert providers.transcriber is None
    assert providers.can_transcribe is False
    assert providers.synthesizer is None
    assert providers.can_synthesize is False
    assert providers.detector_id == MOCK_VAD_ID
    assert providers.transcriber_id == NO_TRANSCRIBER_ID
    assert providers.synthesizer_id == NO_SYNTHESIZER_ID


def test_a_detector_is_always_present() -> None:
    """Endpointing and barge-in must work with or without a model."""

    detector, identifier = build_voice_activity_detector(_settings())
    assert detector is not None
    assert identifier == MOCK_VAD_ID


# --------------------------------------------------------------------------------------
# Invalid configuration fails at import, not at the first spoken turn
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("speech_vad_provider", "silero"),
        ("speech_vad_provider", ""),
        ("speech_stt_provider", "whisper"),
        ("speech_stt_provider", "openai"),
        ("speech_tts_provider", "coqui"),
        ("speech_tts_provider", ""),
    ],
)
def test_unknown_provider_name_is_rejected(field: str, value: str) -> None:
    with pytest.raises(ValueError) as error:
        _settings(**{field: value})
    assert field in str(error.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("speech_vad_mode", -1),
        ("speech_vad_mode", 4),
        ("speech_stt_beam_size", 0),
        ("speech_stt_language", "de"),
        ("speech_stt_language", "english"),
    ],
)
def test_out_of_range_speech_settings_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        _settings(**{field: value})


def test_auto_detect_language_is_the_empty_string() -> None:
    assert _settings().speech_stt_language == ""
    assert _settings(speech_stt_language="hi").speech_stt_language == "hi"


# --------------------------------------------------------------------------------------
# A configured provider that cannot be built is a startup error
# --------------------------------------------------------------------------------------


def test_configured_vad_without_the_extra_refuses_to_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falling back to the mock would hide that the configured detector is not running."""

    monkeypatch.setattr(f"{_PROVIDERS}.WEBRTC_VAD_AVAILABLE", False)
    with pytest.raises(PermanentAdapterError) as error:
        build_voice_activity_detector(_settings(speech_vad_provider="webrtc"))
    message = str(error.value)
    assert "pitchbot[webrtc-vad]" in message
    assert "silent downgrade" in message


def test_configured_stt_without_the_extra_refuses_to_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falling back to no transcriber would drop every utterance silently."""

    monkeypatch.setattr(f"{_PROVIDERS}.FASTER_WHISPER_AVAILABLE", False)
    with pytest.raises(PermanentAdapterError) as error:
        build_speech_to_text(_settings(speech_stt_provider="faster-whisper"))
    message = str(error.value)
    assert "pitchbot[faster-whisper]" in message
    assert "transcriber-unavailable" in message


def test_configured_tts_without_the_extra_refuses_to_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falling back would leave the browser speaking without anyone being told."""

    monkeypatch.setattr(f"{_PROVIDERS}.PIPER_AVAILABLE", False)
    with pytest.raises(PermanentAdapterError) as error:
        build_text_to_speech(_voice_settings())
    message = str(error.value)
    assert "pitchbot[piper-tts]" in message
    assert "browser's own voice" in message


def test_build_speech_providers_fails_once_for_a_misconfiguration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(f"{_PROVIDERS}.FASTER_WHISPER_AVAILABLE", False)
    with pytest.raises(PermanentAdapterError):
        build_speech_providers(
            _settings(speech_vad_provider="mock", speech_stt_provider="faster-whisper")
        )


def test_absent_extra_does_not_affect_the_default_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default path must not consult an optional dependency at all."""

    monkeypatch.setattr(f"{_PROVIDERS}.WEBRTC_VAD_AVAILABLE", False)
    monkeypatch.setattr(f"{_PROVIDERS}.FASTER_WHISPER_AVAILABLE", False)
    providers = build_speech_providers(_settings())
    assert isinstance(providers.detector, MockVoiceActivityDetector)
    assert providers.transcriber is None


# --------------------------------------------------------------------------------------
# Configured and available
# --------------------------------------------------------------------------------------


@pytest.mark.skipif(
    not __import__("pitchbot.adapters.webrtc_vad", fromlist=["x"]).WEBRTC_VAD_AVAILABLE,
    reason="webrtcvad is not installed",
)
def test_configured_vad_builds_the_real_detector() -> None:
    detector, identifier = build_voice_activity_detector(
        _settings(speech_vad_provider="webrtc", speech_vad_mode=3)
    )
    assert isinstance(detector, WebRtcVoiceActivityDetector)
    assert detector.mode == 3
    assert identifier == "py-webrtcvad"


@pytest.mark.skipif(
    not __import__("pitchbot.adapters.faster_whisper_stt", fromlist=["x"]).FASTER_WHISPER_AVAILABLE,
    reason="faster-whisper is not installed",
)
def test_configured_stt_builds_the_real_adapter_without_loading_weights() -> None:
    """Construction must not touch the model; that is what ``preload`` is for."""

    transcriber, identifier = build_speech_to_text(
        _settings(speech_stt_provider="faster-whisper", speech_stt_model="small")
    )
    assert isinstance(transcriber, FasterWhisperSpeechToTextAdapter)
    assert transcriber.model_size == "small"
    assert transcriber.is_loaded is False
    assert identifier == "faster-whisper:small"


@pytest.mark.skipif(
    not __import__("pitchbot.adapters.faster_whisper_stt", fromlist=["x"]).FASTER_WHISPER_AVAILABLE,
    reason="faster-whisper is not installed",
)
def test_declared_language_reaches_the_adapter() -> None:
    transcriber, _ = build_speech_to_text(
        _settings(speech_stt_provider="faster-whisper", speech_stt_language="hi")
    )
    assert isinstance(transcriber, FasterWhisperSpeechToTextAdapter)
    assert transcriber.language is LanguageCode.HINDI


@pytest.mark.skipif(
    not __import__("pitchbot.adapters.faster_whisper_stt", fromlist=["x"]).FASTER_WHISPER_AVAILABLE,
    reason="faster-whisper is not installed",
)
def test_empty_language_means_auto_detect() -> None:
    transcriber, _ = build_speech_to_text(_settings(speech_stt_provider="faster-whisper"))
    assert isinstance(transcriber, FasterWhisperSpeechToTextAdapter)
    assert transcriber.language is None


@pytest.mark.skipif(
    not __import__("pitchbot.adapters.faster_whisper_stt", fromlist=["x"]).FASTER_WHISPER_AVAILABLE,
    reason="faster-whisper is not installed",
)
@pytest.mark.asyncio
async def test_download_is_disabled_unless_explicitly_enabled() -> None:
    """Weights must never be fetched as a side effect of starting the app."""

    transcriber, _ = build_speech_to_text(
        _settings(
            speech_stt_provider="faster-whisper",
            speech_stt_model="large-v3",
            speech_stt_download_root="/pitchbot-nonexistent-root",
        )
    )
    assert isinstance(transcriber, FasterWhisperSpeechToTextAdapter)

    with pytest.raises(PermanentAdapterError) as error:
        await transcriber.preload()

    message = str(error.value)
    assert "allow_download=True" in message
    assert "downloading is disabled" in message


# --------------------------------------------------------------------------------------
# Enum surface
# --------------------------------------------------------------------------------------


def test_provider_enums_match_the_accepted_configuration_values() -> None:
    assert {item.value for item in VadProvider} == {"mock", "webrtc"}
    assert {item.value for item in SttProvider} == {"none", "faster-whisper"}
    assert {item.value for item in TtsProvider} == {"none", "piper"}


# --------------------------------------------------------------------------------------
# The wiring itself - the gap this module exists to close
# --------------------------------------------------------------------------------------


def test_build_service_passes_the_configured_providers_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Before this, ``_build_service`` never passed any of them.

    PR 33 and PR 34 landed real adapters that the running application could not construct,
    so they were reachable only from tests. This asserts the seam is actually connected;
    the private attributes are read deliberately, because "was it wired" is exactly what is
    under test.
    """

    from pitchbot.simulator import router as router_module

    monkeypatch.setattr(router_module, "settings", _settings())
    default_service = router_module._build_service()
    assert isinstance(default_service._speech_detector, MockVoiceActivityDetector)
    assert default_service._speech_transcriber is None
    assert default_service._speech_synthesizer is None

    class _StubDetector(MockVoiceActivityDetector):
        pass

    stub_detector = _StubDetector()
    stub_transcriber = object()
    stub_synthesizer = object()
    monkeypatch.setattr(
        router_module,
        "speech_providers",
        SpeechProviders(
            detector=stub_detector,
            transcriber=stub_transcriber,  # type: ignore[arg-type]
            synthesizer=stub_synthesizer,  # type: ignore[arg-type]
            detector_id="stub-detector",
            transcriber_id="stub-transcriber",
            synthesizer_id="stub-synthesizer",
        ),
    )
    wired_service = router_module._build_service()
    assert wired_service._speech_detector is stub_detector
    assert wired_service._speech_transcriber is stub_transcriber
    assert wired_service._speech_synthesizer is stub_synthesizer


def test_providers_are_built_at_import_so_a_misconfiguration_stops_startup() -> None:
    """Construction is module-level, so a bad provider fails before the app serves.

    Deliberately *not* inside ``_build_service``: building it at module scope means an
    unbuildable provider raises while the router is being imported, which is as early as
    the failure can surface. The refusal itself is covered by
    ``test_build_speech_providers_fails_once_for_a_misconfiguration``.
    """

    from pitchbot.simulator import router as router_module

    assert isinstance(router_module.speech_providers, SpeechProviders)
    assert router_module.speech_providers.detector is not None


# --------------------------------------------------------------------------------------
# Text to speech
# --------------------------------------------------------------------------------------


def test_no_synthesizer_is_configured_by_default() -> None:
    """Absence is a working fallback here: the browser speaks the reply itself."""

    synthesizer, identifier = build_text_to_speech(_settings())

    assert synthesizer is None
    assert identifier == NO_SYNTHESIZER_ID


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"speech_tts_voice_dir": ""}, "speech_tts_voice_dir"),
        ({"speech_tts_voices": ""}, "speech_tts_voices"),
    ],
)
def test_an_enabled_provider_with_nothing_to_speak_with_is_rejected(
    overrides: dict[str, object],
    expected: str,
) -> None:
    """A synthesiser with no voice would produce a server that is silently mute."""

    with pytest.raises(ValueError) as error:
        _voice_settings(**overrides)

    assert expected in str(error.value)


@pytest.mark.parametrize(
    "value",
    ["en_US-joe-medium", "en=", "=en_US-joe-medium", "de=some-voice"],
)
def test_a_malformed_voice_mapping_is_rejected_at_import(value: str) -> None:
    with pytest.raises(ValueError):
        _voice_settings(speech_tts_voices=value)


def test_a_voice_mapping_is_parsed_into_languages() -> None:
    assert parse_voice_map("en=en_US-joe-medium, hi=hi_IN-pratham-medium") == {
        LanguageCode.ENGLISH: "en_US-joe-medium",
        LanguageCode.HINDI: "hi_IN-pratham-medium",
    }
    assert parse_voice_map("") == {}


def test_a_language_mapped_twice_is_refused_rather_than_resolved_by_ordering() -> None:
    with pytest.raises(PermanentAdapterError) as error:
        parse_voice_map("en=en_US-joe-medium,en=en_US-libritts_r-medium")

    assert "mapped twice" in str(error.value)


@requires_piper
def test_a_voice_with_no_reviewed_license_is_refused() -> None:
    """The licence decision must not be made by whoever edits the .env file."""

    with pytest.raises(PermanentAdapterError) as error:
        build_text_to_speech(_voice_settings(speech_tts_voices="en=some-unreviewed-voice"))

    assert "reviewed license" in str(error.value)


@requires_piper
def test_a_non_commercial_voice_is_denied_by_default() -> None:
    """Every reviewed Piper Hindi voice is non-commercial; PitchBot is a sales assistant."""

    with pytest.raises(PermanentAdapterError) as error:
        build_text_to_speech(_voice_settings(speech_tts_voices="hi=hi_IN-pratham-medium"))

    assert "does not permit commercial use" in str(error.value)


@requires_piper
def test_a_non_commercial_voice_is_allowed_only_when_explicitly_enabled() -> None:
    synthesizer, identifier = build_text_to_speech(
        _voice_settings(
            speech_tts_voices="hi=hi_IN-pratham-medium",
            speech_tts_allow_non_commercial=True,
        )
    )

    assert isinstance(synthesizer, PiperTextToSpeechAdapter)
    assert identifier == "piper:hi_IN-pratham-medium"


@requires_piper
def test_a_configured_voice_builds_the_adapter_without_loading_weights() -> None:
    """Construction must not touch the model file; the lifespan preloads it."""

    synthesizer, identifier = build_text_to_speech(
        _voice_settings(speech_tts_voices="en=en_US-joe-medium")
    )

    assert isinstance(synthesizer, PiperTextToSpeechAdapter)
    assert identifier == "piper:en_US-joe-medium"
    assert synthesizer.registry.languages == {LanguageCode.ENGLISH}
    assert synthesizer.synthesis is None


@requires_piper
def test_deterministic_synthesis_is_opt_in() -> None:
    synthesizer, _ = build_text_to_speech(_voice_settings(speech_tts_deterministic=True))

    assert isinstance(synthesizer, PiperTextToSpeechAdapter)
    assert synthesizer.synthesis is DETERMINISTIC_SYNTHESIS


@requires_piper
@pytest.mark.asyncio
async def test_preload_loads_both_models_and_is_a_no_op_by_default() -> None:
    """A denied voice or a missing model must stop startup, not one conversation."""

    await preload_speech_providers(build_speech_providers(_settings()))

    providers = build_speech_providers(_voice_settings())
    with pytest.raises(PermanentAdapterError) as error:
        await preload_speech_providers(providers)

    assert "model file not found" in str(error.value)


# --------------------------------------------------------------------------------------
# A language whose engine Piper cannot license
# --------------------------------------------------------------------------------------


def test_supertonic_is_off_by_default_and_piper_still_serves_everything() -> None:
    """The route must be opt-in: its weights carry a content-disclosure obligation."""

    adapter, describe = build_text_to_speech(_voice_settings())

    assert not isinstance(adapter, LanguageRoutedTextToSpeech)
    assert "supertonic" not in describe


def test_an_unknown_language_in_the_supertonic_route_refuses_to_start() -> None:
    with pytest.raises(PermanentAdapterError, match="unknown language"):
        build_text_to_speech(_voice_settings(speech_tts_supertonic_languages="klingon"))


def test_a_language_supertonic_does_not_have_refuses_to_start() -> None:
    """Telugu is absent from the model, so routing it there is a silent wrong voice."""

    with pytest.raises(PermanentAdapterError, match="not offered for 'te'"):
        build_text_to_speech(_voice_settings(speech_tts_supertonic_languages="te"))


def test_a_missing_supertonic_dependency_refuses_to_fall_back_to_piper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falling back would ship the exact voice this project denies.

    The languages routed to Supertonic are, by construction, the ones Piper cannot serve
    under a commercial licence. Quietly serving them with Piper anyway would turn a
    licensing decision into a startup accident.
    """

    monkeypatch.setattr(f"{_PROVIDERS}.SUPERTONIC_AVAILABLE", False)

    with pytest.raises(PermanentAdapterError, match="Refusing to fall back"):
        build_text_to_speech(_voice_settings(speech_tts_supertonic_languages="hi"))


def test_hindi_is_routed_away_from_piper_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(f"{_PROVIDERS}.SUPERTONIC_AVAILABLE", True)

    adapter, describe = build_text_to_speech(_voice_settings(speech_tts_supertonic_languages="hi"))

    assert isinstance(adapter, LanguageRoutedTextToSpeech)
    assert adapter.routed_languages == frozenset({LanguageCode.HINDI})
    assert isinstance(adapter.adapter_for(LanguageCode.HINDI), SupertonicTextToSpeechAdapter)
    assert isinstance(adapter.adapter_for(LanguageCode.ENGLISH), PiperTextToSpeechAdapter)
    assert "supertonic:hi" in describe
