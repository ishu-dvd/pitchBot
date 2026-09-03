"""Tests for re-cutting one reply's synthesis into frames the socket can abandon.

Piper hands back one chunk per sentence, measured at 80 KB to 352 KB, and the largest of
those carried 7.99 s of audio. Two properties matter and both are asserted here: a frame is
small enough that abandoning the stream costs the buyer well under a second of unwanted
speech, and a frame is always a whole number of 16-bit samples so the client can rebuild
the reply as an ``Int16Array`` without every later sample being byte-shifted.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from pitchbot.adapters.contracts import SynthesizedAudioChunk
from pitchbot.adapters.errors import PermanentAdapterError
from pitchbot.domain import LanguageCode
from pitchbot.speech.reply_audio import (
    DEFAULT_FRAME_BYTES,
    MAX_FRAME_BYTES,
    ReplyAudio,
    ReplyAudioFrame,
)

RATE = 22_050


def chunk(
    size: int, *, sequence: int = 0, rate: int = RATE, media: str = "audio/pcm"
) -> SynthesizedAudioChunk:
    return SynthesizedAudioChunk(
        data=b"\x01\x02" * (size // 2) + (b"\x03" if size % 2 else b""),
        sequence=sequence,
        is_final=False,
        media_type=media,
        sample_rate_hz=rate,
    )


class StubSynthesizer:
    """Yields exactly the chunks it was given, including none at all.

    ``MockTextToSpeechAdapter`` substitutes a default chunk for an empty list, so it
    cannot express "this reply synthesised to nothing" - which is precisely what Piper
    returns for punctuation-only text and therefore precisely what must be covered.
    """

    def __init__(self, *chunks: SynthesizedAudioChunk) -> None:
        self._chunks = chunks
        self.requests: list[tuple[str, LanguageCode]] = []

    async def synthesize(
        self,
        text: str,
        language: LanguageCode,
    ) -> AsyncIterator[SynthesizedAudioChunk]:
        self.requests.append((text, language))
        for item in self._chunks:
            yield item


def synthesizer(*chunks: SynthesizedAudioChunk) -> StubSynthesizer:
    return StubSynthesizer(*chunks)


async def collect(audio: ReplyAudio) -> list[ReplyAudioFrame]:
    return [frame async for frame in audio]


# --------------------------------------------------------------------------------------
# Framing
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_sentence_sized_chunk_is_cut_into_bounded_frames() -> None:
    """The 352 KB chunk Piper actually produced must not reach the socket whole."""

    audio = ReplyAudio(
        synthesizer(chunk(352_256)),
        "reply",
        LanguageCode.ENGLISH,
        frame_bytes=32_768,
    )

    frames = await collect(audio)

    assert len(frames) == 11
    assert all(len(frame.data) == 32_768 for frame in frames[:-1])
    assert len(frames[-1].data) == 352_256 % 32_768
    assert [frame.sequence for frame in frames] == list(range(11))
    assert audio.byte_count == 352_256
    assert audio.truncated is False


@pytest.mark.asyncio
async def test_frames_are_re_cut_across_chunk_boundaries() -> None:
    """Framing follows the frame size, not the sentence boundaries it arrived on."""

    audio = ReplyAudio(
        synthesizer(chunk(1_000), chunk(1_000, sequence=1), chunk(1_000, sequence=2)),
        "reply",
        LanguageCode.ENGLISH,
        frame_bytes=1_024,
    )

    frames = await collect(audio)

    assert [len(frame.data) for frame in frames] == [1_024, 1_024, 952]
    assert audio.byte_count == 3_000


@pytest.mark.asyncio
async def test_a_reply_that_synthesises_to_nothing_yields_no_frames() -> None:
    """Piper returns zero chunks for punctuation-only text.

    The stream must then be reported as carrying no audio rather than as an empty one:
    ``sample_rate_hz`` is a property of the voice and is still unknown.
    """

    audio = ReplyAudio(synthesizer(), "...", LanguageCode.ENGLISH)

    assert await collect(audio) == []
    assert audio.frame_count == 0
    assert audio.sample_rate_hz == 0
    assert audio.media_type == ""


@pytest.mark.asyncio
async def test_framing_is_taken_from_the_first_chunk() -> None:
    audio = ReplyAudio(synthesizer(chunk(64, rate=16_000)), "reply", LanguageCode.ENGLISH)

    frames = await collect(audio)

    assert audio.sample_rate_hz == 16_000
    assert audio.media_type == "audio/pcm"
    assert frames[0].sample_rate_hz == 16_000
    assert frames[0].duration_ms == pytest.approx(2.0)


# --------------------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_sample_rate_change_mid_reply_is_refused() -> None:
    """Re-cutting assumes one format. A silent change would replay at the wrong pitch."""

    audio = ReplyAudio(
        synthesizer(chunk(64), chunk(64, sequence=1, rate=16_000)),
        "reply",
        LanguageCode.ENGLISH,
        frame_bytes=1_024,
    )

    with pytest.raises(PermanentAdapterError) as error:
        await collect(audio)

    assert "sample rate" in str(error.value)


@pytest.mark.asyncio
async def test_a_media_type_change_mid_reply_is_refused() -> None:
    audio = ReplyAudio(
        synthesizer(chunk(64), chunk(64, sequence=1, media="audio/wav")),
        "reply",
        LanguageCode.ENGLISH,
        frame_bytes=1_024,
    )

    with pytest.raises(PermanentAdapterError):
        await collect(audio)


@pytest.mark.asyncio
async def test_a_chunk_that_is_not_whole_samples_is_refused() -> None:
    """The contract carries no sample-width field, so this cannot be inferred.

    Non-16-bit audio re-cut into 16-bit frames plays as fluent noise rather than failing,
    so it is refused with an error that names the cause.
    """

    audio = ReplyAudio(synthesizer(chunk(65)), "reply", LanguageCode.ENGLISH)

    with pytest.raises(PermanentAdapterError) as error:
        await collect(audio)

    assert "16-bit samples" in str(error.value)


@pytest.mark.parametrize("frame_bytes", [0, -1, MAX_FRAME_BYTES + 1])
def test_an_out_of_range_frame_size_is_refused(frame_bytes: int) -> None:
    with pytest.raises(ValueError):
        ReplyAudio(synthesizer(), "reply", LanguageCode.ENGLISH, frame_bytes=frame_bytes)


def test_an_odd_frame_size_is_refused() -> None:
    """An odd frame splits a 16-bit sample, which byte-shifts everything after it."""

    with pytest.raises(ValueError) as error:
        ReplyAudio(synthesizer(), "reply", LanguageCode.ENGLISH, frame_bytes=1_001)

    assert "16-bit samples" in str(error.value)


def test_a_non_positive_byte_cap_is_refused() -> None:
    with pytest.raises(ValueError):
        ReplyAudio(synthesizer(), "reply", LanguageCode.ENGLISH, max_bytes=0)


def test_a_frame_must_carry_whole_samples() -> None:
    with pytest.raises(ValueError):
        ReplyAudioFrame(data=b"\x00", sequence=0, sample_rate_hz=RATE, media_type="audio/pcm")


def test_a_frame_must_carry_audio() -> None:
    with pytest.raises(ValueError):
        ReplyAudioFrame(data=b"", sequence=0, sample_rate_hz=RATE, media_type="audio/pcm")


# --------------------------------------------------------------------------------------
# The byte cap
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_byte_cap_cuts_at_a_frame_boundary() -> None:
    """A partial frame is never emitted: the client would rebuild a fragment as audio."""

    audio = ReplyAudio(
        synthesizer(chunk(4_096)),
        "reply",
        LanguageCode.ENGLISH,
        frame_bytes=1_024,
        max_bytes=2_500,
    )

    frames = await collect(audio)

    assert [len(frame.data) for frame in frames] == [1_024, 1_024]
    assert audio.byte_count == 2_048
    assert audio.truncated is True


@pytest.mark.asyncio
async def test_a_normal_reply_never_reports_truncation() -> None:
    """Measured replies are 84 characters, about 155 KB. The cap is a backstop."""

    audio = ReplyAudio(synthesizer(chunk(155_000)), "reply", LanguageCode.ENGLISH)

    await collect(audio)

    assert audio.truncated is False
    assert audio.frame_count == 5
    assert DEFAULT_FRAME_BYTES == 32_768
