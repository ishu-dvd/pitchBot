"""Saying something while the buyer waits, instead of going silent.

Measured 2026-09-04 on the shipped local path: the gap between a buyer finishing a sentence
and the first audio of the reply is **~4.5 seconds** in English and Hindi. Transcription is
essentially all of it - 3,982 ms of 4,507 ms in English, 4,453 ms of 4,553 ms in Hindi -
while planning costs ~1-25 ms and synthesising the reply with a resident voice costs
92-501 ms. Four and a half seconds of dead air is long enough that a person assumes the
line has dropped.

That measurement decides where this hooks in. Because the wait is almost entirely
transcription, a filler can only cover it if it starts when the **endpointer closes the
utterance**, before anyone knows what was said. Waiting for the text would mean speaking
into the last 500 ms of a 4,500 ms silence.

And because nothing is known about the turn at that moment, the filler has to be safe
whatever the buyer just said. That is the rule this module is built around:

    **A filler may assert receipt. It may never assert assent.**

"Hmm" and "got it" say only *I heard you*. "Ok", "yes", "sure" and "theek hai" say *I
agree* - and if the sentence that has not been transcribed yet was *"so you'll do it for
fifty thousand?"*, the agent has just agreed to a number nobody quoted, out loud, in a
sales call. That asymmetry is why the obvious, natural-sounding tokens are deliberately
absent here.

Cost and headroom, same measurement, voices resident:

===============  ========  =========  ===========
Filler           Spoken    All-in     Headroom
===============  ========  =========  ===========
``Hmm.``         0.37 s    421 ms     4,086 ms
``Got it.``      0.57 s    627 ms     3,880 ms
``Let me see.``  0.81 s    859 ms     3,648 ms
``समझ गया।``      0.88 s    935 ms     3,618 ms
``Samajh gaya.`` 1.07 s    1,124 ms   3,429 ms
===============  ========  =========  ===========

Every candidate fits several times over, which is what makes a *second* filler on a long
wait affordable rather than a gamble.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

from pitchbot.domain import LanguageCode

FIRST_AFTER_MS: Final[int] = 700
"""How long to wait before filling the silence at all.

Below this a person would not say anything either - a beat of silence after someone stops
speaking is normal turn-taking, not a gap. It also keeps the filler off any path that is
already fast: a typed turn plans in ~1 ms and must never be padded to 700.
"""

SECOND_AFTER_MS: Final[int] = 2_500
"""When to say a second, slightly longer thing because the wait is clearly long.

Measured, the English and Hindi gap is ~4.5 s, so this lands with over a second still to
go. It is not a fixed cadence: the second phrase comes from the ``patient`` list, because
repeating an acknowledgement the buyer has already heard sounds like a stuck recording,
while "one moment" is what a person actually says when they know they are taking a while.
"""

MAX_PER_TURN: Final[int] = 2
"""Two is company, three is a nervous habit.

The measured headroom would allow four. It is capped at two because the purpose is to show
the line is alive, and a filler every second reads as anxious rather than attentive.
"""


@dataclass(frozen=True, slots=True)
class BackchannelPhrases:
    """What one language says while it thinks.

    Split by length rather than by meaning: a short wait gets a token, a long one gets a
    phrase that acknowledges the wait itself. Both lists are receipt-only.
    """

    brief: tuple[str, ...]
    patient: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.brief or not self.patient:
            raise ValueError("backchannel phrases must offer both a brief and a patient option")


_PHRASES: Final[Mapping[LanguageCode, BackchannelPhrases]] = {
    LanguageCode.ENGLISH: BackchannelPhrases(
        brief=("Hmm.", "Got it.", "Right."),
        patient=("Let me see.", "One moment."),
    ),
    LanguageCode.HINDI: BackchannelPhrases(
        brief=("अच्छा।", "समझ गया।", "हूँ।"),
        patient=("एक मिनट।", "देखता हूँ।"),
    ),
    LanguageCode.TELUGU: BackchannelPhrases(
        brief=("అలాగా.", "అర్థమైంది.", "సరి."),
        patient=("ఒక నిమిషం.", "చూస్తాను."),
    ),
    # Romanised, because a Hinglish speaker is answered in Hinglish. Reading a Devanagari
    # backchannel to someone writing Latin script is the same mismatch the reply tables
    # were given a `mixed` entry to fix, and it is more jarring here because a filler is
    # supposed to be the least remarkable thing in the conversation.
    LanguageCode.MIXED: BackchannelPhrases(
        brief=("Hmm.", "Achcha.", "Samajh gaya."),
        patient=("Ek minute.", "Dekhta hoon."),
    ),
}


def backchannel_languages() -> frozenset[LanguageCode]:
    """Languages that can fill a silence, as opposed to ones the enum names."""

    return frozenset(_PHRASES)


@dataclass(slots=True)
class Backchannel:
    """Decides whether enough time has passed to say something, and what.

    Stateful and deliberately not pure, unlike the language policy: the only state is a
    rotation cursor and a per-turn count, neither of which survives a turn, so there is
    nothing here a checkpoint would need to carry.

    Rotation is a cursor rather than a random choice so that a test can assert what is
    said. Saying the identical token every single turn is the thing that makes a
    backchannel sound synthetic, and randomness is not needed to avoid it.
    """

    first_after_ms: int = FIRST_AFTER_MS
    second_after_ms: int = SECOND_AFTER_MS
    max_per_turn: int = MAX_PER_TURN
    _cursor: int = field(default=0, init=False)
    _said_this_turn: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if self.first_after_ms < 0 or self.second_after_ms <= self.first_after_ms:
            raise ValueError("backchannel thresholds must increase")
        if self.max_per_turn < 1:
            raise ValueError("backchannel must allow at least one phrase per turn")

    def begin_turn(self) -> None:
        """Reset the per-turn count. The rotation cursor deliberately survives."""

        self._said_this_turn = 0

    def due(self, waited_ms: float, language: LanguageCode) -> str | None:
        """What to say now, having waited ``waited_ms``, or ``None`` to stay quiet."""

        phrases = _PHRASES.get(language)
        if phrases is None or self._said_this_turn >= self.max_per_turn:
            return None
        if self._said_this_turn == 0:
            if waited_ms < self.first_after_ms:
                return None
            chosen = phrases.brief[self._cursor % len(phrases.brief)]
        else:
            if waited_ms < self.second_after_ms:
                return None
            chosen = phrases.patient[self._cursor % len(phrases.patient)]
        self._said_this_turn += 1
        self._cursor += 1
        return chosen


__all__ = [
    "FIRST_AFTER_MS",
    "MAX_PER_TURN",
    "SECOND_AFTER_MS",
    "Backchannel",
    "BackchannelPhrases",
    "backchannel_languages",
]
