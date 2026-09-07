"""The sales vocabulary is defined once, and everything downstream agrees with it.

This existed in three independent copies before: the extractor matched buyer text against
one, the action policy allowlisted a second, and the deck builder allowlisted a third. They
happened to agree, and nothing made them. Adding a vertical to the extractor produced facts
that the policy silently discarded and the deck builder silently dropped - a failure whose
symptom is a qualified lead that never becomes an action, with no error anywhere.

That class of bug cannot be caught by testing each module, because each module was
internally consistent. It can only be caught by asserting they share a definition, which is
what these tests do.
"""

from __future__ import annotations

import pytest

from pitchbot.actions import decks, policy
from pitchbot.conversation.planning import _PHRASES, supported_languages
from pitchbot.domain import (
    BUSINESS_TYPES,
    FEATURES,
    INTENT_PHRASES,
    INTENT_PRIORITY,
    Intent,
    LanguageCode,
    business_types,
    features,
)


def test_the_action_policy_allowlists_exactly_the_catalogue() -> None:
    """A vertical the agent can qualify must be one the agent can act on."""

    assert policy._BUSINESS_TYPES == business_types()  # noqa: SLF001
    assert policy._FEATURES == features()  # noqa: SLF001


def test_the_deck_builder_allowlists_exactly_the_catalogue() -> None:
    assert decks._ALLOWED_FEATURES == features()  # noqa: SLF001


def test_every_catalogue_entry_has_words_that_identify_it() -> None:
    """An entry with no phrases can never be extracted, so it is dead weight."""

    for key, phrases in {**BUSINESS_TYPES, **FEATURES}.items():
        assert phrases, f"{key} has no identifying words"
        assert all(phrase.strip() for phrase in phrases)


def test_every_stance_that_is_prioritised_has_phrases() -> None:
    """Priority over a stance with no vocabulary would be silently unreachable."""

    assert set(INTENT_PRIORITY) == set(INTENT_PHRASES)
    for intent in INTENT_PRIORITY:
        assert INTENT_PHRASES[intent]


def test_exploring_is_the_absence_of_a_signal_not_a_signal() -> None:
    """Giving the default stance trigger words would let it outrank real ones."""

    assert Intent.EXPLORING not in INTENT_PHRASES
    assert Intent.EXPLORING not in INTENT_PRIORITY


@pytest.mark.parametrize("language", sorted(supported_languages()))
def test_adding_a_vertical_without_a_pitch_fails_at_import(language: LanguageCode) -> None:
    """The completeness check is what makes the catalogue safe to extend.

    Constructing a phrase set that is missing a vertical must raise, so that adding one to
    the catalogue breaks every language at once and immediately, rather than producing a
    generic reply to the first buyer in that vertical.
    """

    from pitchbot.conversation.planning import LanguagePhrases

    existing = _PHRASES[language]  # noqa: SLF001
    with pytest.raises(ValueError, match="missing pitches"):
        LanguagePhrases(
            acknowledge=existing.acknowledge,
            ask=existing.ask,
            ask_again=existing.ask_again,
            objection=existing.objection,
            pitch={key: value for key, value in existing.pitch.items() if key != "toys"},
            closing=existing.closing,
            closing_again=existing.closing_again,
            closing_final=existing.closing_final,
            confirm=existing.confirm,
            repeated=existing.repeated,
            switched=existing.switched,
        )


@pytest.mark.parametrize("language", sorted(supported_languages()))
def test_a_language_that_cannot_answer_an_objection_fails_at_import(
    language: LanguageCode,
) -> None:
    from pitchbot.conversation.planning import LanguagePhrases

    existing = _PHRASES[language]  # noqa: SLF001
    with pytest.raises(ValueError, match="missing objections"):
        LanguagePhrases(
            acknowledge=existing.acknowledge,
            ask=existing.ask,
            ask_again=existing.ask_again,
            objection={
                key: value
                for key, value in existing.objection.items()
                if key is not Intent.OBJECTING
            },
            pitch=existing.pitch,
            closing=existing.closing,
            closing_again=existing.closing_again,
            closing_final=existing.closing_final,
            confirm=existing.confirm,
            repeated=existing.repeated,
            switched=existing.switched,
        )


# --------------------------------------------------------------------------------------
# A deadline can be stated in any language the product sells in (PR 54)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "expected"),
    [
        # English, unchanged - the existing pattern is tried first so this cannot regress.
        ("We want it live in 3 months.", "3 months"),
        ("We need it within 6 weeks.", "6 weeks"),
        # Hindi, in words and in digits, nominative and oblique.
        ("तीन महीने में चालू करना है।", "3 months"),
        ("3 महीने में चाहिए।", "3 months"),
        ("छह महीनों में", "6 months"),
        ("दो हफ्ते में", "2 weeks"),
        # Telugu writes the case ending onto the unit itself.
        ("మూడు నెలల్లో సిద్ధం కావాలి.", "3 months"),
        ("రెండు వారాల్లో", "2 weeks"),
        # Hinglish.
        ("teen mahine mein live karna hai", "3 months"),
        ("do hafte mein", "2 weeks"),
    ],
)
def test_a_deadline_is_understood_in_every_language(said: str, expected: str) -> None:
    """Measured before this existed: eight of ten phrasings failed.

    Three of the four languages this product sells in could not state a deadline at all, so
    the agent asked for it twice, gave up, and the deck reported it as never discussed.
    """

    from pitchbot.conversation.rules import _match_timeline, normalize_text

    assert _match_timeline(normalize_text(said)) == expected


def test_a_deadline_is_normalised_to_one_canonical_shape() -> None:
    """The value reaches a slide, and `policy._TIMELINE` is the allowlist that bounds it.

    Emitting canonical English units keeps that allowlist tight rather than widening it to
    accept arbitrary Devanagari and Telugu, which is the safer of the two directions.
    """

    from pitchbot.actions.policy import _TIMELINE
    from pitchbot.conversation.rules import _match_timeline, normalize_text

    for said in ("तीन महीने में", "మూడు నెలల్లో", "teen mahine mein"):
        value = _match_timeline(normalize_text(said))
        assert value is not None
        assert _TIMELINE.fullmatch(value), f"{said!r} produced {value!r}, which policy drops"


def test_text_with_no_deadline_reports_none() -> None:
    from pitchbot.conversation.rules import _match_timeline, normalize_text

    assert _match_timeline(normalize_text("We sell clothes and want a website.")) is None


# --------------------------------------------------------------------------------------
# A business names itself in the case its language actually uses (PR 54)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "expected"),
    [
        ("we sell clothing", "apparel"),
        ("हम कपड़े की दुकान चलाते हैं", "apparel"),
        # The oblique. This is the wording the product's own deck uses for a clothes shop.
        ("हम कपड़ों की दुकान चलाते हैं", "apparel"),
        ("kapdon ki dukaan", "apparel"),
        # Telugu names a shop with the genitive, so this is the ordinary way to say it.
        ("దుస్తుల దుకాణం", "apparel"),
        ("బట్టల షాప్", "apparel"),
        ("బొమ్మల దుకాణం", "toys"),
        ("పుస్తకాల షాప్", "books"),
        ("खिलौनों की दुकान", "toys"),
    ],
)
def test_a_business_type_survives_inflection(said: str, expected: str) -> None:
    """Six of eleven natural phrasings were missed, all of them inflected forms."""

    from pitchbot.conversation.rules import _match_named_value, normalize_text

    assert _match_named_value(normalize_text(said), BUSINESS_TYPES) == expected


def test_widening_the_vocabulary_did_not_reopen_the_booking_form() -> None:
    """The guard these stems had to survive.

    Plain substring matching once read "a booking form" as the *books* vertical and told a
    furniture buyer the agent thought they sold books. Stems are matched as whole terms
    with only case and number endings allowed, so that stays fixed.
    """

    from pitchbot.conversation.rules import _match_named_value, normalize_text

    said = normalize_text("a booking form for furniture")

    assert _match_named_value(said, BUSINESS_TYPES) is None
