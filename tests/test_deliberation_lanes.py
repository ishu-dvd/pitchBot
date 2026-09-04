"""Who gets the CPU, and how fast the slow lane lets go.

The measurement these tests defend: running both lanes together cost the turn path 3.37x,
and capping the background model's threads made it *worse* (3.59x at four threads, 4.87x at
two). The only mitigation that worked was not running them together, which is affordable
only because stopping costs 0.1 ms.
"""

from __future__ import annotations

import asyncio

import pytest

from pitchbot.deliberation.lanes import LaneScheduler, YieldBudget


class _Clock:
    """A hand-cranked monotonic clock, so a wall-clock cap is testable without sleeping."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


# --------------------------------------------------------------------------------------
# Exclusion
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_slow_lane_is_asked_to_stop_while_a_turn_runs() -> None:
    scheduler = LaneScheduler()

    assert not scheduler.should_yield()
    async with scheduler.turn():
        assert scheduler.should_yield()
    assert not scheduler.should_yield()


@pytest.mark.asyncio
async def test_a_nested_turn_does_not_release_the_claim_early() -> None:
    """Releasing halfway through would resume the slow lane while the buyer still waits."""

    scheduler = LaneScheduler()

    async with scheduler.turn():
        async with scheduler.turn():
            assert scheduler.turn_in_flight
        assert scheduler.turn_in_flight, "the inner turn must not release the outer claim"
    assert not scheduler.turn_in_flight


@pytest.mark.asyncio
async def test_the_claim_is_released_even_when_the_turn_raises() -> None:
    scheduler = LaneScheduler()

    with pytest.raises(RuntimeError):
        async with scheduler.turn():
            raise RuntimeError("the buyer hung up")

    assert not scheduler.turn_in_flight


@pytest.mark.asyncio
async def test_yields_are_counted_so_a_missing_check_is_visible() -> None:
    scheduler = LaneScheduler()

    async with scheduler.turn():
        scheduler.should_yield()
        scheduler.should_yield()

    assert scheduler.stats().yields == 2
    assert scheduler.stats().turns == 1


# --------------------------------------------------------------------------------------
# Waiting for idle
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_waiting_when_already_idle_returns_at_once() -> None:
    assert await LaneScheduler().wait_until_idle(timeout=0.01)


@pytest.mark.asyncio
async def test_a_waiter_is_released_when_the_turn_ends() -> None:
    scheduler = LaneScheduler()
    released = asyncio.Event()

    async def waiter() -> None:
        await scheduler.wait_until_idle(timeout=2.0)
        released.set()

    async with scheduler.turn():
        task = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        assert not released.is_set()

    await asyncio.wait_for(task, timeout=2.0)
    assert released.is_set()


@pytest.mark.asyncio
async def test_waiting_gives_up_rather_than_hanging_forever() -> None:
    scheduler = LaneScheduler()

    async with scheduler.turn():
        assert not await scheduler.wait_until_idle(timeout=0.01)


# --------------------------------------------------------------------------------------
# The budget
# --------------------------------------------------------------------------------------


def test_a_budget_stops_for_a_turn_and_says_why() -> None:
    scheduler = LaneScheduler()
    budget = YieldBudget(scheduler, max_seconds=60.0, monotonic=_Clock())

    assert not budget.should_stop()

    scheduler._turn_depth = 1  # what `async with scheduler.turn()` does
    assert budget.should_stop()
    assert budget.preempted_by_turn
    assert not budget.exhausted


def test_a_budget_stops_when_it_runs_out_of_time_and_says_why() -> None:
    clock = _Clock()
    budget = YieldBudget(LaneScheduler(), max_seconds=45.0, monotonic=clock)

    clock.now = 44.9
    assert not budget.should_stop()

    clock.now = 45.0
    assert budget.should_stop()
    assert budget.exhausted
    assert not budget.preempted_by_turn


def test_a_budget_must_have_a_positive_cap() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        YieldBudget(LaneScheduler(), max_seconds=0.0)


def test_the_buyer_wins_over_the_clock() -> None:
    """Both conditions true at once must report the turn, because that is the urgent one."""

    clock = _Clock()
    scheduler = LaneScheduler()
    budget = YieldBudget(scheduler, max_seconds=1.0, monotonic=clock)

    scheduler._turn_depth = 1
    clock.now = 100.0

    assert budget.should_stop()
    assert budget.preempted_by_turn
    assert not budget.exhausted


# --------------------------------------------------------------------------------------
# Bookkeeping
# --------------------------------------------------------------------------------------


def test_preempted_and_completed_deliberations_are_counted_separately() -> None:
    """Preemption is normal operation; conflating it with failure hides real failures."""

    scheduler = LaneScheduler()
    scheduler.deliberation_started()
    scheduler.deliberation_finished(preempted=True)
    scheduler.deliberation_started()
    scheduler.deliberation_finished(preempted=False)

    stats = scheduler.stats()
    assert stats.deliberations_started == 2
    assert stats.deliberations_preempted == 1
    assert stats.deliberations_completed == 1
