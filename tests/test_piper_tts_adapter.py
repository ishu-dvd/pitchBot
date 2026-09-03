"""Tests for the optional Piper text-to-speech adapter.

Three properties are load-bearing here and are asserted directly rather than assumed.

*The dependency is optional.* Every test in this file either runs with ``piper`` absent or
is skipped by :data:`requires_piper`, and the module-level import of
``pitchbot.adapters.piper_tts`` is itself part of the assertion - it must succeed in an
environment that has never seen the package. The absent-import path is covered even when
Piper *is* installed, by forcing the import to fail.

*Nothing is downloaded.* No test reaches the network. Voices are operator-supplied files
addressed by an explicit path; tests that need one are skipped unless
``PITCHBOT_PIPER_VOICE_DIR`` points at a directory that already contains it.

*The license gate is real logic, not documentation.* The registry, the license catalog,
and every refusal path are dependency-free, so those tests always run - including the one
that pins the finding that no published Piper Hindi voice is cleared for commercial use.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from pathlib import Path

import pytest

from pitchbot.adapters.contracts import SynthesizedAudioChunk, TextToSpeechAdapter
from pitchbot.adapters.errors import PermanentAdapterError
from pitchbot.adapters.piper_tts import (
    ALGORITHM,
    CC0,
    CC_BY_4_0,
    CC_BY_NC_SA_4_0,
    DETERMINISTIC_SYNTHESIS,
    INSTALL_HINT,
    KNOWN_VOICE_LICENSES,
    LICENSE,
    MODEL_WEIGHTS,
    PCM_MEDIA_TYPE,
    PIPER_AVAILABLE,
    PROVIDER_ID,
    PiperSynthesisOptions,
    PiperTextToSpeechAdapter,
    PiperVoiceRegistry,
    PiperVoiceSpec,
    VoiceLicense,
    _import_piper,
    installed_distribution,
    require_piper,
    voice_spec,
)
from pitchbot.domain import LanguageCode

requires_piper = pytest.mark.skipif(
    not PIPER_AVAILABLE,
    reason=f"piper is not installed; install the optional extra: {INSTALL_HINT}",
)

# The voice used for synthesis tests is CC0, so it is the one voice in the catalog that a
# commercial deployment could actually use. Tests are skipped when it is not present:
# voices are never downloaded, least of all by a test run.
_VOICE_ID = "en_US-joe-medium"
_VOICE_DIR_ENV = "PITCHBOT_PIPER_VOICE_DIR"


def _voice_path() -> Path | None:
    directory = os.environ.get(_VOICE_DIR_ENV)
    if not directory:
        return None
    model = Path(directory) / f"{_VOICE_ID}.onnx"
    config = model.with_suffix(".onnx.json")
    if not model.is_file() or not config.is_file():
        return None
    return model


requires_voice = pytest.mark.skipif(
    not PIPER_AVAILABLE or _voice_path() is None,
    reason=(
        f"set {_VOICE_DIR_ENV} to a directory containing {_VOICE_ID}.onnx "
        "and its .onnx.json sidecar; voices are never downloaded"
    ),
)


def _english_spec(path: Path) -> PiperVoiceSpec:
    return voice_spec(_VOICE_ID, LanguageCode.ENGLISH, path)


def _adapter(path: Path, **kwargs: object) -> PiperTextToSpeechAdapter:
    registry = PiperVoiceRegistry([_english_spec(path)])
    return PiperTextToSpeechAdapter(registry, synthesis=DETERMINISTIC_SYNTHESIS, **kwargs)  # type: ignore[arg-type]


async def _collect(
    adapter: PiperTextToSpeechAdapter,
    text: str,
    language: LanguageCode = LanguageCode.ENGLISH,
) -> list[SynthesizedAudioChunk]:
    return [chunk async for chunk in adapter.synthesize(text, language)]


# --------------------------------------------------------------------------------------
# Optionality
# --------------------------------------------------------------------------------------


def test_module_import_does_not_require_piper() -> None:
    """Importing this module must work in either state, so the flag is just a bool."""

    assert isinstance(PIPER_AVAILABLE, bool)
    assert PROVIDER_ID == "piper"
    assert ALGORITHM == "vits-onnx"
    assert "pitchbot[piper-tts]" in INSTALL_HINT


def test_absent_import_is_reported_as_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Covers the piper-absent branch even in an environment that has piper installed."""

    def _raise(name: str) -> object:
        raise ImportError(f"no module named {name}")

    monkeypatch.setattr("pitchbot.adapters.piper_tts.importlib.import_module", _raise)
    assert _import_piper() is None


def test_require_piper_names_the_extra_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pitchbot.adapters.piper_tts._MODULE", None)
    with pytest.raises(PermanentAdapterError) as error:
        require_piper()
    assert INSTALL_HINT in str(error.value)


@requires_piper
def test_require_piper_returns_the_module_when_present() -> None:
    assert require_piper() is not None
    distribution = installed_distribution()
    assert distribution is not None
    name, version = distribution
    assert name in {"piper-tts", "piper"}
    assert version


def test_runtime_license_is_recorded_as_copyleft() -> None:
    """The runtime is GPL-3.0-or-later, unlike the permissive webrtc-vad extra.

    Pinned as a test because it is the fact that dictates the module's shape: Piper is
    never vendored or hard-depended on, and this constant is what a distribution review
    reads.
    """

    assert LICENSE == "GPL-3.0-or-later"
    assert "never downloaded" in MODEL_WEIGHTS


# --------------------------------------------------------------------------------------
# Voice licensing - the finding, encoded
# --------------------------------------------------------------------------------------


def test_no_published_hindi_voice_permits_commercial_use() -> None:
    """Reviewed 2026-09-03: every ``hi_IN`` Piper voice is non-commercial or unresolved.

    PitchBot is a bilingual *sales* assistant, so this is a product-blocking finding
    rather than a footnote, and it is pinned here so that adding a commercially-usable
    Hindi voice is a deliberate act that updates this test.
    """

    hindi = {
        voice_id: license_
        for voice_id, license_ in KNOWN_VOICE_LICENSES.items()
        if voice_id.startswith("hi_")
    }
    assert hindi, "the catalog must carry the reviewed Hindi voices"
    assert not any(license_.permits_commercial_use for license_ in hindi.values())


def test_catalog_offers_at_least_one_commercially_usable_english_voice() -> None:
    usable = {
        voice_id
        for voice_id, license_ in KNOWN_VOICE_LICENSES.items()
        if voice_id.startswith("en_") and license_.permits_commercial_use
    }
    assert _VOICE_ID in usable
    assert KNOWN_VOICE_LICENSES[_VOICE_ID] is CC0


def test_unresolvable_license_is_treated_as_denied() -> None:
    """An unread license document cannot clear a voice through a gate."""

    unresolved = KNOWN_VOICE_LICENSES["hi_IN-rohan-medium"]
    assert "unresolved" in unresolved.identifier
    assert unresolved.permits_commercial_use is False


def test_license_requires_identifier_and_reference() -> None:
    with pytest.raises(ValueError):
        VoiceLicense(
            identifier=" ",
            permits_commercial_use=True,
            attribution_required=False,
            reference_url="https://example.invalid",
        )
    with pytest.raises(ValueError):
        VoiceLicense(
            identifier="X",
            permits_commercial_use=True,
            attribution_required=False,
            reference_url="",
        )


def test_voice_spec_refuses_an_unreviewed_voice() -> None:
    with pytest.raises(PermanentAdapterError) as error:
        voice_spec("en_US-not-reviewed-medium", LanguageCode.ENGLISH, Path("x.onnx"))
    assert "KNOWN_VOICE_LICENSES" in str(error.value)


def test_voice_spec_config_path_is_the_piper_sidecar() -> None:
    spec = voice_spec(_VOICE_ID, LanguageCode.ENGLISH, Path("/voices/en_US-joe-medium.onnx"))
    assert spec.config_path.name == "en_US-joe-medium.onnx.json"


def test_voice_spec_rejects_blank_identifier() -> None:
    with pytest.raises(ValueError):
        PiperVoiceSpec(
            voice_id="  ",
            language=LanguageCode.ENGLISH,
            model_path=Path("x.onnx"),
            license=CC0,
        )


# --------------------------------------------------------------------------------------
# Registry: deny by default, never fall back
# --------------------------------------------------------------------------------------


def test_registry_denies_non_commercial_voice_by_default() -> None:
    spec = voice_spec("hi_IN-pratham-medium", LanguageCode.HINDI, Path("hi.onnx"))
    registry = PiperVoiceRegistry([spec])
    with pytest.raises(PermanentAdapterError) as error:
        registry.resolve(LanguageCode.HINDI)
    message = str(error.value)
    assert "CC-BY-NC-SA-4.0" in message
    assert "allow_non_commercial=True" in message


def test_registry_serves_non_commercial_voice_only_when_asked() -> None:
    spec = voice_spec("hi_IN-pratham-medium", LanguageCode.HINDI, Path("hi.onnx"))
    registry = PiperVoiceRegistry([spec], allow_non_commercial=True)
    assert registry.resolve(LanguageCode.HINDI).voice_id == "hi_IN-pratham-medium"
    assert registry.allow_non_commercial is True


def test_registry_has_no_fallback_voice() -> None:
    """A mismatched voice produces fluent wrong audio, so an unmapped language must fail.

    Piper does not reject Devanagari fed to an English voice; it synthesises confident
    nonsense. Falling back to "whatever was configured" would therefore be silent
    corruption rather than degraded service.
    """

    registry = PiperVoiceRegistry([voice_spec(_VOICE_ID, LanguageCode.ENGLISH, Path("x.onnx"))])
    for language in (LanguageCode.HINDI, LanguageCode.MIXED, LanguageCode.UNKNOWN):
        with pytest.raises(PermanentAdapterError) as error:
            registry.resolve(language)
        assert language.value in str(error.value)
    assert registry.languages == frozenset({LanguageCode.ENGLISH})


def test_registry_refuses_an_ambiguous_mapping() -> None:
    first = voice_spec(_VOICE_ID, LanguageCode.ENGLISH, Path("a.onnx"))
    second = voice_spec("en_US-libritts_r-medium", LanguageCode.ENGLISH, Path("b.onnx"))
    with pytest.raises(PermanentAdapterError) as error:
        PiperVoiceRegistry([first, second])
    assert "mapped twice" in str(error.value)


def test_registry_requires_at_least_one_voice() -> None:
    with pytest.raises(PermanentAdapterError):
        PiperVoiceRegistry([])


def test_registry_accepts_an_explicitly_licensed_voice_outside_the_catalog() -> None:
    """Adding a language is a data change: a spec plus a stated license, no code change."""

    spec = PiperVoiceSpec(
        voice_id="mr_IN-example-medium",
        language=LanguageCode.MIXED,
        model_path=Path("mr.onnx"),
        license=CC_BY_4_0,
    )
    assert PiperVoiceRegistry([spec]).resolve(LanguageCode.MIXED) is spec


# --------------------------------------------------------------------------------------
# Adapter construction and bounds
# --------------------------------------------------------------------------------------


def _dummy_registry() -> PiperVoiceRegistry:
    return PiperVoiceRegistry([voice_spec(_VOICE_ID, LanguageCode.ENGLISH, Path("x.onnx"))])


@pytest.mark.parametrize(("chars", "chunks"), [(0, 1), (1, 0), (-1, 1)])
def test_adapter_rejects_non_positive_bounds(chars: int, chunks: int) -> None:
    with pytest.raises(ValueError):
        PiperTextToSpeechAdapter(_dummy_registry(), max_text_chars=chars, max_chunks=chunks)


def test_adapter_satisfies_the_contract() -> None:
    """``TextToSpeechAdapter`` is not ``@runtime_checkable``, so assert the MRO instead."""

    assert TextToSpeechAdapter in PiperTextToSpeechAdapter.__mro__
    adapter = PiperTextToSpeechAdapter(_dummy_registry())
    assert callable(adapter.synthesize)


@pytest.mark.asyncio
async def test_over_long_text_is_refused_before_any_model_is_touched() -> None:
    """The bound is checked before voice resolution, so it needs neither piper nor a file."""

    adapter = PiperTextToSpeechAdapter(_dummy_registry(), max_text_chars=10)
    with pytest.raises(PermanentAdapterError) as error:
        await _collect(adapter, "x" * 11)
    assert "max_text_chars=10" in str(error.value)


@pytest.mark.asyncio
async def test_missing_model_file_is_refused_without_downloading(tmp_path: Path) -> None:
    absent = tmp_path / f"{_VOICE_ID}.onnx"
    adapter = PiperTextToSpeechAdapter(
        PiperVoiceRegistry([voice_spec(_VOICE_ID, LanguageCode.ENGLISH, absent)])
    )
    with pytest.raises(PermanentAdapterError) as error:
        await _collect(adapter, "hello")
    message = str(error.value)
    assert "never downloaded" in message or INSTALL_HINT in message


@requires_piper
@pytest.mark.asyncio
async def test_missing_config_sidecar_is_refused(tmp_path: Path) -> None:
    model = tmp_path / f"{_VOICE_ID}.onnx"
    model.write_bytes(b"not a real model")
    adapter = PiperTextToSpeechAdapter(
        PiperVoiceRegistry([voice_spec(_VOICE_ID, LanguageCode.ENGLISH, model)])
    )
    with pytest.raises(PermanentAdapterError) as error:
        await _collect(adapter, "hello")
    assert ".onnx.json" in str(error.value)


def test_sample_rate_requires_a_loaded_voice() -> None:
    adapter = PiperTextToSpeechAdapter(_dummy_registry())
    spec = voice_spec(_VOICE_ID, LanguageCode.ENGLISH, Path("x.onnx"))
    with pytest.raises(PermanentAdapterError) as error:
        adapter.voice_sample_rate_hz(spec)
    assert "not loaded" in str(error.value)


def test_synthesis_options_reject_negative_values() -> None:
    with pytest.raises(ValueError):
        PiperSynthesisOptions(volume=-0.1)
    with pytest.raises(ValueError):
        PiperSynthesisOptions(noise_scale=-1.0)
    assert DETERMINISTIC_SYNTHESIS.noise_scale == 0.0
    assert DETERMINISTIC_SYNTHESIS.noise_w_scale == 0.0


# --------------------------------------------------------------------------------------
# Real synthesis
# --------------------------------------------------------------------------------------


@requires_voice
@pytest.mark.asyncio
async def test_synthesis_streams_sentences_with_exactly_one_terminal_chunk() -> None:
    path = _voice_path()
    assert path is not None
    chunks = await _collect(_adapter(path), "One thing. Two things. Three things.")

    assert len(chunks) > 1, "sentences should stream rather than arrive as one blob"
    assert [chunk.sequence for chunk in chunks] == list(range(len(chunks)))
    assert [chunk.is_final for chunk in chunks].count(True) == 1
    assert chunks[-1].is_final is True
    assert all(chunk.media_type == PCM_MEDIA_TYPE for chunk in chunks)
    assert all(chunk.sample_rate_hz > 0 for chunk in chunks)
    assert all(len(chunk.data) % 2 == 0 for chunk in chunks), "16-bit PCM is 2-byte aligned"


@requires_voice
@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", "   ", "...", "\n\t "])
async def test_text_with_no_speech_still_terminates_the_stream(text: str) -> None:
    """Piper yields zero chunks for these, which would strand a consumer on ``is_final``."""

    path = _voice_path()
    assert path is not None
    chunks = await _collect(_adapter(path), text)

    assert len(chunks) == 1
    assert chunks[0].is_final is True
    assert chunks[0].data == b""
    assert chunks[0].sequence == 0
    assert chunks[0].sample_rate_hz > 0


@requires_voice
@pytest.mark.asyncio
async def test_deterministic_option_makes_synthesis_reproducible() -> None:
    path = _voice_path()
    assert path is not None
    adapter = _adapter(path)

    first = b"".join(chunk.data for chunk in await _collect(adapter, "Say it."))
    second = b"".join(chunk.data for chunk in await _collect(adapter, "Say it."))

    assert first == second
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()
    assert first, "deterministic synthesis must still produce audio"


@requires_voice
@pytest.mark.asyncio
async def test_synthesis_does_not_block_the_event_loop() -> None:
    """The reason chunks are advanced on a worker thread.

    Piper runs ONNX inference per sentence. Doing that inline would stall every other task
    on the loop - including the audio socket that barge-in depends on - for the whole
    utterance. A heartbeat runs concurrently and must keep ticking.

    The voice is preloaded first, because loading is a separate and much larger stall that
    :func:`test_voice_loading_is_the_expensive_step_and_preload_moves_it` covers. Mixing
    the two would let a passing synthesis hide behind a load, or a slow load fail a test
    about synthesis.
    """

    path = _voice_path()
    assert path is not None
    adapter = _adapter(path)
    await adapter.preload()
    text = " ".join(f"Sentence number {index} of this turn." for index in range(1, 9))

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
        chunks = await _collect(adapter, text)
    finally:
        stop.set()
        await beat

    assert len(chunks) > 1
    assert gaps, "the heartbeat must have run during synthesis"
    assert max(gaps) < 0.25, f"event loop stalled for {max(gaps):.3f}s during synthesis"


@requires_voice
@pytest.mark.asyncio
async def test_preload_loads_every_mapped_voice_before_any_call() -> None:
    """Loading holds the GIL, so it must be movable off the live path.

    Measured 2026-09-03: a lazy first synthesis stalls the loop for ~2.1 s while a
    preloaded one stalls it for ~20 ms. ``preload`` exists so that cost is paid at
    startup instead of by whichever caller arrives first.
    """

    path = _voice_path()
    assert path is not None
    adapter = _adapter(path)
    spec = _english_spec(path)

    assert adapter.is_loaded(spec) is False
    await adapter.preload()
    assert adapter.is_loaded(spec) is True
    assert adapter.voice_sample_rate_hz(spec) > 0


@pytest.mark.asyncio
async def test_preload_applies_the_license_gate_at_startup() -> None:
    """A denied voice must fail during preload, not on the first live call."""

    registry = PiperVoiceRegistry(
        [voice_spec("hi_IN-pratham-medium", LanguageCode.HINDI, Path("hi.onnx"))]
    )
    with pytest.raises(PermanentAdapterError) as error:
        await PiperTextToSpeechAdapter(registry).preload()
    assert "does not permit commercial use" in str(error.value)


@requires_voice
@pytest.mark.asyncio
async def test_concurrent_synthesis_through_one_adapter_is_consistent() -> None:
    """Measured safe, so the adapter does not serialize; this pins that it stays correct."""

    path = _voice_path()
    assert path is not None
    adapter = _adapter(path)
    texts = ["First caller.", "Second caller asks about pricing.", "Third."]

    serial = [b"".join(chunk.data for chunk in await _collect(adapter, text)) for text in texts]
    concurrent = await asyncio.gather(*(_collect(adapter, text) for text in texts))

    assert [b"".join(chunk.data for chunk in result) for result in concurrent] == serial


@requires_voice
@pytest.mark.asyncio
async def test_provenance_records_both_licenses() -> None:
    path = _voice_path()
    assert path is not None
    adapter = _adapter(path)
    spec = _english_spec(path)
    await _collect(adapter, "Warm the voice.")

    provenance = adapter.provenance(spec)

    assert provenance.provider_id == PROVIDER_ID
    assert provenance.runtime_license == LICENSE
    assert provenance.voice_license == CC0.identifier
    assert provenance.voice_permits_commercial_use is True
    assert provenance.sample_rate_hz > 0
    assert provenance.package_version


@requires_voice
@pytest.mark.asyncio
async def test_chunk_count_bound_is_enforced() -> None:
    path = _voice_path()
    assert path is not None
    adapter = _adapter(path, max_chunks=1)
    with pytest.raises(PermanentAdapterError) as error:
        await _collect(adapter, "One. Two. Three. Four.")
    assert "max_chunks=1" in str(error.value)


@requires_voice
@pytest.mark.asyncio
async def test_non_commercial_voice_is_refused_even_with_the_file_present() -> None:
    """The gate is on the license, not on whether the model happens to be installed."""

    path = _voice_path()
    assert path is not None
    mislabelled = PiperVoiceSpec(
        voice_id="hi_IN-pratham-medium",
        language=LanguageCode.ENGLISH,
        model_path=path,
        license=CC_BY_NC_SA_4_0,
    )
    adapter = PiperTextToSpeechAdapter(PiperVoiceRegistry([mislabelled]))
    with pytest.raises(PermanentAdapterError) as error:
        await _collect(adapter, "hello")
    assert "does not permit commercial use" in str(error.value)
