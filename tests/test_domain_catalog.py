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
from pitchbot.actions.summary_text import stated_budget
from pitchbot.conversation.planning import _PHRASES, supported_languages
from pitchbot.conversation.rules import _BUDGET_PATTERN
from pitchbot.domain import (
    BUDGET_CUES,
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


@pytest.mark.parametrize(
    ("stated", "figure"),
    [
        ("Our budget is 150000.", "150000"),
        ("हमारा बजट दो लाख है।", "दो लाख"),
        ("మా బడ్జెట్ రెండు లక్షలు.", "రెండు లక్షలు"),
        ("Budget pachas hazaar hai.", "pachas hazaar"),
    ],
)
def test_a_budget_the_agent_hears_is_a_budget_the_deck_receives(stated: str, figure: str) -> None:
    """The extractor and the outbound minimiser must agree in every language.

    They did not. The minimiser re-declared the cue list without `బడ్జెట్`, so a Telugu
    buyer who stated a budget was handed a deck reading "budget: not yet discussed"; and
    its character class relied on ``\\w``, which excludes the combining marks that spell
    the vowels in these scripts, so ``बजट दो लाख`` was truncated to ``बजट द``.

    Both were invisible to every existing test, because every existing budget test was
    written in English and put a digit before the first vowel sign.
    """

    heard = _BUDGET_PATTERN.search(stated)
    assert heard is not None, f"extractor did not hear a budget in {stated!r}"
    assert figure in heard.group(0)

    carried = policy._BUDGET.search(heard.group(0))  # noqa: SLF001
    assert carried is not None, f"minimiser discarded {heard.group(0)!r}"
    assert figure in carried.group(0), f"minimiser truncated to {carried.group(0)!r}"


def test_every_budget_cue_reaches_both_the_extractor_and_the_minimiser() -> None:
    """Adding a language to the catalogue must not require remembering two more places."""

    for cue in BUDGET_CUES:
        stated = f"{cue} 150000"
        assert _BUDGET_PATTERN.search(stated) is not None, cue
        assert policy._BUDGET.search(stated) is not None, cue  # noqa: SLF001


def test_the_deck_strips_every_budget_cue_it_could_be_handed() -> None:
    """Text already labelled "Budget" must not repeat the word in any language.

    Shared by the deck and the WhatsApp follow-up, so one assertion covers both.
    """

    for cue in BUDGET_CUES:
        assert stated_budget(f"{cue} is 150000") == "150000"
        assert stated_budget(f"{cue} 150000") == "150000"


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


@pytest.mark.parametrize(
    ("said", "figure"),
    [
        # English, in words
        ("Our budget is two lakh.", "two lakh"),
        ("We have a budget of five lakhs for this.", "five lakhs"),
        ("Budget is around fifty thousand rupees.", "fifty thousand"),
        ("Budget is one crore.", "one crore"),
        ("We can spend up to ten lakh.", "ten lakh"),
        # Hinglish
        ("Budget do lakh hai.", "do lakh"),
        ("Hamara budget paanch lakh ke aas paas hai.", "paanch lakh"),
        ("Budget pachas hazaar hai.", "pachas hazaar"),
        # Hindi and Telugu, in their own scripts
        ("हमारा बजट दो लाख है।", "दो लाख"),
        ("बजट पचास हज़ार के आसपास है।", "पचास हज़ार"),
        ("మా బడ్జెట్ రెండు లక్షలు.", "రెండు లక్షలు"),
        ("బడ్జెట్ యాభై వేలు.", "యాభై వేలు"),
        # Digits, which already worked and must keep working
        ("Our budget is 150000.", "150000"),
        ("Budget is 5 lakh.", "5 lakh"),
    ],
)
def test_a_budget_can_be_said_in_words(said: str, figure: str) -> None:
    """People say "two lakh" on a sales call far more often than they say "200000".

    Measured on seventeen phrasings a buyer would actually use: five were heard. Every
    miss was a figure written as a word, which is every figure spoken in Hindi, Telugu or
    Hinglish and most of them spoken in Indian English.
    """

    match = _BUDGET_PATTERN.search(said)

    assert match is not None, f"no budget heard in {said!r}"
    assert figure in match.group(0)


@pytest.mark.parametrize(
    "said",
    [
        # No cue: a quantity in a sentence about something else.
        "We sold five lakh units last year.",
        "We have three stores.",
        # A cue with no figure. "one" alone is not a budget, so requiring the scale word
        # keeps "budget is one of our concerns" from becoming a budget of one.
        "Budget is not decided yet.",
        "Budget is one of our concerns.",
        # Bare "spend" is what the company already pays someone else, not what they will
        # pay us. Only the modal forms are budget cues.
        "We spend five lakh on ads every year.",
    ],
)
def test_a_quantity_that_is_not_a_budget_stays_unheard(said: str) -> None:
    """A wrong figure is quoted back to the buyer and prices a proposal.

    Missing a budget costs one more question. Inventing one costs the deal, so every
    widening here is anchored to an explicit cue rather than to the shape of a number.
    """

    assert _BUDGET_PATTERN.search(said) is None


def test_the_deck_can_render_every_deadline_the_matcher_can_emit() -> None:
    """A unit added to the extractor without deck copy reaches the buyer untranslated.

    The two live in different layers and cannot import each other, so the only thing that
    keeps them aligned is this assertion. Adding "years" to the matcher without adding it
    to the deck tables fails here rather than on a customer's slide.
    """

    from pitchbot.actions.deck_content import TIMELINE_UNITS
    from pitchbot.conversation.rules import _TIMELINE_UNIT_STEMS

    emitted = set(_TIMELINE_UNIT_STEMS.values()) | {"near-term"}

    assert emitted == TIMELINE_UNITS


def test_a_stated_budget_is_evidence_however_it_is_stated() -> None:
    """Extraction and classification must learn a new phrasing together.

    They did not. The extractor learned "we can spend up to ten lakh"; the classifier's
    own copy of the budget vocabulary did not, so the turn produced no evidence at all.
    With no evidence the lead classifies REVIEW_NEEDED and `ActionPolicy` blocks every
    action - measured end to end, a call that filled business type, features, budget and
    timeline was refused its deck. A fourth copy of the same vocabulary, in a fourth
    module, with the loudest possible symptom and no error anywhere.
    """

    from pitchbot.conversation.rules import _POSITIVE_EVIDENCE, _contains_any, normalize_text
    from pitchbot.domain import BUDGET_CUES, BUDGET_INTENT_CUES

    budget_phrases = next(
        phrases for dimension, _, phrases in _POSITIVE_EVIDENCE if dimension == "budget"
    )
    for cue in (*BUDGET_CUES, *BUDGET_INTENT_CUES):
        assert cue in budget_phrases, f"{cue} states a budget but is not evidence of one"

    assert _contains_any(normalize_text("We can spend up to ten lakh."), budget_phrases)


def test_a_stated_deadline_is_evidence_however_it_is_stated() -> None:
    """Every unit the timeline matcher accepts must also count as timeline evidence.

    The evidence list stopped at "weeks", so "in 3 months" filled the `timeline` slot and
    contributed nothing to the classification that decides whether the buyer may be sent
    anything.
    """

    from pitchbot.conversation.rules import (
        _POSITIVE_EVIDENCE,
        _TIMELINE_UNIT_STEMS,
        _contains_any,
        normalize_text,
    )

    timeline_phrases = next(
        phrases for dimension, _, phrases in _POSITIVE_EVIDENCE if dimension == "timeline"
    )
    for stem in _TIMELINE_UNIT_STEMS:
        assert stem in timeline_phrases, f"{stem} is a deadline the classifier cannot see"

    assert _contains_any(normalize_text("we want it live in 3 months"), timeline_phrases)
