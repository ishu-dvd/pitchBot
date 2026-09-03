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
            objection=existing.objection,
            pitch={key: value for key, value in existing.pitch.items() if key != "toys"},
            closing=existing.closing,
            confirm=existing.confirm,
            repeated=existing.repeated,
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
            objection={
                key: value
                for key, value in existing.objection.items()
                if key is not Intent.OBJECTING
            },
            pitch=existing.pitch,
            closing=existing.closing,
            confirm=existing.confirm,
            repeated=existing.repeated,
        )
