"""Tests for speaking a reply without blocking the socket that listens for interruptions.

Three properties are asserted, and each of them is the reason a background task was used
rather than synthesising inline:

*The receive loop keeps running.* ``start`` returns as soon as the task is scheduled, so
the caller is free to classify the next audio frame while the reply is still synthesising.

*A stream is always terminated.* The client hands the floor back when playback ends, so a
reply that produced no audio, or failed, must still be closed out - otherwise the buyer
stays muted until the server's own floor timeout expires.

*Aborting actually stops it.* Barge-in cancels the task and tells the client to discard
what it buffered. A cancelled stream must not go on to report that it finished normally.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from pitchbot.adapters.contracts import SynthesizedAudioChunk
from pitchbot.adapters.errors import PermanentAdapterError
from pitchbot.domain import LanguageCode
from pitchbot.simulator.speech_output import (
    REPLY_AUDIO_BEGIN,
    REPLY_AUDIO_END,
    LockedSocket,
    ReplyAudioSender,
)

RATE = 22_050


def chunk(size: int, sequence: int = 0) -> SynthesizedAudioChunk:
    return SynthesizedAudioChunk(
        data=b"\x01\x02" * (size // 2),
        sequence=sequence,
        is_final=False,
        media_type="audio/pcm",
        sample_rate_hz=RATE,
    )


class RecordingSocket:
    """Captures the write order, which is the whole contract with the browser."""

    def __init__(self, fail_after: int | None = None) -> None:
        self.messages: list[dict[str, object]] = []
        self.frames: list[bytes] = []
        self.order: list[str] = []
        self._writes = 0
        self._fail_after = fail_after

    def _count(self) -> None:
        self._writes += 1
        if self._fail_after is not None and self._writes > self._fail_after:
            raise RuntimeError("socket is closed")

    async def send_json(self, message: dict[str, object]) -> None:
        self._count()
        self.messages.append(message)
        self.order.append(str(message.get("type")))

    async def send_bytes(self, payload: bytes) -> None:
        self._count()
        self.frames.append(payload)
        self.order.append("binary")

    def locked(self) -> LockedSocket:
        return LockedSocket(self.send_json, self.send_bytes)

    def typed(self, name: str) -> dict[str, object]:
        return next(item for item in self.messages if item.get("type") == name)


class StubSynthesizer:
    def __init__(
        self,
        *chunks: SynthesizedAudioChunk,
        gate: asyncio.Event | None = None,
        error: Exception | None = None,
    ) -> None:
        self._chunks = chunks
        self._gate = gate
        self._error = error
        self.calls: list[str] = []

    async def synthesize(
        self,
        text: str,
        language: LanguageCode,
    ) -> AsyncIterator[SynthesizedAudioChunk]:
        self.calls.append(text)
        if self._error is not None:
            raise self._error
        for index, item in enumerate(self._chunks):
            if self._gate is not None and index:
                # Hold the stream open after the first chunk so a test can abort it while
                # it is genuinely mid-reply rather than already finished.
                await self._gate.wait()
            yield item


async def drain(sender: ReplyAudioSender) -> None:
    """Let the sender's task run to completion."""

    for _ in range(200):
        if not sender.streaming:
            return
        await asyncio.sleep(0)
    raise AssertionError("reply audio task did not finish")


# --------------------------------------------------------------------------------------
# The normal path
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_time_to_the_first_frame_is_reported() -> None:
    """Reply audio is a background task, so nothing else can time it.

    `TurnStage.SYNTHESIZE` was declared and never recorded, which left the only number a
    voice product is judged on - how long until the buyer hears anything - unmeasurable in
    production. Measured at ~167 ms for a short English reply
    (`probe_time_to_first_audio.py`), which is small; the point is that it is now visible
    when it stops being small.
    """

    seen: list[tuple[float, LanguageCode]] = []
    socket = RecordingSocket()
    sender = ReplyAudioSender(
        socket.locked(),
        StubSynthesizer(chunk(2_048)),
        frame_bytes=1_024,
        on_first_frame=lambda milliseconds, language: seen.append((milliseconds, language)),
    )

    await sender.start("hello", LanguageCode.ENGLISH)
    await drain(sender)

    assert len(seen) == 1, "reported once per reply, not once per frame"
    milliseconds, language = seen[0]
    assert milliseconds >= 0.0
    assert language is LanguageCode.ENGLISH


@pytest.mark.asyncio
async def test_a_reply_that_produces_no_audio_reports_no_timing() -> None:
    """Piper returns nothing for punctuation-only text; there was no first frame to time."""

    seen: list[tuple[float, LanguageCode]] = []
    socket = RecordingSocket()
    sender = ReplyAudioSender(
        socket.locked(),
        StubSynthesizer(),
        frame_bytes=1_024,
        on_first_frame=lambda milliseconds, language: seen.append((milliseconds, language)),
    )

    await sender.start("...", LanguageCode.ENGLISH)
    await drain(sender)

    assert seen == []
    assert socket.typed(REPLY_AUDIO_END)["frame_count"] == 0


@pytest.mark.asyncio
async def test_a_failing_timing_callback_does_not_cost_the_reply() -> None:
    """Measuring a reply must never be able to stop it being spoken."""

    def explode(milliseconds: float, language: LanguageCode) -> None:
        raise RuntimeError("metrics backend is down")

    socket = RecordingSocket()
    sender = ReplyAudioSender(
        socket.locked(),
        StubSynthesizer(chunk(2_048)),
        frame_bytes=1_024,
        on_first_frame=explode,
    )

    await sender.start("hello", LanguageCode.ENGLISH)
    await drain(sender)

    assert socket.typed(REPLY_AUDIO_BEGIN)["sample_rate_hz"] == RATE
    assert socket.typed(REPLY_AUDIO_END)["failed"] is False
    assert socket.frames


@pytest.mark.asyncio
async def test_a_reply_is_announced_streamed_and_terminated() -> None:
    socket = RecordingSocket()
    sender = ReplyAudioSender(
        socket.locked(),
        StubSynthesizer(chunk(2_048)),
        frame_bytes=1_024,
    )

    await sender.start("A reply.", LanguageCode.ENGLISH)
    await drain(sender)

    assert socket.order == [REPLY_AUDIO_BEGIN, "binary", "binary", REPLY_AUDIO_END]
    begin = socket.typed(REPLY_AUDIO_BEGIN)
    assert begin["sample_rate_hz"] == RATE
    assert begin["media_type"] == "audio/pcm"
    assert begin["bytes_per_sample"] == 2
    end = socket.typed(REPLY_AUDIO_END)
    assert end["aborted"] is False
    assert end["failed"] is False
    assert end["truncated"] is False
    assert end["frame_count"] == 2
    assert end["byte_count"] == 2_048
    assert end["duration_ms"] == pytest.approx(46.4, abs=0.1)
    assert socket.frames == [b"\x01\x02" * 512, b"\x01\x02" * 512]


@pytest.mark.asyncio
async def test_start_returns_before_the_reply_has_been_sent() -> None:
    """The caller is the only thing classifying buyer audio; it must not wait here."""

    socket = RecordingSocket()
    sender = ReplyAudioSender(socket.locked(), StubSynthesizer(chunk(2_048)), frame_bytes=1_024)

    await sender.start("A reply.", LanguageCode.ENGLISH)

    assert socket.order == []
    assert sender.streaming is True
    await drain(sender)


# --------------------------------------------------------------------------------------
# Always terminated
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_reply_that_produced_no_audio_is_still_terminated() -> None:
    """Piper returns nothing for punctuation-only text.

    Without a terminator the client would never report playback finished, so the buyer
    would stay muted until the server reclaimed the floor on its own timeout.
    """

    socket = RecordingSocket()
    sender = ReplyAudioSender(socket.locked(), StubSynthesizer())

    await sender.start("...", LanguageCode.ENGLISH)
    await drain(sender)

    assert socket.order == [REPLY_AUDIO_END]
    end = socket.typed(REPLY_AUDIO_END)
    assert end["frame_count"] == 0
    assert end["failed"] is False
    assert end["duration_ms"] == 0.0


@pytest.mark.asyncio
async def test_a_synthesis_failure_is_reported_not_raised() -> None:
    """The reply text is already on the socket, so audio is best effort."""

    socket = RecordingSocket()
    sender = ReplyAudioSender(
        socket.locked(),
        StubSynthesizer(error=PermanentAdapterError("voice not loaded")),
    )

    await sender.start("A reply.", LanguageCode.ENGLISH)
    await drain(sender)

    assert socket.order == [REPLY_AUDIO_END]
    assert socket.typed(REPLY_AUDIO_END)["failed"] is True


@pytest.mark.asyncio
async def test_a_closed_socket_stops_the_stream_without_raising() -> None:
    socket = RecordingSocket(fail_after=1)
    sender = ReplyAudioSender(
        socket.locked(),
        StubSynthesizer(chunk(4_096)),
        frame_bytes=1_024,
    )

    await sender.start("A reply.", LanguageCode.ENGLISH)
    await drain(sender)

    # The announcement was written; the first frame failed and nothing further was tried.
    assert socket.order == [REPLY_AUDIO_BEGIN]


# --------------------------------------------------------------------------------------
# Aborting
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aborting_stops_the_stream_and_tells_the_client_to_discard() -> None:
    gate = asyncio.Event()
    socket = RecordingSocket()
    sender = ReplyAudioSender(
        socket.locked(),
        StubSynthesizer(chunk(1_024), chunk(1_024, 1), gate=gate),
        frame_bytes=1_024,
    )

    await sender.start("A long reply.", LanguageCode.ENGLISH)
    for _ in range(50):
        if socket.frames:
            break
        await asyncio.sleep(0)
    await sender.abort()

    assert sender.streaming is False
    end = socket.typed(REPLY_AUDIO_END)
    assert end["aborted"] is True
    assert end["reason"] == "interrupted"
    # The reply was cut off: only the frame already written was sent.
    assert len(socket.frames) == 1
    gate.set()


@pytest.mark.asyncio
async def test_aborting_when_nothing_is_streaming_is_a_no_op() -> None:
    """Barge-in and disconnect both call it unconditionally rather than testing first."""

    socket = RecordingSocket()
    sender = ReplyAudioSender(socket.locked(), StubSynthesizer())

    await sender.abort()

    assert socket.order == []


@pytest.mark.asyncio
async def test_a_second_reply_abandons_the_first() -> None:
    """Only one reply holds the floor; two voices in one PCM stream is worse than either."""

    gate = asyncio.Event()
    socket = RecordingSocket()
    sender = ReplyAudioSender(
        socket.locked(),
        StubSynthesizer(chunk(1_024), chunk(1_024, 1), gate=gate),
        frame_bytes=1_024,
    )

    await sender.start("First reply.", LanguageCode.ENGLISH)
    for _ in range(50):
        if socket.frames:
            break
        await asyncio.sleep(0)
    await sender.start("Second reply.", LanguageCode.ENGLISH)

    assert socket.typed(REPLY_AUDIO_END)["aborted"] is True
    gate.set()
    await drain(sender)


# --------------------------------------------------------------------------------------
# Disabled by default
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_without_a_synthesizer_nothing_is_sent_at_all() -> None:
    """The browser speaks the reply in its own voice, exactly as it did before."""

    socket = RecordingSocket()
    sender = ReplyAudioSender(socket.locked(), None)

    assert sender.enabled is False
    await sender.start("A reply.", LanguageCode.ENGLISH)

    assert socket.order == []
    assert sender.streaming is False


# --------------------------------------------------------------------------------------
# Serialised writes
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_writes_are_serialised() -> None:
    """Two tasks now write to one socket, and interleaved writes corrupt the stream."""

    overlaps = 0
    inside = 0

    async def send_json(message: dict[str, object]) -> None:
        nonlocal overlaps, inside
        inside += 1
        if inside > 1:
            overlaps += 1
        await asyncio.sleep(0)
        inside -= 1

    async def send_bytes(payload: bytes) -> None:
        nonlocal overlaps, inside
        inside += 1
        if inside > 1:
            overlaps += 1
        await asyncio.sleep(0)
        inside -= 1

    socket = LockedSocket(send_json, send_bytes)
    await asyncio.gather(
        *(socket.send_json({"type": "ack"}) for _ in range(10)),
        *(socket.send_bytes(b"\x00\x01") for _ in range(10)),
    )

    assert overlaps == 0


@pytest.mark.asyncio
async def test_a_failed_write_latches_the_socket_closed() -> None:
    """One warning per reply, not one per frame, once the listener has gone."""

    attempts = 0

    async def send_json(message: dict[str, object]) -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("socket is closed")

    async def send_bytes(payload: bytes) -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("socket is closed")

    socket = LockedSocket(send_json, send_bytes)

    assert await socket.try_send_json({"type": "ack"}) is False
    assert socket.closed is True
    assert await socket.try_send_bytes(b"\x00\x01") is False
    assert attempts == 1
