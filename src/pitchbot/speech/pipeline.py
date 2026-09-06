from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from enum import StrEnum
from time import perf_counter
from typing import Protocol, runtime_checkable

from pitchbot.adapters import (
    AdapterError,
    AudioChunk,
    Clock,
    SpeechToTextAdapter,
    SystemClock,
    TranscriptChunk,
    VoiceActivity,
    VoiceActivityDetector,
)
from pitchbot.adapters.errors import UnsupportedLanguageError
from pitchbot.domain import LanguageCode
from pitchbot.speech.models import BargeIn, SpeechFrame, SpeechSegment, TurnTakingState
from pitchbot.speech.turn_taking import TurnTaking, TurnTakingConfig

logger = logging.getLogger(__name__)

MAX_UTTERANCE_BYTES = 2 * 1024 * 1024
MAX_TRANSCRIPT_CHARS = 2_000
MIN_TRANSCRIPT_CONFIDENCE = 0.3

DEFAULT_TRANSCRIBE_TIMEOUT_MS = 6_000.0
"""Wall-clock ceiling on transcribing one utterance. ``0`` disables it.

Chosen from measurement rather than taste. Healthy transcriptions on the shipped
`small`/int8 model cluster tightly and do **not** scale with audio length - 3.2 s of
English costs ~1.9 s, 16.1 s costs ~2.2 s, Hindi ~2.5 s - because Whisper pads every clip
to a 30 s window. The live endpointer caps an utterance at 20 s, so ~2.5 s is the worst
healthy case the socket can produce and 6 s clears it by better than 2x.

The pathology it exists for is an order of magnitude away, not a few hundred milliseconds:
a 3.2 s Hindi clip measured at 11,455 ms median and 28,656 ms at worst. Anything between
2.5 s and 11 s is unclaimed territory, which is what makes a single fixed number honest
here - there is nothing to tune it against.

Callers submitting more than 20 s of audio (the adapter permits 120 s) should raise it:
four 30 s windows cost roughly four times one.
"""

_PCM_SAMPLE_RATE_HZ = 16_000
_PCM_SAMPLE_WIDTH_BYTES = 2
# The frame lengths WebRTC's detector accepts. A byte count that maps onto one of these at
# the chunk's own sample rate is PCM of exactly that duration; anything else is encoded or
# proxy data whose length says nothing about how long it lasts.
_PCM_FRAME_DURATIONS_MS: frozenset[int] = frozenset((10, 20, 30))
"""The one audio shape this path carries: 16 kHz mono 16-bit PCM.

Only used to turn a duration into a byte count for the early-detection threshold. Every
transcriber in this repository refuses any other rate outright rather than resampling, so
assuming it here cannot silently mis-measure a stream that would otherwise have worked.
"""


class UtteranceOutcome(StrEnum):
    """Why an endpointed utterance did or did not become a buyer turn."""

    TRANSCRIBED = "transcribed"
    NO_SPEECH_RECOGNIZED = "no-speech-recognized"
    LOW_CONFIDENCE = "low-confidence"
    OVERSIZE = "oversize"
    TRANSCRIBER_UNAVAILABLE = "transcriber-unavailable"
    TRANSCRIPTION_TIMED_OUT = "transcription-timed-out"
    """The transcriber was still working long after any healthy utterance would be done.

    Not a hypothetical. Measured 2026-09-06 on `small`/int8, a **3.2 s** Hindi utterance -
    a supported language, in the shipped configuration - took a median of **11,455 ms**,
    and the same clip has been observed at **28,656 ms**. It was not a runaway output
    (one segment, 40 characters, compression ratio 1.24); the decoder simply searched.

    Nothing bounded that. `max_audio_seconds` bounds how much audio may be *submitted*,
    and cost is nearly flat in audio length - 16.1 s of speech costs 2,245 ms - so audio
    length was never the thing that needed bounding. The consequence is worse than a slow
    reply: the socket's receive loop is blocked inside `push` for the whole time, so the
    buyer cannot interrupt either. Twenty-eight seconds is 140x the ~200 ms gap a human
    leaves between turns, and the agent is deaf for all of it.

    What the deadline recovers is the **turn**, not the CPU: `asyncio.to_thread` cannot be
    interrupted, so the worker keeps decoding until it finishes on its own. That is the
    honest reason for a generous default rather than an aggressive one - every timeout
    leaves a thread competing with whatever runs next.
    """
    LANGUAGE_UNSUPPORTED = "language-unsupported"
    """The buyer is speaking a language this transcriber demonstrably cannot transcribe.

    Measured 2026-09-05, Telugu through Whisper ``small`` returns nonsense in every decoder
    configuration tried - the reference "మేము రిటైల్ షాప్ నడుపుతాము" came back as
    "మరింరIsn claiming the jammals from the charity sponsor" - and takes **37,533 ms** to do
    it, five and a half times real time. Understanding scores at or below guessing in the
    same language and is already gated out.

    So the honest outcome is the one this project already uses when no transcriber is
    configured at all: say so, immediately, rather than spend thirty-seven seconds of a
    shared CPU producing text nobody should act on. Reported only when the language was
    *confidently* identified; an uncertain guess still transcribes.
    """


@dataclass(frozen=True, slots=True)
class UtteranceResult:
    """A closed utterance and, when it could be understood, what was said."""

    segment: SpeechSegment
    outcome: UtteranceOutcome
    text: str | None
    language: LanguageCode | None
    confidence: float | None
    transcribe_ms: float
    dropped_frames: int
    detect_language_ms: float | None = None
    """How long the early language detection took, or ``None`` when none was used.

    ``None`` covers three different situations that all mean "no hint was applied": early
    detection is switched off, the transcriber does not support it, or it was still running
    when the buyer stopped and was abandoned rather than waited for. Only the last of those
    is interesting, and it is the one the operator cannot otherwise see - a detection that
    never lands leaves transcription paying the auto-detect cost this feature exists to
    remove, with nothing in the logs to say so.
    """

    @property
    def is_turn(self) -> bool:
        return self.outcome is UtteranceOutcome.TRANSCRIBED and bool(self.text)


@dataclass(frozen=True, slots=True)
class FrameResult:
    """What one received audio frame produced."""

    barge_in: BargeIn | None = None
    utterance: UtteranceResult | None = None


@runtime_checkable
class RetunableTranscriber(Protocol):
    """A transcriber whose expected language can be changed without rebuilding it."""

    def set_language(self, language: LanguageCode | None) -> None: ...


@runtime_checkable
class EarlyDetectingTranscriber(Protocol):
    """A transcriber that can identify a language before the utterance has finished.

    Structural, like :class:`RetunableTranscriber`, so a transcriber without this stays a
    perfectly valid transcriber and simply never gets asked. ``runtime_checkable`` verifies
    only that the method exists, which is the same guarantee the sibling protocol relies on.

    The hint is deliberately typed as an opaque ``object``. The pipeline's whole job here is
    to carry a token from the detection call to the transcription call without inspecting
    it; only the transcriber that minted it knows what a language hint means or when it is
    safe to act on one. Typing it concretely would drag the transcriber's internals into the
    turn machine for no benefit.
    """

    async def detect_prefix_language(self, payload: bytes) -> object | None: ...

    def transcribe(
        self,
        audio: AsyncIterator[AudioChunk],
        *,
        language_hint: object | None = None,
    ) -> AsyncIterator[TranscriptChunk]: ...


class SpeechTurnPipeline:
    """Turns a stream of audio frames into endpointed, transcribed buyer utterances.

    Audio is held only for the utterance currently being spoken, is capped by
    ``max_utterance_bytes``, and is released as soon as transcription returns or the cap
    is hit. Nothing is written to disk, journaled, or echoed back to the browser, so the
    server's standing ``audio_retained: false`` promise continues to hold.

    The pipeline never invents buyer speech. If the transcriber returns nothing, returns
    text below ``min_confidence``, fails, or the utterance exceeds its cap, the utterance
    is reported with the reason and no turn is created.

    ``transcriber`` may be ``None`` while no speech model has been benchmarked and selected.
    Endpointing and barge-in still work, every utterance is reported as
    ``transcriber-unavailable``, and no audio is buffered at all, because buffering audio
    that can never be transcribed would be pure cost and pure retention risk.
    """

    def __init__(
        self,
        *,
        detector: VoiceActivityDetector,
        transcriber: SpeechToTextAdapter | None,
        language: LanguageCode,
        config: TurnTakingConfig | None = None,
        clock: Clock | None = None,
        frame_duration_ms: int = 250,
        max_utterance_bytes: int = MAX_UTTERANCE_BYTES,
        min_confidence: float = MIN_TRANSCRIPT_CONFIDENCE,
        early_detection_seconds: float = 0.0,
        transcribe_timeout_ms: float = DEFAULT_TRANSCRIBE_TIMEOUT_MS,
        on_thinking: Callable[[], None] | None = None,
    ) -> None:
        if not 1 <= max_utterance_bytes <= MAX_UTTERANCE_BYTES:
            raise ValueError(f"max_utterance_bytes must be between 1 and {MAX_UTTERANCE_BYTES}")
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be between 0 and 1")
        if early_detection_seconds < 0:
            raise ValueError("early_detection_seconds must not be negative")
        if transcribe_timeout_ms < 0:
            raise ValueError("transcribe_timeout_ms must not be negative")
        self._detector = detector
        self._transcriber = transcriber
        self._language = language
        self._turn_taking = TurnTaking(config)
        self._clock = clock or SystemClock()
        self._frame_duration_ms = frame_duration_ms
        self._max_utterance_bytes = max_utterance_bytes
        self._min_confidence = min_confidence
        self._transcribe_timeout_ms = transcribe_timeout_ms
        self._on_thinking = on_thinking
        """Called when an utterance closes and transcription is about to start.

        This exists because of where the time actually goes. Measured, the wait between a
        buyer finishing a sentence and the reply being audible is ~4.5 s, of which
        transcription is 3,982 ms of 4,507 ms in English. Anything that wants to cover
        that silence has to be told at this moment - by the time the transcript exists,
        almost the whole gap has already been spent in silence.
        """
        self._buffer: list[AudioChunk] = []
        self._buffered_bytes = 0
        self._oversize = False
        self._dropped_frames = 0
        self._early_detection_bytes = int(
            early_detection_seconds * _PCM_SAMPLE_RATE_HZ * _PCM_SAMPLE_WIDTH_BYTES
        )
        """Speech to accumulate before asking the transcriber to identify the language.

        Zero disables the whole mechanism, which is the behaviour that shipped before it
        existed: one auto-detecting transcription after the buyer stops.
        """
        self._detection_task: asyncio.Task[tuple[object | None, float]] | None = None
        self._detection_started = False

    @property
    def turn_taking(self) -> TurnTaking:
        return self._turn_taking

    @property
    def language(self) -> LanguageCode:
        """The language this pipeline expects the buyer to be speaking."""

        return self._language

    def set_language(self, language: LanguageCode) -> None:
        """Follow a conversation that has changed language.

        Until this existed the ``language`` constructor argument was stored and never
        read - the pipeline accepted a language, promised by its own signature to
        transcribe in it, and passed it nowhere. The transcriber's language came only
        from its own constructor, so a caller who re-pointed the pipeline changed
        nothing at all and had no way to find that out.

        Forwarded to the transcriber only when the transcriber can accept it. A
        transcriber built around a fixed language is a legitimate implementation, and one
        that has none - the ``None`` case this pipeline is explicitly built to tolerate -
        has nothing to re-point.
        """

        self._language = language
        transcriber = self._transcriber
        if isinstance(transcriber, RetunableTranscriber):
            transcriber.set_language(language)

    @property
    def can_transcribe(self) -> bool:
        return self._transcriber is not None

    def agent_started_speaking(self) -> None:
        self._turn_taking.agent_started_speaking()
        self._release_audio()

    def agent_stopped_speaking(self) -> None:
        # Only a floor that was actually held abandons buffered audio: anything captured
        # during AGENT_SPEAKING belongs to an interruption run that yielding the floor
        # discards. Outside that state the machine treats this as a no-op, so releasing
        # would amputate the buyer's open utterance and transcribe half a sentence.
        yielding_floor = self._turn_taking.state is TurnTakingState.AGENT_SPEAKING
        self._turn_taking.agent_stopped_speaking()
        if yielding_floor:
            self._release_audio()

    async def push(self, chunk: AudioChunk) -> FrameResult:
        """Classify one frame and, when the buyer has finished, transcribe the utterance."""

        activity: VoiceActivity | None
        try:
            activity = self._detector.detect(chunk)
        except (AdapterError, RuntimeError, ValueError):
            # A detector failure must not end the call, and the frame must still reach the
            # machine. Dropping it would stall trailing silence, so an already-open
            # utterance would never endpoint and the buyer would hold the floor for the
            # rest of the call. Counting it as silence closes on the normal silence path.
            logger.warning("Voice activity detection failed for a frame", exc_info=True)
            self._dropped_frames += 1
            activity = None

        frame = SpeechFrame(
            sequence=chunk.sequence,
            byte_count=len(chunk.data),
            duration_ms=self._duration_of(chunk),
            is_speech=activity is not None and activity.is_speech,
            captured_at=chunk.captured_at,
        )
        decision = self._turn_taking.observe(frame)
        if decision.discarded:
            # The machine abandoned an utterance without producing a segment, so its
            # audio must be dropped now rather than prepended to whatever is said next.
            self._release_audio()
        if decision.capture:
            # Only an open or provisional utterance is buffered. Silence before the buyer
            # starts is never sent to the transcriber, which keeps both the work and the
            # latency proportional to what was actually said.
            self._buffer_audio(chunk)
        if decision.barge_in is not None:
            return FrameResult(barge_in=decision.barge_in)
        if decision.segment is None:
            return FrameResult()
        return FrameResult(utterance=await self._transcribe(decision.segment))

    async def stop(self) -> UtteranceResult | None:
        segment = self._turn_taking.stop()
        if segment is None:
            self._release_audio()
            return None
        return await self._transcribe(segment)

    def _duration_of(self, chunk: AudioChunk) -> int:
        """How much time this frame actually represents.

        Every threshold the endpointer owns - `end_silence_ms`, `max_utterance_ms`,
        `min_speech_ms`, `barge_in_speech_ms` - is a sum of these. Getting it wrong does not
        raise: it silently rescales the buyer's clock.

        It was wrong. `frame_duration_ms` defaults to 250 because the browser client calls
        `MediaRecorder.start(250)`, and `SimulatorService.create_speech_pipeline` never
        overrode it, so the socket path counted a 30 ms microphone frame as 250 ms - **8.3x**.
        `max_utterance_ms` then fired after 80 frames, which is 2.4 s of real speech rather
        than 20 s, and a buyer speaking one continuous sentence was cut into fragments, each
        answered as though it were a separate remark. Measured live: 8.4 s of continuous
        English became **four** utterances and four replies.

        Mono 16-bit PCM carries its own duration, so it is derived rather than assumed. The
        result is only trusted when it is a frame length WebRTC's detector accepts (10, 20 or
        30 ms), which is exactly the set for which "these bytes are PCM at this rate" is a
        safe reading. Anything else - an encoded frame from `MediaRecorder`, a length proxy
        from a benchmark source - keeps the configured value, because its byte count says
        nothing about its duration.
        """

        measured = len(chunk.data) / _PCM_SAMPLE_WIDTH_BYTES / chunk.sample_rate_hz * 1_000
        if measured.is_integer() and int(measured) in _PCM_FRAME_DURATIONS_MS:
            return int(measured)
        return self._frame_duration_ms

    def _buffer_audio(self, chunk: AudioChunk) -> None:
        if self._oversize or self._transcriber is None:
            return
        if self._buffered_bytes + len(chunk.data) > self._max_utterance_bytes:
            # Fail closed: drop the whole utterance rather than transcribe a truncated
            # one and attribute a half sentence to the buyer.
            self._release_audio()
            self._oversize = True
            return
        self._buffer.append(chunk)
        self._buffered_bytes += len(chunk.data)
        self._maybe_start_early_detection()

    def _maybe_start_early_detection(self) -> None:
        """Ask the transcriber to identify the language while the buyer is still speaking.

        Whisper pads every clip to a 30 s window, so identifying the language costs the same
        ~1.6 s whether it is handed 2 s of audio or 8 s. That cost cannot be reduced - it can
        only be moved off the critical path, into the time the buyer is still talking. When
        it lands before the endpoint, transcription skips its own detection pass, which is
        1,622 ms of the 3,982 ms an English utterance currently spends being transcribed.

        Fires at most once per utterance. Firing repeatedly as more audio arrived would
        spend that CPU over and over for an answer that does not change.
        """

        if self._early_detection_bytes <= 0 or self._detection_started:
            return
        if self._buffered_bytes < self._early_detection_bytes:
            return
        transcriber = self._transcriber
        if not isinstance(transcriber, EarlyDetectingTranscriber):
            return
        payload = b"".join(chunk.data for chunk in self._buffer)
        self._detection_started = True
        self._detection_task = asyncio.create_task(self._timed_detection(transcriber, payload))

    @staticmethod
    async def _timed_detection(
        transcriber: EarlyDetectingTranscriber, payload: bytes
    ) -> tuple[object | None, float]:
        """Detect, and carry back how long it took.

        Timed here rather than where the result is consumed because the two are not the
        same interval: this runs while the buyer is still speaking, and is read some time
        after it finished.
        """

        started = perf_counter()
        hint = await transcriber.detect_prefix_language(payload)
        return hint, (perf_counter() - started) * 1000

    def _take_detection_hint(self) -> tuple[object | None, float | None]:
        """Consume a finished early detection, or give up on an unfinished one.

        Deliberately does not wait. The utterance is already endpointed, so waiting would
        add silence to the very gap this exists to shrink, and an unfinished detection is
        not a failure: transcription simply proceeds exactly as it did before this existed.
        """

        task = self._detection_task
        self._detection_task = None
        if task is None:
            return None, None
        if not task.done():
            task.cancel()
            return None, None
        if task.cancelled():
            return None, None
        error = task.exception()
        if error is not None:
            logger.warning("Early language detection failed", exc_info=error)
            return None, None
        hint, elapsed = task.result()
        return hint, elapsed

    def _cancel_detection(self) -> None:
        """Abandon an in-flight detection whose utterance no longer exists.

        Cancellation reaches the coroutine, not the worker thread underneath it: the
        transcriber runs inference through ``asyncio.to_thread`` and Whisper offers no
        mid-inference stop, so the thread finishes its pass regardless and its result is
        discarded. That is bounded waste - one detection pass - and the alternative, holding
        the utterance open until the thread returns, would delay the next turn.
        """

        task = self._detection_task
        self._detection_task = None
        self._detection_started = False
        if task is None:
            return
        if task.done():
            if not task.cancelled():
                # Retrieve it so asyncio does not log "exception was never retrieved".
                task.exception()
            return
        task.cancel()

    def _release_audio(self) -> None:
        """Drop every buffered byte and clear the oversize latch with it.

        The latch is scoped to one utterance. Leaving it set across a release would let
        one abandoned utterance permanently fail every later one.
        """

        self._buffer = []
        self._buffered_bytes = 0
        self._oversize = False
        self._cancel_detection()

    async def _transcribe(self, segment: SpeechSegment) -> UtteranceResult:
        dropped = self._dropped_frames
        self._dropped_frames = 0
        if self._transcriber is None:
            self._release_audio()
            return self._result(segment, UtteranceOutcome.TRANSCRIBER_UNAVAILABLE, 0.0, dropped)
        if self._oversize:
            self._release_audio()
            return self._result(segment, UtteranceOutcome.OVERSIZE, 0.0, dropped)

        chunks = self._buffer
        # Before _release_audio, which cancels any in-flight detection along with the
        # buffer it was computed from.
        hint, detect_ms = self._take_detection_hint()
        self._release_audio()
        if self._on_thinking is not None:
            # Before the await, so the listener starts counting from the moment the buyer
            # actually stopped rather than from whenever this coroutine is next scheduled.
            # Failures are contained: a backchannel is a courtesy, and losing the turn
            # because the courtesy raised would be a strictly worse conversation.
            try:
                self._on_thinking()
            except Exception:  # noqa: BLE001 - a filler must never cost a turn
                logger.warning("Backchannel notification failed", exc_info=True)
        started = perf_counter()
        try:
            best = await self._transcribe_within_deadline(chunks, hint)
        except TimeoutError:
            elapsed = (perf_counter() - started) * 1000
            logger.warning(
                "Transcription exceeded its deadline and the turn was released",
                extra={"transcribe_timeout_ms": self._transcribe_timeout_ms},
            )
            return self._result(
                segment,
                UtteranceOutcome.TRANSCRIPTION_TIMED_OUT,
                elapsed,
                dropped,
                detect_ms,
            )
        except UnsupportedLanguageError as decline:
            # Ordered before the generic handler on purpose. Nothing failed here: the
            # transcriber identified the language, recognised it as one it cannot serve, and
            # declined. Reporting that as `transcriber-unavailable` would be a lie about a
            # working component, and would hide the one fact the operator needs - which
            # language was heard.
            elapsed = (perf_counter() - started) * 1000
            logger.info(
                "Declined an utterance in a language this transcriber cannot serve",
                extra={"declined_language": decline.language},
            )
            return self._result(
                segment,
                UtteranceOutcome.LANGUAGE_UNSUPPORTED,
                elapsed,
                dropped,
                detect_ms,
            )
        except (AdapterError, RuntimeError, ValueError):
            # Transcription is best effort. A failure loses one utterance; it must never
            # drop the call or fabricate what the buyer said.
            logger.warning("Transcription failed for an utterance", exc_info=True)
            elapsed = (perf_counter() - started) * 1000
            return self._result(
                segment,
                UtteranceOutcome.TRANSCRIBER_UNAVAILABLE,
                elapsed,
                dropped,
                detect_ms,
            )
        elapsed = (perf_counter() - started) * 1000
        if best is None or not best.text.strip():
            return self._result(
                segment, UtteranceOutcome.NO_SPEECH_RECOGNIZED, elapsed, dropped, detect_ms
            )
        if best.confidence < self._min_confidence:
            return self._result(
                segment, UtteranceOutcome.LOW_CONFIDENCE, elapsed, dropped, detect_ms
            )
        return UtteranceResult(
            segment=segment,
            outcome=UtteranceOutcome.TRANSCRIBED,
            text=best.text.strip()[:MAX_TRANSCRIPT_CHARS],
            language=best.language,
            confidence=best.confidence,
            transcribe_ms=elapsed,
            dropped_frames=dropped,
            detect_language_ms=detect_ms,
        )

    async def _transcribe_within_deadline(
        self,
        chunks: list[AudioChunk],
        hint: object | None,
    ) -> TranscriptChunk | None:
        """Transcribe, but give the turn back if the decoder will not finish.

        The deadline is a wall-clock bound on one utterance, not a budget scaled to its
        length, because measured cost barely moves with length: 3.2 s of audio and 16.1 s
        of audio both cost about two seconds when the decoder behaves. What varies by an
        order of magnitude is whether it behaves.
        """

        if self._transcribe_timeout_ms <= 0:
            return await self._best_transcript(chunks, hint)
        return await asyncio.wait_for(
            self._best_transcript(chunks, hint),
            self._transcribe_timeout_ms / 1000,
        )

    async def _best_transcript(
        self,
        chunks: list[AudioChunk],
        hint: object | None = None,
    ) -> TranscriptChunk | None:
        """Prefer the last final transcript, falling back to the last partial."""

        if self._transcriber is None:  # pragma: no cover - guarded by _transcribe
            return None
        transcriber = self._transcriber
        stream = _as_stream(chunks)
        if hint is not None and isinstance(transcriber, EarlyDetectingTranscriber):
            transcripts = transcriber.transcribe(stream, language_hint=hint)
        else:
            transcripts = transcriber.transcribe(stream)
        final: TranscriptChunk | None = None
        partial: TranscriptChunk | None = None
        async for transcript in transcripts:
            if transcript.is_final:
                final = transcript
            else:
                partial = transcript
        return final or partial

    def _result(
        self,
        segment: SpeechSegment,
        outcome: UtteranceOutcome,
        transcribe_ms: float,
        dropped_frames: int,
        detect_language_ms: float | None = None,
    ) -> UtteranceResult:
        return UtteranceResult(
            segment=segment,
            outcome=outcome,
            text=None,
            language=None,
            confidence=None,
            transcribe_ms=transcribe_ms,
            dropped_frames=dropped_frames,
            detect_language_ms=detect_language_ms,
        )


async def _as_stream(chunks: list[AudioChunk]) -> AsyncIterator[AudioChunk]:
    for chunk in chunks:
        yield chunk


__all__ = [
    "EarlyDetectingTranscriber",
    "FrameResult",
    "RetunableTranscriber",
    "SpeechTurnPipeline",
    "UtteranceOutcome",
    "UtteranceResult",
]
