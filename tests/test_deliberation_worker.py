"""The slow lane: what it produces, and everything it refuses to do.

Most of these tests are about refusal, which is the point. A background model that runs when
it should not costs the turn path 3.37x; one that writes a half-formed plan costs the buyer
a confident answer about a business we have not finished hearing about.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from pitchbot.adapters.contracts import StructuredCompletion
from pitchbot.adapters.errors import DeliberationPreempted, TransientAdapterError
from pitchbot.deliberation.briefing import Briefing, Topic
from pitchbot.deliberation.deliberator import (
    SCHEMA_NAME,
    BackgroundDeliberation,
    Deliberator,
)
from pitchbot.deliberation.lanes import LaneScheduler

PLAN = {
    "competitors": ["template shops", "marketplaces", "custom agencies"],
    "differentiator": "Wholesale-first ordering for boutiques.",
    "pages": ["Home", "Catalogue", "Reorder", "Contact"],
}


_DEFAULT = object()


class FakeModel:
    """A model that answers on demand, and can be told to check ``should_stop`` first."""

    def __init__(
        self,
        value: object = _DEFAULT,
        *,
        error: Exception | None = None,
        honour_stop: bool = False,
    ) -> None:
        # Typed loosely on purpose: several tests hand this a value that a real model could
        # never return, to prove the mapping refuses it rather than trusting the schema.
        self.value: Any = PLAN if value is _DEFAULT else value
        self.error = error
        self.honour_stop = honour_stop
        self.calls: list[str] = []
        self.saw_stop_callback = False

    async def complete_structured(
        self,
        instruction: str,
        schema_name: str,
        *,
        should_stop: Callable[[], bool] | None = None,
    ) -> StructuredCompletion:
        self.calls.append(schema_name)
        self.saw_stop_callback = should_stop is not None
        if self.honour_stop and should_stop is not None and should_stop():
            raise DeliberationPreempted("stopped")
        if self.error is not None:
            raise self.error
        return StructuredCompletion(value=self.value, model_version="fake")


def _briefing(topics: int = 2) -> Briefing:
    briefing = Briefing()
    values = [
        (Topic.BUSINESS_TYPE, "wholesaler of handmade leather bags"),
        (Topic.REQUESTED_FEATURES, "wholesale pricing tiers"),
        (Topic.BUDGET_STATED, "about two lakh rupees"),
        (Topic.TIMELINE, "before Diwali"),
    ]
    for topic, value in values[:topics]:
        briefing.observe(topic, value)
    return briefing


# --------------------------------------------------------------------------------------
# What it produces
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_plan_is_stored_against_the_picture_it_was_drawn_from() -> None:
    briefing = _briefing()
    scheduler = LaneScheduler()
    deliberator = Deliberator(FakeModel(), scheduler, model_id="fake")

    result = await deliberator.deliberate(briefing)

    assert result is not None
    assert result.derived_from_version == briefing.version
    assert briefing.current_deliberation() is result
    assert result.plan.pages == ("Home", "Catalogue", "Reorder", "Contact")


@pytest.mark.asyncio
async def test_it_answers_only_the_site_plan_schema() -> None:
    model = FakeModel()
    await Deliberator(model, LaneScheduler()).deliberate(_briefing())

    assert model.calls == [SCHEMA_NAME]


@pytest.mark.asyncio
async def test_it_always_offers_the_model_a_way_to_stop() -> None:
    """Without this the 3.37x penalty applies for the whole of a ten-second generation."""

    model = FakeModel()
    await Deliberator(model, LaneScheduler()).deliberate(_briefing())

    assert model.saw_stop_callback


# --------------------------------------------------------------------------------------
# When it refuses to run
# --------------------------------------------------------------------------------------


def test_it_will_not_start_while_a_turn_is_in_flight() -> None:
    scheduler = LaneScheduler()
    scheduler._turn_depth = 1

    assert not Deliberator(FakeModel(), scheduler).should_deliberate(_briefing())


def test_it_will_not_plan_from_a_single_fact() -> None:
    """One fact yields a template any buyer would get, which looks specific and is not."""

    assert not Deliberator(FakeModel(), LaneScheduler()).should_deliberate(_briefing(topics=1))


def test_it_will_not_redo_work_that_is_still_current() -> None:
    briefing = _briefing()
    scheduler = LaneScheduler()
    deliberator = Deliberator(FakeModel(), scheduler)

    assert deliberator.should_deliberate(briefing)

    asyncio.run(deliberator.deliberate(briefing))

    assert not deliberator.should_deliberate(briefing)


def test_it_will_plan_again_once_the_buyer_says_something_new() -> None:
    briefing = _briefing()
    deliberator = Deliberator(FakeModel(), LaneScheduler())
    asyncio.run(deliberator.deliberate(briefing))

    briefing.observe(Topic.TIMELINE, "before Diwali")

    assert deliberator.should_deliberate(briefing)


@pytest.mark.asyncio
async def test_nothing_observed_means_nothing_to_think_about() -> None:
    assert await Deliberator(FakeModel(), LaneScheduler()).deliberate(Briefing()) is None


# --------------------------------------------------------------------------------------
# When it gives up
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_preempted_plan_is_discarded_whole() -> None:
    """A plan with competitors and no pages is the half-formed conclusion to avoid."""

    briefing = _briefing()
    scheduler = LaneScheduler()
    scheduler._turn_depth = 1
    deliberator = Deliberator(FakeModel(honour_stop=True), scheduler)

    assert await deliberator.deliberate(briefing) is None
    assert briefing.deliberation is None
    assert scheduler.stats().deliberations_preempted == 1


@pytest.mark.asyncio
async def test_a_model_failure_leaves_the_previous_plan_alone() -> None:
    briefing = _briefing()
    good = Deliberator(FakeModel(), LaneScheduler())
    await good.deliberate(briefing)
    before = briefing.deliberation

    briefing.observe(Topic.TIMELINE, "before Diwali")
    broken = Deliberator(FakeModel(error=TransientAdapterError("busy")), LaneScheduler())

    assert await broken.deliberate(briefing) is None
    assert briefing.deliberation is before


@pytest.mark.parametrize(
    "value",
    [
        {"competitors": [], "differentiator": "d", "pages": ["Home"]},
        {"competitors": ["a"], "differentiator": "", "pages": ["Home"]},
        {"competitors": ["a"], "differentiator": "d", "pages": []},
        {"competitors": ["  "], "differentiator": "d", "pages": ["Home"]},
        "not an object",
        None,
    ],
)
@pytest.mark.asyncio
async def test_an_incomplete_plan_is_not_a_plan(value: object) -> None:
    briefing = _briefing()

    result = await Deliberator(FakeModel(value), LaneScheduler()).deliberate(briefing)

    assert result is None
    assert briefing.deliberation is None


@pytest.mark.asyncio
async def test_a_plan_overtaken_while_it_was_being_written_is_dropped() -> None:
    """Two can be in flight after a preemption; the loser must not be returned."""

    briefing = _briefing()
    newer = Deliberator(FakeModel(), LaneScheduler())
    briefing.observe(Topic.TIMELINE, "before Diwali")
    await newer.deliberate(briefing)

    stale = Briefing()
    stale.observe(Topic.BUSINESS_TYPE, "leather bags")
    stale.observe(Topic.BUDGET_STATED, "two lakh")

    # Simulate an old deliberation completing against the newer briefing.
    old = await Deliberator(FakeModel(), LaneScheduler()).deliberate(stale)
    assert old is not None
    assert not briefing.conclude(type(old)(plan=old.plan, derived_from_version=1, model_id="fake"))


# --------------------------------------------------------------------------------------
# Task lifetime
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_background_task_starts_only_once() -> None:
    briefing = _briefing()
    background = BackgroundDeliberation(Deliberator(FakeModel(), LaneScheduler()), briefing)

    assert background.maybe_start()
    assert not background.maybe_start()

    await background.stop()


@pytest.mark.asyncio
async def test_stopping_leaves_no_generation_running() -> None:
    briefing = _briefing()
    background = BackgroundDeliberation(Deliberator(FakeModel(), LaneScheduler()), briefing)
    background.maybe_start()

    await background.stop(timeout=2.0)

    assert not background.is_running


@pytest.mark.asyncio
async def test_stopping_something_that_never_started_is_harmless() -> None:
    background = BackgroundDeliberation(Deliberator(FakeModel(), LaneScheduler()), Briefing())

    await background.stop()

    assert not background.is_running


def test_a_deliberator_needs_a_positive_time_budget() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        Deliberator(FakeModel(), LaneScheduler(), max_seconds=0.0)
