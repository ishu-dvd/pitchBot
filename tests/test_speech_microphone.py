"""The microphone, tested without a microphone.

Every assertion here runs on a machine with no audio device and no ``sounddevice``
installed, which is the point: CI has neither, and a voice loop that can only be tested by
speaking into it is a voice loop that is never tested. The device is replaced by a fake
PortAudio stream that hands frames to the same callback the real driver would.

What is deliberately *not* asserted is that PortAudio works. That was established by
measurement on the target hardware and recorded in ``docs/BENCHMARKS.md``; repeating it here
would make the suite depend on a driver and prove nothing about this module's own logic,
which is framing, back-pressure and floor control.
"""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType
from typing import Any

import pytest

from pitchbot.adapters.errors import AdapterError
from pitchbot.speech import microphone as mic_module
from pitchbot.speech.microphone import (
    FRAME_BYTES,
    FRAME_MS,
    FRAME_SAMPLES,
    SAMPLE_RATE_HZ,
    Microphone,
)


class FakeStream:
    """A PortAudio stream that delivers frames when told to, on this thread."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.started = False
        self.closed = False
        self._callback = kwargs["callback"]

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def close(self) -> None:
        self.closed = True

    def deliver(self, payload: bytes) -> None:
        self._callback(payload, len(payload) // 2, None, None)


class FakeSoundDevice(ModuleType):
    """Stands in for the ``sounddevice`` module."""

    __version__ = "0.5.6-fake"

    def __init__(self, *, fail: str | None = None) -> None:
        super().__init__("sounddevice")
        self.streams: list[FakeStream] = []
        self._fail = fail

    def RawInputStream(self, **kwargs: Any) -> FakeStream:  # noqa: N802 - mirrors the real API
        if self._fail is not None:
            raise OSError(self._fail)
        stream = FakeStream(**kwargs)
        self.streams.append(stream)
        return stream

    def query_devices(self) -> list[dict[str, Any]]:
        return [
            {"name": "Fake Mic", "max_input_channels": 1},
            {"name": "Fake Speaker", "max_input_channels": 0},
        ]


@pytest.fixture
def fake_sounddevice(monkeypatch: pytest.MonkeyPatch) -> FakeSoundDevice:
    module = FakeSoundDevice()
    monkeypatch.setitem(sys.modules, "sounddevice", module)
    return module


def test_frame_size_is_exactly_what_the_detector_accepts() -> None:
    """30 ms of 16 kHz mono 16-bit PCM. WebRTC's VAD rejects anything else outright."""

    assert FRAME_MS in (10, 20, 30)
    assert SAMPLE_RATE_HZ == 16_000
    assert FRAME_SAMPLES == 480
    assert FRAME_BYTES == 960


def test_a_frame_size_the_detector_would_reject_is_refused_at_construction() -> None:
    """Better here than as a per-frame error deep inside a call."""

    with pytest.raises(ValueError, match="frame_ms"):
        Microphone(frame_ms=25)
    with pytest.raises(ValueError, match="sample_rate_hz"):
        Microphone(sample_rate_hz=22_050)
    with pytest.raises(ValueError, match="max_queued_frames"):
        Microphone(max_queued_frames=0)


def test_a_missing_package_names_the_extra_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mic_module, "_import_sounddevice", lambda: None)

    assert mic_module.is_available() is False
    assert mic_module.installed_distribution() is None
    assert mic_module.input_devices() == ()
    with pytest.raises(AdapterError, match="pitchbot\\[microphone\\]"):
        mic_module.require_sounddevice()


def test_input_devices_lists_only_capture_devices(fake_sounddevice: FakeSoundDevice) -> None:
    assert mic_module.input_devices() == ((0, "Fake Mic"),)


@pytest.mark.asyncio
async def test_captured_audio_is_yielded_as_detector_ready_chunks(
    fake_sounddevice: FakeSoundDevice,
) -> None:
    mic = Microphone()
    await mic.start()
    stream = fake_sounddevice.streams[0]

    assert stream.kwargs["samplerate"] == SAMPLE_RATE_HZ
    assert stream.kwargs["blocksize"] == FRAME_SAMPLES
    assert stream.kwargs["channels"] == 1
    assert stream.kwargs["dtype"] == "int16"

    frames = mic.frames()
    stream.deliver(b"\x01\x02" * FRAME_SAMPLES)
    chunk = await asyncio.wait_for(frames.__anext__(), timeout=2)

    assert len(chunk.data) == FRAME_BYTES
    assert chunk.sample_rate_hz == SAMPLE_RATE_HZ
    assert chunk.sequence == 1
    assert chunk.captured_at.tzinfo is not None
    await mic.stop()


@pytest.mark.asyncio
async def test_a_slow_consumer_loses_the_oldest_audio_not_the_newest(
    fake_sounddevice: FakeSoundDevice,
) -> None:
    """Back-pressure that discarded new frames would endpoint on silence already broken.

    A consumer that stalls must resume on what the buyer is saying *now*. Keeping the
    oldest frames and dropping arrivals would feed the endpointer a stale run of speech,
    so the utterance would close in the wrong place - and the queue is bounded precisely
    so that a stall cannot retain unbounded call audio either.
    """

    mic = Microphone(max_queued_frames=2)
    await mic.start()
    stream = fake_sounddevice.streams[0]

    for marker in (b"\x01", b"\x02", b"\x03"):
        stream.deliver(marker * FRAME_BYTES)
        await asyncio.sleep(0)

    frames = mic.frames()
    first = await asyncio.wait_for(frames.__anext__(), timeout=2)
    second = await asyncio.wait_for(frames.__anext__(), timeout=2)

    assert first.data[:1] == b"\x02"
    assert second.data[:1] == b"\x03"
    assert mic.dropped_frames == 1
    await mic.stop()


@pytest.mark.asyncio
async def test_pausing_drops_the_agents_own_voice(fake_sounddevice: FakeSoundDevice) -> None:
    """Half duplex, and the reason for it.

    There is no echo cancellation, so audio captured while the agent speaks is the agent.
    Delivering it would make the endpointer report a buyer turn that nobody took. Resuming
    also drains what arrived during the pause, because that audio is equally not the buyer.
    """

    mic = Microphone()
    await mic.start()
    stream = fake_sounddevice.streams[0]

    mic.pause()
    stream.deliver(b"\xaa" * FRAME_BYTES)
    await asyncio.sleep(0)
    mic.resume()
    stream.deliver(b"\xbb" * FRAME_BYTES)

    frames = mic.frames()
    chunk = await asyncio.wait_for(frames.__anext__(), timeout=2)
    assert chunk.data[:1] == b"\xbb"
    await mic.stop()


@pytest.mark.asyncio
async def test_stopping_closes_the_device_and_ends_the_stream(
    fake_sounddevice: FakeSoundDevice,
) -> None:
    """A conversation that ends must not leave a microphone open."""

    async with Microphone() as mic:
        stream = fake_sounddevice.streams[0]
        assert stream.started is True
        assert mic.is_open is True

    assert stream.closed is True
    assert mic.is_open is False


@pytest.mark.asyncio
async def test_starting_twice_opens_one_device(fake_sounddevice: FakeSoundDevice) -> None:
    mic = Microphone()
    await mic.start()
    await mic.start()

    assert len(fake_sounddevice.streams) == 1
    await mic.stop()


@pytest.mark.asyncio
async def test_a_device_that_will_not_open_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """Installing the extra does not mean the machine has a microphone."""

    monkeypatch.setitem(sys.modules, "sounddevice", FakeSoundDevice(fail="no default device"))
    mic = Microphone()
    with pytest.raises(AdapterError, match="could not open the microphone"):
        await mic.start()
    assert mic.is_open is False


def test_provenance_records_the_licence(fake_sounddevice: FakeSoundDevice) -> None:
    """Every optional component in this project records what it is licensed under."""

    provenance = Microphone().provenance()
    assert provenance.license == "MIT"
    assert provenance.sample_rate_hz == SAMPLE_RATE_HZ
    assert provenance.frame_ms == FRAME_MS
