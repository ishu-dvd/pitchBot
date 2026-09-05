"""Piper adapter behind the existing ``TextToSpeechAdapter`` contract.

This is the first real *speech-producing* provider in the repository. It is opt-in,
never a default, and it selects no voice on your behalf: a caller supplies a
:class:`PiperVoiceRegistry` that maps each :class:`LanguageCode` it intends to serve to
an on-disk voice, and anything unmapped is refused. ADR-0002 anticipates exactly this -
a real provider arriving behind an unchanged contract - so
``synthesize(text, language) -> AsyncIterator[SynthesizedAudioChunk]`` is implemented as
written and the protocol is untouched.

**The dependency is optional.** ``pitchbot`` imports, and every test that does not name
this module passes, with ``piper`` absent; this module itself also imports cleanly without
it, so callers can probe :data:`PIPER_AVAILABLE` instead of guarding an ``ImportError``.
Only loading a voice requires the package, and that failure is a
:class:`PermanentAdapterError` naming the extra. Install it with
``pip install "pitchbot[piper-tts]"``.

**Nothing is downloaded, ever.** A voice is addressed by an explicit filesystem path that
must already exist. Piper's own downloader is never invoked, so import, construction, and
synthesis are offline by construction. Verified by running load and synthesis with the
process's sockets disabled.

Licensing is the reason this module is shaped the way it is
=============================================================

``docs/BENCHMARKS.md`` registered Piper with the gate *"Distribution review + each voice
license required"*. That gate is load-bearing, and satisfying it produced two findings
that are encoded here in code rather than left in prose:

1. **The Piper runtime is GPL-3.0-or-later.** ``piper-tts`` 1.7.0 ships the full GPL-3
   text and bundles ``espeak-ng`` data, which is itself GPL-3. PitchBot therefore never
   vendors, re-distributes, or hard-depends on it; it is an extra the operator installs
   deliberately, imported at runtime through :mod:`importlib`. Shipping a combined
   artifact that includes Piper is a distribution question this repository has not
   answered - see ``docs/BENCHMARKS.md``.

2. **A voice's license is not the runtime's license, and most Piper voices are
   non-commercial.** Every reviewed ``hi_IN`` voice is either CC BY-NC-SA 4.0 or carries
   a license that could not be retrieved at all, and the widely-used ``en_US-amy-low`` is
   a finetune of RyanSpeech, which is CC BY-NC-SA 4.0. PitchBot is a *sales* assistant, so
   "non-commercial" is disqualifying for its stated purpose rather than a footnote.

Consequently :class:`PiperVoiceRegistry` is **deny-by-default**: a voice whose license
does not permit commercial use, or whose license could not be established, is refused
unless the caller explicitly passes ``allow_non_commercial=True``. That flag exists
because local evaluation and benchmarking are legitimate non-commercial uses, and it is
deliberately noisy to write down.

Behavioural notes that a caller has to know
===========================================

**Piper cannot detect a language mismatch.** Feeding Devanagari to an English voice does
not fail - it produces confident, fluent-sounding, wrong audio. There is therefore no
implicit fallback voice anywhere in this module: an unmapped language raises rather than
being served by "whatever was configured first".

**Exactly one chunk always carries ``is_final=True``.** Piper yields one chunk per
sentence and yields *nothing at all* for empty, whitespace-only, or punctuation-only text.
A consumer that waits for ``is_final`` to release a playback buffer would wait forever, so
this adapter emits a single empty final chunk in that case. The invariant is: every
completed ``synthesize`` call terminates with exactly one ``is_final=True`` chunk.

**The event loop is never blocked.** Piper's generator is lazy - constructing it is free
and each ``next()`` runs one sentence of ONNX inference - so this adapter advances it one
chunk at a time inside :func:`asyncio.to_thread`. Audio therefore starts flowing after the
first sentence instead of after the whole utterance, which is what makes barge-in
survivable. If a consumer abandons the stream mid-utterance the in-flight thread finishes
the sentence it already started; that is bounded by one sentence and is not cancellable
through Piper's API.

**Synthesis is concurrency-safe per voice, and that was measured rather than assumed.**
An earlier draft of this adapter serialized synthesis behind a per-voice lock on the
theory that an ONNX session's re-entrancy is undocumented. Four threads synthesising
different text through one loaded voice produce output byte-identical to the serial
baseline, so the lock was removed: it cost throughput, bought no correctness, and
introduced a way for an abandoned stream to block an unrelated one.

**Output is non-deterministic by default, and can be made deterministic.** Piper's VITS
models use stochastic duration prediction, so the same text synthesised twice is *not*
byte-identical. Pass :data:`DETERMINISTIC_SYNTHESIS` to get reproducible audio. This
matters beyond testing: ``docs/BENCHMARKS.md`` requires a corpus item's SHA-256 to cover
the exact file, which a non-deterministic generator cannot satisfy.

**Loading a voice stalls the loop; synthesis does not. Call :meth:`preload` at startup.**
Measured 2026-09-03: the first synthesis through a voice stalls the event loop for
**~2.1 s**, while synthesis through an already-loaded voice stalls it for **~20 ms**. Both
run on a worker thread, but constructing the ONNX session holds the GIL, so the thread
cannot yield. Left lazy, that cost lands on whichever caller happens to arrive first -
freezing the audio socket mid-conversation for two seconds. :meth:`preload` moves it to
startup, where it is merely slow instead of a live-call stall.
"""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import AsyncIterator, Iterable, Iterator, Mapping
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from types import ModuleType
from typing import Any, Final

from pitchbot.adapters.contracts import SynthesizedAudioChunk, TextToSpeechAdapter
from pitchbot.adapters.errors import PermanentAdapterError
from pitchbot.domain import LanguageCode


def _import_piper() -> ModuleType | None:
    """Import Piper if present, without a static dependency on it.

    ``importlib`` rather than a guarded ``import piper`` for the same reason as
    :mod:`pitchbot.adapters.webrtc_vad`: a static import makes ``mypy`` report a
    *different* diagnostic depending on whether the optional extra happens to be installed
    in the checking environment, and no single suppression is correct in both states.
    """

    try:
        return importlib.import_module("piper")
    except ImportError:
        return None


_MODULE: Final[ModuleType | None] = _import_piper()

PIPER_AVAILABLE: Final[bool] = _MODULE is not None
"""Whether the optional ``piper`` package is importable in this environment."""

INSTALL_HINT: Final[str] = 'pip install "pitchbot[piper-tts]"'

PROVIDER_ID: Final[str] = "piper"
ALGORITHM: Final[str] = "vits-onnx"
"""Piper synthesises with VITS models exported to ONNX and run on CPU."""

LICENSE: Final[str] = "GPL-3.0-or-later"
"""Reviewed 2026-09-03 from the COPYING file shipped in the installed distribution.

``piper-tts`` 1.7.0 ships the verbatim GNU GPL v3 text and bundles ``espeak-ng`` phoneme
data, which is GPL-3.0-or-later. This is a **copyleft** runtime, unlike the permissive
dependency reviewed in PR 30, which is why it is an operator-installed extra that
PitchBot never vendors or redistributes.
"""

MODEL_WEIGHTS: Final[str] = "per-voice, operator-supplied, never downloaded"
"""Each voice is a separate ``.onnx`` + ``.onnx.json`` pair the operator places on disk."""

PCM_MEDIA_TYPE: Final[str] = "audio/pcm"
"""Mono 16-bit little-endian PCM at the chunk's own ``sample_rate_hz``."""

_EXPECTED_SAMPLE_WIDTH_BYTES: Final[int] = 2
_EXPECTED_CHANNELS: Final[int] = 1

DEFAULT_MAX_TEXT_CHARS: Final[int] = 5_000
"""Bound on a single synthesis request. Synthesis cost is linear in text length."""

DEFAULT_MAX_CHUNKS: Final[int] = 512
"""Defensive bound on sentences per request; a normal agent turn is a handful."""

LICENSE_REVIEW_DATE: Final[str] = "2026-09-03"


@dataclass(frozen=True, slots=True)
class PiperSynthesisOptions:
    """Synthesis knobs, mirrored locally so this module imports without Piper.

    These are converted to Piper's own ``SynthesisConfig`` at call time. ``None`` means
    "use the voice's own default" for that field.
    """

    length_scale: float | None = None
    noise_scale: float | None = None
    noise_w_scale: float | None = None
    volume: float = 1.0
    normalize_audio: bool = True

    def __post_init__(self) -> None:
        if self.volume < 0.0:
            raise ValueError("volume must not be negative")
        for name in ("length_scale", "noise_scale", "noise_w_scale"):
            value = getattr(self, name)
            if value is not None and value < 0.0:
                raise ValueError(f"{name} must not be negative")


DETERMINISTIC_SYNTHESIS: Final[PiperSynthesisOptions] = PiperSynthesisOptions(
    noise_scale=0.0,
    noise_w_scale=0.0,
)
"""Reproducible synthesis: identical text yields byte-identical audio.

Verified 2026-09-03 by synthesising the same text three times through one voice and
comparing SHA-256. Without this the same call produces different bytes each time, because
VITS samples its duration predictor. Use it for tests, for regression baselines, and for
any generated corpus that must carry a stable ``audio_sha256``.
"""


@dataclass(frozen=True, slots=True)
class VoiceLicense:
    """The license of a voice's *weights*, which is not the license of the runtime.

    ``permits_commercial_use`` is the field that matters to this product. It is ``False``
    both for a license that forbids commercial use and for a license that could not be
    established, because "unknown" and "denied" must behave identically at a gate.
    """

    identifier: str
    permits_commercial_use: bool
    attribution_required: bool
    reference_url: str
    reviewed_on: str = LICENSE_REVIEW_DATE

    def __post_init__(self) -> None:
        if not self.identifier.strip():
            raise ValueError("identifier must not be empty")
        if not self.reference_url.strip():
            raise ValueError("reference_url must not be empty")


CC0: Final[VoiceLicense] = VoiceLicense(
    identifier="CC0-1.0",
    permits_commercial_use=True,
    attribution_required=False,
    reference_url="https://creativecommons.org/publicdomain/zero/1.0/",
)
PUBLIC_DOMAIN: Final[VoiceLicense] = VoiceLicense(
    identifier="public-domain",
    permits_commercial_use=True,
    attribution_required=False,
    reference_url="https://huggingface.co/rhasspy/piper-voices",
)
"""Voices whose upstream MODEL_CARD states, verbatim, ``* License: public domain``.

Recorded separately from :data:`CC0` because they are not the same claim. CC0 is a specific
waiver instrument with text to point at; "public domain" is an assertion by the publisher
about the training data. Both permit commercial use without attribution, so they behave
identically at the gate - but conflating them would lose which one was actually reviewed.
"""
CC_BY_4_0: Final[VoiceLicense] = VoiceLicense(
    identifier="CC-BY-4.0",
    permits_commercial_use=True,
    attribution_required=True,
    reference_url="https://creativecommons.org/licenses/by/4.0/",
)
CC_BY_NC_SA_4_0: Final[VoiceLicense] = VoiceLicense(
    identifier="CC-BY-NC-SA-4.0",
    permits_commercial_use=False,
    attribution_required=True,
    reference_url="https://creativecommons.org/licenses/by-nc-sa/4.0/",
)
CC_BY_SA_4_0: Final[VoiceLicense] = VoiceLicense(
    identifier="CC-BY-SA-4.0",
    permits_commercial_use=True,
    attribution_required=True,
    reference_url="https://creativecommons.org/licenses/by-sa/4.0/",
)
BLIZZARD_2013_RESTRICTED: Final[VoiceLicense] = VoiceLicense(
    identifier="Blizzard-2013-Lessac-restricted",
    permits_commercial_use=False,
    attribution_required=True,
    reference_url="https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/license.html",
)
IITM_INDICTTS_UNRESOLVED: Final[VoiceLicense] = VoiceLicense(
    identifier="IITM-IndicTTS-custom (unresolved)",
    permits_commercial_use=False,
    attribution_required=True,
    reference_url="https://www.iitm.ac.in/donlab/indictts/downloads/license.pdf",
)
"""The license document did not respond when fetched on the review date.

An unresolvable license is recorded as *not* permitting commercial use. That is the
conservative reading and the only one that is safe to encode: the alternative is to let a
voice through a gate on the strength of a document nobody has read.
"""


KNOWN_VOICE_LICENSES: Final[Mapping[str, VoiceLicense]] = {
    # --- English, commercially usable -------------------------------------------------
    # Female and `high` quality. The tier matters as much as the speaker: a `high` model is
    # larger and carries more prosody, which is most of what "robotic" describes. Both are
    # confirmed female by measurement (236 Hz and 202 Hz median F0) rather than by name.
    "en_US-ljspeech-high": PUBLIC_DOMAIN,
    "en_GB-cori-high": PUBLIC_DOMAIN,
    # Speaker unverified: 160 Hz median F0 falls between the adult male and female bands, so
    # the licence table does not claim a gender it cannot support.
    "en_US-kristin-medium": PUBLIC_DOMAIN,
    "en_US-joe-medium": CC0,
    "en_US-libritts_r-medium": CC_BY_4_0,
    "en_GB-alba-medium": CC_BY_4_0,
    "en_GB-southern_english_female-low": CC_BY_SA_4_0,
    # --- English, NOT commercially usable ---------------------------------------------
    "en_US-amy-low": CC_BY_NC_SA_4_0,
    "en_US-ryan-low": CC_BY_NC_SA_4_0,
    "en_US-hfc_female-medium": CC_BY_NC_SA_4_0,
    "en_US-lessac-medium": BLIZZARD_2013_RESTRICTED,
    # --- Hindi: every reviewed voice is non-commercial or unresolved -------------------
    "hi_IN-pratham-medium": CC_BY_NC_SA_4_0,
    "hi_IN-priyamvada-medium": CC_BY_NC_SA_4_0,
    "hi_IN-rohan-medium": IITM_INDICTTS_UNRESOLVED,
    # --- Telugu: the only Indic language Piper serves under a commercial license --------
    # Both are trained on ai4bharat/indicvoices_r, whose HF dataset card states
    # ``license: cc-by-4.0``. Verified against the upstream dataset, not only the voice
    # MODEL_CARD, because a voice card restating a license is not evidence of one.
    "te_IN-padmavathi-medium": CC_BY_4_0,
    "te_IN-venkatesh-medium": CC_BY_4_0,
    # Trained from the IITM IndicTTS corpus, the same unresolved license as hi_IN-rohan.
    "te_IN-maya-medium": IITM_INDICTTS_UNRESOLVED,
}
"""Voice-weight licenses reviewed on 2026-09-03 from each voice's upstream MODEL_CARD.

Adding a language is a **data** change - a row here plus a voice file - and never a code
change, which is the property the project's direction requires. The Indic rows carry the
finding: at review time Piper published exactly three ``hi_IN`` voices and **none of them
is cleared for commercial use**, while two of the three ``te_IN`` voices are CC-BY-4.0.
Telugu, the third language added, is therefore the *only* Indic language this project can
speak commercially today - the opposite of what the language order would suggest. Both
findings are recorded in ``docs/BENCHMARKS.md``.
"""


@dataclass(frozen=True, slots=True)
class PiperVoiceSpec:
    """One voice the operator has placed on disk and accepted the license of."""

    voice_id: str
    language: LanguageCode
    model_path: Path
    license: VoiceLicense

    def __post_init__(self) -> None:
        if not self.voice_id.strip():
            raise ValueError("voice_id must not be empty")

    @property
    def config_path(self) -> Path:
        """Piper's sidecar config, which it resolves as ``<model>.json``."""

        return self.model_path.with_suffix(self.model_path.suffix + ".json")


def voice_spec(voice_id: str, language: LanguageCode, model_path: Path | str) -> PiperVoiceSpec:
    """Build a spec for a voice whose license has already been reviewed here.

    Refuses an unknown ``voice_id`` rather than assuming a license, because assuming one
    is precisely the failure this module exists to prevent. For a voice outside
    :data:`KNOWN_VOICE_LICENSES`, construct :class:`PiperVoiceSpec` directly and state the
    license explicitly.
    """

    known = KNOWN_VOICE_LICENSES.get(voice_id)
    if known is None:
        raise PermanentAdapterError(
            f"voice {voice_id!r} has no reviewed license in KNOWN_VOICE_LICENSES; "
            "construct PiperVoiceSpec directly with an explicit VoiceLicense"
        )
    return PiperVoiceSpec(
        voice_id=voice_id,
        language=language,
        model_path=Path(model_path),
        license=known,
    )


def require_piper() -> ModuleType:
    """The imported package, or a permanent adapter error naming the extra."""

    if _MODULE is None:
        raise PermanentAdapterError(
            f"piper is not installed; install the optional extra with: {INSTALL_HINT}"
        )
    return _MODULE


def installed_distribution() -> tuple[str, str] | None:
    """The distribution providing ``piper``, and its exact version, if installed."""

    for distribution in ("piper-tts", "piper"):
        try:
            return distribution, metadata.version(distribution)
        except metadata.PackageNotFoundError:
            continue
    return None


@dataclass(frozen=True, slots=True)
class PiperProvenance:
    """Exact identity of what produced audio, as ADR-0004 requires it to be captured."""

    provider_id: str
    package: str
    package_version: str
    algorithm: str
    runtime_license: str
    voice_id: str
    voice_license: str
    voice_permits_commercial_use: bool
    model_weights: str
    sample_rate_hz: int


class PiperVoiceRegistry:
    """Language-to-voice mapping with a deny-by-default license gate.

    There is no default voice and no fallback. A language that was not mapped is an error,
    because the alternative - serving Hindi text through an English voice - produces
    confident wrong audio rather than a failure (see the module docstring).
    """

    def __init__(
        self,
        specs: Iterable[PiperVoiceSpec],
        *,
        allow_non_commercial: bool = False,
    ) -> None:
        resolved: dict[LanguageCode, PiperVoiceSpec] = {}
        for spec in specs:
            if spec.language in resolved:
                raise PermanentAdapterError(
                    f"language {spec.language.value!r} is mapped twice, to "
                    f"{resolved[spec.language].voice_id!r} and {spec.voice_id!r}; "
                    "an ambiguous mapping is refused rather than resolved by ordering"
                )
            resolved[spec.language] = spec
        if not resolved:
            raise PermanentAdapterError("a registry must map at least one language")
        self._specs = resolved
        self._allow_non_commercial = allow_non_commercial

    @property
    def allow_non_commercial(self) -> bool:
        return self._allow_non_commercial

    @property
    def languages(self) -> frozenset[LanguageCode]:
        return frozenset(self._specs)

    def resolve(self, language: LanguageCode) -> PiperVoiceSpec:
        spec = self._specs.get(language)
        if spec is None:
            mapped = ", ".join(sorted(item.value for item in self._specs))
            raise PermanentAdapterError(
                f"no Piper voice is mapped for language {language.value!r}; "
                f"mapped languages are: {mapped or '(none)'}. "
                "There is no fallback voice: a mismatched voice produces fluent wrong audio"
            )
        if not spec.license.permits_commercial_use and not self._allow_non_commercial:
            raise PermanentAdapterError(
                f"voice {spec.voice_id!r} is licensed {spec.license.identifier!r}, which "
                "does not permit commercial use, and this registry denies non-commercial "
                "voices. PitchBot is a sales assistant, so this is disqualifying for "
                "production. Pass allow_non_commercial=True only for local evaluation. "
                f"License: {spec.license.reference_url}"
            )
        return spec


def _advance(iterator: Iterator[Any]) -> Any | None:
    """Pull one Piper chunk, or ``None`` at exhaustion. Runs on a worker thread."""

    return next(iterator, None)


class PiperTextToSpeechAdapter(TextToSpeechAdapter):
    """Piper synthesis, streamed one sentence at a time, off the event loop."""

    def __init__(
        self,
        registry: PiperVoiceRegistry,
        *,
        synthesis: PiperSynthesisOptions | None = None,
        max_text_chars: int = DEFAULT_MAX_TEXT_CHARS,
        max_chunks: int = DEFAULT_MAX_CHUNKS,
    ) -> None:
        if max_text_chars < 1:
            raise ValueError("max_text_chars must be positive")
        if max_chunks < 1:
            raise ValueError("max_chunks must be positive")
        self._registry = registry
        self._synthesis = synthesis
        self._max_text_chars = max_text_chars
        self._max_chunks = max_chunks
        self._voices: dict[str, Any] = {}
        self._load_lock = asyncio.Lock()

    @property
    def registry(self) -> PiperVoiceRegistry:
        return self._registry

    @property
    def synthesis(self) -> PiperSynthesisOptions | None:
        return self._synthesis

    def _synthesis_config(self, module: ModuleType) -> Any | None:
        """Convert the local options to Piper's own config, or ``None`` for defaults."""

        if self._synthesis is None:
            return None
        return module.SynthesisConfig(
            length_scale=self._synthesis.length_scale,
            noise_scale=self._synthesis.noise_scale,
            noise_w_scale=self._synthesis.noise_w_scale,
            volume=self._synthesis.volume,
            normalize_audio=self._synthesis.normalize_audio,
        )

    async def preload(self, *languages: LanguageCode) -> None:
        """Load voices now so that no live call pays the load cost.

        Constructing a voice's ONNX session stalls the event loop for roughly two seconds
        even though it runs on a worker thread, because the work holds the GIL (measured
        2026-09-03). Synthesis through a loaded voice does not. Calling this during
        startup therefore converts an unpredictable mid-conversation freeze into a
        predictable startup cost.

        With no arguments every mapped language is loaded. The registry's license gate
        applies here exactly as it does during synthesis, so a denied voice fails at
        startup rather than on first use - which is the point.
        """

        targets = languages or tuple(sorted(self._registry.languages))
        for language in targets:
            await self._load_voice(self._registry.resolve(language))

    def is_loaded(self, spec: PiperVoiceSpec) -> bool:
        """Whether this voice's model is already resident."""

        return spec.voice_id in self._voices

    async def _load_voice(self, spec: PiperVoiceSpec) -> Any:
        """Load and cache a voice. Loading reads ~60 MB, so it happens on a thread."""

        cached = self._voices.get(spec.voice_id)
        if cached is not None:
            return cached
        async with self._load_lock:
            cached = self._voices.get(spec.voice_id)
            if cached is not None:
                return cached
            module = require_piper()
            if not spec.model_path.is_file():
                raise PermanentAdapterError(
                    f"voice {spec.voice_id!r} model file not found at {spec.model_path}; "
                    "voices are operator-supplied and are never downloaded"
                )
            if not spec.config_path.is_file():
                raise PermanentAdapterError(
                    f"voice {spec.voice_id!r} config not found at {spec.config_path}; "
                    "Piper needs the .onnx.json sidecar beside the model"
                )
            try:
                voice = await asyncio.to_thread(module.PiperVoice.load, str(spec.model_path))
            except Exception as error:
                raise PermanentAdapterError(
                    f"piper failed to load voice {spec.voice_id!r} from {spec.model_path}: {error}"
                ) from error
            self._voices[spec.voice_id] = voice
            return voice

    def _validate_framing(self, chunk: Any, spec: PiperVoiceSpec) -> None:
        """Refuse audio that is not mono 16-bit PCM.

        ``SynthesizedAudioChunk`` carries no channel or sample-width field, so a stereo or
        32-bit chunk would be silently reinterpreted as mono 16-bit downstream rather than
        rejected. Every published Piper voice is mono 16-bit; this guards the assumption
        instead of relying on it.
        """

        if int(chunk.sample_width) != _EXPECTED_SAMPLE_WIDTH_BYTES:
            raise PermanentAdapterError(
                f"voice {spec.voice_id!r} produced {chunk.sample_width}-byte samples; "
                f"only {_EXPECTED_SAMPLE_WIDTH_BYTES}-byte (16-bit) PCM is supported"
            )
        if int(chunk.sample_channels) != _EXPECTED_CHANNELS:
            raise PermanentAdapterError(
                f"voice {spec.voice_id!r} produced {chunk.sample_channels} channels; "
                "only mono is supported"
            )

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
        spec = self._registry.resolve(language)
        voice = await self._load_voice(spec)
        async for chunk in self._stream(voice, spec, text):
            yield chunk

    async def _stream(
        self,
        voice: Any,
        spec: PiperVoiceSpec,
        text: str,
    ) -> AsyncIterator[SynthesizedAudioChunk]:
        """One-chunk lookahead so ``is_final`` is truthful rather than guessed.

        Piper's iterator cannot say whether a chunk is the last one, so each chunk is held
        until the next pull resolves that question. The cost is one sentence of latency on
        the final chunk only.
        """

        iterator: Iterator[Any] = iter(
            voice.synthesize(text, syn_config=self._synthesis_config(require_piper()))
        )
        sequence = 0
        try:
            pending = await asyncio.to_thread(_advance, iterator)
        except Exception as error:
            raise PermanentAdapterError(
                f"piper failed to synthesise with voice {spec.voice_id!r}: {error}"
            ) from error

        while pending is not None:
            if sequence >= self._max_chunks:
                raise PermanentAdapterError(
                    f"synthesis produced more than max_chunks={self._max_chunks} chunks"
                )
            self._validate_framing(pending, spec)
            try:
                upcoming = await asyncio.to_thread(_advance, iterator)
            except Exception as error:
                raise PermanentAdapterError(
                    f"piper failed to synthesise with voice {spec.voice_id!r}: {error}"
                ) from error
            yield SynthesizedAudioChunk(
                data=bytes(pending.audio_int16_bytes),
                sequence=sequence,
                is_final=upcoming is None,
                media_type=PCM_MEDIA_TYPE,
                sample_rate_hz=int(pending.sample_rate),
            )
            sequence += 1
            pending = upcoming

        if sequence == 0:
            # Empty, whitespace-only, and punctuation-only text all yield zero Piper
            # chunks. Emitting nothing would leave a consumer waiting for is_final
            # forever, so the stream is terminated explicitly with no audio.
            yield SynthesizedAudioChunk(
                data=b"",
                sequence=0,
                is_final=True,
                media_type=PCM_MEDIA_TYPE,
                sample_rate_hz=self.voice_sample_rate_hz(spec),
            )

    def voice_sample_rate_hz(self, spec: PiperVoiceSpec) -> int:
        """The loaded voice's declared output rate; requires the voice to be loaded.

        Piper voices do not share a rate - the reviewed ones are 16 kHz and 22.05 kHz - so
        this is read from the model config rather than assumed.
        """

        voice = self._voices.get(spec.voice_id)
        if voice is None:
            raise PermanentAdapterError(
                f"voice {spec.voice_id!r} is not loaded; call synthesize first"
            )
        return int(voice.config.sample_rate)

    def provenance(self, spec: PiperVoiceSpec) -> PiperProvenance:
        distribution = installed_distribution()
        package, version = distribution if distribution is not None else (PROVIDER_ID, "unknown")
        return PiperProvenance(
            provider_id=PROVIDER_ID,
            package=package,
            package_version=version,
            algorithm=ALGORITHM,
            runtime_license=LICENSE,
            voice_id=spec.voice_id,
            voice_license=spec.license.identifier,
            voice_permits_commercial_use=spec.license.permits_commercial_use,
            model_weights=MODEL_WEIGHTS,
            sample_rate_hz=self.voice_sample_rate_hz(spec),
        )


__all__ = [
    "ALGORITHM",
    "BLIZZARD_2013_RESTRICTED",
    "CC0",
    "CC_BY_4_0",
    "CC_BY_NC_SA_4_0",
    "CC_BY_SA_4_0",
    "DEFAULT_MAX_CHUNKS",
    "DEFAULT_MAX_TEXT_CHARS",
    "DETERMINISTIC_SYNTHESIS",
    "IITM_INDICTTS_UNRESOLVED",
    "INSTALL_HINT",
    "KNOWN_VOICE_LICENSES",
    "LICENSE",
    "LICENSE_REVIEW_DATE",
    "MODEL_WEIGHTS",
    "PCM_MEDIA_TYPE",
    "PIPER_AVAILABLE",
    "PROVIDER_ID",
    "PUBLIC_DOMAIN",
    "PiperProvenance",
    "PiperSynthesisOptions",
    "PiperTextToSpeechAdapter",
    "PiperVoiceRegistry",
    "PiperVoiceSpec",
    "VoiceLicense",
    "installed_distribution",
    "require_piper",
    "voice_spec",
]
