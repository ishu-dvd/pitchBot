"""Tests for speaking Hindi at all, and for sending each language to an engine that can.

PitchBot has never been able to say a Hindi word aloud in a deployment that sells anything.
Every published Piper Hindi voice reviewed is CC-BY-NC-SA or points at a licence that
returns 403, and this project treats an unread licence and a denied one identically. That
is not a missing voice file, it is a structural hole: one synthesiser served every
language, so a language its engine could not license was simply unspeakable.

Two pieces close it, and both are tested without the optional dependency installed - the
routing is pure, and the Supertonic adapter's contract is checked against a fake engine.

The measurement that justifies any of it is in `docs/BENCHMARKS.md`: Supertonic 3 at 8
steps scores **13.2% CER** on Hindi against **18.3%** for the Piper voice PitchBot may not
ship, and costs ~1,130 ms per sentence against Piper's 126-448 ms.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

import pytest

from pitchbot.adapters.contracts import SynthesizedAudioChunk
from pitchbot.adapters.errors import PermanentAdapterError
from pitchbot.adapters.routing_tts import LanguageRoutedTextToSpeech
from pitchbot.adapters.supertonic_tts import (
    SUPPORTED_LANGUAGES,
    SupertonicTextToSpeechAdapter,
    split_sentences,
)
from pitchbot.domain import LanguageCode


class RecordingAdapter:
    """Names itself in the audio, so a routing test can see which engine spoke."""

    def __init__(self, name: str, rate: int = 22_050) -> None:
        self.name = name
        self.calls: list[tuple[str, LanguageCode]] = []
        self._rate = rate

    async def synthesize(
        self,
        text: str,
        language: LanguageCode,
    ) -> AsyncIterator[SynthesizedAudioChunk]:
        self.calls.append((text, language))
        yield SynthesizedAudioChunk(
            data=self.name.encode(),
            sequence=0,
            is_final=True,
            media_type="audio/pcm",
            sample_rate_hz=self._rate,
        )


class FakeSupertonic:
    """A stand-in for the ONNX engine, so the adapter's contract is testable offline."""

    sample_rate = 44_100

    def __init__(self, *, fail: bool = False) -> None:
        self.spoken: list[tuple[str, str, int]] = []
        self._fail = fail

    def get_voice_style(self, name: str) -> str:
        if name == "missing":
            raise KeyError(name)
        return f"style:{name}"

    def synthesize(
        self,
        text: str,
        style: str,
        total_steps: int = 8,
        speed: float = 1.05,
        lang: str | None = None,
    ) -> tuple[object, object]:
        if self._fail:
            raise RuntimeError("engine exploded")
        self.spoken.append((text, lang or "", total_steps))
        # A plain list, not a numpy array: numpy arrives with the optional extra and the
        # adapter must not need it, so the fake must not quietly supply it either.
        step = 2.0 / 4_409
        samples = [-1.0 + step * index for index in range(4_410)]
        return samples, samples


# --------------------------------------------------------------------------------------
# Routing: a language goes to the engine that can serve it
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_routed_language_goes_to_its_own_engine() -> None:
    piper = RecordingAdapter("piper")
    supertonic = RecordingAdapter("supertonic", rate=44_100)
    routed = LanguageRoutedTextToSpeech(piper, {LanguageCode.HINDI: supertonic})

    chunks = [chunk async for chunk in routed.synthesize("नमस्ते", LanguageCode.HINDI)]

    assert [text for text, _ in supertonic.calls] == ["नमस्ते"]
    assert piper.calls == []
    assert chunks[0].data == b"supertonic"
    # The route carries the engine's own sample rate through untouched, which is what the
    # browser is told to play at - 44,100 here rather than Piper's 22,050.
    assert chunks[0].sample_rate_hz == 44_100


@pytest.mark.asyncio
async def test_an_unrouted_language_still_goes_to_the_default() -> None:
    piper = RecordingAdapter("piper")
    routed = LanguageRoutedTextToSpeech(piper, {LanguageCode.HINDI: RecordingAdapter("other")})

    chunks = [chunk async for chunk in routed.synthesize("Hello", LanguageCode.ENGLISH)]

    assert chunks[0].data == b"piper"
    assert [language for _text, language in piper.calls] == [LanguageCode.ENGLISH]


def test_routing_with_no_routes_is_refused() -> None:
    """A router with nothing to route is the default adapter wearing a disguise."""

    with pytest.raises(ValueError, match="at least one route"):
        LanguageRoutedTextToSpeech(RecordingAdapter("piper"), {})


def test_the_route_is_inspectable() -> None:
    routed = LanguageRoutedTextToSpeech(
        RecordingAdapter("piper"), {LanguageCode.HINDI: RecordingAdapter("supertonic")}
    )

    assert routed.routed_languages == frozenset({LanguageCode.HINDI})
    assert cast(RecordingAdapter, routed.adapter_for(LanguageCode.HINDI)).name == "supertonic"
    assert cast(RecordingAdapter, routed.adapter_for(LanguageCode.TELUGU)).name == "piper"


# --------------------------------------------------------------------------------------
# The Supertonic adapter's contract
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_each_sentence_is_synthesised_separately() -> None:
    """The library returns one array for the whole text, so a reply would be silent until
    its last sentence existed. Splitting here is what makes a long reply start speaking."""

    engine = FakeSupertonic()
    adapter = SupertonicTextToSpeechAdapter(engine=engine)

    chunks = [
        chunk async for chunk in adapter.synthesize("First one. Second one!", LanguageCode.ENGLISH)
    ]

    assert [text for text, _lang, _steps in engine.spoken] == ["First one.", "Second one!"]
    assert chunks[-1].is_final is True
    assert sum(1 for chunk in chunks if chunk.is_final) == 1, "exactly one final chunk"


@pytest.mark.asyncio
async def test_the_engine_is_told_which_language_to_read() -> None:
    engine = FakeSupertonic()
    adapter = SupertonicTextToSpeechAdapter(engine=engine)

    _ = [chunk async for chunk in adapter.synthesize("नमस्ते।", LanguageCode.HINDI)]

    assert [lang for _text, lang, _steps in engine.spoken] == ["hi"]


@pytest.mark.asyncio
async def test_a_language_the_model_does_not_have_is_refused() -> None:
    """Telugu is absent from the model. Handing it over produces fluent audio in the wrong
    language, which is worse than silence and much harder to notice."""

    adapter = SupertonicTextToSpeechAdapter(engine=FakeSupertonic())

    with pytest.raises(PermanentAdapterError, match="not configured for 'te'"):
        _ = [chunk async for chunk in adapter.synthesize("హలో", LanguageCode.TELUGU)]


def test_telugu_is_not_claimed() -> None:
    assert LanguageCode.TELUGU not in SUPPORTED_LANGUAGES
    assert LanguageCode.HINDI in SUPPORTED_LANGUAGES


@pytest.mark.asyncio
async def test_audio_is_converted_to_the_pcm_the_socket_expects() -> None:
    """The model emits float32 in [-1, 1] at 44,100 Hz; the wire wants 16-bit LE PCM."""

    engine = FakeSupertonic()
    adapter = SupertonicTextToSpeechAdapter(engine=engine)

    chunks = [chunk async for chunk in adapter.synthesize("Hello.", LanguageCode.ENGLISH)]
    payload = b"".join(chunk.data for chunk in chunks)

    assert chunks[0].sample_rate_hz == 44_100
    assert chunks[0].media_type == "audio/pcm"
    assert len(payload) == 4_410 * 2, "one 16-bit sample per float"
    assert len(payload) % 2 == 0
    # A sample of exactly +1.0 must not wrap to the most negative value.
    import struct

    assert struct.unpack("<h", payload[-2:])[0] > 0


@pytest.mark.asyncio
async def test_frames_are_bounded() -> None:
    engine = FakeSupertonic()
    adapter = SupertonicTextToSpeechAdapter(engine=engine, frame_bytes=1_024)

    chunks = [chunk async for chunk in adapter.synthesize("Hello.", LanguageCode.ENGLISH)]

    assert all(len(chunk.data) <= 1_024 for chunk in chunks)
    assert len(chunks) > 1


@pytest.mark.asyncio
async def test_a_failing_engine_is_a_permanent_error_and_not_a_crash() -> None:
    adapter = SupertonicTextToSpeechAdapter(engine=FakeSupertonic(fail=True))

    with pytest.raises(PermanentAdapterError, match="failed to synthesise"):
        _ = [chunk async for chunk in adapter.synthesize("Hello.", LanguageCode.ENGLISH)]


@pytest.mark.asyncio
async def test_empty_text_produces_no_audio_rather_than_an_error() -> None:
    engine = FakeSupertonic()
    adapter = SupertonicTextToSpeechAdapter(engine=engine)

    chunks = [chunk async for chunk in adapter.synthesize("   ", LanguageCode.ENGLISH)]

    assert chunks == []
    assert engine.spoken == []


@pytest.mark.asyncio
async def test_text_longer_than_the_cap_is_refused_before_the_model_runs() -> None:
    engine = FakeSupertonic()
    adapter = SupertonicTextToSpeechAdapter(engine=engine, max_text_chars=10)

    with pytest.raises(PermanentAdapterError, match="max_text_chars"):
        _ = [chunk async for chunk in adapter.synthesize("x" * 11, LanguageCode.ENGLISH)]
    assert engine.spoken == []


def test_bad_construction_is_refused() -> None:
    with pytest.raises(ValueError, match="total_steps"):
        SupertonicTextToSpeechAdapter(total_steps=0)
    with pytest.raises(ValueError, match="speed"):
        SupertonicTextToSpeechAdapter(speed=0)
    with pytest.raises(ValueError, match="frame_bytes"):
        SupertonicTextToSpeechAdapter(frame_bytes=1_023)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("One. Two. Three.", ["One.", "Two.", "Three."]),
        ("नमस्ते। आप कैसे हैं?", ["नमस्ते।", "आप कैसे हैं?"]),
        ("No terminator", ["No terminator"]),
        ("   ", []),
    ],
)
def test_sentences_split_on_english_and_devanagari_terminators(
    text: str, expected: list[str]
) -> None:
    assert split_sentences(text, 100) == expected
