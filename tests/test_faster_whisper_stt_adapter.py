"""Tests for the optional faster-whisper speech-to-text adapter.

Three properties are load-bearing here and are asserted directly rather than assumed.

*The dependency is optional.* Every test either runs with ``faster_whisper`` absent or is
skipped by :data:`requires_faster_whisper`, and the module-level import of
``pitchbot.adapters.faster_whisper_stt`` is itself part of the assertion. The absent-import
path is covered even when the package *is* installed, by forcing the import to fail.

*Nothing is downloaded.* No test reaches the network. Model weights are only loaded when
``PITCHBOT_WHISPER_MODEL`` names a size the machine has already cached, and the adapter
passes ``local_files_only=True`` regardless, so a test run cannot trigger a download.

*The single-final invariant is enforced.* ``SpeechTurnPipeline._best_transcript`` keeps the
**last final** transcript, so an adapter that emitted a final per segment would silently
discard everything the buyer said before the last one. That is asserted explicitly.
"""

from __future__ import annotations

import array
import asyncio
import os
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from pitchbot.adapters.contracts import AudioChunk, SpeechToTextAdapter, TranscriptChunk
from pitchbot.adapters.errors import PermanentAdapterError
from pitchbot.adapters.faster_whisper_stt import (
    ALGORITHM,
    DEFAULT_MAX_AUDIO_SECONDS,
    DEFAULT_MIN_LANGUAGE_PROBABILITY,
    DEFAULT_MODEL_SIZE,
    FASTER_WHISPER_AVAILABLE,
    INSTALL_HINT,
    KNOWN_MODEL_LICENSES,
    LICENSE,
    PROVIDER_ID,
    SUPPORTED_SAMPLE_RATE_HZ,
    FasterWhisperSpeechToTextAdapter,
    ModelLicense,
    _decode_pcm,
    _import,
    installed_distribution,
    model_license,
    require_faster_whisper,
)
from pitchbot.domain import LanguageCode

requires_faster_whisper = pytest.mark.skipif(
    not FASTER_WHISPER_AVAILABLE,
    reason=f"faster-whisper is not installed; install the optional extra: {INSTALL_HINT}",
)

_MODEL_ENV = "PITCHBOT_WHISPER_MODEL"
_MODEL_SIZE = os.environ.get(_MODEL_ENV, "")

requires_model = pytest.mark.skipif(
    not FASTER_WHISPER_AVAILABLE or not _MODEL_SIZE,
    reason=(
        f"set {_MODEL_ENV} to a Whisper size already cached on this machine "
        "(e.g. 'small'); weights are never downloaded by a test run"
    ),
)

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def _pcm(samples: list[int]) -> bytes:
    return array.array("h", samples).tobytes()


async def _stream(
    payload: bytes,
    *,
    sample_rate_hz: int = SUPPORTED_SAMPLE_RATE_HZ,
    frame_bytes: int = 8_000,
) -> AsyncIterator[AudioChunk]:
    if not payload:
        return
    for index in range(0, len(payload), frame_bytes):
        yield AudioChunk(
            data=payload[index : index + frame_bytes],
            captured_at=_EPOCH,
            sequence=index // frame_bytes,
            sample_rate_hz=sample_rate_hz,
        )


async def _collect(
    adapter: FasterWhisperSpeechToTextAdapter,
    audio: AsyncIterator[AudioChunk],
) -> list[TranscriptChunk]:
    return [chunk async for chunk in adapter.transcribe(audio)]


def _adapter(**kwargs: object) -> FasterWhisperSpeechToTextAdapter:
    options: dict[str, object] = {"model_size": _MODEL_SIZE or DEFAULT_MODEL_SIZE}
    options.update(kwargs)
    return FasterWhisperSpeechToTextAdapter(**options)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# Optionality
# --------------------------------------------------------------------------------------


def test_module_import_does_not_require_faster_whisper() -> None:
    assert isinstance(FASTER_WHISPER_AVAILABLE, bool)
    assert PROVIDER_ID == "faster-whisper"
    assert ALGORITHM == "whisper-encoder-decoder-ctranslate2"
    assert "pitchbot[faster-whisper]" in INSTALL_HINT


def test_absent_import_is_reported_as_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Covers the absent branch even where faster-whisper is installed."""

    def _raise(name: str) -> object:
        raise ImportError(f"no module named {name}")

    monkeypatch.setattr("pitchbot.adapters.faster_whisper_stt.importlib.import_module", _raise)
    assert _import("faster_whisper") is None


@pytest.mark.parametrize("missing", ["_MODULE", "_NUMPY"])
def test_require_names_the_extra_when_absent(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    monkeypatch.setattr(f"pitchbot.adapters.faster_whisper_stt.{missing}", None)
    with pytest.raises(PermanentAdapterError) as error:
        require_faster_whisper()
    assert INSTALL_HINT in str(error.value)


@requires_faster_whisper
def test_require_returns_modules_when_present() -> None:
    module, numpy = require_faster_whisper()
    assert module is not None and numpy is not None
    distribution = installed_distribution()
    assert distribution is not None and distribution[1]


# --------------------------------------------------------------------------------------
# Licensing
# --------------------------------------------------------------------------------------


def test_package_and_weights_are_permissive() -> None:
    """Unlike the Piper voices, nothing here is non-commercial - but it is checked."""

    assert LICENSE == "MIT"
    assert KNOWN_MODEL_LICENSES
    assert all(item.permits_commercial_use for item in KNOWN_MODEL_LICENSES.values())
    assert model_license(DEFAULT_MODEL_SIZE).permits_commercial_use is True


def test_unreviewed_model_is_refused() -> None:
    with pytest.raises(PermanentAdapterError) as error:
        model_license("some-unreviewed-finetune")
    assert "KNOWN_MODEL_LICENSES" in str(error.value)
    with pytest.raises(PermanentAdapterError):
        FasterWhisperSpeechToTextAdapter(model_size="some-unreviewed-finetune")


def test_model_license_requires_identifier_and_reference() -> None:
    with pytest.raises(ValueError):
        ModelLicense(identifier=" ", permits_commercial_use=True, reference_url="https://x")
    with pytest.raises(ValueError):
        ModelLicense(identifier="MIT", permits_commercial_use=True, reference_url="")


def test_default_model_is_small_because_smaller_ones_emit_the_wrong_script() -> None:
    """Measured: ``tiny`` returns romanised Latin and ``base`` returns Urdu for Hindi.

    Pinned so that "make it faster by using base" is a deliberate change that has to argue
    with this test rather than a silent default edit.
    """

    assert DEFAULT_MODEL_SIZE == "small"


# --------------------------------------------------------------------------------------
# Construction and bounds
# --------------------------------------------------------------------------------------


def test_adapter_satisfies_the_contract() -> None:
    """``SpeechToTextAdapter`` is not ``@runtime_checkable``, so assert the MRO."""

    assert SpeechToTextAdapter in FasterWhisperSpeechToTextAdapter.__mro__
    assert callable(FasterWhisperSpeechToTextAdapter().transcribe)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"beam_size": 0},
        {"max_audio_seconds": 0},
        {"max_audio_seconds": -1.0},
        {"min_language_probability": -0.1},
        {"min_language_probability": 1.1},
    ],
)
def test_adapter_rejects_invalid_configuration(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        FasterWhisperSpeechToTextAdapter(**kwargs)  # type: ignore[arg-type]


def test_defaults_are_cpu_only_and_download_free() -> None:
    adapter = FasterWhisperSpeechToTextAdapter()
    assert adapter.is_loaded is False
    assert adapter.max_audio_bytes == int(DEFAULT_MAX_AUDIO_SECONDS * SUPPORTED_SAMPLE_RATE_HZ * 2)
    provenance = adapter.provenance()
    assert provenance.device == "cpu"
    assert provenance.compute_type == "int8"
    assert provenance.sample_rate_hz == SUPPORTED_SAMPLE_RATE_HZ
    assert DEFAULT_MIN_LANGUAGE_PROBABILITY == 0.5


def test_decode_pcm_maps_int16_into_unit_range() -> None:
    decoded = _decode_pcm(_pcm([0, 32767, -32768, 16384]))
    assert decoded[0] == 0.0
    assert 0.999 < decoded[1] < 1.0
    assert decoded[2] == -1.0
    assert decoded[3] == 0.5
    assert all(-1.0 <= value <= 1.0 for value in decoded)


# --------------------------------------------------------------------------------------
# Audio is refused rather than repaired - no model needed
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_16khz_audio_is_refused_before_the_model_loads() -> None:
    """Whisper would not fail on 22.05 kHz - it would transcribe pitch-shifted speech."""

    adapter = FasterWhisperSpeechToTextAdapter()
    with pytest.raises(PermanentAdapterError) as error:
        await _collect(adapter, _stream(_pcm([0] * 1_000), sample_rate_hz=22_050))
    message = str(error.value)
    assert "16000 Hz" in message
    assert "22050" in message
    assert adapter.is_loaded is False, "the rate check must precede model loading"


@pytest.mark.asyncio
async def test_oversize_utterance_is_refused_before_the_model_loads() -> None:
    adapter = FasterWhisperSpeechToTextAdapter(max_audio_seconds=0.1)
    with pytest.raises(PermanentAdapterError) as error:
        await _collect(adapter, _stream(_pcm([0] * 16_000)))
    assert "max_audio_seconds=0.1" in str(error.value)
    assert adapter.is_loaded is False


@pytest.mark.asyncio
async def test_odd_byte_count_is_refused() -> None:
    adapter = FasterWhisperSpeechToTextAdapter()

    async def _odd() -> AsyncIterator[AudioChunk]:
        yield AudioChunk(data=b"\x00\x00\x00", captured_at=_EPOCH, sequence=0)

    with pytest.raises(PermanentAdapterError) as error:
        await _collect(adapter, _odd())
    assert "16-bit samples" in str(error.value)


@requires_faster_whisper
@pytest.mark.asyncio
async def test_missing_model_reports_how_to_prefetch_rather_than_downloading() -> None:
    """A model that is not cached must fail loudly, never fetch hundreds of megabytes."""

    adapter = FasterWhisperSpeechToTextAdapter(
        model_size="large-v3",
        download_root="/pitchbot-nonexistent-model-root",
    )
    with pytest.raises(PermanentAdapterError) as error:
        await _collect(adapter, _stream(_pcm([0] * 1_000)))
    message = str(error.value)
    assert "allow_download=True" in message
    assert "downloading is disabled" in message


# --------------------------------------------------------------------------------------
# Real transcription
# --------------------------------------------------------------------------------------


@requires_model
@pytest.mark.asyncio
async def test_stream_ends_with_exactly_one_final_carrying_the_whole_transcript() -> None:
    """``SpeechTurnPipeline`` keeps only the last final, so there must be exactly one.

    A per-segment final would silently discard everything said before the last segment.
    """

    adapter = _adapter()
    tone = _pcm([int(8_000 * ((index % 40) - 20) / 20) for index in range(16_000 * 2)])
    chunks = await _collect(adapter, _stream(tone))

    finals = [chunk for chunk in chunks if chunk.is_final]
    assert len(finals) == 1
    assert chunks[-1].is_final is True
    assert [chunk.sequence for chunk in chunks] == list(range(len(chunks)))
    assert all(0.0 <= chunk.confidence <= 1.0 for chunk in chunks)


@requires_model
@pytest.mark.asyncio
async def test_silence_yields_one_empty_final_and_an_unknown_language() -> None:
    """Whisper labels silence confidently; the adapter must not pass that through.

    Measured: two seconds of digital silence is reported as ``en`` with probability 0.362.
    Below ``min_language_probability`` that becomes ``UNKNOWN`` rather than a fabricated
    English detection, and the empty text lets the pipeline report
    ``no-speech-recognized``.
    """

    adapter = _adapter()
    chunks = await _collect(adapter, _stream(_pcm([0] * 16_000 * 2)))

    assert len([chunk for chunk in chunks if chunk.is_final]) == 1
    final = chunks[-1]
    assert final.text == ""
    assert final.language is LanguageCode.UNKNOWN


@requires_model
@pytest.mark.asyncio
async def test_partials_can_be_disabled() -> None:
    adapter = _adapter(emit_partials=False)
    chunks = await _collect(adapter, _stream(_pcm([0] * 16_000)))
    assert len(chunks) == 1
    assert chunks[0].is_final is True


@requires_model
@pytest.mark.asyncio
async def test_preload_loads_the_model_before_any_call() -> None:
    """Model construction stalls the loop, so it must be movable to startup."""

    adapter = _adapter()
    assert adapter.is_loaded is False
    await adapter.preload()
    assert adapter.is_loaded is True


@requires_model
@pytest.mark.asyncio
async def test_transcription_does_not_block_the_event_loop() -> None:
    """Segments are advanced on a worker thread so the audio socket keeps serving."""

    adapter = _adapter()
    await adapter.preload()
    tone = _pcm([int(6_000 * ((index % 32) - 16) / 16) for index in range(16_000 * 3)])

    gaps: list[float] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        previous = time.perf_counter()
        while not stop.is_set():
            await asyncio.sleep(0.005)
            now = time.perf_counter()
            gaps.append(now - previous)
            previous = now

    beat = asyncio.create_task(heartbeat())
    try:
        await _collect(adapter, _stream(tone))
    finally:
        stop.set()
        await beat

    assert gaps, "the heartbeat must have run during transcription"
    assert max(gaps) < 0.5, f"event loop stalled for {max(gaps):.3f}s during transcription"


@requires_model
@pytest.mark.asyncio
async def test_forced_language_is_reported_as_declared() -> None:
    adapter = _adapter(language=LanguageCode.HINDI)
    chunks = await _collect(adapter, _stream(_pcm([0] * 16_000)))
    assert chunks[-1].language is LanguageCode.HINDI


@requires_model
@pytest.mark.asyncio
async def test_provenance_records_package_and_model_licenses() -> None:
    adapter = _adapter()
    await adapter.preload()
    provenance = adapter.provenance()

    assert provenance.provider_id == PROVIDER_ID
    assert provenance.package_license == LICENSE
    assert provenance.model_license == "MIT"
    assert provenance.model_size == _MODEL_SIZE
    assert provenance.package_version
