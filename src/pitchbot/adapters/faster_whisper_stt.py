"""faster-whisper adapter behind the existing ``SpeechToTextAdapter`` contract.

This is the first provider that turns buyer audio into text. It is opt-in, never a default,
and it selects no model on your behalf. ADR-0002 anticipates exactly this - a real provider
arriving behind an unchanged contract - so
``transcribe(AsyncIterator[AudioChunk]) -> AsyncIterator[TranscriptChunk]`` is implemented
as written and the protocol is untouched.

**The dependency is optional.** ``pitchbot`` imports, and every test that does not name this
module passes, with ``faster_whisper`` absent; this module itself also imports cleanly
without it, so callers can probe :data:`FASTER_WHISPER_AVAILABLE` instead of guarding an
``ImportError``. Install it with ``pip install "pitchbot[faster-whisper]"``.

**Nothing is downloaded unless you say so.** ``WhisperModel`` fetches weights from Hugging
Face on first use by default; this adapter inverts that and passes ``local_files_only=True``
unless the caller explicitly sets ``allow_download=True``. A missing model is a
:class:`PermanentAdapterError` that names how to pre-fetch it, rather than a silent
multi-hundred-megabyte download in the middle of a call - or in the middle of CI.

Why this adapter is utterance-batch and not streaming
=====================================================

Whisper always encodes a **padded 30-second mel window**, so its cost is essentially
constant per call rather than proportional to the audio. Measured 2026-09-03 with ``small``
on CPU/int8: 3.58 s of audio took 2.09 s, 7.15 s took 2.15 s, 14.30 s took 2.10 s, and
28.61 s took 2.09 s - twelve times the audio for 1.9x the time, and that 1.9x appears only
at 42.91 s, where the clip crosses into a *second* window and costs about twice as much.

Three consequences are designed in here:

1. **Real-time factor is a misleading metric for this model.** It looks alarming on a short
   clip and excellent on a long one while the model does identical work. The number that
   matters is *latency after end-of-speech*, which is roughly **2.1 s** and roughly
   constant.
2. **Chopping audio into small streaming chunks would be pathological**, because every
   chunk pays a full window pass. So this adapter consumes the whole endpointed utterance
   and transcribes it once. ``SpeechTurnPipeline`` already buffers exactly that way.
3. **Partials are real, not fabricated.** Whisper emits segments as it decodes and never
   revises them, so each segment is yielded as a non-final chunk carrying the transcript
   *so far*. Every stream then ends with exactly one final chunk carrying the complete
   text. That last point is load-bearing: ``SpeechTurnPipeline._best_transcript`` keeps the
   **last final** transcript, so emitting per-segment finals would silently discard
   everything the buyer said before the last segment.

Audio is refused rather than repaired
=====================================

Whisper expects 16 kHz mono. ``AudioChunk`` carries a declared ``sample_rate_hz``, and an
utterance at any other rate raises rather than being reinterpreted, because feeding 22.05
kHz samples to a 16 kHz model does not fail - it silently transcribes pitch-shifted,
time-stretched audio and reports a plausible wrong duration. Resampling would need a
dependency this repository does not carry, and guessing is worse than refusing.

Model choice is a measured constraint, not a preference
=======================================================

Round-trip measurement (Piper-synthesised speech, CPU/int8) showed ``tiny`` and ``base``
are **disqualified for this product**: on Hindi they do not merely score badly, they emit
the wrong *script* entirely - romanised Latin for ``tiny``, Urdu/Arabic for ``base`` - which
no threshold tuning fixes. ``small`` returns correct Devanagari. :data:`DEFAULT_MODEL_SIZE`
is therefore ``small``, and this is recorded in ``docs/BENCHMARKS.md``.

That is a floor, not a quality claim: those numbers come from synthesised speech, so they
cannot separate speech-recognition quality from text-to-speech quality. **No provider is
selected.**
"""

from __future__ import annotations

import array
import asyncio
import importlib
import logging
import math
import sys
from collections.abc import AsyncIterator, Iterator, Mapping
from dataclasses import dataclass
from importlib import metadata
from types import ModuleType
from typing import Any, Final

from pitchbot.adapters.contracts import AudioChunk, SpeechToTextAdapter, TranscriptChunk
from pitchbot.adapters.errors import PermanentAdapterError
from pitchbot.domain import LanguageCode
from pitchbot.speech.scripts import repair_telugu_transcript

logger = logging.getLogger(__name__)


def _import(name: str) -> ModuleType | None:
    """Import a module if present, without a static dependency on it."""

    try:
        return importlib.import_module(name)
    except ImportError:
        return None


_MODULE: Final[ModuleType | None] = _import("faster_whisper")
_NUMPY: Final[ModuleType | None] = _import("numpy")

FASTER_WHISPER_AVAILABLE: Final[bool] = _MODULE is not None and _NUMPY is not None
"""Whether the optional ``faster_whisper`` stack is importable in this environment.

``numpy`` is checked too because audio is handed over as a float array. ``faster_whisper``
depends on ``numpy``, so in practice these are present or absent together; checking both
keeps this module importable in a partially installed environment rather than raising a
confusing error later.
"""

INSTALL_HINT: Final[str] = 'pip install "pitchbot[faster-whisper]"'

PROVIDER_ID: Final[str] = "faster-whisper"
ALGORITHM: Final[str] = "whisper-encoder-decoder-ctranslate2"
"""OpenAI's Whisper encoder/decoder, executed through CTranslate2."""

LICENSE: Final[str] = "MIT"
"""Reviewed 2026-09-03. ``faster-whisper`` (SYSTRAN) is MIT, as is CTranslate2."""

SUPPORTED_SAMPLE_RATE_HZ: Final[int] = 16_000
_SAMPLE_WIDTH_BYTES: Final[int] = 2
_INT16_FULL_SCALE: Final[float] = 32_768.0

DEFAULT_MODEL_SIZE: Final[str] = "small"
"""Measured floor for bilingual use: ``tiny`` and ``base`` emit the wrong script for Hindi."""

DEFAULT_COMPUTE_TYPE: Final[str] = "int8"
DEFAULT_DEVICE: Final[str] = "cpu"
DEFAULT_BEAM_SIZE: Final[int] = 1

DEFAULT_MAX_AUDIO_SECONDS: Final[float] = 120.0
"""Bound on one utterance. Cost quantises per 30 s window, so this is four windows."""

DEFAULT_MIN_LANGUAGE_PROBABILITY: Final[float] = 0.5
"""Below this, the detected language is reported as ``UNKNOWN`` rather than guessed.

Whisper returns a language for anything, including silence - measured 2026-09-03, two
seconds of digital silence was reported as ``en`` with probability 0.362. Passing that
through as a confident English detection would be fabricating a decision the model did not
make.
"""

LICENSE_REVIEW_DATE: Final[str] = "2026-09-03"


@dataclass(frozen=True, slots=True)
class ModelLicense:
    """The license of a model's *weights*, which is not the package's license."""

    identifier: str
    permits_commercial_use: bool
    reference_url: str
    reviewed_on: str = LICENSE_REVIEW_DATE

    def __post_init__(self) -> None:
        if not self.identifier.strip():
            raise ValueError("identifier must not be empty")
        if not self.reference_url.strip():
            raise ValueError("reference_url must not be empty")


SYSTRAN_MIT: Final[ModelLicense] = ModelLicense(
    identifier="MIT",
    permits_commercial_use=True,
    reference_url="https://huggingface.co/Systran/faster-whisper-small",
)

KNOWN_MODEL_LICENSES: Final[Mapping[str, ModelLicense]] = {
    "tiny": SYSTRAN_MIT,
    "tiny.en": SYSTRAN_MIT,
    "base": SYSTRAN_MIT,
    "base.en": SYSTRAN_MIT,
    "small": SYSTRAN_MIT,
    "small.en": SYSTRAN_MIT,
    "medium": SYSTRAN_MIT,
    "medium.en": SYSTRAN_MIT,
    "large-v3": SYSTRAN_MIT,
}
"""Weight licenses reviewed 2026-09-03 from the Hugging Face model cards.

The CTranslate2 conversions published under ``Systran/faster-whisper-*`` are **MIT**, and
the upstream ``openai/whisper-*`` models they convert are **Apache-2.0**. Both are
permissive, so unlike the Piper *voices* reviewed in PR 33 there is no commercial-use
restriction here - but the check is performed and recorded rather than assumed, because
PR 33 found a non-commercial license hiding behind a finetune.
"""

_LANGUAGE_BY_WHISPER_CODE: Final[Mapping[str, LanguageCode]] = {
    "en": LanguageCode.ENGLISH,
    "hi": LanguageCode.HINDI,
    "te": LanguageCode.TELUGU,
}
"""Whisper reports one ISO code; anything outside this map becomes ``UNKNOWN``.

Whisper has no notion of code-switched Hinglish, so it labels such an utterance with
whichever language dominated. This adapter never *infers* :attr:`LanguageCode.MIXED`,
because deriving it from a single-label model would be inventing a distinction the model
did not draw. A caller may still *declare* ``language=LanguageCode.MIXED``, in which case
that declaration is reported as-is and Whisper runs in auto-detect.
"""

_WHISPER_FORCEABLE: Final[frozenset[LanguageCode]] = frozenset(
    {LanguageCode.ENGLISH, LanguageCode.HINDI, LanguageCode.TELUGU}
)
"""Only these can be forced on Whisper; ``MIXED``/``UNKNOWN`` are not Whisper languages."""

_SCRIPT_REPAIRED: Final[frozenset[LanguageCode]] = frozenset({LanguageCode.TELUGU})
"""Languages whose transcript Whisper writes in the wrong alphabet.

Telugu is the measured case: ``small`` and ``medium`` both returned **100% Devanagari and
0% Telugu letters** on every clip, while auto-detecting the language as ``te`` at 0.76-0.98
confidence. The sounds are right and the script is Hindi's. Transliterating afterwards takes
character error rate from 100% to 41%; the alternative fix, an ``initial_prompt`` script
anchor, corrects the alphabet and destroys the words (CER 90-116%). See
:mod:`pitchbot.speech.scripts` for the measurement and the mapping.

Repair runs only when the caller **declared** the language, never on an auto-detected one.
A detected label is a guess, and rewriting a Hindi transcript into Telugu on the strength
of a guess would corrupt the language this project already supports.
"""


def require_faster_whisper() -> tuple[ModuleType, ModuleType]:
    """The imported modules, or a permanent adapter error naming the extra."""

    if _MODULE is None or _NUMPY is None:
        missing = "faster_whisper" if _MODULE is None else "numpy"
        raise PermanentAdapterError(
            f"{missing} is not installed; install the optional extra with: {INSTALL_HINT}"
        )
    return _MODULE, _NUMPY


def installed_distribution() -> tuple[str, str] | None:
    """The distribution providing ``faster_whisper``, and its exact version."""

    try:
        return "faster-whisper", metadata.version("faster-whisper")
    except metadata.PackageNotFoundError:
        return None


def model_license(model_size: str) -> ModelLicense:
    """The reviewed license for a model identifier, refusing an unreviewed one."""

    known = KNOWN_MODEL_LICENSES.get(model_size)
    if known is None:
        raise PermanentAdapterError(
            f"model {model_size!r} has no reviewed license in KNOWN_MODEL_LICENSES; "
            "review the model card and add it before using this model"
        )
    return known


@dataclass(frozen=True, slots=True)
class WhisperProvenance:
    """Exact identity of what produced a transcript, as ADR-0004 requires."""

    provider_id: str
    package: str
    package_version: str
    algorithm: str
    package_license: str
    model_size: str
    model_license: str
    compute_type: str
    device: str
    beam_size: int
    sample_rate_hz: int


def _decode_pcm(payload: bytes) -> list[float]:
    """Mono 16-bit little-endian PCM to floats in [-1, 1), without numpy.

    Done with :mod:`array` so that the conversion itself carries no dependency; the result
    is handed to numpy only when the optional stack is present.
    """

    samples = array.array("h")
    samples.frombytes(payload)
    if samples.itemsize != _SAMPLE_WIDTH_BYTES:  # pragma: no cover - 'h' is 2 bytes
        raise PermanentAdapterError("platform 'h' array is not 16-bit")
    if sys.byteorder == "big":  # pragma: no cover - little-endian in CI and on target
        samples.byteswap()
    return [value / _INT16_FULL_SCALE for value in samples]


def _advance(iterator: Iterator[Any]) -> Any | None:
    """Pull one Whisper segment, or ``None`` at exhaustion. Runs on a worker thread."""

    return next(iterator, None)


class FasterWhisperSpeechToTextAdapter(SpeechToTextAdapter):
    """Whisper transcription of one endpointed utterance at a time."""

    def __init__(
        self,
        *,
        model_size: str = DEFAULT_MODEL_SIZE,
        device: str = DEFAULT_DEVICE,
        compute_type: str = DEFAULT_COMPUTE_TYPE,
        beam_size: int = DEFAULT_BEAM_SIZE,
        download_root: str | None = None,
        allow_download: bool = False,
        language: LanguageCode | None = None,
        max_audio_seconds: float = DEFAULT_MAX_AUDIO_SECONDS,
        min_language_probability: float = DEFAULT_MIN_LANGUAGE_PROBABILITY,
        emit_partials: bool = True,
    ) -> None:
        if beam_size < 1:
            raise ValueError("beam_size must be positive")
        if max_audio_seconds <= 0:
            raise ValueError("max_audio_seconds must be positive")
        if not 0.0 <= min_language_probability <= 1.0:
            raise ValueError("min_language_probability must be between 0 and 1")
        self._license = model_license(model_size)
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._beam_size = beam_size
        self._download_root = download_root
        self._allow_download = allow_download
        self._language = language
        self._max_audio_seconds = max_audio_seconds
        self._min_language_probability = min_language_probability
        self._emit_partials = emit_partials
        self._model: Any | None = None
        self._load_lock = asyncio.Lock()

    @property
    def model_size(self) -> str:
        return self._model_size

    @property
    def language(self) -> LanguageCode | None:
        """The declared language, or ``None`` when Whisper auto-detects."""

        return self._language

    @property
    def license(self) -> ModelLicense:
        return self._license

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def max_audio_bytes(self) -> int:
        return int(self._max_audio_seconds * SUPPORTED_SAMPLE_RATE_HZ * _SAMPLE_WIDTH_BYTES)

    async def preload(self) -> None:
        """Load the model now so that no live call pays the load cost.

        Model construction was measured at ~3.6 s and, like Piper's voice loading, holds
        the GIL for much of that even on a worker thread. Calling this during startup turns
        an unpredictable mid-conversation freeze into a predictable startup cost.
        """

        await self._load_model()

    async def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        async with self._load_lock:
            if self._model is not None:
                return self._model
            module, _ = require_faster_whisper()
            try:
                model = await asyncio.to_thread(
                    module.WhisperModel,
                    self._model_size,
                    device=self._device,
                    compute_type=self._compute_type,
                    download_root=self._download_root,
                    local_files_only=not self._allow_download,
                )
            except Exception as error:
                hint = (
                    ""
                    if self._allow_download
                    else (
                        " The model is not present locally and downloading is disabled. "
                        "Pre-fetch it, or construct the adapter with allow_download=True "
                        "to permit a one-time download."
                    )
                )
                raise PermanentAdapterError(
                    f"faster-whisper failed to load model {self._model_size!r}: {error}.{hint}"
                ) from error
            self._model = model
            return model

    def provenance(self) -> WhisperProvenance:
        distribution = installed_distribution()
        package, version = distribution if distribution is not None else (PROVIDER_ID, "unknown")
        return WhisperProvenance(
            provider_id=PROVIDER_ID,
            package=package,
            package_version=version,
            algorithm=ALGORITHM,
            package_license=LICENSE,
            model_size=self._model_size,
            model_license=self._license.identifier,
            compute_type=self._compute_type,
            device=self._device,
            beam_size=self._beam_size,
            sample_rate_hz=SUPPORTED_SAMPLE_RATE_HZ,
        )

    async def _collect_audio(self, audio: AsyncIterator[AudioChunk]) -> bytes:
        """Drain the utterance, refusing anything Whisper cannot correctly consume."""

        limit = self.max_audio_bytes
        payload = bytearray()
        async for chunk in audio:
            if chunk.sample_rate_hz != SUPPORTED_SAMPLE_RATE_HZ:
                raise PermanentAdapterError(
                    f"whisper requires {SUPPORTED_SAMPLE_RATE_HZ} Hz mono audio, frame "
                    f"{chunk.sequence} declares {chunk.sample_rate_hz} Hz. Audio is refused "
                    "rather than resampled: reinterpreting the rate silently transcribes "
                    "pitch-shifted, time-stretched speech instead of failing"
                )
            if len(payload) + len(chunk.data) > limit:
                raise PermanentAdapterError(
                    f"utterance exceeds max_audio_seconds={self._max_audio_seconds}"
                    f" ({limit} bytes at {SUPPORTED_SAMPLE_RATE_HZ} Hz)"
                )
            payload.extend(chunk.data)
        if len(payload) % _SAMPLE_WIDTH_BYTES:
            raise PermanentAdapterError(
                f"utterance carries {len(payload)} bytes, which is not a whole number of "
                "16-bit samples"
            )
        return bytes(payload)

    def _resolve_language(self, detected: str, probability: float) -> LanguageCode:
        if self._language is not None:
            return self._language
        if probability < self._min_language_probability:
            return LanguageCode.UNKNOWN
        return _LANGUAGE_BY_WHISPER_CODE.get(detected, LanguageCode.UNKNOWN)

    @staticmethod
    def _segment_confidence(segment: Any) -> float:
        """``exp(avg_logprob)`` - the geometric mean token probability of the segment.

        This is a real quantity the decoder produced, not a fabricated score: ``avg_logprob``
        is the mean log probability per token, so its exponential is a probability in
        ``(0, 1]``. It is clamped only to absorb floating-point drift at the boundary.
        """

        try:
            value = math.exp(float(segment.avg_logprob))
        except (OverflowError, ValueError, TypeError):  # pragma: no cover - defensive
            return 0.0
        return min(1.0, max(0.0, value))

    async def transcribe(
        self,
        audio: AsyncIterator[AudioChunk],
    ) -> AsyncIterator[TranscriptChunk]:
        payload = await self._collect_audio(audio)
        model = await self._load_model()
        _, numpy = require_faster_whisper()

        samples = numpy.asarray(_decode_pcm(payload), dtype=numpy.float32)
        whisper_language = self._language.value if self._language in _WHISPER_FORCEABLE else None

        try:
            segments, info = await asyncio.to_thread(
                model.transcribe,
                samples,
                language=whisper_language,
                beam_size=self._beam_size,
                vad_filter=False,
            )
        except Exception as error:
            raise PermanentAdapterError(f"faster-whisper failed to transcribe: {error}") from error

        iterator: Iterator[Any] = iter(segments)
        pieces: list[str] = []
        weighted_confidence = 0.0
        total_duration = 0.0
        sequence = 0

        while True:
            try:
                segment = await asyncio.to_thread(_advance, iterator)
            except Exception as error:
                raise PermanentAdapterError(
                    f"faster-whisper failed while decoding: {error}"
                ) from error
            if segment is None:
                break
            pieces.append(str(segment.text))
            duration = max(0.0, float(segment.end) - float(segment.start))
            weighted_confidence += self._segment_confidence(segment) * duration
            total_duration += duration
            if self._emit_partials:
                # Cumulative, not per-segment: a consumer that reads a single partial must
                # see everything said so far, because Whisper never revises a segment.
                yield TranscriptChunk(
                    text=self._repair_script("".join(pieces).strip()),
                    language=self._resolve_language(info.language, info.language_probability),
                    confidence=self._segment_confidence(segment),
                    is_final=False,
                    sequence=sequence,
                )
                sequence += 1

        confidence = (weighted_confidence / total_duration) if total_duration > 0 else 0.0
        # Exactly one final chunk, carrying the complete text. SpeechTurnPipeline keeps the
        # last final transcript, so a per-segment final would discard everything before it.
        yield TranscriptChunk(
            text=self._repair_script("".join(pieces).strip()),
            language=self._resolve_language(info.language, info.language_probability),
            confidence=min(1.0, max(0.0, confidence)),
            is_final=True,
            sequence=sequence,
        )

    def _repair_script(self, text: str) -> str:
        """Rewrite a declared-Telugu transcript that Whisper returned in Devanagari.

        Applied to partials as well as the final chunk so a consumer never sees the text
        change alphabet mid-turn, which would look like the model changing its mind about
        the language rather than a transliteration being applied.
        """

        if self._language not in _SCRIPT_REPAIRED:
            return text
        repaired, changed = repair_telugu_transcript(text)
        if changed:
            logger.debug(
                "transliterated declared-%s transcript from Devanagari",
                self._language.value,
            )
        return repaired


__all__ = [
    "ALGORITHM",
    "DEFAULT_BEAM_SIZE",
    "DEFAULT_COMPUTE_TYPE",
    "DEFAULT_DEVICE",
    "DEFAULT_MAX_AUDIO_SECONDS",
    "DEFAULT_MIN_LANGUAGE_PROBABILITY",
    "DEFAULT_MODEL_SIZE",
    "FASTER_WHISPER_AVAILABLE",
    "INSTALL_HINT",
    "KNOWN_MODEL_LICENSES",
    "LICENSE",
    "LICENSE_REVIEW_DATE",
    "PROVIDER_ID",
    "SUPPORTED_SAMPLE_RATE_HZ",
    "FasterWhisperSpeechToTextAdapter",
    "ModelLicense",
    "WhisperProvenance",
    "installed_distribution",
    "model_license",
    "require_faster_whisper",
]
