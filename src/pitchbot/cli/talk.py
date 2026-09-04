"""Talk to PitchBot from a terminal, and watch it think.

Everything this project measures - transcription, planning, synthesis - has until now been
reachable only through the HTTP API, the WebSocket, or a test. That is enough to know the
parts work and not enough to know whether talking to it is any good, which is a different
question and the one that decides whether the product is worth building.

So this runs the **real** conversation engine, not a demo path: the same
:class:`ConversationEngine`, the same rule extractors, the same reply planner, the same
optional model and the same Piper voices the server uses. A reply seen here is a reply the
server would send.

It also shows its working. After each turn it prints which slots are now known, which one
it chose to ask for next, the phase and lead temperature, and how long the turn took -
because "why did it say that" is the question a person actually has, and answering it from
logs afterwards is not the same as seeing it.

Nothing here needs an optional extra. Text in, text out, no model and no audio is the
default and is fully functional; ``--speak`` and ``--understand`` add the parts that cost
a download.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from pitchbot.adapters.contracts import AudioChunk
from pitchbot.conversation import ConversationEngine
from pitchbot.conversation.models import ConversationResult
from pitchbot.conversation.planning import Slot, supported_languages
from pitchbot.domain import LanguageCode
from pitchbot.domain.models import RequirementFact

BANNER = "PitchBot - local sales conversation. Ctrl-C or an empty line to stop."
VOICE_BANNER = "PitchBot - speak when it says listening. Ctrl-C to stop."

LANGUAGE_NAMES = {
    LanguageCode.ENGLISH: "English",
    LanguageCode.HINDI: "Hindi",
    LanguageCode.TELUGU: "Telugu",
    LanguageCode.MIXED: "Hinglish",
}

OPENERS = {
    LanguageCode.ENGLISH: "Hello, I am PitchBot. Tell me about your business.",
    LanguageCode.HINDI: "नमस्ते, मैं पिचबॉट हूँ। अपने व्यवसाय के बारे में बताइए।",
    LanguageCode.TELUGU: "నమస్కారం, నేను పిచ్‌బాట్. మీ వ్యాపారం గురించి చెప్పండి.",
    LanguageCode.MIXED: "Namaste, main PitchBot hoon. Apne business ke baare mein bataiye.",
}

VOICE_PREFIXES = {
    LanguageCode.ENGLISH: "en_",
    LanguageCode.HINDI: "hi_",
    LanguageCode.TELUGU: "te_",
}


def known_slots(facts: Iterable[RequirementFact]) -> list[str]:
    """Which planner slots the conversation has filled, in the order it asks for them."""

    keys = {fact.key for fact in facts}
    return [slot.value for slot in Slot if slot.value in keys]


def render_turn(
    result: ConversationResult,
    known: Sequence[str],
    elapsed_ms: float,
    *,
    verbose: bool,
) -> str:
    """One turn, formatted so the reasoning is visible without reading a log.

    ``known`` is passed in from the session snapshot rather than read off ``result``:
    ``ConversationResult.facts`` carries only what *this* turn produced, so displaying it
    made a conversation look like it kept forgetting - which is what this was written to
    disprove, and would have shipped as a screenshot of a bug that does not exist.
    """

    lines = [f"  bot  \u203a {result.reply}"]
    if not verbose:
        return "\n".join(lines)

    missing = [slot.value for slot in Slot if slot.value not in known]
    lines.append(
        f"       \u251c language {result.language.value}   phase {result.phase.value}"
        f"   lead {result.classification.temperature.value}"
    )
    lines.append(f"       \u251c knows    {', '.join(known) if known else '(nothing yet)'}")
    lines.append(f"       \u251c missing  {', '.join(missing) if missing else '(all filled)'}")
    if result.safety_signals:
        signals = ", ".join(signal.value for signal in result.safety_signals)
        lines.append(f"       \u251c SAFETY   {signals}")
    if result.repeated_turn:
        lines.append("       \u251c repeated turn - acknowledgement suppressed")
    lines.append(f"       \u2514 turn {result.turn_count} in {elapsed_ms:.0f} ms")
    return "\n".join(lines)


def play_wav(path: Path) -> str | None:
    """Play a WAV file with a player the operating system already has.

    Shelling out rather than adding an audio dependency is deliberate: this exists so a
    person can *hear* the product, and making them install a sound library first would
    defeat the point.

    The Windows branch tests ``sys.platform`` rather than ``platform.system()`` because
    only the former is narrowed by type checkers - on Linux ``winsound`` is a stub with no
    attributes, so a runtime-only check type-checks on Windows and fails in CI.
    """

    if sys.platform == "win32":
        import winsound

        winsound.PlaySound(str(path), winsound.SND_FILENAME)
        return None
    player = next((name for name in ("afplay", "paplay", "aplay") if shutil.which(name)), None)
    if player is None:
        return "no audio player found (tried afplay, paplay, aplay)"
    completed = subprocess.run([player, str(path)], capture_output=True, check=False)  # noqa: S603
    return None if completed.returncode == 0 else f"{player} exited {completed.returncode}"


class Speaker:
    """Synthesises a reply with Piper and plays it on the default audio device."""

    def __init__(self, adapter: Any, language: LanguageCode) -> None:
        self._adapter = adapter
        self._language = language

    @property
    def language(self) -> LanguageCode:
        return self._language

    async def say(self, text: str) -> str | None:
        """Speak ``text``. Returns a human-readable problem, or ``None`` on success."""

        chunks: list[bytes] = []
        rate = 22050
        async for chunk in self._adapter.synthesize(text, self._language):
            chunks.append(chunk.data)
            rate = chunk.sample_rate_hz
        if not chunks:
            return "no audio produced"

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            path = Path(handle.name)
        try:
            with wave.open(str(path), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(rate)
                out.writeframes(b"".join(chunks))
            return await asyncio.to_thread(play_wav, path)
        finally:
            path.unlink(missing_ok=True)


class Voices:
    """Piper speakers for whichever languages a conversation turns out to need.

    Built lazily and kept, because a buyer who switches to Hindi and back to English
    should pay the voice load once per language rather than once per switch. Loading a
    Piper voice was measured at about 2.1 s and holds the interpreter while it happens,
    so repeating it mid-call is a silence the buyer hears.

    A language with no reviewed voice is cached as a failure too. Re-attempting a load
    that has already failed would stall every switch back to that language for the rest
    of the call, and would reprint the same complaint each time.
    """

    def __init__(self, voices_dir: Path) -> None:
        self._voices_dir = voices_dir
        self._cache: dict[LanguageCode, tuple[Speaker | None, str]] = {}

    def speaker(self, language: LanguageCode) -> tuple[Speaker | None, str]:
        if language not in self._cache:
            self._cache[language] = build_speaker(language, self._voices_dir)
        return self._cache[language]


def build_speaker(language: LanguageCode, voices_dir: Path) -> tuple[Speaker | None, str]:
    """A speaker for this language, or the reason there is not one."""

    from pitchbot.adapters.piper_tts import (
        INSTALL_HINT,
        KNOWN_VOICE_LICENSES,
        PIPER_AVAILABLE,
        PiperTextToSpeechAdapter,
        PiperVoiceRegistry,
        voice_spec,
    )

    if not PIPER_AVAILABLE:
        return None, f"not installed. Install it with: {INSTALL_HINT}"
    if not voices_dir.exists():
        return None, f"no voices directory at {voices_dir}"
    prefix = VOICE_PREFIXES.get(language)
    if prefix is None:
        return None, f"no voice mapping for language {language.value!r}"

    candidates = sorted(
        path for path in voices_dir.glob(f"{prefix}*.onnx") if path.stem in KNOWN_VOICE_LICENSES
    )
    if not candidates:
        return None, f"no reviewed {language.value} voice in {voices_dir}"

    # A non-commercial voice is fine for someone trying the product on their own machine,
    # and refusing to speak would teach the wrong lesson here. The license is still
    # recorded, and it is still printed, so nobody can adopt one unknowingly.
    spec = voice_spec(candidates[0].stem, language, candidates[0])
    adapter = PiperTextToSpeechAdapter(PiperVoiceRegistry([spec], allow_non_commercial=True))
    note = f"{spec.voice_id} ({spec.license.identifier})"
    if not spec.license.permits_commercial_use:
        note += " - NOT licensed for commercial use"
    return Speaker(adapter, language), note


async def build_understanding(model_dir: Path, model_id: str | None) -> tuple[Any | None, str]:
    """The optional local model, or the reason there is not one.

    ``model_id`` is required and is not inferred from the directory name. The licence gate
    checks the **upstream** model id, because a quantised re-upload does not relicense what
    it converts; deriving an id from whatever the folder happens to be called would let a
    non-commercial model through a gate built to stop exactly that.
    """

    from pitchbot.adapters.onnx_genai_model import (
        INSTALL_HINT,
        KNOWN_MODEL_LICENSES,
        ONNX_GENAI_AVAILABLE,
        OnnxGenAiModelAdapter,
    )
    from pitchbot.conversation.model_understanding import ModelTurnUnderstanding

    if not ONNX_GENAI_AVAILABLE:
        return None, f"not installed. Install it with: {INSTALL_HINT}"
    if not model_id:
        known = ", ".join(sorted(KNOWN_MODEL_LICENSES))
        return None, f"--model-id is required. Reviewed ids: {known}"
    found = sorted(model_dir.rglob("genai_config.json"))
    if not found:
        return None, f"no genai_config.json under {model_dir}"

    adapter = OnnxGenAiModelAdapter(found[0].parent, model_id, allow_non_commercial=True)
    await adapter.preload()
    licence = KNOWN_MODEL_LICENSES[model_id]
    note = f"{model_id} ({licence.identifier})"
    if not licence.permits_commercial_use:
        note += " - NOT licensed for commercial use"
    return ModelTurnUnderstanding(adapter), note


@dataclass(frozen=True, slots=True)
class HeardTurn:
    """One finished buyer utterance, and what the transcriber made of its language.

    The language travels with the text because the conversation needs it to decide whether
    the buyer has switched. It was previously computed by the pipeline and dropped here,
    which left the transcriber's own evidence unused on the one path where speech is the
    only input there is.
    """

    text: str
    language: LanguageCode | None


class Listener:
    """Turns live microphone audio into finished buyer turns.

    The pipeline is the same :class:`~pitchbot.speech.pipeline.SpeechTurnPipeline` the
    server runs, so what is heard here is what would be heard on a call. Only the source
    differs, and that is the point: everything downstream of the microphone was already
    tested and had simply never been given a live voice.

    Turn-taking is **half duplex**. There is no acoustic echo cancellation, so a microphone
    left open while the agent speaks hears the agent and endpoints on it. Capture is paused
    for the duration of each reply instead. The cost is that a buyer cannot interrupt, which
    the pipeline is otherwise capable of; that is a real limitation and is stated rather
    than papered over with a barge-in that would trigger on our own voice.
    """

    def __init__(self, microphone: Any, pipeline: Any) -> None:
        self._microphone = microphone
        self._pipeline = pipeline
        self._frames: AsyncIterator[AudioChunk] | None = None

    async def next_turn(self, *, on_skip: Callable[[str], None] | None = None) -> HeardTurn | None:
        """Wait until the buyer finishes a sentence and return it.

        Returns ``None`` only when the microphone closes, which is how the conversation
        ends. Utterances that endpoint but cannot be understood are reported through
        ``on_skip`` and waited past rather than returned: handing an empty turn to the
        engine would spend a reply on silence, and staying quiet about it would look like
        the program had hung.
        """

        if self._frames is None:
            self._frames = self._microphone.frames()
        async for chunk in self._frames:
            outcome = await self._pipeline.push(chunk)
            utterance = outcome.utterance
            if utterance is None:
                continue
            if utterance.is_turn and utterance.text:
                return HeardTurn(str(utterance.text), utterance.language)
            if on_skip is not None:
                on_skip(utterance.outcome.value)
        return None

    def agent_started_speaking(self) -> None:
        self._microphone.pause()
        self._pipeline.agent_started_speaking()

    def set_language(self, language: LanguageCode) -> None:
        """Follow a conversation that has changed language.

        Only the transcriber's *expectation* moves. The decoder is already running in
        auto-detect (see :func:`build_listener`), so this does not gate what Whisper is
        allowed to hear - which matters, because gating it is precisely what would stop
        the next switch being noticed.
        """

        self._pipeline.set_language(language)

    def agent_stopped_speaking(self) -> None:
        self._pipeline.agent_stopped_speaking()
        self._microphone.resume()

    async def close(self) -> None:
        """Release the device. Ending the conversation must not leave a mic open."""

        await self._microphone.stop()


def build_listener(language: LanguageCode, args: argparse.Namespace) -> tuple[Listener | None, str]:
    """A microphone-backed listener for this language, or the reason there is not one."""

    from pitchbot.adapters.faster_whisper_stt import (
        FASTER_WHISPER_AVAILABLE,
        FasterWhisperSpeechToTextAdapter,
    )
    from pitchbot.adapters.faster_whisper_stt import INSTALL_HINT as STT_HINT
    from pitchbot.adapters.webrtc_vad import INSTALL_HINT as VAD_HINT
    from pitchbot.adapters.webrtc_vad import WEBRTC_VAD_AVAILABLE, WebRtcVoiceActivityDetector
    from pitchbot.speech.microphone import FRAME_MS, SAMPLE_RATE_HZ, Microphone, is_available
    from pitchbot.speech.pipeline import SpeechTurnPipeline

    if not is_available():
        return None, "sounddevice is not installed: pip install 'pitchbot[microphone]'"
    if not WEBRTC_VAD_AVAILABLE:
        return None, f"no voice-activity detector. Install it with: {VAD_HINT}"
    if not FASTER_WHISPER_AVAILABLE:
        return None, f"no transcriber. Install it with: {STT_HINT}"

    microphone = Microphone(device=args.input_device, sample_rate_hz=SAMPLE_RATE_HZ)
    pipeline = SpeechTurnPipeline(
        detector=WebRtcVoiceActivityDetector(mode=args.vad_mode, sample_rate_hz=SAMPLE_RATE_HZ),
        transcriber=FasterWhisperSpeechToTextAdapter(
            model_size=args.whisper_model,
            language=language,
            # Expected, not imposed. Forcing the opening language is what makes a switch
            # undetectable: measured 2026-09-04, Hindi speech forced to `en` came back as
            # fluent English the buyer never said, labelled `en` at probability 1.00, with
            # the Devanagari gone. Auto-detect cost nothing to avoid that - identical CER
            # on English and Hindi, better on Telugu. The expectation is still used for
            # Telugu script repair, which is the only thing it was ever needed for here.
            force_language=not args.fixed_language,
        ),
        language=language,
        # Must match what the microphone actually produces. The default assumes 250 ms
        # frames, and a mismatch here does not fail loudly - it silently misreports every
        # duration the endpointer reasons about, so silence is measured eight times too
        # long and the buyer never gets to finish a sentence.
        frame_duration_ms=FRAME_MS,
    )
    listener = Listener(microphone, pipeline)
    hint = "forced" if args.fixed_language else "auto-detect"
    note = f"webrtc vad (mode {args.vad_mode}) + faster-whisper {args.whisper_model} ({hint})"
    return listener, note


def scripted_turns(path: Path | None) -> list[str] | None:
    """Buyer turns read from a file, so a demo can be replayed exactly.

    Blank lines end nothing and ``#`` comments are skipped, so a script file can be
    annotated with what each turn is meant to demonstrate.
    """

    if path is None:
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


async def run(args: argparse.Namespace) -> int:
    language = LanguageCode(args.language)
    if language not in supported_languages() and language is not LanguageCode.MIXED:
        print(f"no phrases for language {language.value!r}", file=sys.stderr)
        return 2

    speaker: Speaker | None = None
    voices = Voices(Path(args.voices_dir))
    audio_enabled = bool(args.speak or args.listen)
    if audio_enabled:
        # Listening without speaking is a conversation the buyer can only lose, so --listen
        # implies --speak. A voice loop that answers in text is a demo of the microphone,
        # not of the product.
        speaker, note = voices.speaker(language)
        print(f"  voice     : {note}")

    listener: Listener | None = None
    if args.listen:
        listener, note = build_listener(language, args)
        print(f"  listening : {note}")
        if listener is None:
            return 2

    understanding: Any | None = None
    if args.understand:
        understanding, note = await build_understanding(Path(args.model_dir), args.model_id)
        print(f"  model     : {note}")

    engine = ConversationEngine(detect_language_switch=not args.fixed_language)
    session_id = uuid4()
    engine.create_session(session_id)

    print(BANNER if listener is None else VOICE_BANNER)
    print(f"  language  : {LANGUAGE_NAMES.get(language, language.value)}")
    print()
    opener = OPENERS[language]
    print(f"  bot  \u203a {opener}")
    await speak(opener, speaker, listener)

    script = scripted_turns(Path(args.script) if args.script else None)
    index = 0

    while True:
        transcribed_as: LanguageCode | None = None
        if listener is not None:
            print("\n  listening...", flush=True)
            heard = await listener.next_turn(
                on_skip=lambda reason: print(f"       ! ignored: {reason}", flush=True)
            )
            if heard is None:
                break
            text = heard.text
            transcribed_as = heard.language
            print(f"  you  \u203a {text}")
        elif script is not None:
            if index >= len(script):
                break
            text = script[index]
            index += 1
            print(f"\n  you  \u203a {text}")
        else:
            try:
                text = input("\n  you  \u203a ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not text:
                break

        started = time.perf_counter()
        read = None
        if understanding is not None:
            known_keys = {fact.key for fact in engine.snapshot(session_id).facts}
            read = await understanding.understand(text, language, known_keys)
        result = engine.process_turn(
            session_id,
            text=text,
            language=language,
            understanding=read,
            transcribed_as=transcribed_as,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        known = known_slots(engine.snapshot(session_id).facts)

        if result.language_switched:
            # Announced before the reply is printed, because the reply is already in the
            # new language and reading it first makes the notice look like it arrived a
            # turn late. Everything downstream of the decision has to move with it too,
            # and each part is a separate failure if it does not: a stale voice reads
            # Hindi with an English one, and a stale transcriber expectation leaves
            # Telugu unrepaired.
            language = result.language
            print(f"       * language -> {LANGUAGE_NAMES.get(language, language.value)}")
            if listener is not None:
                listener.set_language(language)
            if audio_enabled:
                # Re-resolved from `audio_enabled` rather than from `speaker`, so a switch
                # into a language with no reviewed voice silences only that language.
                # Keying it off `speaker` would make the first such switch permanent, and
                # a buyer who moved to Telugu and back would never be spoken to again.
                speaker, note = voices.speaker(language)
                print(f"       * voice    : {note}")

        print(render_turn(result, known, elapsed_ms, verbose=not args.quiet))

        if speaker is not None:
            problem = await speak(result.reply, speaker, listener)
            if problem is not None:
                # Stop trying after the first failure. Repeating the same audio error on
                # every turn would bury the conversation it is meant to accompany.
                print(f"       ! audio disabled: {problem}")
                speaker = None
                audio_enabled = False

    if listener is not None:
        await listener.close()

    snapshot = engine.snapshot(session_id)
    print(
        f"\n  {snapshot.turn_count} turns, {len(snapshot.facts)} facts, "
        f"phase {snapshot.phase.value}"
    )
    return 0


async def speak(text: str, speaker: Speaker | None, listener: Listener | None) -> str | None:
    """Say ``text``, holding the floor so the microphone does not hear the agent.

    The floor is yielded in a ``finally`` because a synthesis failure that left the
    microphone paused would end the conversation silently: the buyer would keep talking to
    a program that had stopped listening and would never say so.
    """

    if speaker is None:
        return None
    if listener is not None:
        listener.agent_started_speaking()
    try:
        return await speaker.say(text)
    finally:
        if listener is not None:
            listener.agent_stopped_speaking()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pitchbot-talk",
        description="Hold a conversation with PitchBot on this machine.",
    )
    parser.add_argument(
        "--language",
        default="en",
        choices=[code.value for code in LanguageCode if code is not LanguageCode.UNKNOWN],
        help="language to speak; en/hi/te have their own phrases, mixed answers in Hindi",
    )
    parser.add_argument(
        "--speak",
        action="store_true",
        help="say each reply out loud with Piper (needs the tts extra and a voice file)",
    )
    parser.add_argument(
        "--voices-dir",
        default="models/piper",
        help="directory holding Piper .onnx voice files",
    )
    parser.add_argument(
        "--understand",
        action="store_true",
        help="read each turn with the local language model (needs the local-llm extra)",
    )
    parser.add_argument(
        "--model-dir",
        default="models/onnx-genai",
        help="directory holding an ONNX GenAI model",
    )
    parser.add_argument(
        "--model-id",
        help="upstream model id for the licence check, e.g. microsoft/Phi-3.5-mini-instruct",
    )
    parser.add_argument(
        "--listen",
        action="store_true",
        help="hold the conversation by voice using the microphone (needs the microphone, "
        "webrtc-vad and faster-whisper extras); implies --speak",
    )
    parser.add_argument(
        "--input-device",
        help="microphone to capture from, by index or name; omit for the system default",
    )
    parser.add_argument(
        "--vad-mode",
        type=int,
        default=2,
        choices=(0, 1, 2, 3),
        help="webrtc voice-activity aggressiveness; higher discards more non-speech",
    )
    parser.add_argument(
        "--whisper-model",
        default="small",
        help="faster-whisper model size; 'small' is the smallest that reads Hindi at all",
    )
    parser.add_argument(
        "--fixed-language",
        action="store_true",
        help="never change language mid-conversation; also forces the transcriber to "
        "--language instead of letting it detect (which is what hides a switch)",
    )
    parser.add_argument(
        "--script",
        help="replay buyer turns from a file, one per line, instead of typing them",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print only replies, without the slot and phase breakdown",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
