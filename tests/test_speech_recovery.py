"""Tests for what the agent says when it captured a turn and then failed to use it.

The deadline gives the turn back; these pin the half that was missing. Before this, an
utterance with no transcript took the early return in ``_handle_utterance`` and produced a
JSON outcome and **nothing else** - so a buyer who spoke, waited out a 6 s deadline, and
heard silence had no way to tell the agent apart from a dropped call.

Two properties matter more than the phrasing:

*Only system failures are answered.* An agent that says "sorry?" to a cough is worse than
one that ignores it, and ``no-speech-recognized`` cannot tell a cough from a sentence.

*The phrase owns the failure.* The buyer may have spoken perfectly; the decoder timed out.
"""

from __future__ import annotations

import pytest

from pitchbot.domain import LanguageCode
from pitchbot.speech.pipeline import UtteranceOutcome
from pitchbot.speech.recovery import (
    RECOVERABLE_OUTCOMES,
    recovery_languages,
    recovery_phrase,
)

SPEAKABLE = (
    LanguageCode.ENGLISH,
    LanguageCode.HINDI,
    LanguageCode.TELUGU,
    LanguageCode.MIXED,
)


@pytest.mark.parametrize("language", SPEAKABLE)
def test_a_system_failure_is_answered_in_every_language_the_agent_speaks(
    language: LanguageCode,
) -> None:
    phrase = recovery_phrase(UtteranceOutcome.TRANSCRIPTION_TIMED_OUT, language)

    assert phrase is not None
    assert phrase.strip() == phrase
    assert phrase.endswith("?"), "a recovery has to invite the buyer to speak again"


@pytest.mark.parametrize(
    "outcome",
    [
        UtteranceOutcome.NO_SPEECH_RECOGNIZED,
        UtteranceOutcome.LOW_CONFIDENCE,
        UtteranceOutcome.OVERSIZE,
        UtteranceOutcome.LANGUAGE_UNSUPPORTED,
        UtteranceOutcome.TRANSCRIBED,
    ],
)
def test_everything_that_is_not_a_system_failure_stays_silent(
    outcome: UtteranceOutcome,
) -> None:
    """Each of these is a judgement, not an oversight - see the module docstring.

    `no-speech-recognized` is the sharpest: it may be a cough, a door or a chair, and this
    outcome cannot distinguish those from speech. `language-unsupported` is the subtlest -
    the agent has just decided it cannot serve this language, and answering anyway would
    contradict the decision it made a millisecond earlier.
    """

    assert recovery_phrase(outcome, LanguageCode.ENGLISH) is None


def test_the_recoverable_set_is_exactly_the_two_system_failures() -> None:
    assert RECOVERABLE_OUTCOMES == frozenset(
        {
            UtteranceOutcome.TRANSCRIPTION_TIMED_OUT,
            UtteranceOutcome.TRANSCRIBER_UNAVAILABLE,
        }
    )


def test_an_unknown_language_is_answered_with_silence_rather_than_english() -> None:
    """Apologising in a language the buyer did not use is not better than saying nothing."""

    assert recovery_phrase(UtteranceOutcome.TRANSCRIPTION_TIMED_OUT, LanguageCode.UNKNOWN) is None


def test_the_phrase_never_blames_the_buyer() -> None:
    """The decoder failed. Telling the buyer they were unclear is untrue, and expensive.

    Asserted on the English and Hinglish lines because those are the two a reviewer can
    read; the Devanagari and Telugu lines are held to the same rule by the same review.
    """

    english = recovery_phrase(UtteranceOutcome.TRANSCRIPTION_TIMED_OUT, LanguageCode.ENGLISH)
    mixed = recovery_phrase(UtteranceOutcome.TRANSCRIPTION_TIMED_OUT, LanguageCode.MIXED)
    assert english is not None and mixed is not None

    blame = ("you were", "you weren't", "unclear", "mumbl", "aap saaf", "speak clearly")
    assert not any(word in english.lower() for word in blame)
    assert not any(word in mixed.lower() for word in blame)
    # ...and it says whose fault it was.
    assert "sorry" in english.lower()
    assert "sorry" in mixed.lower()


def test_hinglish_is_answered_in_hinglish_and_not_devanagari() -> None:
    """Same rule the reply tables follow: switching a Hinglish speaker into literary Hindi
    reads as correcting them, and an apology is the worst moment to do that."""

    mixed = recovery_phrase(UtteranceOutcome.TRANSCRIPTION_TIMED_OUT, LanguageCode.MIXED)
    assert mixed is not None
    assert all(ord(character) < 0x0900 for character in mixed)


def test_every_speakable_language_has_a_phrase() -> None:
    assert recovery_languages() == frozenset(SPEAKABLE)
