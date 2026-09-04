"""The shared state between the two lanes.

The design claim these tests defend is that overwriting and misconception are *impossible*
rather than prevented. If any of these can be made to fail by adding a method to
``Briefing``, that method has broken the guarantee.
"""

from __future__ import annotations

import pytest

from pitchbot.conversation.planning import ASK_ORDER
from pitchbot.deliberation.briefing import (
    MAX_OBSERVATIONS,
    MAX_VALUE_CHARS,
    Briefing,
    BriefingOwnershipError,
    Deliberation,
    Observation,
    SitePlan,
    Topic,
    describe,
)


def _plan(differentiator: str = "Wholesale-first, boutique-friendly.") -> SitePlan:
    return SitePlan(
        competitors=("generic template shops", "marketplaces", "custom agencies"),
        differentiator=differentiator,
        pages=("Home", "Catalogue", "Reorder"),
    )


def _deliberation(version: int, differentiator: str = "d") -> Deliberation:
    return Deliberation(
        plan=_plan(differentiator),
        derived_from_version=version,
        model_id="test-model",
    )


# --------------------------------------------------------------------------------------
# Ownership
# --------------------------------------------------------------------------------------


def test_the_topics_the_slow_lane_reasons_about_match_the_slots_the_agent_asks_for() -> None:
    """A divergence would let the slow lane plan around a topic nobody can ask about."""

    assert {topic.value for topic in Topic} == {slot.value for slot in ASK_ORDER}


def test_the_briefing_has_no_method_that_writes_both_lanes_fields() -> None:
    """The ownership guarantee is structural: the mistake must not be available.

    Asserted on the public surface rather than by inspection, so adding a convenience
    method that writes both fields fails here instead of silently reintroducing the race.
    """

    writers = {
        name
        for name in dir(Briefing)
        if not name.startswith("_") and callable(getattr(Briefing, name, None))
    }
    assert writers == {
        "observe",  # fast lane only
        "conclude",  # slow lane only
        "current_deliberation",
        "latest_by_topic",
        "known_topics",
    }


def test_observing_never_touches_the_deliberation() -> None:
    briefing = Briefing()
    briefing.observe(Topic.BUSINESS_TYPE, "leather bags")
    stored = _deliberation(briefing.version)
    assert briefing.conclude(stored)

    briefing.observe(Topic.BUDGET_STATED, "two lakh")

    assert briefing.deliberation is stored, "an observation must not erase a conclusion"


def test_concluding_never_touches_the_observations() -> None:
    briefing = Briefing()
    briefing.observe(Topic.BUSINESS_TYPE, "leather bags")
    before = briefing.observations

    briefing.conclude(_deliberation(briefing.version))

    assert briefing.observations == before


# --------------------------------------------------------------------------------------
# Misconception: a conclusion drawn from a picture that has changed
# --------------------------------------------------------------------------------------


def test_a_conclusion_is_current_while_nothing_new_has_been_said() -> None:
    briefing = Briefing()
    briefing.observe(Topic.BUSINESS_TYPE, "leather bags")
    briefing.conclude(_deliberation(briefing.version))

    assert not briefing.is_stale
    assert briefing.current_deliberation() is not None


def test_a_conclusion_stops_being_current_the_moment_the_buyer_adds_something() -> None:
    """The plan was built for a different business; using it anyway is the misconception."""

    briefing = Briefing()
    briefing.observe(Topic.BUSINESS_TYPE, "leather bags")
    briefing.conclude(_deliberation(briefing.version))

    briefing.observe(Topic.BUDGET_STATED, "two lakh rupees")

    assert briefing.is_stale
    assert briefing.current_deliberation() is None
    assert briefing.deliberation is not None, "stale is readable, just not current"


def test_a_conclusion_cannot_claim_to_know_more_than_was_ever_observed() -> None:
    briefing = Briefing()
    briefing.observe(Topic.BUSINESS_TYPE, "leather bags")

    with pytest.raises(BriefingOwnershipError):
        briefing.conclude(_deliberation(briefing.version + 5))


# --------------------------------------------------------------------------------------
# Overwriting: two conclusions in flight
# --------------------------------------------------------------------------------------


def test_a_later_finishing_but_older_conclusion_is_dropped() -> None:
    """A deliberation takes ~10 s, so two can be in flight; the last to finish is not newest."""

    briefing = Briefing()
    briefing.observe(Topic.BUSINESS_TYPE, "leather bags")
    older = briefing.version
    briefing.observe(Topic.BUDGET_STATED, "two lakh")
    newer = briefing.version

    assert briefing.conclude(_deliberation(newer, "newer"))
    assert not briefing.conclude(_deliberation(older, "older"))

    current = briefing.deliberation
    assert current is not None
    assert current.plan.differentiator == "newer"


def test_a_conclusion_from_the_same_version_replaces_the_previous_one() -> None:
    briefing = Briefing()
    briefing.observe(Topic.BUSINESS_TYPE, "leather bags")

    assert briefing.conclude(_deliberation(briefing.version, "first"))
    assert briefing.conclude(_deliberation(briefing.version, "second"))

    current = briefing.deliberation
    assert current is not None
    assert current.plan.differentiator == "second"


# --------------------------------------------------------------------------------------
# Bounds
# --------------------------------------------------------------------------------------


def test_the_version_never_repeats_even_after_history_is_dropped() -> None:
    """Dropping history may make a plan look stale; it must never make one look current."""

    briefing = Briefing()
    for index in range(MAX_OBSERVATIONS + 20):
        briefing.observe(Topic.BUSINESS_TYPE, f"value {index}")

    assert briefing.version == MAX_OBSERVATIONS + 20
    assert len(briefing.observations) == MAX_OBSERVATIONS


def test_a_pasted_essay_cannot_become_the_deliberation_prompt() -> None:
    briefing = Briefing()
    stored = briefing.observe(Topic.BUSINESS_TYPE, "x" * (MAX_VALUE_CHARS * 3))

    assert len(stored.value) == MAX_VALUE_CHARS


def test_an_empty_observation_is_refused() -> None:
    with pytest.raises(ValueError, match="not an observation"):
        Observation(topic=Topic.BUDGET_STATED, value="   ", version=1)


def test_a_plan_with_no_pages_is_refused() -> None:
    with pytest.raises(ValueError, match="not a plan"):
        SitePlan(competitors=("a",), differentiator="d", pages=())


# --------------------------------------------------------------------------------------
# The prompt the slow lane reads
# --------------------------------------------------------------------------------------


def test_the_same_facts_always_describe_the_same_way() -> None:
    """A deliberation is only reproducible if its prompt is."""

    first = Briefing()
    first.observe(Topic.BUDGET_STATED, "two lakh")
    first.observe(Topic.BUSINESS_TYPE, "leather bags")

    second = Briefing()
    second.observe(Topic.BUSINESS_TYPE, "leather bags")
    second.observe(Topic.BUDGET_STATED, "two lakh")

    assert describe(first.observations) == describe(second.observations)


def test_only_the_latest_value_per_topic_is_described() -> None:
    briefing = Briefing()
    briefing.observe(Topic.BUDGET_STATED, "one lakh")
    briefing.observe(Topic.BUDGET_STATED, "two lakh")

    described = describe(briefing.observations)

    assert "two lakh" in described
    assert "one lakh" not in described


def test_nothing_observed_describes_as_nothing() -> None:
    assert describe(Briefing().observations) == ""
