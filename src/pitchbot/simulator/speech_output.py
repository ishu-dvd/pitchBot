"""Send one reply's audio without ever blocking the socket's receive loop.

The receive loop is the only thing classifying buyer audio, so whatever it is doing is
also the upper bound on how quickly an interruption can be noticed. Synthesising inside it
would therefore trade the feature this socket exists for - barge-in - against the feature
being added. A long reply was measured at 1,052 ms of synthesis, so doing it inline would
blind the detector for about a second every single turn.

Audio is consequently produced by a background task, and two consequences follow.

**Every send is serialised.** Two tasks now write to one WebSocket, and a WebSocket is not
safe for concurrent sends: interleaved writes corrupt the frame stream rather than merely
arriving out of order. :class:`LockedSocket` is the only way either task writes.

**Cancellation is the abort path.** Measured 2026-09-03: cancelling between chunks stops
the Piper generator immediately, produces no further chunks, and leaves the adapter
byte-identical on its next use. A cancel landing *inside* a write may drop that frame
part-written, which is acceptable precisely because the stream is being abandoned - the
client is told to discard what it has buffered.

The reply **text** has already been delivered over the socket before any of this runs. So
a synthesis failure, a closed socket, or an abandoned stream costs the buyer the voice and
never the answer, and none of these paths retries or invents audio.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Final, Protocol

from pitchbot.adapters.contracts import TextToSpeechAdapter
from pitchbot.adapters.errors import AdapterError
from pitchbot.domain import LanguageCode
from pitchbot.speech.backchannel import Backchannel
from pitchbot.speech.reply_audio import (
    BYTES_PER_SAMPLE,
    DEFAULT_FRAME_BYTES,
    DEFAULT_MAX_REPLY_BYTES,
    ReplyAudio,
)

logger = logging.getLogger(__name__)

REPLY_AUDIO_BEGIN: Final[str] = "reply-audio-begin"
REPLY_AUDIO_END: Final[str] = "reply-audio-end"


class SocketSender(Protocol):
    """The two writes this module needs, so it can be tested without a WebSocket."""

    async def send_json(self, message: dict[str, object]) -> None: ...
    async def send_bytes(self, payload: bytes) -> None: ...


class LockedSocket:
    """Serialises writes from the receive loop and the reply-audio task.

    ``closed`` latches on the first failed write. After a socket is gone every later write
    would raise the same way, and a background task discovering that separately would log
    one warning per frame for a reply that no longer has a listener.
    """

    def __init__(
        self,
        send_json: Callable[[dict[str, object]], Awaitable[None]],
        send_bytes: Callable[[bytes], Awaitable[None]],
    ) -> None:
        self._send_json = send_json
        self._send_bytes = send_bytes
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    async def send_json(self, message: dict[str, object]) -> None:
        async with self._lock:
            await self._send_json(message)

    async def send_bytes(self, payload: bytes) -> None:
        async with self._lock:
            await self._send_bytes(payload)

    async def try_send_json(self, message: dict[str, object]) -> bool:
        """Write, or record that the socket is gone. Never raises."""

        if self._closed:
            return False
        try:
            await self.send_json(message)
        except (RuntimeError, OSError):
            self._closed = True
            return False
        return True

    async def try_send_bytes(self, payload: bytes) -> bool:
        if self._closed:
            return False
        try:
            await self.send_bytes(payload)
        except (RuntimeError, OSError):
            self._closed = True
            return False
        return True


class ReplyAudioSender:
    """At most one reply is being spoken at a time; starting a new one abandons the old.

    Only one reply can hold the floor, so a second ``start`` while a first is still
    streaming means the first is no longer wanted. Letting both run would interleave two
    voices into one PCM stream, which is worse than either.
    """

    def __init__(
        self,
        socket: LockedSocket,
        synthesizer: TextToSpeechAdapter | None,
        *,
        frame_bytes: int = DEFAULT_FRAME_BYTES,
        max_bytes: int = DEFAULT_MAX_REPLY_BYTES,
        on_first_frame: Callable[[float, LanguageCode], None] | None = None,
    ) -> None:
        self._socket = socket
        self._synthesizer = synthesizer
        self._frame_bytes = frame_bytes
        self._max_bytes = max_bytes
        # Reported rather than recorded here, so this module keeps no opinion about
        # metrics and stays testable without one. The caller decides what to do with it.
        self._on_first_frame = on_first_frame
        self._task: asyncio.Task[None] | None = None

    @property
    def enabled(self) -> bool:
        """Whether a reply will be spoken by the server rather than by the browser."""

        return self._synthesizer is not None

    @property
    def streaming(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, text: str, language: LanguageCode, *, filler: bool = False) -> None:
        """Begin speaking ``text``. Returns as soon as the task is scheduled.

        ``filler`` marks the stream as a backchannel rather than a reply. It changes
        nothing about how the audio is produced or framed - only what the client is told,
        because a filler must not be reported back as playback of a turn. The floor is
        handed back when a *reply* finishes playing, and a filler that reported the same
        thing would hand back a floor it never held, silencing the reply that follows it.
        """

        if self._synthesizer is None:
            return
        await self.abort()
        self._task = asyncio.create_task(
            self._speak(self._synthesizer, text, language, filler=filler)
        )

    async def drain(self) -> None:
        """Wait for the stream in flight to finish sending, without cancelling it.

        The counterpart to :meth:`abort`: used when the current stream is still wanted and
        the caller simply must not write over it. Returns immediately when nothing is
        streaming, and never raises what the stream raised - a failed reply is already
        reported to the client by the task itself.
        """

        task = self._task
        if task is None or task.done():
            return
        await asyncio.wait({task})

    async def abort(self) -> None:
        """Stop any reply in flight and tell the client to discard what it buffered.

        Safe to call when nothing is streaming, which is the common case: barge-in and
        disconnect both call it unconditionally rather than testing first.
        """

        task = self._task
        self._task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            if not task.cancelled():
                # The cancellation was delivered to *us*, not to the reply task - this
                # caller is itself being torn down. The reply task was already cancelled
                # on the line above, so it is never orphaned; there is simply nobody left
                # to send a terminator to.
                raise
        await self._socket.try_send_json(
            {"type": REPLY_AUDIO_END, "aborted": True, "reason": "interrupted"}
        )

    def _report_first_frame(self, milliseconds: float, language: LanguageCode) -> None:
        if self._on_first_frame is None:
            return
        try:
            self._on_first_frame(milliseconds, language)
        except Exception:  # noqa: BLE001 - measuring a reply must never cost the reply
            logger.warning("Reply audio timing callback failed", exc_info=True)

    async def _speak(
        self,
        synthesizer: TextToSpeechAdapter,
        text: str,
        language: LanguageCode,
        *,
        filler: bool = False,
    ) -> None:
        audio = ReplyAudio(
            synthesizer,
            text,
            language,
            frame_bytes=self._frame_bytes,
            max_bytes=self._max_bytes,
        )
        failed = False
        begun = False
        started = perf_counter()
        try:
            async for frame in audio:
                if not begun:
                    # The sample rate belongs to the voice and is unknown until the first
                    # frame exists, so the stream cannot be announced any earlier.
                    begun = True
                    # Taken before the send: this is how long the buyer waited for the
                    # voice to start, and putting the socket write inside it would blame
                    # synthesis for the network. Not reported for a filler - a backchannel
                    # is not the reply, and counting it would report a synthesis time for
                    # a turn whose reply had not been planned yet.
                    if not filler:
                        self._report_first_frame((perf_counter() - started) * 1000, language)
                    if not await self._socket.try_send_json(
                        {
                            "type": REPLY_AUDIO_BEGIN,
                            "sample_rate_hz": frame.sample_rate_hz,
                            "media_type": frame.media_type,
                            "bytes_per_sample": BYTES_PER_SAMPLE,
                            "filler": filler,
                        }
                    ):
                        return
                if not await self._socket.try_send_bytes(frame.data):
                    return
        except asyncio.CancelledError:
            # `abort` sends the terminator: it is still running, whereas this task is not
            # allowed to await anything more once cancellation has been delivered.
            raise
        except (AdapterError, RuntimeError, ValueError, OSError):
            # Best effort. The buyer already has the reply as text on this same socket.
            logger.warning("Reply audio synthesis failed", exc_info=True)
            failed = True
        # Always terminated, including when synthesis produced nothing at all. The client
        # hands the floor back when playback ends, so a stream with no terminator would
        # leave the buyer muted until the server's own floor timeout expired.
        await self._socket.try_send_json(
            {
                "type": REPLY_AUDIO_END,
                "aborted": False,
                "failed": failed,
                "filler": filler,
                "truncated": audio.truncated,
                "frame_count": audio.frame_count,
                "byte_count": audio.byte_count,
                "duration_ms": _duration_ms(audio.byte_count, audio.sample_rate_hz),
            }
        )


def _duration_ms(byte_count: int, sample_rate_hz: int) -> float:
    if sample_rate_hz <= 0:
        return 0.0
    return round(byte_count / BYTES_PER_SAMPLE / sample_rate_hz * 1000, 1)


DEFAULT_SETTLE_TIMEOUT_S: Final[float] = 1.5
"""How long the reply will wait for a filler to stop talking before talking over it.

Bounded because this is awaited by the receive loop, which is the only thing classifying
buyer audio. The longest measured filler is 1.1 s all-in, so this clears every one of them
with margin; a filler still speaking after 1.5 s is a stuck synthesiser, and delaying the
answer indefinitely to be polite to it is the wrong trade.
"""


class ThinkingFiller:
    """Says "hmm" over the socket while the transcriber works, instead of going silent.

    The CLI has done this since the backchannel was measured; the browser never has. The
    hook existed on :class:`~pitchbot.speech.pipeline.SpeechTurnPipeline` and only
    ``cli/talk.py`` passed it, so every spoken turn in the simulator spent the whole
    measured ~2.6 s gap in silence - about thirteen times the ~200 ms gap Stivers et al.
    (PNAS 2009) measured between human turns, and six times ITU-T G.114's 400 ms ceiling
    for interactive voice.

    It fills that silence rather than shortening it, which is the honest description: the
    literature on filled pauses is about *perceived* delay, and no measured millisecond
    moves. Transcription is still 66% of the wait.

    Three properties make it safe to bolt onto a live socket:

    **One stream at a time.** Everything goes through the same :class:`ReplyAudioSender`,
    so a filler and a reply can never interleave into one PCM stream.

    **It never holds the floor.** A filler is explicitly designed to be talked over, so
    buyer speech during one is an ordinary turn rather than a barge-in, and the client is
    told not to report playback of it - reporting would hand back a floor the filler never
    took, muting the reply that follows.

    **It says nothing that could be agreement.** That rule lives in
    :mod:`pitchbot.speech.backchannel` and is inherited whole: a filler may assert receipt
    and never assent, because at the moment it speaks nobody knows what was said yet.
    """

    def __init__(
        self,
        *,
        language_of: Callable[[], LanguageCode],
        backchannel: Backchannel | None = None,
        settle_timeout_s: float = DEFAULT_SETTLE_TIMEOUT_S,
    ) -> None:
        self._language_of = language_of
        self._backchannel = backchannel if backchannel is not None else Backchannel()
        self._settle_timeout_s = settle_timeout_s
        self._sender: ReplyAudioSender | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def attach(self, sender: ReplyAudioSender) -> None:
        """Give the filler its voice after construction.

        Needed because the two are built in opposite orders: the pipeline must be handed
        ``start`` before it can be constructed, and the sender needs the accepted socket.
        Attaching keeps that knot explicit instead of hiding it in a closure.
        """

        self._sender = sender

    @property
    def enabled(self) -> bool:
        return self._sender is not None and self._sender.enabled

    def start(self) -> None:
        """Begin filling, called the moment an utterance closes and transcription begins.

        Synchronous because :class:`SpeechTurnPipeline` calls it from inside ``push``,
        before the transcription await, so that the wait is counted from when the buyer
        actually stopped rather than from whenever a coroutine is next scheduled.
        """

        if not self.enabled:
            return
        if self._task is not None and not self._task.done():
            # A previous turn never settled. Speaking twice at once is worse than not
            # speaking, and the running task will be settled by its own turn.
            return
        self._stop = asyncio.Event()
        self._backchannel.begin_turn()
        self._task = asyncio.get_running_loop().create_task(self._fill())

    async def settle(self) -> None:
        """Let any filler in flight finish, so the reply does not chop it mid-word.

        Called before the reply's audio starts. Idempotent, and a no-op when nothing is
        filling, which is the common case for a typed turn or an utterance that produced
        no transcript.
        """

        task, self._task = self._task, None
        if task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(task, self._settle_timeout_s)
        except TimeoutError:
            # `wait_for` has already cancelled it. The reply is worth more than the tail
            # of an "hmm", and the sender's abort tells the client to drop what it holds.
            logger.warning("Backchannel did not settle within %.1fs", self._settle_timeout_s)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a courtesy must never cost the reply
            logger.warning("Backchannel failed", exc_info=True)

    async def abort(self) -> None:
        """Stop filling now, for teardown. The audio in flight is abandoned."""

        task, self._task = self._task, None
        if task is None:
            return
        self._stop.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _fill(self) -> None:
        started = perf_counter()
        sender = self._sender
        if sender is None:  # pragma: no cover - guarded by `enabled`
            return
        for target in (self._backchannel.first_after_ms, self._backchannel.second_after_ms):
            elapsed_ms = (perf_counter() - started) * 1000
            if elapsed_ms < target and await self._sleep_until(target - elapsed_ms):
                return
            # `max` rather than a fresh reading alone: having slept *to* the threshold a
            # re-measurement can land a fraction of a millisecond below it, the policy
            # would decline, and the second filler would silently never fire on a timer
            # that looked correct.
            waited_ms = max(float(target), (perf_counter() - started) * 1000)
            phrase = self._backchannel.due(waited_ms, self._language_of())
            if phrase is None:
                continue
            await sender.start(phrase, self._language_of(), filler=True)
            # Drained here rather than left in flight, so that `settle` awaiting this task
            # is the same thing as the filler having finished speaking.
            await sender.drain()

    async def _sleep_until(self, milliseconds: float) -> bool:
        """Sleep, or return ``True`` as soon as the reply is ready and wants the floor."""

        try:
            await asyncio.wait_for(self._stop.wait(), milliseconds / 1000)
        except TimeoutError:
            return False
        return True


__all__ = [
    "DEFAULT_SETTLE_TIMEOUT_S",
    "REPLY_AUDIO_BEGIN",
    "REPLY_AUDIO_END",
    "LockedSocket",
    "ReplyAudioSender",
    "SocketSender",
    "ThinkingFiller",
]
