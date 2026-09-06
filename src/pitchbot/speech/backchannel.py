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
"""How long to wait, **since the buyer stopped speaking**, before filling the silence.

Below this a person would not say anything either - a beat of silence after someone stops
speaking is normal turn-taking, not a gap. It also keeps the filler off any path that is
already fast: a typed turn plans in ~1 ms and must never be padded to 700.

That "since the buyer stopped" is load-bearing and used not to be true. This threshold is
counted from :meth:`ThinkingFiller.start`, which the pipeline calls when the *endpointer
closes the utterance* - and an utterance only closes after ``end_silence_ms`` of trailing
silence. Measured in audio time on the real pipeline (``probe_filler_timing.py``), the
endpointer closed at 720 ms and the first filler landed at **1,420 ms**: exactly twice its
own documented value, and 7.1x the ~200 ms gap Stivers et al. (PNAS 2009) measured between
human turns. The elapsed silence is now handed to the filler so 700 means 700.
"""

SECOND_AFTER_MS: Final[int] = 3_200
"""When to say a second, slightly longer thing because the wait is clearly long.

Also counted from the buyer's last word. 3,200 rather than the 2,500 it read before, so
that it keeps the position it actually had: 2,500 measured from a close that was already
720 ms late put this at 3,220 ms, and the whole spoken turn is ~2,587 ms. Left at 2,500
once the reference frame was corrected, it would have fired **87 ms before the reply was
ready** - and because the reply waits for a filler to finish rather than chopping it, a
second filler that starts just before the reply does not cover the wait, it *extends* it.

So this is deliberately beyond the typical reply and only reached when a turn is genuinely
slow - a transcription outlier, which is measured at up to 11 s. It is not a fixed cadence:
the second phrase comes from the ``patient`` list, because repeating an acknowledgement the
buyer has already heard sounds like a stuck recording, while "one moment" is what a person
actually says when they know they are taking a while.
"""

MIN_WORK_MS: Final[int] = 200
"""The shortest time we will work before saying anything, however long the buyer has waited.

:data:`FIRST_AFTER_MS` is measured from the buyer's last word, which is the right frame for
"has this become a gap?" but the wrong one for "is this turn already fast?". By the time a
spoken utterance closes, 700 ms of that threshold is *already spent* on endpointing, so
against buyer-silence alone every spoken turn qualifies immediately - including one whose
reply is a few milliseconds away.

That matters because a filler is not free to abandon: the reply waits for one to finish
rather than chopping it mid-word, so a filler that starts just before the reply is ready
does not cover the wait, it **extends** it by its own length (0.37-1.07 s measured).

So there are two clocks and a filler must satisfy both - enough silence for the buyer to
feel a gap, and enough work for us to be sure there is one. 200 ms is the gap Stivers et al.
(PNAS 2009) measured between human turns: the same beat a person takes before deciding
someone else's pause needs filling. A reply that arrives inside it cancels the filler
outright, which is how the fast path stays fast.
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
    min_work_ms: int | None = None
    max_per_turn: int = MAX_PER_TURN
    _cursor: int = field(default=0, init=False)
    _said_this_turn: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if self.first_after_ms < 0 or self.second_after_ms <= self.first_after_ms:
            raise ValueError("backchannel thresholds must increase")
        if self.min_work_ms is not None and self.min_work_ms < 0:
            raise ValueError("min_work_ms must not be negative")
        if self.max_per_turn < 1:
            raise ValueError("backchannel must allow at least one phrase per turn")

    @property
    def work_floor_ms(self) -> int:
        """The resolved deadband: how long to work before saying anything, at minimum.

        Capped at the beat itself so it is a floor and never the binding constraint: with
        no silence credited - a typed turn - the wait is exactly ``first_after_ms``,
        unchanged. It only bites once endpointing has already spent part of that threshold,
        which is the case it exists for.
        """

        if self.min_work_ms is not None:
            return self.min_work_ms
        return min(MIN_WORK_MS, self.first_after_ms)

    def work_target_ms(self, target_ms: int, already_silent_ms: float) -> float:
        """How long to keep working before ``target_ms`` may be honoured.

        Translates a threshold measured from the buyer's last word into one measured from
        the moment we learned there was work, which is the only clock a filler can actually
        sleep on. Both constraints in one number: whatever silence has already elapsed is
        credited against the threshold, and :attr:`work_floor_ms` keeps a turn whose reply
        is imminent from being padded with a filler.
        """

        return max(float(target_ms) - already_silent_ms, float(self.work_floor_ms))

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
    "MIN_WORK_MS",
    "SECOND_AFTER_MS",
    "Backchannel",
    "BackchannelPhrases",
    "backchannel_languages",
]
