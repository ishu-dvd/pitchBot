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
from typing import Final, Protocol

from pitchbot.adapters.contracts import TextToSpeechAdapter
from pitchbot.adapters.errors import AdapterError
from pitchbot.domain import LanguageCode
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
    ) -> None:
        self._socket = socket
        self._synthesizer = synthesizer
        self._frame_bytes = frame_bytes
        self._max_bytes = max_bytes
        self._task: asyncio.Task[None] | None = None

    @property
    def enabled(self) -> bool:
        """Whether a reply will be spoken by the server rather than by the browser."""

        return self._synthesizer is not None

    @property
    def streaming(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, text: str, language: LanguageCode) -> None:
        """Begin speaking ``text``. Returns as soon as the task is scheduled."""

        if self._synthesizer is None:
            return
        await self.abort()
        self._task = asyncio.create_task(self._speak(self._synthesizer, text, language))

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

    async def _speak(
        self,
        synthesizer: TextToSpeechAdapter,
        text: str,
        language: LanguageCode,
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
        try:
            async for frame in audio:
                if not begun:
                    # The sample rate belongs to the voice and is unknown until the first
                    # frame exists, so the stream cannot be announced any earlier.
                    begun = True
                    if not await self._socket.try_send_json(
                        {
                            "type": REPLY_AUDIO_BEGIN,
                            "sample_rate_hz": frame.sample_rate_hz,
                            "media_type": frame.media_type,
                            "bytes_per_sample": BYTES_PER_SAMPLE,
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


__all__ = [
    "REPLY_AUDIO_BEGIN",
    "REPLY_AUDIO_END",
    "LockedSocket",
    "ReplyAudioSender",
    "SocketSender",
]
