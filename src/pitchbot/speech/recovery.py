"""What the agent says when it captured a turn and then failed to use it.

The deadline in :mod:`pitchbot.speech.pipeline` gives the *turn* back when a transcription
will not finish. That is only half a fix. Measured against the shipped socket path, the
other half was missing: an utterance that produces no transcript takes the early return in
``_handle_utterance``, which sends a JSON outcome and nothing else - **no reply, no audio**.

So a buyer who spoke, waited out a 6 s deadline, and got silence had no way to tell the
agent apart from a dropped call. Silence is the one response a voice product cannot use,
because it is indistinguishable from a fault in every layer beneath it.

**Only genuine system failures get a phrase.** The set is deliberately small:

``TRANSCRIPTION_TIMED_OUT``
    The buyer definitely spoke - an utterance only endpoints after ``min_speech_ms`` - and
    the decoder definitely failed. Nothing about this is the buyer's doing.

``TRANSCRIBER_UNAVAILABLE``
    Same shape: speech was captured and the component that should have read it did not.

Everything else is deliberately left silent, and each exclusion is a judgement rather than
an oversight:

``NO_SPEECH_RECOGNIZED``
    May be a cough, a door, or a chair. An agent that says "sorry?" to a cough is worse
    than one that ignores it, and this outcome cannot distinguish the two.

``LOW_CONFIDENCE``
    A product judgement about a transcript that *exists*, not a failure to produce one.
    Changing what happens here is a separate decision from fixing a dropped turn.

``OVERSIZE``
    The buyer ran past the utterance cap. Asking them to repeat a speech that was already
    too long is the wrong remedy.

``LANGUAGE_UNSUPPORTED``
    The agent has just determined it cannot serve this language. Answering anyway - in a
    language it cannot speak, or in one the buyer did not use - would contradict the
    decision it just made.

**The phrasing owns the failure.** Every line here says the agent missed it, never that the
buyer was unclear. The buyer may have spoken perfectly; the decoder timed out. Blaming a
listener for a fault in the machine is both untrue and, in a sales call, expensive.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from pitchbot.domain import LanguageCode
from pitchbot.speech.pipeline import UtteranceOutcome

RECOVERABLE_OUTCOMES: Final[frozenset[UtteranceOutcome]] = frozenset(
    {
        UtteranceOutcome.TRANSCRIPTION_TIMED_OUT,
        UtteranceOutcome.TRANSCRIBER_UNAVAILABLE,
    }
)
"""Outcomes where the buyer spoke and the system - not the buyer - is at fault."""

_PHRASES: Final[Mapping[LanguageCode, str]] = {
    LanguageCode.ENGLISH: "Sorry, I missed that. Could you say it again?",
    LanguageCode.HINDI: "माफ़ कीजिए, मैं सुन नहीं पाया। एक बार फिर बताइए?",
    LanguageCode.TELUGU: "క్షమించండి, నేను వినలేకపోయాను. మళ్ళీ చెప్పగలరా?",
    # Romanised, for the same reason the reply tables carry a `MIXED` entry rather than
    # redirecting to Hindi: answering a Hinglish speaker in literary Devanagari reads as
    # correcting them, and an apology is the worst possible moment to do that.
    LanguageCode.MIXED: "Sorry, main sun nahi paaya. Ek baar phir bataiye?",
}


def recovery_phrase(outcome: UtteranceOutcome, language: LanguageCode) -> str | None:
    """What to say about ``outcome``, or ``None`` to stay silent.

    ``None`` for an unknown language as well as an unrecoverable outcome: apologising in a
    language the buyer did not use is not better than saying nothing.
    """

    if outcome not in RECOVERABLE_OUTCOMES:
        return None
    return _PHRASES.get(language)


def recovery_languages() -> frozenset[LanguageCode]:
    """Languages that can apologise, as opposed to ones the enum names."""

    return frozenset(_PHRASES)


__all__ = [
    "RECOVERABLE_OUTCOMES",
    "recovery_languages",
    "recovery_phrase",
]
