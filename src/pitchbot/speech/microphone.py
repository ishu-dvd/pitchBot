"""Let PitchBot hear a real person, not a file.

Everything upstream of this module was already able to listen: there is a voice-activity
detector, an endpointing state machine, a transcriber, and a pipeline that joins them. None
of it had a source. Audio arrived from a WebSocket in the simulator or from a synthesised
WAV in a test, so the only voices PitchBot had ever heard were ones it had been handed.
This is the missing end of that pipe.

**Frames are produced at exactly the size the detector demands.** WebRTC's VAD accepts only
10, 20 or 30 ms of mono 16-bit PCM at one of four sample rates, and rejects anything else
outright. Rather than capture at whatever the device prefers and repack downstream, the
stream is opened at 16 kHz mono with a block size of exactly one frame, so every callback
produces one legal frame and no buffering or resampling code exists to get wrong. Measured
on the target hardware, PortAudio accepts a 16 kHz mono int16 input stream directly and
resamples internally, so this costs nothing.

**The device is opened once and kept open.** Opening cost ~840 ms in measurement, which is
longer than most of the utterances it would be opened for. A stream per utterance would put
that latency between the buyer finishing a sentence and the agent noticing.

**Audio is never written anywhere.** Frames are handed to the caller and forgotten; the
queue is bounded and drops the *oldest* frame when the consumer falls behind. Dropping the
newest would be easier and is wrong: stale audio pushed into an endpointer later reports
silence that has already been broken, so the buyer's turn ends in the wrong place. A
bounded queue that discards history is also the only version of this that cannot grow
without limit while the agent is busy, which matters because the standing promise is that
no call audio is retained.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from types import ModuleType, TracebackType
from typing import Any, Final

from pitchbot.adapters.clock import Clock, SystemClock
from pitchbot.adapters.contracts import AudioChunk
from pitchbot.adapters.errors import AdapterError

logger = logging.getLogger(__name__)

SAMPLE_RATE_HZ: Final[int] = 16_000
"""What both the WebRTC detector and Whisper want, so nothing has to convert."""

FRAME_MS: Final[int] = 30
"""The longest frame WebRTC's VAD accepts, so the fewest callbacks per second."""

_SAMPLE_WIDTH_BYTES: Final[int] = 2

FRAME_SAMPLES: Final[int] = SAMPLE_RATE_HZ * FRAME_MS // 1_000
FRAME_BYTES: Final[int] = FRAME_SAMPLES * _SAMPLE_WIDTH_BYTES

DEFAULT_QUEUE_FRAMES: Final[int] = 100
"""Three seconds of audio. Long enough to ride out a slow turn, short enough that what
survives a stall is still recent enough to be worth transcribing."""

_LICENSE: Final[str] = "MIT"
"""Reviewed 2026-09-04 from the published package metadata.

``sounddevice`` declares ``license_expression: MIT`` and bundles PortAudio, itself MIT.
Both are permissive with no distribution obligation, unlike the ``piper-tts`` extra, which
is GPL-3.0-or-later. It is a pure-Python ``cffi`` binding - there are no model weights, and
installing it downloads nothing at import or capture time.
"""


def _import_sounddevice() -> ModuleType | None:
    try:
        return importlib.import_module("sounddevice")
    except Exception:  # pragma: no cover - depends on host audio libraries
        # Deliberately broad. sounddevice raises OSError when PortAudio itself cannot be
        # loaded, which is a normal state on a headless machine and must not look
        # different from the package simply being absent.
        return None


def require_sounddevice() -> ModuleType:
    """The module, or a clear explanation of what to install."""

    module = _import_sounddevice()
    if module is None:
        raise AdapterError(
            "microphone capture needs the 'microphone' extra: pip install 'pitchbot[microphone]'"
        )
    return module


def installed_distribution() -> tuple[str, str] | None:
    """``(name, version)`` when sounddevice is importable, for provenance."""

    module = _import_sounddevice()
    if module is None:
        return None
    return "sounddevice", str(getattr(module, "__version__", "unknown"))


def is_available() -> bool:
    """Whether a microphone could be opened at all, without opening one."""

    return _import_sounddevice() is not None


@dataclass(frozen=True, slots=True)
class MicrophoneProvenance:
    """What produced the audio, recorded alongside anything measured from it."""

    distribution: str
    version: str
    license: str
    device: str
    sample_rate_hz: int
    frame_ms: int


def input_devices() -> tuple[tuple[int, str], ...]:
    """Indexes and names of devices that can capture, for choosing one by hand.

    Returns empty rather than raising when audio is unavailable, because "there is no
    microphone" is an ordinary answer on a server and a caller listing devices is asking
    exactly that question.
    """

    module = _import_sounddevice()
    if module is None:
        return ()
    try:
        devices = module.query_devices()
    except Exception:  # pragma: no cover - depends on host audio configuration
        return ()
    found: list[tuple[int, str]] = []
    for index, device in enumerate(devices):
        if int(device.get("max_input_channels", 0)) > 0:
            found.append((index, str(device.get("name", f"device {index}"))))
    return tuple(found)


class Microphone:
    """A live microphone as an async stream of detector-ready frames.

    Used as an async context manager so the device is always closed, including when the
    conversation is abandoned mid-utterance:

    >>> async with Microphone() as mic:            # doctest: +SKIP
    ...     async for chunk in mic.frames():
    ...         await pipeline.push(chunk)

    :meth:`pause` and :meth:`resume` exist for half-duplex turn-taking. Without acoustic
    echo cancellation - which this project does not have and will not write - a microphone
    left open while the agent speaks through the same machine's speakers hears the agent,
    and the endpointer treats it as the buyer talking. Pausing is the honest fix; it costs
    the ability to interrupt, which is stated plainly wherever this is offered rather than
    hidden behind a barge-in feature that would fire on the agent's own voice.
    """

    def __init__(
        self,
        *,
        device: int | str | None = None,
        sample_rate_hz: int = SAMPLE_RATE_HZ,
        frame_ms: int = FRAME_MS,
        max_queued_frames: int = DEFAULT_QUEUE_FRAMES,
        clock: Clock | None = None,
    ) -> None:
        if frame_ms not in (10, 20, 30):
            raise ValueError(
                f"frame_ms must be 10, 20 or 30 to satisfy the detector, got {frame_ms}"
            )
        if sample_rate_hz not in (8_000, 16_000, 32_000, 48_000):
            raise ValueError(
                f"sample_rate_hz must be a detector-supported rate, got {sample_rate_hz}"
            )
        if max_queued_frames < 1:
            raise ValueError("max_queued_frames must be at least 1")
        self._device = device
        self._sample_rate_hz = sample_rate_hz
        self._frame_ms = frame_ms
        self._frame_samples = sample_rate_hz * frame_ms // 1_000
        self._clock = clock or SystemClock()
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=max_queued_frames)
        self._stream: Any | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sequence = 0
        self._dropped_frames = 0
        self._paused = False

    @property
    def frame_bytes(self) -> int:
        return self._frame_samples * _SAMPLE_WIDTH_BYTES

    @property
    def dropped_frames(self) -> int:
        """Frames discarded because the consumer could not keep up."""

        return self._dropped_frames

    @property
    def is_open(self) -> bool:
        return self._stream is not None

    def provenance(self) -> MicrophoneProvenance:
        distribution = installed_distribution() or ("sounddevice", "unavailable")
        return MicrophoneProvenance(
            distribution=distribution[0],
            version=distribution[1],
            license=_LICENSE,
            device=str(self._device) if self._device is not None else "default",
            sample_rate_hz=self._sample_rate_hz,
            frame_ms=self._frame_ms,
        )

    def _on_audio(self, data: Any, _frames: int, _time: Any, status: Any) -> None:
        """Called on a PortAudio thread, never on the event loop.

        Nothing here may block or touch asyncio state directly, which is why the only
        action is a thread-safe hand-off. ``status`` reports overflows from the driver;
        they are logged once per occurrence rather than raised, because an audio glitch
        must not end a sales call.
        """

        if status:  # pragma: no cover - driver-dependent
            logger.debug("Microphone reported %s", status)
        loop = self._loop
        if loop is None or self._paused:
            return
        payload = bytes(data)
        loop.call_soon_threadsafe(self._offer, payload)

    def _offer(self, payload: bytes) -> None:
        """Enqueue on the event loop thread, discarding the oldest frame when full."""

        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover - drained concurrently
                pass
            else:
                self._dropped_frames += 1
        try:
            self._queue.put_nowait(payload)
        except asyncio.QueueFull:  # pragma: no cover - refilled concurrently
            self._dropped_frames += 1

    async def start(self) -> None:
        """Open the device. Safe to call twice; the second call does nothing."""

        if self._stream is not None:
            return
        module = require_sounddevice()
        self._loop = asyncio.get_running_loop()
        try:
            stream = module.RawInputStream(
                samplerate=self._sample_rate_hz,
                blocksize=self._frame_samples,
                device=self._device,
                channels=1,
                dtype="int16",
                callback=self._on_audio,
            )
            stream.start()
        except Exception as error:
            self._loop = None
            raise AdapterError(f"could not open the microphone: {error}") from error
        self._stream = stream

    async def stop(self) -> None:
        """Close the device and drop anything still queued."""

        stream, self._stream = self._stream, None
        self._loop = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # pragma: no cover - driver-dependent
                logger.debug("Microphone did not close cleanly", exc_info=True)
        while not self._queue.empty():
            self._queue.get_nowait()

    def pause(self) -> None:
        """Stop delivering frames without closing the device.

        The stream keeps running so that resuming costs nothing; captured audio is simply
        dropped at the callback. Closing instead would reintroduce the device-open latency
        between every agent reply and the buyer's next word.
        """

        self._paused = True

    def resume(self) -> None:
        """Deliver frames again, discarding whatever arrived while paused.

        The queue is drained first because anything captured during the pause is either
        the agent's own voice leaking through the speakers or silence while it spoke.
        Neither is buyer speech, and feeding it to the endpointer would open an utterance
        that nobody started.
        """

        while not self._queue.empty():
            self._queue.get_nowait()
        self._paused = False

    async def frames(self) -> AsyncIterator[AudioChunk]:
        """Yield captured frames until the device is stopped.

        The iterator ends when :meth:`stop` is called, so a caller can end a conversation
        by closing the microphone from another task rather than by cancelling this one.
        """

        if self._stream is None:
            await self.start()
        while self._stream is not None:
            try:
                payload = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except TimeoutError:
                # A silent device is normal. Looping re-checks whether the stream was
                # closed, which is what actually ends this iterator.
                continue
            self._sequence += 1
            yield AudioChunk(
                data=payload,
                captured_at=self._clock.now(),
                sequence=self._sequence,
                sample_rate_hz=self._sample_rate_hz,
            )

    async def __aenter__(self) -> Microphone:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.stop()


__all__ = [
    "DEFAULT_QUEUE_FRAMES",
    "FRAME_BYTES",
    "FRAME_MS",
    "FRAME_SAMPLES",
    "SAMPLE_RATE_HZ",
    "Microphone",
    "MicrophoneProvenance",
    "input_devices",
    "installed_distribution",
    "is_available",
    "require_sounddevice",
]
