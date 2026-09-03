"""Tests for planning what to say, which replaced one fixed sentence per language.

Every ordinary turn used to return *"Thanks. What matters most next: features, budget,
timeline, or the decision process?"* regardless of what the buyer said or how often they
had already answered. These assert the three properties that replaced it: never ask for
something known, acknowledge what was just heard, and never ask the same thing forever.
"""

from __future__ import annotations

import pytest

from pitchbot.conversation.planning import (
    _PHRASES,
    ANSWERABLE_OBJECTIONS,
    ASK_ORDER,
    MAX_ASKS_PER_SLOT,
    Intent,
    ReplyPlan,
    SalesMove,
    Slot,
    TurnUnderstanding,
    plan_reply,
    render_reply,
    understanding_from_facts,
)
from pitchbot.domain import LanguageCode, business_types

ALL_SLOTS = frozenset(Slot)


def test_the_first_missing_slot_is_asked_for() -> None:
    plan = plan_reply(TurnUnderstanding())

    assert plan.ask is ASK_ORDER[0]
    assert plan.acknowledge is None


def test_a_known_slot_is_never_asked_for_again() -> None:
    """The old reply asked the same question of a buyer who had already answered."""

    understanding = TurnUnderstanding(known_slots=frozenset({Slot.BUSINESS_TYPE}))

    plan = plan_reply(understanding)

    assert plan.ask is not Slot.BUSINESS_TYPE
    assert plan.ask is ASK_ORDER[1]


def test_what_was_just_heard_is_acknowledged() -> None:
    understanding = TurnUnderstanding(
        known_slots=frozenset({Slot.BUDGET}),
        filled_now=frozenset({Slot.BUDGET}),
    )

    plan = plan_reply(understanding)

    assert plan.acknowledge is Slot.BUDGET
    assert Slot.BUDGET not in (plan.ask,)


def test_the_most_advanced_slot_is_acknowledged_when_several_land_at_once() -> None:
    """A buyer who gives a budget and a business type together is further along."""

    understanding = TurnUnderstanding(
        known_slots=frozenset({Slot.BUSINESS_TYPE, Slot.BUDGET}),
        filled_now=frozenset({Slot.BUSINESS_TYPE, Slot.BUDGET}),
    )

    assert plan_reply(understanding).acknowledge is Slot.BUDGET


def test_a_repeated_turn_is_not_acknowledged() -> None:
    """Reflecting a slot back at a buyer who repeated themselves reads as a loop."""

    understanding = TurnUnderstanding(
        known_slots=frozenset({Slot.BUDGET}),
        filled_now=frozenset({Slot.BUDGET}),
    )

    assert plan_reply(understanding, repeated=True).acknowledge is None


def test_all_slots_known_moves_to_a_next_step() -> None:
    plan = plan_reply(TurnUnderstanding(known_slots=ALL_SLOTS))

    assert plan.ask is None
    assert plan.is_closing is True


# --------------------------------------------------------------------------------------
# Not asking forever
# --------------------------------------------------------------------------------------


def test_a_slot_is_abandoned_after_repeated_asking() -> None:
    """Measured against the shipped extractors, this is not hypothetical.

    The budget pattern requires digits, so *"our budget is around two lakh rupees"* fills
    no slot - and without a limit the agent asks for the budget on every remaining turn.
    """

    understanding = TurnUnderstanding(known_slots=frozenset({Slot.BUSINESS_TYPE}))
    counts = {Slot.REQUESTED_FEATURES.value: MAX_ASKS_PER_SLOT}

    plan = plan_reply(understanding, asked_counts=counts)

    assert plan.ask is not Slot.REQUESTED_FEATURES
    assert plan.ask is Slot.BUDGET


def test_every_slot_exhausted_closes_rather_than_looping() -> None:
    counts = {slot.value: MAX_ASKS_PER_SLOT for slot in Slot}

    plan = plan_reply(TurnUnderstanding(), asked_counts=counts)

    assert plan.ask is None
    assert plan.is_closing is True


# --------------------------------------------------------------------------------------
# Building understanding from fact keys
# --------------------------------------------------------------------------------------


def test_understanding_is_built_from_the_engines_own_fact_keys() -> None:
    understanding = understanding_from_facts(
        ["business_type", "budget_stated"],
        ["budget_stated"],
    )

    assert understanding.known_slots == frozenset({Slot.BUSINESS_TYPE, Slot.BUDGET})
    assert understanding.filled_now == frozenset({Slot.BUDGET})


def test_a_fact_key_that_is_not_a_slot_is_ignored() -> None:
    """Extractors legitimately produce facts that are not slots."""

    understanding = understanding_from_facts(["business_type", "something_else"])

    assert understanding.known_slots == frozenset({Slot.BUSINESS_TYPE})


def test_a_slot_cannot_be_filled_without_being_known() -> None:
    with pytest.raises(ValueError):
        TurnUnderstanding(known_slots=frozenset(), filled_now=frozenset({Slot.BUDGET}))


# --------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "language",
    [LanguageCode.ENGLISH, LanguageCode.HINDI, LanguageCode.MIXED, LanguageCode.UNKNOWN],
)
def test_every_language_renders_a_reply_for_every_slot(language: LanguageCode) -> None:
    for slot in Slot:
        text = render_reply(ReplyPlan(acknowledge=slot, ask=slot), language)
        assert text.strip()
    assert render_reply(ReplyPlan(acknowledge=None, ask=None), language).strip()


def test_hinglish_is_answered_in_hindi() -> None:
    """A buyer writing code-switched Hindi reads Hindi; answering in English is worse."""

    mixed = render_reply(ReplyPlan(acknowledge=None, ask=Slot.BUDGET), LanguageCode.MIXED)
    hindi = render_reply(ReplyPlan(acknowledge=None, ask=Slot.BUDGET), LanguageCode.HINDI)

    assert mixed == hindi


def test_an_unidentified_language_is_answered_in_english() -> None:
    """Guessing Hindi for an unknown language is a worse failure than being formal."""

    unknown = render_reply(ReplyPlan(acknowledge=None, ask=Slot.BUDGET), LanguageCode.UNKNOWN)
    english = render_reply(ReplyPlan(acknowledge=None, ask=Slot.BUDGET), LanguageCode.ENGLISH)

    assert unknown == english


def test_a_rendered_reply_never_contains_buyer_text() -> None:
    """A safety property: this path cannot echo a fabricated price or an injected string.

    The renderer composes fixed phrases only, so there is no argument through which buyer
    text could reach the agent's own words.
    """

    for language in LanguageCode:
        for slot in Slot:
            for repeated in (False, True):
                text = render_reply(
                    ReplyPlan(acknowledge=slot, ask=slot, intent=Intent.EXPLORING),
                    language,
                    repeated=repeated,
                )
                assert "{" not in text and "}" not in text
                assert slot.value not in text


def test_a_repeated_turn_says_so_instead_of_acknowledging() -> None:
    text = render_reply(
        ReplyPlan(acknowledge=Slot.BUDGET, ask=Slot.TIMELINE),
        LanguageCode.ENGLISH,
        repeated=True,
    )

    assert "noted" in text.lower()


# --------------------------------------------------------------------------------------
# Selling: answering pushback, pitching, and closing
#
# The planner used to compute `intent` and hand it to a renderer that never read it, so a
# buyer who objected received the identical next question as one who had not spoken at all.
# These assert that the stance now changes what is said.
# --------------------------------------------------------------------------------------


def test_an_objection_is_answered_as_well_as_progressed() -> None:
    """Answering and then falling silent would trade one failure for another."""

    plan = plan_reply(TurnUnderstanding(intent=Intent.OBJECTING))

    assert plan.move is SalesMove.ANSWER_OBJECTION
    assert plan.objection is Intent.OBJECTING
    # The conversation still moves; being heard is not the same as being finished with.
    assert plan.ask is ASK_ORDER[0]


@pytest.mark.parametrize("intent", ANSWERABLE_OBJECTIONS)
def test_every_answerable_stance_reaches_the_reply(intent: Intent) -> None:
    plan = plan_reply(TurnUnderstanding(intent=intent))
    answered = render_reply(plan, LanguageCode.ENGLISH)
    ignored = render_reply(plan_reply(TurnUnderstanding()), LanguageCode.ENGLISH)

    assert answered != ignored


def test_a_ready_buyer_is_closed_even_with_slots_unknown() -> None:
    """Asking a buyer who has agreed for their timeline is how a closed sale is lost."""

    plan = plan_reply(TurnUnderstanding(intent=Intent.READY))

    assert plan.move is SalesMove.CLOSE
    assert plan.ask is None
    assert plan.is_closing is True


def test_agreement_outranks_a_concern_stated_in_the_same_breath() -> None:
    """`INTENT_PRIORITY` exists for exactly this sentence shape."""

    from pitchbot.conversation.rules import detect_intent

    assert detect_intent("It is expensive but let's start.") is Intent.READY


def test_the_vertical_is_pitched_when_it_is_first_learned() -> None:
    understanding = TurnUnderstanding(
        known_slots=frozenset({Slot.BUSINESS_TYPE}),
        filled_now=frozenset({Slot.BUSINESS_TYPE}),
        business_type="toys",
    )
    plan = plan_reply(understanding)

    assert plan.pitch == "toys"
    assert plan.move is SalesMove.PITCH
    assert "toy" in render_reply(plan, LanguageCode.ENGLISH).lower()


def test_the_vertical_is_not_pitched_again_on_later_turns() -> None:
    """Tied to `filled_now`, so it needs no stored flag and cannot repeat."""

    understanding = TurnUnderstanding(
        known_slots=frozenset({Slot.BUSINESS_TYPE}),
        filled_now=frozenset(),
        business_type="toys",
    )

    assert plan_reply(understanding).pitch is None


def test_a_repeated_turn_is_never_pitched_at() -> None:
    understanding = TurnUnderstanding(
        known_slots=frozenset({Slot.BUSINESS_TYPE}),
        filled_now=frozenset({Slot.BUSINESS_TYPE}),
        business_type="toys",
    )

    assert plan_reply(understanding, repeated=True).pitch is None


def test_a_business_type_that_is_not_a_catalogue_key_is_dropped() -> None:
    """Only catalogue keys may index the pitch table; that is what keeps it safe."""

    understanding = understanding_from_facts(
        ["business_type"], ["business_type"], business_type="whatever the buyer typed"
    )

    assert understanding.business_type is None
    assert plan_reply(understanding).pitch is None


def test_nothing_the_buyer_wrote_can_reach_the_reply() -> None:
    """The safety property, asserted rather than assumed.

    Every rendered part is looked up in a per-language table by an enum member or a
    catalogue key, so there is no argument shape that injects text.
    """

    for intent in ANSWERABLE_OBJECTIONS:
        for vertical in sorted(business_types()):
            plan = ReplyPlan(
                acknowledge=Slot.BUDGET,
                ask=Slot.TIMELINE,
                objection=intent,
                pitch=vertical,
            )
            reply = render_reply(plan, LanguageCode.ENGLISH)
            phrases = _PHRASES[LanguageCode.ENGLISH]
            assert phrases.objection[intent] in reply
            assert phrases.pitch[vertical] in reply


# --------------------------------------------------------------------------------------
# Budget extraction, which the selling path depends on
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Our budget is around 150000 rupees.",
        "budget is about 2 lakh",
        "budget is up to 50000",
        "हमारा बजट लगभग 150000 रुपये है",
        "మా బడ్జెట్ దాదాపు 150000 రూపాయలు",
    ],
)
def test_a_hedged_budget_is_still_a_budget(text: str) -> None:
    """Found by running `examples/sales-en.txt`, not by reading the pattern.

    "Our budget is around 150000 rupees" filled no slot, because the digits had to follow
    the cue immediately. The buyer answered, the answer was thrown away, the agent asked
    again, hit its ask limit and closed without a budget - which is `MAX_ASKS_PER_SLOT`
    bounding a symptom whose cause was here all along.
    """

    from pitchbot.conversation.rules import _BUDGET_PATTERN, normalize_text

    assert _BUDGET_PATTERN.search(normalize_text(text)) is not None


@pytest.mark.parametrize(
    "text",
    [
        "budget is not decided, we sold 500 units last month",
        "no budget yet but we shipped 900 orders",
    ],
)
def test_a_number_that_is_not_a_budget_is_not_read_as_one(text: str) -> None:
    """Why the hedges are a closed list and not "allow a couple of words".

    A permissive gap would read the sentences above as budgets of 500 and 900. A missed
    budget costs one more question; an invented one is quoted back to the buyer and shapes
    a proposal, so the failure directions are not comparable.
    """

    from pitchbot.conversation.rules import _BUDGET_PATTERN, normalize_text

    assert _BUDGET_PATTERN.search(normalize_text(text)) is None
