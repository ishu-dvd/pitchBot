"""Every language the agent speaks must also be a language it can be refused in.

Telugu was added to this project with phrases, a voice and a disclosure, and shipped in
development with **no safety vocabulary at all**: a buyer typing
``నాకు వద్దు, దయచేసి మళ్ళీ కాల్ చేయవద్దు`` - "I don't want it, please don't call again" -
was answered with the next qualifying question. Every unit test passed, because every unit
test that knew about opt-out was written in English or Hindi.

That is the failure this module exists to prevent, and it is worth being precise about why
it is severe. A missing *phrase* table raises ``KeyError`` and is caught by the first turn.
A missing *safety* vocabulary fails silently and in the direction that keeps the
conversation going, so the only signal is a buyer being ignored after asking to be left
alone. It cannot be found by testing what the agent says; it can only be found by testing
what the agent hears.

So these tests are driven by :func:`supported_languages` rather than by a hard-coded list.
Adding a language to the planner without adding its refusals now fails here, at the point
the language is added, rather than in production.
"""

from __future__ import annotations

import pytest

from pitchbot.conversation.engine import ConversationEngine
from pitchbot.conversation.models import ConversationDisposition, SafetySignal
from pitchbot.conversation.planning import ASK_ORDER, supported_languages
from pitchbot.conversation.rules import detect_safety_signals
from pitchbot.domain import LanguageCode
from pitchbot.simulator.service import DISCLOSURES

# One way each supported language can say "do not contact me again". Written out rather
# than generated because a refusal is idiomatic: Hindi negates with a separate particle
# (`मत`), Telugu with a verb suffix (`-వద్దు`), and English with an auxiliary. A test that
# translated word-for-word would pass while matching nothing a person would type.
OPT_OUT_SAMPLES: dict[LanguageCode, str] = {
    LanguageCode.ENGLISH: "Please do not call me again.",
    LanguageCode.HINDI: "मुझे दोबारा कॉल मत कीजिए।",
    LanguageCode.TELUGU: "నాకు వద్దు, దయచేసి మళ్ళీ కాల్ చేయవద్దు.",
}

ABUSE_SAMPLES: dict[LanguageCode, str] = {
    LanguageCode.ENGLISH: "You are an idiot.",
    LanguageCode.HINDI: "तुम बेवकूफ हो।",
    LanguageCode.TELUGU: "నువ్వు మూర్ఖుడు.",
}


def test_every_supported_language_has_an_opt_out_sample() -> None:
    """A language with no sample here is untested, which is how Telugu shipped unsafe."""

    assert supported_languages() <= set(OPT_OUT_SAMPLES)
    assert supported_languages() <= set(ABUSE_SAMPLES)


@pytest.mark.parametrize("language", sorted(supported_languages()))
def test_opt_out_is_detected_in_every_supported_language(language: LanguageCode) -> None:
    assert SafetySignal.OPT_OUT in detect_safety_signals(OPT_OUT_SAMPLES[language])


@pytest.mark.parametrize("language", sorted(supported_languages()))
def test_abuse_is_detected_in_every_supported_language(language: LanguageCode) -> None:
    assert SafetySignal.ABUSE in detect_safety_signals(ABUSE_SAMPLES[language])


@pytest.mark.parametrize("language", sorted(supported_languages()))
def test_opt_out_closes_the_conversation_in_every_supported_language(
    language: LanguageCode,
) -> None:
    """Detection is not enough: the turn must actually stop the conversation."""

    from uuid import uuid4

    engine = ConversationEngine()
    session_id = uuid4()
    engine.create_session(session_id)
    result = engine.process_turn(session_id, text=OPT_OUT_SAMPLES[language], language=language)

    assert result.disposition is ConversationDisposition.STOP
    assert SafetySignal.OPT_OUT in result.safety_signals
    assert engine.snapshot(session_id).stopped
    # The refusal must be answered in the buyer's own language. Acknowledging an opt-out in
    # English to a Telugu speaker is the moment they most need to understand the reply.
    assert result.reply == engine._reply(language, "opt_out")  # noqa: SLF001


@pytest.mark.parametrize("language", sorted(supported_languages()))
def test_every_supported_language_has_a_disclosure(language: LanguageCode) -> None:
    """The AI disclosure is a legal obligation, not a nicety, so it cannot fall back."""

    assert language in DISCLOSURES
    assert DISCLOSURES[language].strip()


@pytest.mark.parametrize("language", sorted(supported_languages()))
def test_every_supported_language_has_every_safety_reply(language: LanguageCode) -> None:
    for key in ("opt_out", "abuse_redirect", "abuse_stop", "unsafe_request", "clarify_goals"):
        engine = ConversationEngine()
        assert engine._reply(language, key).strip()  # noqa: SLF001


@pytest.mark.parametrize("language", sorted(supported_languages()))
def test_every_supported_language_can_ask_for_every_slot(language: LanguageCode) -> None:
    """Completeness of the phrase set, enforced from the slot order rather than a list."""

    from pitchbot.conversation.planning import ReplyPlan, render_reply

    for slot in ASK_ORDER:
        reply = render_reply(ReplyPlan(acknowledge=slot, ask=slot), language)
        assert reply.strip()
    assert render_reply(ReplyPlan(acknowledge=None, ask=None), language).strip()


def test_a_bare_refusal_of_an_offer_is_not_an_opt_out() -> None:
    """Declining a demo must not be read as "never contact me".

    Telugu's ``వద్దు`` and Hindi's ``बंद करो`` both mean "no" to whatever was just offered.
    Opt-out is irreversible, so the vocabulary deliberately requires the verb that names
    calling or contacting, not the bare negative.
    """

    for text in ("వద్దు", "బంద్ చేయండి", "बंद करो", "no", "nahi"):
        assert SafetySignal.OPT_OUT not in detect_safety_signals(text)
