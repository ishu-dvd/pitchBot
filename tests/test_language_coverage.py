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
from pitchbot.conversation.planning import (
    ANSWERABLE_OBJECTIONS,
    ASK_ORDER,
    ReplyPlan,
    SalesMove,
    render_reply,
    supported_languages,
)
from pitchbot.conversation.rules import detect_intent, detect_safety_signals
from pitchbot.domain import Intent, LanguageCode, business_types
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

# One way each supported language can push back, agree, hesitate, or shop around. The same
# reasoning as the refusals above applies: these are idiomatic, not translated. A buyer
# objecting to a price in Telugu says the price *is* expensive (`ఖరీదు`), where an English
# buyer says it is expensive *for us*.
INTENT_SAMPLES: dict[LanguageCode, dict[Intent, str]] = {
    LanguageCode.ENGLISH: {
        Intent.READY: "Okay, let's start.",
        Intent.OBJECTING: "That is too expensive for us.",
        Intent.STALLING: "Let me think about it.",
        Intent.COMPARING: "We are comparing another vendor.",
    },
    LanguageCode.HINDI: {
        Intent.READY: "ठीक है, शुरू करें।",
        Intent.OBJECTING: "यह बहुत महंगा है।",
        Intent.STALLING: "मैं बाद में बताता हूँ।",
        Intent.COMPARING: "हम दूसरी कंपनी से बात कर रहे हैं।",
    },
    LanguageCode.TELUGU: {
        Intent.READY: "సరే, ప్రారంభిద్దాం.",
        Intent.OBJECTING: "ఇది చాలా ఖరీదు.",
        Intent.STALLING: "నేను తరువాత ఆలోచిస్తాను.",
        Intent.COMPARING: "మేము వేరే కంపెనీని చూస్తున్నాము.",
    },
}

# A sentence in each language that names a business the catalogue knows.
BUSINESS_SAMPLES: dict[LanguageCode, tuple[str, str]] = {
    LanguageCode.ENGLISH: ("We sell toys.", "toys"),
    LanguageCode.HINDI: ("हम कपड़े बेचते हैं।", "apparel"),
    LanguageCode.TELUGU: ("మేము బొమ్మలు అమ్ముతాము.", "toys"),
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


# --------------------------------------------------------------------------------------
# Selling, in every language
#
# The same argument as the refusals above. A language can be given phrases, a voice and a
# disclosure and still be unable to *sell* in, because objection vocabulary and pitches are
# separate tables. Driving these from `supported_languages()` and `business_types()` means
# adding either a language or a vertical fails here rather than in front of a buyer.
# --------------------------------------------------------------------------------------


def test_every_supported_language_has_selling_samples() -> None:
    assert supported_languages() <= set(INTENT_SAMPLES)
    assert supported_languages() <= set(BUSINESS_SAMPLES)


@pytest.mark.parametrize("language", sorted(supported_languages()))
def test_every_stance_is_detected_in_every_supported_language(language: LanguageCode) -> None:
    """Detection needs no model. This is the whole reason it was moved into the rules."""

    for intent, sample in INTENT_SAMPLES[language].items():
        assert detect_intent(sample) is intent, f"{language.value}: {sample!r}"


@pytest.mark.parametrize("language", sorted(supported_languages()))
def test_every_objection_can_be_answered_in_every_supported_language(
    language: LanguageCode,
) -> None:
    for intent in ANSWERABLE_OBJECTIONS:
        reply = render_reply(ReplyPlan(acknowledge=None, ask=None, objection=intent), language)
        assert reply.strip()


@pytest.mark.parametrize("language", sorted(supported_languages()))
def test_every_vertical_can_be_pitched_in_every_supported_language(
    language: LanguageCode,
) -> None:
    """A business the extractor recognises must be one the planner can talk about."""

    for vertical in sorted(business_types()):
        reply = render_reply(ReplyPlan(acknowledge=None, ask=None, pitch=vertical), language)
        assert reply.strip()


@pytest.mark.parametrize("language", sorted(supported_languages()))
def test_an_objection_is_answered_rather_than_ignored(language: LanguageCode) -> None:
    """The defect this replaces: pushback produced the identical next question.

    Asserting that the objecting reply *differs* from the neutral one is deliberately
    weaker than pinning the sentence, and stronger than checking a flag. It fails if
    objection handling is disconnected anywhere along the path - rules, engine, planner or
    renderer - which is exactly how it was broken before, in two places at once.
    """

    from uuid import uuid4

    def reply_to(text: str) -> str:
        engine = ConversationEngine()
        session_id = uuid4()
        engine.create_session(session_id)
        return engine.process_turn(session_id, text=text, language=language).reply

    neutral = reply_to("hello")
    objecting = reply_to(INTENT_SAMPLES[language][Intent.OBJECTING])
    assert objecting != neutral


@pytest.mark.parametrize("language", sorted(supported_languages()))
def test_a_ready_buyer_is_closed_rather_than_questioned(language: LanguageCode) -> None:
    """A buyer who says "let's start" must not be asked for their budget.

    They must also not be asked the *closing* question again, which is what happened
    until the shipped sales script was run: agreeing produced the identical
    "demo or proposal?" sentence they had just answered.
    """

    from uuid import uuid4

    from pitchbot.conversation.planning import _PHRASES, _table

    engine = ConversationEngine()
    session_id = uuid4()
    engine.create_session(session_id)
    result = engine.process_turn(
        session_id, text=INTENT_SAMPLES[language][Intent.READY], language=language
    )

    phrases = _PHRASES[_table(language)]  # noqa: SLF001
    assert result.reply == phrases.confirm
    assert phrases.closing not in result.reply


@pytest.mark.parametrize("language", sorted(supported_languages()))
def test_naming_a_business_produces_a_pitch_about_it(language: LanguageCode) -> None:
    """Learning the vertical must change what is said, not only what is stored.

    The expected phrase is read from the table rather than sliced out of a rendered
    reply. Slicing on ``"."`` looked equivalent and is not: Hindi ends a sentence with a
    danda (``।``) and the split silently returned the whole string, so the first version of
    this test failed against correct output.
    """

    from uuid import uuid4

    from pitchbot.conversation.planning import _PHRASES, _table

    text, vertical = BUSINESS_SAMPLES[language]
    engine = ConversationEngine()
    session_id = uuid4()
    engine.create_session(session_id)
    result = engine.process_turn(session_id, text=text, language=language)

    expected = _PHRASES[_table(language)].pitch[vertical]  # noqa: SLF001
    assert expected in result.reply


# One way each supported language can ask to be spoken to *in that language*, written in
# that language. A buyer asking for Hindi types the request in Hindi, so a request table
# that only knew the English word "hindi" would miss every request a Hindi speaker makes.
SWITCH_REQUEST_SAMPLES: dict[LanguageCode, str] = {
    LanguageCode.ENGLISH: "Could you please speak in English?",
    LanguageCode.HINDI: "कृपया हिंदी में बात कीजिए।",
    LanguageCode.TELUGU: "దయచేసి తెలుగులో మాట్లాడండి.",
}


@pytest.mark.parametrize("language", sorted(supported_languages()))
def test_every_language_can_be_asked_for_in_itself(language: LanguageCode) -> None:
    """A language nobody can request is a language nobody can switch into by asking.

    Driven from `supported_languages` for the same reason the refusals above are: adding
    a language without a way to ask for it fails here, when the language is added, rather
    than for the first buyer who tries.
    """

    from pitchbot.conversation.language import LanguageEvidence, detect_language

    reading = detect_language(SWITCH_REQUEST_SAMPLES[language])
    assert reading.evidence is LanguageEvidence.REQUESTED
    assert reading.language is language


@pytest.mark.parametrize("language", sorted(supported_languages()))
def test_every_language_can_acknowledge_being_switched_into(language: LanguageCode) -> None:
    """The acknowledgement is the whole answer to a buyer who asked, so it cannot be blank.

    It is also the one phrase guaranteed to be read in a language the buyer has just
    started using, which makes a placeholder here more visible than anywhere else.
    """

    from pitchbot.conversation.planning import _PHRASES, _table

    phrases = _PHRASES[_table(language)]  # noqa: SLF001
    assert phrases.switched.strip()
    plan = ReplyPlan(
        ask=None,
        acknowledge=None,
        intent=None,
        objection=None,
        pitch=None,
        move=SalesMove.CLOSE,
    )
    assert render_reply(plan, language, switched=True).startswith(phrases.switched)
