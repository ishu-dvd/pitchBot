"""Turn one agent reply into bounded audio frames the socket can stream and abandon.

Piper emits **one chunk per sentence**, and a sentence is large: measured 2026-09-03 with
``en_US-joe-medium``, a six-sentence reply produced five chunks of 80 KB to 352 KB, the
largest carrying 7.99 s of audio in a single object. Two facts follow, and together they
are the whole reason this module exists.

**A sentence is too coarse a unit to send.** A 352 KB frame exceeds the 256 KB bound the
inbound side of the same socket enforces, and it cannot be abandoned part-way: once it is
written, the buyer will hear all eight seconds of it. Barge-in that can only take effect
on a sentence boundary is not barge-in. Frames are therefore re-cut to a fixed size that
is a whole number of samples, which bounds both the write and the audio already committed
when the buyer interrupts.

**A sentence is too coarse a unit to wait for, but that does not matter.** Synthesis runs
at roughly 19x realtime once the voice is resident (measured: 1,052 ms produced 20.75 s of
audio), so the entire reply is available long before any of it finishes playing. There is
consequently nothing to gain from pacing the send to realtime, and something to lose: the
audio is delivered while the network is known to be working rather than during whatever
happens twelve seconds from now. Frames are emitted as fast as they are produced and the
client schedules playback.

Audio is **best effort**. The reply text has already been sent over the socket by the time
this runs, so a synthesis failure costs the buyer the voice, not the answer. Nothing here
retries, and nothing here fabricates audio: a reply that cannot be spoken is reported as
not spoken.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Final

from pitchbot.adapters.contracts import SynthesizedAudioChunk, TextToSpeechAdapter
from pitchbot.adapters.errors import PermanentAdapterError
from pitchbot.domain import LanguageCode

BYTES_PER_SAMPLE: Final[int] = 2
"""Every supported voice is mono 16-bit PCM; the Piper adapter refuses anything else."""

DEFAULT_FRAME_BYTES: Final[int] = 32_768
"""0.74 s of audio at 22.05 kHz - small enough to abandon, large enough to be cheap.

A reply of twenty seconds becomes about twenty-eight frames rather than five, so the audio
already committed when the buyer interrupts falls from as much as eight seconds to under
one, and no single write approaches the 256 KB the inbound side of this socket allows.
"""

MAX_FRAME_BYTES: Final[int] = 262_144
"""The same ceiling the inbound audio path enforces, applied symmetrically outbound."""

DEFAULT_MAX_REPLY_BYTES: Final[int] = 2 * 1024 * 1024
"""About 47 s of 22.05 kHz audio, mirroring the inbound ``MAX_UTTERANCE_BYTES`` cap.

Measured agent replies are 84 characters, roughly 3.5 s and 155 KB, so this is a defensive
backstop against a pathological reply rather than a limit normal traffic approaches.
"""


@dataclass(frozen=True, slots=True)
class ReplyAudioFrame:
    """A fixed-size, sample-aligned slice of one reply's audio."""

    data: bytes
    sequence: int
    sample_rate_hz: int
    media_type: str

    def __post_init__(self) -> None:
        if not self.data:
            raise ValueError("a frame must carry audio")
        if len(self.data) % BYTES_PER_SAMPLE:
            raise ValueError("frame length must be a whole number of 16-bit samples")
        if self.sequence < 0:
            raise ValueError("sequence must not be negative")
        if self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")

    @property
    def duration_ms(self) -> float:
        return len(self.data) / BYTES_PER_SAMPLE / self.sample_rate_hz * 1000


class ReplyAudio:
    """One reply's audio, re-cut into bounded frames.

    Iterating yields frames. The stream describes itself only *after* iteration, because
    the sample rate is a property of the voice and is not known until the first chunk
    arrives - which is also why a caller must not announce an audio stream before the
    first frame exists. A reply that synthesises to nothing, which is what Piper returns
    for punctuation-only text, yields no frames at all and must be announced as no audio
    rather than as an empty stream.
    """

    def __init__(
        self,
        synthesizer: TextToSpeechAdapter,
        text: str,
        language: LanguageCode,
        *,
        frame_bytes: int = DEFAULT_FRAME_BYTES,
        max_bytes: int = DEFAULT_MAX_REPLY_BYTES,
    ) -> None:
        if not 1 <= frame_bytes <= MAX_FRAME_BYTES:
            raise ValueError(f"frame_bytes must be between 1 and {MAX_FRAME_BYTES}")
        if frame_bytes % BYTES_PER_SAMPLE:
            # An odd frame length would split a 16-bit sample across two frames. The
            # client reassembles frames into an Int16 buffer, so every later sample would
            # be byte-shifted: the reply would not merely click, it would become noise.
            raise ValueError("frame_bytes must be a whole number of 16-bit samples")
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self._synthesizer = synthesizer
        self._text = text
        self._language = language
        self._frame_bytes = frame_bytes
        self._max_bytes = max_bytes
        self._frame_count = 0
        self._byte_count = 0
        self._sample_rate_hz = 0
        self._media_type = ""
        self._truncated = False

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def byte_count(self) -> int:
        return self._byte_count

    @property
    def sample_rate_hz(self) -> int:
        """The voice's rate, or ``0`` before the first frame has been produced."""

        return self._sample_rate_hz

    @property
    def media_type(self) -> str:
        return self._media_type

    @property
    def truncated(self) -> bool:
        """Whether the byte cap cut the reply short. Normal replies never set this."""

        return self._truncated

    def __aiter__(self) -> AsyncIterator[ReplyAudioFrame]:
        return self.frames()

    async def frames(self) -> AsyncIterator[ReplyAudioFrame]:
        buffer = bytearray()
        async for chunk in self._synthesizer.synthesize(self._text, self._language):
            self._adopt_framing(chunk)
            buffer.extend(chunk.data)
            while len(buffer) >= self._frame_bytes:
                frame = self._take(buffer, self._frame_bytes)
                if frame is None:
                    return
                yield frame
        if buffer:
            # The tail is shorter than a full frame. It is still whole samples, because
            # every chunk Piper produces is, and the cap only ever removes whole frames.
            remainder = self._take(buffer, len(buffer))
            if remainder is not None:
                yield remainder

    def _adopt_framing(self, chunk: SynthesizedAudioChunk) -> None:
        """Fix the stream's framing on the first chunk and refuse any later change.

        Re-cutting frames assumes every byte in the buffer belongs to the same format. A
        voice that changed rate or encoding part-way through would be silently resampled
        by the client at the rate announced for the first frame, so the reply would play
        at the wrong pitch rather than fail.
        """

        if len(chunk.data) % BYTES_PER_SAMPLE:
            # Caught here rather than at frame construction so the error names the cause.
            # The contract carries no sample-width field, so an adapter that emitted 8- or
            # 24-bit audio would otherwise be re-cut into frames the client reinterprets
            # as 16-bit: fluent noise rather than a failure.
            raise PermanentAdapterError(
                f"synthesis produced a {len(chunk.data)}-byte chunk, which is not a whole "
                "number of 16-bit samples; only mono 16-bit PCM can be re-framed"
            )
        if self._sample_rate_hz == 0:
            self._sample_rate_hz = chunk.sample_rate_hz
            self._media_type = chunk.media_type
            return
        if chunk.sample_rate_hz != self._sample_rate_hz:
            raise PermanentAdapterError(
                f"synthesis changed sample rate mid-reply, from {self._sample_rate_hz} Hz "
                f"to {chunk.sample_rate_hz} Hz; frames cannot be re-cut across rates"
            )
        if chunk.media_type != self._media_type:
            raise PermanentAdapterError(
                f"synthesis changed media type mid-reply, from {self._media_type!r} "
                f"to {chunk.media_type!r}"
            )

    def _take(self, buffer: bytearray, size: int) -> ReplyAudioFrame | None:
        """Cut one frame, or ``None`` once the byte cap has been reached.

        The cap drops the frame that would cross it rather than emitting a partial one, so
        the reply is cut at a frame boundary and the client never receives a fragment.
        """

        if self._byte_count + size > self._max_bytes:
            self._truncated = True
            buffer.clear()
            return None
        data = bytes(buffer[:size])
        del buffer[:size]
        frame = ReplyAudioFrame(
            data=data,
            sequence=self._frame_count,
            sample_rate_hz=self._sample_rate_hz,
            media_type=self._media_type,
        )
        self._frame_count += 1
        self._byte_count += size
        return frame


__all__ = [
    "BYTES_PER_SAMPLE",
    "DEFAULT_FRAME_BYTES",
    "DEFAULT_MAX_REPLY_BYTES",
    "MAX_FRAME_BYTES",
    "ReplyAudio",
    "ReplyAudioFrame",
]
