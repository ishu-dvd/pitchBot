"""Supertonic 3: the only engine measured that can speak Hindi commercially.

PitchBot has never been able to say a Hindi word aloud in a deployment that sells anything.
Every published Piper Hindi voice reviewed on 2026-09-03 is CC-BY-NC-SA or points at an
IITM licence that returns 403, and this project treats an unread licence and a denied one
identically. English and Telugu are cleared; Hindi was simply unavailable.

A survey on 2026-09-06 left one candidate that clears the gate *and* runs on CPU, and it
was verified at source rather than from Hugging Face metadata - which matters, because the
metadata is exactly where this check goes wrong: `k2-fsa/OmniVoice`, the engine behind the
most popular "VoiceStudio" project, publishes an **empty** `license` field while its model
card says the weights are CC-BY-NC.

Measured on this hardware (`probe_supertonic_hindi.py`, 8 Hindi sales turns, 16 cores):

===========  =========  ===========  ==================
total_steps  median ms  Hindi CER    note
===========  =========  ===========  ==================
2            509 ms     46.2%        too few steps
4            658 ms     21.9%        ~ Piper's quality
8            1,130 ms   **13.2%**    better than Piper
16           2,048 ms   13.5%        no gain, 1.8x cost
===========  =========  ===========  ==================

The comparison is `hi_IN-pratham-medium` at **18.3%** CER through the same transcriber - a
voice this project may not ship. So at 8 steps Supertonic is *more* intelligible than the
Hindi voice PitchBot could never use, and legal.

Two costs are real and are not hidden here:

**It does not stream within a sentence.** ``synthesize()`` returns one complete waveform,
so time-to-first-audio for a single sentence is its whole synthesis time - 1,130 ms at 8
steps against Piper's 126-448 ms. This adapter therefore synthesises **one sentence at a
time** and yields each as it lands, which is how Piper behaves and is the only reason a
multi-sentence reply starts speaking before the last sentence exists.

**It has no Telugu.** 31 languages, `hi` among them, `te` not. It can only ever be a route
for some languages, never a replacement - which is what
:class:`~pitchbot.adapters.routing_tts.LanguageRoutedTextToSpeech` exists for.

Licensing, read from the files rather than the badge:

- sample code: MIT.
- weights: BigScience **OpenRAIL-M**. Commercial use is permitted, subject to the
  Attachment A use restrictions. Clause (e) forbids disseminating generated content
  "without expressly and intelligibly disclaiming that the information and/or content is
  machine generated", and clause (g) forbids impersonation without consent. Those are
  obligations on the **deployment**, not on this file, and they are the reason this
  provider is off by default and names them in its startup error.
"""

from __future__ import annotations

import array
import asyncio
import importlib
import logging
import re
import sys
from collections.abc import AsyncIterator
from types import ModuleType
from typing import Any, Final

from pitchbot.adapters.contracts import SynthesizedAudioChunk, TextToSpeechAdapter
from pitchbot.adapters.errors import PermanentAdapterError
from pitchbot.domain import LanguageCode

logger = logging.getLogger(__name__)

SUPERTONIC_INSTALL_HINT: Final[str] = 'pip install -e ".[supertonic-tts]"'

DEFAULT_TOTAL_STEPS: Final[int] = 8
"""Flow-matching steps per sentence. The quality/latency dial, measured above.

8 rather than the library default of 8 by coincidence and by measurement: 4 halves the
latency and costs 8.7 points of CER, 16 costs 1.8x the latency for nothing at all.
"""

DEFAULT_SPEED: Final[float] = 1.05
"""The library default, kept so that changing it is a deliberate act with its own evidence."""

DEFAULT_FRAME_BYTES: Final[int] = 32_768
DEFAULT_MAX_TEXT_CHARS: Final[int] = 2_000
DEFAULT_MAX_CHUNKS: Final[int] = 512

SUPPORTED_LANGUAGES: Final[frozenset[LanguageCode]] = frozenset(
    {LanguageCode.ENGLISH, LanguageCode.HINDI}
)
"""What this adapter will serve, which is narrower than what the model supports.

The model lists 31 languages. Only the two PitchBot has measured are offered, because an
unmeasured language is a claim rather than a capability. Telugu is absent from the model
entirely, and `MIXED` is absent from this set on purpose: romanised Hinglish through a
Hindi frontend is a different question that has not been measured.
"""

_LANGUAGE_CODES: Final[dict[LanguageCode, str]] = {
    LanguageCode.ENGLISH: "en",
    LanguageCode.HINDI: "hi",
}

# Sentence boundaries for English and Devanagari. Splitting here rather than letting the
# library chunk internally is what makes a multi-sentence reply start speaking early: the
# library returns one array for the whole text, so its internal chunking is invisible.
_SENTENCE_END = re.compile(r"(?<=[.!?।])\s+")


def _import_supertonic() -> ModuleType | None:
    """Import Supertonic if present, without a static dependency on it.

    ``importlib`` rather than a guarded ``import supertonic``, for the same reason as
    :mod:`pitchbot.adapters.piper_tts`: a static import makes ``mypy`` report a *different*
    diagnostic depending on whether the optional extra happens to be installed on the
    machine running it, which turns a clean type-check into a local accident.
    """

    try:
        return importlib.import_module("supertonic")
    except Exception:  # noqa: BLE001 - absence is the answer, whatever the cause
        return None


SUPERTONIC_AVAILABLE: Final[bool] = _import_supertonic() is not None


def require_supertonic() -> ModuleType:
    module = _import_supertonic()
    if module is None:
        raise PermanentAdapterError(
            f"supertonic is not installed. Install it with: {SUPERTONIC_INSTALL_HINT}"
        )
    return module


def _to_pcm16(samples: Any) -> bytes:
    """Float samples in [-1, 1] to 16-bit little-endian PCM, without numpy.

    The stdlib rather than ``numpy.astype`` deliberately. numpy arrives *with* Supertonic,
    but this project's rule is that an optional dependency is never a runtime requirement -
    and a static ``import numpy`` here made ``mypy`` fail on any machine without the extra,
    which is exactly the class of local-accident diagnostic the importlib pattern exists to
    avoid. Measured against 658-1,130 ms of synthesis per sentence, converting ~130k
    samples in Python costs a few percent.

    32767 rather than 32768 so a sample of exactly 1.0 does not wrap to the most negative
    value, and the result is byte-swapped on a big-endian host because the socket contract
    is little-endian regardless of where the server runs.
    """

    out = array.array("h", (int(max(-1.0, min(1.0, float(s))) * 32767.0) for s in samples))
    if sys.byteorder == "big":  # pragma: no cover - CI and the target host are little-endian
        out.byteswap()
    return out.tobytes()


def split_sentences(text: str, limit: int) -> list[str]:
    """Sentences, so the first one can be spoken while the rest are still being made."""

    pieces = [piece.strip() for piece in _SENTENCE_END.split(text.strip()) if piece.strip()]
    return pieces[:limit] if pieces else []


class SupertonicTextToSpeechAdapter(TextToSpeechAdapter):
    """Supertonic 3, synthesised one sentence at a time, off the event loop."""

    def __init__(
        self,
        *,
        voice_style: str = "F1",
        model_dir: str | None = None,
        total_steps: int = DEFAULT_TOTAL_STEPS,
        speed: float = DEFAULT_SPEED,
        frame_bytes: int = DEFAULT_FRAME_BYTES,
        max_text_chars: int = DEFAULT_MAX_TEXT_CHARS,
        max_chunks: int = DEFAULT_MAX_CHUNKS,
        allow_download: bool = False,
        engine: Any | None = None,
    ) -> None:
        if total_steps < 1:
            raise ValueError("total_steps must be positive")
        if speed <= 0:
            raise ValueError("speed must be positive")
        if frame_bytes < 2 or frame_bytes % 2:
            raise ValueError("frame_bytes must be a positive even number of bytes")
        self._voice_style = voice_style
        self._model_dir = model_dir
        self._total_steps = total_steps
        self._speed = speed
        self._frame_bytes = frame_bytes
        self._max_text_chars = max_text_chars
        self._max_chunks = max_chunks
        self._allow_download = allow_download
        self._engine = engine
        self._style: Any | None = None
        self._lock = asyncio.Lock()

    @property
    def languages(self) -> frozenset[LanguageCode]:
        return SUPPORTED_LANGUAGES

    async def _load(self) -> tuple[Any, Any]:
        """Load once. Measured cold load is ~10 s including the download."""

        async with self._lock:
            engine: Any = self._engine
            if engine is None:
                module = require_supertonic()
                try:
                    engine = await asyncio.to_thread(
                        module.TTS,
                        model="supertonic-3",
                        model_dir=self._model_dir,
                        auto_download=self._allow_download,
                    )
                except Exception as error:  # noqa: BLE001
                    raise PermanentAdapterError(f"supertonic failed to load: {error}") from error
                self._engine = engine
            if self._style is None:
                try:
                    self._style = await asyncio.to_thread(engine.get_voice_style, self._voice_style)
                except Exception as error:  # noqa: BLE001
                    raise PermanentAdapterError(
                        f"supertonic has no voice style {self._voice_style!r}: {error}"
                    ) from error
            return engine, self._style

    async def synthesize(
        self,
        text: str,
        language: LanguageCode,
    ) -> AsyncIterator[SynthesizedAudioChunk]:
        if len(text) > self._max_text_chars:
            raise PermanentAdapterError(
                f"text of {len(text)} characters exceeds max_text_chars="
                f"{self._max_text_chars}; split the turn before synthesising"
            )
        code = _LANGUAGE_CODES.get(language)
        if code is None:
            # Refused rather than guessed. Handing Telugu to a model that has no Telugu
            # produces fluent audio in the wrong language, which is worse than silence.
            raise PermanentAdapterError(
                f"supertonic is not configured for {language.value!r}; "
                f"served languages are {sorted(c.value for c in SUPPORTED_LANGUAGES)}"
            )
        sentences = split_sentences(text, self._max_chunks)
        if not sentences:
            return
        engine, style = await self._load()

        sequence = 0
        for index, sentence in enumerate(sentences):
            payload, rate = await self._speak(engine, style, sentence, code)
            last_sentence = index == len(sentences) - 1
            for offset in range(0, len(payload), self._frame_bytes):
                frame = payload[offset : offset + self._frame_bytes]
                if sequence >= self._max_chunks:
                    return
                yield SynthesizedAudioChunk(
                    data=frame,
                    sequence=sequence,
                    is_final=last_sentence and offset + self._frame_bytes >= len(payload),
                    media_type="audio/pcm",
                    sample_rate_hz=rate,
                )
                sequence += 1

    async def _speak(self, engine: Any, style: Any, sentence: str, code: str) -> tuple[bytes, int]:
        def run() -> tuple[bytes, int]:
            audio, _extra = engine.synthesize(
                sentence,
                style,
                total_steps=self._total_steps,
                speed=self._speed,
                lang=code,
            )
            # `reshape(-1)` when it is a numpy array, a plain sequence otherwise. The model
            # emits float32 in [-1, 1]; the socket contract is 16-bit little-endian PCM at
            # the voice's own rate, which is 44,100 Hz here and not the 16,000 the rest of
            # the pipeline uses.
            flat = audio.reshape(-1) if hasattr(audio, "reshape") else audio
            return _to_pcm16(flat), int(engine.sample_rate)

        try:
            return await asyncio.to_thread(run)
        except Exception as error:  # noqa: BLE001
            raise PermanentAdapterError(f"supertonic failed to synthesise: {error}") from error


__all__ = [
    "DEFAULT_MAX_CHUNKS",
    "DEFAULT_MAX_TEXT_CHARS",
    "DEFAULT_SPEED",
    "DEFAULT_TOTAL_STEPS",
    "SUPERTONIC_AVAILABLE",
    "SUPERTONIC_INSTALL_HINT",
    "SUPPORTED_LANGUAGES",
    "SupertonicTextToSpeechAdapter",
    "require_supertonic",
    "split_sentences",
]
