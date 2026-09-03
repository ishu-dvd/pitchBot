"""Tests for building speech providers from configuration.

The property under test is that this is the *only* place configuration becomes a provider,
and that it behaves in two specific ways:

*Deny by default.* With no configuration the result is exactly what shipped before the
real adapters existed - the byte-size mock detector and no transcriber at all - so a spoken
utterance is reported as ``transcriber-unavailable`` rather than invented.

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
from pitchbot.adapters.webrtc_vad import WebRtcVoiceActivityDetector
from pitchbot.config import Settings
from pitchbot.domain import LanguageCode
from pitchbot.speech.providers import (
    MOCK_VAD_ID,
    NO_TRANSCRIBER_ID,
    SpeechProviders,
    SttProvider,
    VadProvider,
    build_speech_providers,
    build_speech_to_text,
    build_voice_activity_detector,
)

_PROVIDERS = "pitchbot.speech.providers"


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# Deny by default
# --------------------------------------------------------------------------------------


def test_default_configuration_is_the_pre_adapter_behaviour() -> None:
    """No provider has satisfied ADR-0004, so none may be a default."""

    providers = build_speech_providers(_settings())

    assert isinstance(providers.detector, MockVoiceActivityDetector)
    assert providers.transcriber is None
    assert providers.can_transcribe is False
    assert providers.detector_id == MOCK_VAD_ID
    assert providers.transcriber_id == NO_TRANSCRIBER_ID


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


# --------------------------------------------------------------------------------------
# The wiring itself - the gap this module exists to close
# --------------------------------------------------------------------------------------


def test_build_service_passes_the_configured_providers_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Before this, ``_build_service`` never passed either one.

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

    class _StubDetector(MockVoiceActivityDetector):
        pass

    stub_detector = _StubDetector()
    stub_transcriber = object()
    monkeypatch.setattr(
        router_module,
        "speech_providers",
        SpeechProviders(
            detector=stub_detector,
            transcriber=stub_transcriber,  # type: ignore[arg-type]
            detector_id="stub-detector",
            transcriber_id="stub-transcriber",
        ),
    )
    wired_service = router_module._build_service()
    assert wired_service._speech_detector is stub_detector
    assert wired_service._speech_transcriber is stub_transcriber


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
