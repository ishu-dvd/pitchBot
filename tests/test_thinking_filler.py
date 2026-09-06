"""Tests for saying "hmm" over the socket while the transcriber works.

The gap this covers was measured, not assumed: a spoken turn takes ~2,587 ms from the
buyer finishing to the first audio, about thirteen times the ~200 ms gap Stivers et al.
(PNAS 2009) measured between human turns, and two thirds of it is transcription. The
backchannel and the ``on_thinking`` hook both already existed - only ``cli/talk.py`` ever
passed the hook, so every browser turn spent that whole gap in silence.

Filling it on a live socket is only safe because of three properties, and each test below
exists for one of them:

*One stream at a time.* Filler and reply share one :class:`ReplyAudioSender`, so they can
never interleave into a single PCM stream.

*The filler never holds the floor.* It is marked so the client plays it without reporting
playback. Reporting would hand back a floor the filler never took, releasing the one the
reply is about to hold and silencing barge-in for that turn.

*The reply never chops it mid-word.* ``settle`` drains the filler before the reply starts,
bounded, because the caller is the receive loop and the answer outranks the courtesy.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from pitchbot.adapters.contracts import SynthesizedAudioChunk
from pitchbot.domain import LanguageCode
from pitchbot.simulator.speech_output import (
    REPLY_AUDIO_BEGIN,
    REPLY_AUDIO_END,
    LockedSocket,
    ReplyAudioSender,
    ThinkingFiller,
)
from pitchbot.speech.backchannel import Backchannel

RATE = 22_050


def chunk(size: int = 64) -> SynthesizedAudioChunk:
    return SynthesizedAudioChunk(
        data=b"\x01\x02" * (size // 2),
        sequence=0,
        is_final=True,
        media_type="audio/pcm",
        sample_rate_hz=RATE,
    )


class RecordingSocket:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.frames: list[bytes] = []

    async def send_json(self, message: dict[str, object]) -> None:
        self.messages.append(message)

    async def send_bytes(self, payload: bytes) -> None:
        self.frames.append(payload)

    def locked(self) -> LockedSocket:
        return LockedSocket(self.send_json, self.send_bytes)

    def of_type(self, name: str) -> list[dict[str, object]]:
        return [item for item in self.messages if item.get("type") == name]


class StubSynthesizer:
    """Records what it was asked to say, which is the whole point of a filler test.

    ``delay_s`` holds the stream open *between* chunks so a filler can be genuinely
    mid-speech when the reply arrives. Without it a stub finishes inside one event-loop
    tick, and a test that then asserts the reply did not chop the filler off proves
    nothing: it would pass even if the reply aborted every filler it ever saw.
    """

    def __init__(
        self, *, gate: asyncio.Event | None = None, chunks: int = 1, delay_s: float = 0.0
    ) -> None:
        self.spoken: list[tuple[str, LanguageCode]] = []
        self._gate = gate
        self._chunks = chunks
        self._delay_s = delay_s

    async def synthesize(
        self,
        text: str,
        language: LanguageCode,
    ) -> AsyncIterator[SynthesizedAudioChunk]:
        self.spoken.append((text, language))
        if self._gate is not None:
            await self._gate.wait()
        for index in range(self._chunks):
            if index and self._delay_s:
                await asyncio.sleep(self._delay_s)
            yield chunk()


def build(
    *,
    synthesizer: StubSynthesizer | None = None,
    first_after_ms: int = 10,
    second_after_ms: int = 10_000,
    max_per_turn: int = 2,
    language: LanguageCode = LanguageCode.ENGLISH,
    settle_timeout_s: float = 1.5,
) -> tuple[RecordingSocket, ReplyAudioSender, ThinkingFiller, StubSynthesizer | None]:
    socket = RecordingSocket()
    sender = ReplyAudioSender(socket.locked(), synthesizer)
    filler = ThinkingFiller(
        language_of=lambda: language,
        backchannel=Backchannel(
            first_after_ms=first_after_ms,
            second_after_ms=second_after_ms,
            max_per_turn=max_per_turn,
        ),
        settle_timeout_s=settle_timeout_s,
    )
    filler.attach(sender)
    return socket, sender, filler, synthesizer


# --------------------------------------------------------------------------------------
# It speaks, and what it speaks is safe
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_silence_is_filled_after_the_threshold() -> None:
    synthesizer = StubSynthesizer()
    socket, _, filler, _ = build(synthesizer=synthesizer)

    filler.start()
    await asyncio.sleep(0.05)
    await filler.settle()

    assert [text for text, _ in synthesizer.spoken] == ["Hmm."]
    assert socket.of_type(REPLY_AUDIO_BEGIN)[0]["filler"] is True
    assert socket.of_type(REPLY_AUDIO_END)[0]["filler"] is True


@pytest.mark.asyncio
async def test_nothing_is_said_when_the_reply_arrives_first() -> None:
    """A fast turn must not be padded to the threshold just to say "hmm" into it."""

    synthesizer = StubSynthesizer()
    _, _, filler, _ = build(synthesizer=synthesizer, first_after_ms=5_000, second_after_ms=9_000)

    filler.start()
    await filler.settle()

    assert synthesizer.spoken == []


@pytest.mark.asyncio
async def test_a_fast_turn_is_not_delayed_by_the_filler_it_never_used() -> None:
    """`settle` must wake the waiting filler, not wait out its own timeout.

    The difference is invisible in what gets *said* - either way the phrase never lands -
    and costs the settle timeout on every turn the reply beats the threshold, which is
    every short turn. Found by mutating the stop signal away.
    """

    synthesizer = StubSynthesizer()
    _, _, filler, _ = build(
        synthesizer=synthesizer,
        first_after_ms=5_000,
        second_after_ms=9_000,
        settle_timeout_s=1.0,
    )

    filler.start()
    started = asyncio.get_running_loop().time()
    await filler.settle()
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 0.3, "the reply waited out the settle timeout instead of being let go"
    assert synthesizer.spoken == []


@pytest.mark.asyncio
async def test_a_long_wait_earns_a_second_and_only_a_second_phrase() -> None:
    """Two is company. The measured headroom allows four; a filler a second is anxious.

    The second phrase comes from the ``patient`` list and the rotation cursor has already
    moved, so it is neither a repeat of the first nor the same pairing every turn -
    repeating an acknowledgement the buyer just heard sounds like a stuck recording.
    """

    synthesizer = StubSynthesizer()
    _, _, filler, _ = build(synthesizer=synthesizer, first_after_ms=5, second_after_ms=15)

    filler.start()
    await asyncio.sleep(0.12)
    await filler.settle()

    assert [text for text, _ in synthesizer.spoken] == ["Hmm.", "One moment."]


@pytest.mark.asyncio
async def test_the_filler_never_asserts_agreement() -> None:
    """The transcript does not exist yet, so anything that could mean "yes" is a hazard.

    If the untranscribed sentence was *"so you'll do it for fifty thousand?"*, an "ok"
    has just agreed to a number nobody quoted, out loud, in a sales call.
    """

    synthesizer = StubSynthesizer()
    _, _, filler, _ = build(synthesizer=synthesizer, first_after_ms=5, second_after_ms=15)

    filler.start()
    await asyncio.sleep(0.12)
    await filler.settle()

    said = {text.strip().rstrip(".").lower() for text, _ in synthesizer.spoken}
    assert said.isdisjoint({"ok", "okay", "yes", "sure", "right you are", "theek hai"})


@pytest.mark.asyncio
async def test_the_language_is_read_when_the_filler_speaks() -> None:
    """A buyer who switched language last turn is answered in the language they switched to.

    Read lazily rather than captured, because the filler speaks *before* this turn's
    transcript exists - the last settled language is the best available answer.
    """

    synthesizer = StubSynthesizer()
    language = LanguageCode.ENGLISH
    socket = RecordingSocket()
    sender = ReplyAudioSender(socket.locked(), synthesizer)
    filler = ThinkingFiller(
        language_of=lambda: language,
        backchannel=Backchannel(first_after_ms=5, second_after_ms=10_000),
    )
    filler.attach(sender)

    language = LanguageCode.HINDI
    filler.start()
    await asyncio.sleep(0.05)
    await filler.settle()

    assert [code for _, code in synthesizer.spoken] == [LanguageCode.HINDI]
    assert synthesizer.spoken[0][0] == "अच्छा।"


# --------------------------------------------------------------------------------------
# It never takes the floor, and never fights the reply for the socket
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settle_lets_the_filler_finish_instead_of_cutting_it_off() -> None:
    """The reply aborting a filler would tell the client to discard a half-said word.

    A clipped syllable sounds like a fault where a completed one sounds like a person, so
    the reply waits for the filler rather than overwriting it.

    The filler is deliberately still streaming when the reply arrives - three chunks with
    a real gap between them. A stub that finished instantly would let this pass even if
    ``settle`` did nothing at all.
    """

    synthesizer = StubSynthesizer(chunks=3, delay_s=0.05)
    socket, sender, filler, _ = build(synthesizer=synthesizer, first_after_ms=1)

    filler.start()
    await asyncio.sleep(0.02)
    assert sender.streaming, "the filler must still be speaking for this test to mean anything"
    await filler.settle()
    assert not sender.streaming, "settle must wait for the filler, not abandon it"
    await sender.start("The answer.", LanguageCode.ENGLISH)
    await sender.drain()

    ends = socket.of_type(REPLY_AUDIO_END)
    assert [item["aborted"] for item in ends] == [False, False]
    assert [item["filler"] for item in ends] == [True, False]
    assert [text for text, _ in synthesizer.spoken] == ["Hmm.", "The answer."]


@pytest.mark.asyncio
async def test_settle_is_bounded_so_a_stuck_filler_cannot_hold_the_reply() -> None:
    """`settle` is awaited by the receive loop - the only thing classifying buyer audio.

    The gate is released on a timer rather than after the assertion, so that removing the
    bound makes this test *fail* rather than hang. A hang tells CI nothing about what
    broke, and the difference was found by mutating the timeout away.
    """

    gate = asyncio.Event()
    synthesizer = StubSynthesizer(gate=gate)
    _, _, filler, _ = build(synthesizer=synthesizer, first_after_ms=1, settle_timeout_s=0.05)

    async def release_late() -> None:
        await asyncio.sleep(0.6)
        gate.set()

    releaser = asyncio.create_task(release_late())
    filler.start()
    await asyncio.sleep(0.02)
    started = asyncio.get_running_loop().time()
    await filler.settle()
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 0.4, "an unbounded settle would have waited for the stuck synthesiser"
    gate.set()
    await releaser


@pytest.mark.asyncio
async def test_settle_is_a_no_op_when_nothing_is_filling() -> None:
    """Called on every utterance, including the noise that produced no transcript."""

    synthesizer = StubSynthesizer()
    _, _, filler, _ = build(synthesizer=synthesizer)

    await filler.settle()
    await filler.settle()

    assert synthesizer.spoken == []


@pytest.mark.asyncio
async def test_one_turn_never_starts_a_second_filler_over_the_first() -> None:
    """Two fillers at once are worse than none: one aborts the other mid-word.

    Asserted on the streams rather than on the phrases. A second ``start`` cancels the
    first before its synthesiser is ever iterated, so counting what was *said* would miss
    it entirely - the visible damage is an abandoned stream the client is told to discard.
    """

    synthesizer = StubSynthesizer(chunks=3, delay_s=0.05)
    socket, _, filler, _ = build(synthesizer=synthesizer, first_after_ms=1, second_after_ms=10_000)

    filler.start()
    await asyncio.sleep(0.02)
    filler.start()
    await asyncio.sleep(0.02)
    await filler.settle()

    begins = socket.of_type(REPLY_AUDIO_BEGIN)
    ends = socket.of_type(REPLY_AUDIO_END)
    assert len(begins) == 1
    assert [item["aborted"] for item in ends] == [False]


@pytest.mark.asyncio
async def test_abort_abandons_the_filler_for_teardown() -> None:
    gate = asyncio.Event()
    synthesizer = StubSynthesizer(gate=gate)
    socket, _, filler, _ = build(synthesizer=synthesizer, first_after_ms=1)

    filler.start()
    await asyncio.sleep(0.02)
    await filler.abort()

    assert socket.of_type(REPLY_AUDIO_END) == []
    gate.set()


# --------------------------------------------------------------------------------------
# It stays out of the way when there is no voice, and never distorts the reply's numbers
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_without_a_voice_there_is_nothing_to_fill_with() -> None:
    """Deployments with no synthesiser get silence either way; this must not cost them."""

    socket, _, filler, _ = build(synthesizer=None)

    assert filler.enabled is False
    filler.start()
    await asyncio.sleep(0.03)
    await filler.settle()

    assert socket.messages == []


@pytest.mark.asyncio
async def test_a_filler_is_not_reported_as_the_replys_synthesis_time() -> None:
    """`TurnStage.SYNTHESIZE` answers "how long until the buyer heard the answer".

    A backchannel is not the answer - it is spoken before the reply has been planned - so
    counting it would report a synthesis time for a turn that had not been planned yet.
    """

    seen: list[float] = []
    socket = RecordingSocket()
    synthesizer = StubSynthesizer()
    sender = ReplyAudioSender(
        socket.locked(),
        synthesizer,
        on_first_frame=lambda milliseconds, _: seen.append(milliseconds),
    )
    filler = ThinkingFiller(
        language_of=lambda: LanguageCode.ENGLISH,
        backchannel=Backchannel(first_after_ms=5, second_after_ms=1_000),
    )
    filler.attach(sender)

    filler.start()
    await asyncio.sleep(0.05)
    await filler.settle()

    assert synthesizer.spoken != []
    assert seen == []
