"""When a model's reading of a turn may be believed.

These tests encode two measured facts, and both were live defects before this module
existed.

1. A model that answers the same thing every turn must not be able to steer the reply.
   Qwen2.5-0.5B answered ``stalling`` to 8/8 test turns, and ``STALLING`` is an answerable
   objection, so every reply became "answer the stall".
2. A model must not fill a slot for a language it cannot read. Telugu measured 1/6 (Qwen3)
   and 2/6 (Phi-3.5-mini) - at or below guessing among five values - and Phi's failure mode
   was a *confident* ``business_type``, which retires a qualification question forever.
"""

from __future__ import annotations

import pytest

from pitchbot.adapters.contracts import StructuredCompletion
from pitchbot.adapters.mocks import MockModelAdapter
from pitchbot.conversation.model_trust import (
    TRUSTED_LANGUAGES,
    accept_slots,
    corroborates,
    is_trusted,
    markers_for,
)
from pitchbot.conversation.model_understanding import ModelTurnUnderstanding
from pitchbot.conversation.planning import ANSWERABLE_OBJECTIONS, ASK_ORDER, Slot, plan_reply
from pitchbot.domain import LanguageCode


def _completion(topic: str) -> StructuredCompletion:
    return StructuredCompletion(value={"topic": topic}, model_version="test")


# --------------------------------------------------------------------------------------
# The language gate
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "language",
    [LanguageCode.ENGLISH, LanguageCode.HINDI, LanguageCode.MIXED],
)
def test_languages_the_model_was_measured_to_help_on_are_trusted(
    language: LanguageCode,
) -> None:
    assert is_trusted(language)


def test_telugu_is_not_trusted_because_it_was_measured_and_failed() -> None:
    """1/6 and 2/6 across the two models, below guessing among five enum values."""

    assert not is_trusted(LanguageCode.TELUGU)


def test_an_undetermined_language_is_not_trusted() -> None:
    """No language means no evidence about that language, which is not the same as fine."""

    assert not is_trusted(LanguageCode.UNKNOWN)


@pytest.mark.asyncio
async def test_an_untrusted_language_does_not_even_ask_the_model() -> None:
    """Asking costs 0.6-4.3 s of a turn the buyer waits through, for an answer we discard."""

    adapter = MockModelAdapter([_completion("business_type")])

    source = ModelTurnUnderstanding(adapter)
    result = await source.understand("మేము తోలు బ్యాగులు అమ్ముతాము.", LanguageCode.TELUGU, [])

    assert result is None
    assert not adapter.requests, "the model must not be consulted for an untrusted language"


# --------------------------------------------------------------------------------------
# Corroboration
# --------------------------------------------------------------------------------------


def test_every_slot_has_markers() -> None:
    """A slot with no markers can never be corroborated, so the model could never fill it."""

    missing = [slot.value for slot in ASK_ORDER if not markers_for(slot)]
    assert not missing, f"slots with no corroboration markers: {missing}"


@pytest.mark.parametrize(
    ("text", "slot"),
    [
        ("Our budget is around two lakh rupees.", Slot.BUDGET),
        ("हमारा बजट लगभग दो लाख रुपये है।", Slot.BUDGET),
        ("hamara budget do lakh rupaye hai", Slot.BUDGET),
        ("We sell handmade leather bags to boutiques.", Slot.BUSINESS_TYPE),
        ("हम जयपुर में फर्नीचर बनाते हैं।", Slot.BUSINESS_TYPE),
        ("We need wholesale pricing tiers and a cart.", Slot.REQUESTED_FEATURES),
        ("humein wholesale pricing chahiye", Slot.REQUESTED_FEATURES),
        ("Can you launch before Diwali?", Slot.TIMELINE),
        ("क्या आप दिवाली से पहले लॉन्च कर सकते हैं?", Slot.TIMELINE),
    ],
)
def test_a_turn_about_a_topic_corroborates_that_topic(text: str, slot: Slot) -> None:
    assert corroborates(text, slot)


@pytest.mark.parametrize(
    ("text", "slot"),
    [
        # The measured fabrication: Phi-3.5-mini claimed `business_type` for this.
        ("I am just looking around for now.", Slot.BUSINESS_TYPE),
        ("Hmm, let me think about it.", Slot.BUSINESS_TYPE),
        ("Okay.", Slot.BUDGET),
        # A sentiment word about money is not a stated budget. Phi labelled this
        # `budget_stated`; the buyer stated no budget.
        ("That sounds expensive.", Slot.BUDGET),
    ],
)
def test_a_turn_that_is_not_about_a_topic_does_not_corroborate_it(text: str, slot: Slot) -> None:
    assert not corroborates(text, slot)


def test_markers_match_whole_tokens_not_substrings() -> None:
    """ "banate" must not match inside "banatee"; short markers otherwise fire on anything."""

    assert corroborates("hum furniture banate hain", Slot.BUSINESS_TYPE)
    assert not corroborates("banatee banateen", Slot.BUSINESS_TYPE)


def test_accept_slots_drops_everything_for_an_untrusted_language() -> None:
    assert accept_slots("మా బడ్జెట్ రెండు లక్షలు.", LanguageCode.TELUGU, [Slot.BUDGET]) == frozenset()


def test_accept_slots_keeps_only_corroborated_claims() -> None:
    accepted = accept_slots(
        "Our budget is around two lakh rupees.",
        LanguageCode.ENGLISH,
        [Slot.BUDGET, Slot.BUSINESS_TYPE],
    )
    assert accepted == frozenset({Slot.BUDGET})


# --------------------------------------------------------------------------------------
# The regression: a constant model must not steer the reply
# --------------------------------------------------------------------------------------


def test_stalling_is_still_an_answerable_objection() -> None:
    """The premise of the regression below. If this changes, re-read that test."""

    from pitchbot.conversation.planning import Intent

    assert Intent.STALLING in ANSWERABLE_OBJECTIONS


@pytest.mark.asyncio
async def test_a_model_can_never_make_the_agent_answer_an_objection() -> None:
    """The live defect: every reply became "answer the stall", including at agreement.

    The model here is the measured one - it answers the same thing regardless of input.
    Whatever it says, the plan must come from the rules' reading of the buyer's words.
    """

    always_the_same = MockModelAdapter([_completion("business_type") for _ in range(4)])
    source = ModelTurnUnderstanding(always_the_same)

    for text in (
        "Yes, let us go ahead with the proposal.",
        "We sell handmade leather bags to boutiques.",
        "Our budget is around two lakh rupees.",
        "Can you launch before Diwali?",
    ):
        understanding = await source.understand(text, LanguageCode.ENGLISH, [])
        assert understanding is not None
        assert understanding.intent is None, "the model must not supply a stance"
        plan = plan_reply(understanding, asked_counts={}, max_asks=2)
        assert plan.objection is None, f"a model answer turned {text!r} into an objection"


@pytest.mark.asyncio
async def test_a_model_claim_the_turn_does_not_support_is_dropped() -> None:
    """Phi claimed `business_type` for a hedge, which would retire the question forever."""

    source = ModelTurnUnderstanding(MockModelAdapter([_completion("business_type")]))

    understanding = await source.understand(
        "I am just looking around for now.",
        LanguageCode.ENGLISH,
        [],
    )

    assert understanding is not None
    assert understanding.filled_now == frozenset()
    assert Slot.BUSINESS_TYPE not in understanding.known_slots


def test_the_trusted_set_is_a_subset_of_the_languages_the_agent_speaks() -> None:
    """Trusting a language the planner cannot reply in would be trust with no use."""

    from pitchbot.conversation.planning import supported_languages

    assert TRUSTED_LANGUAGES <= set(supported_languages())
