"""Who is allowed to use the CPU right now.

Two local models on one CPU is not two workers; it is one resource with two claimants. The
measurements are unambiguous:

===========================================  ==========================================
Measurement                                  Result
===========================================  ==========================================
Turn path alone                              p50 453 ms, p95 494 ms
Turn path while the slow lane generates      p50 1,504 ms, p95 1,755 ms  (**3.37x**)
Slow lane capped to 4 threads                p50 1,582 ms  (**3.59x** - worse)
Slow lane capped to 2 threads                p50 2,146 ms  (**4.87x** - worse still)
Stopping the slow lane                       **0.1 ms**
First turn after stopping                    241 ms vs a 247 ms idle baseline (**0.98x**)
===========================================  ==========================================

Two things follow, and they are the whole design.

**Capping threads is not the mitigation.** It is the intuitive fix and it makes things
worse: the same work spread over fewer cores takes longer, so the slow lane overlaps *more*
turns, not fewer. Measured on a 16-core box; the 8-core target has less room, not more.

**Preemption is free, so exclusion is affordable.** Stopping costs 0.1 ms and the very next
turn is already at baseline. The lanes do not need to share cores gracefully - they need to
not run at the same time, and the switch is cheap enough to do on every single turn.

So this scheduler is not a thread pool or a priority queue. It is one flag, and the rule
that the slow lane must look at it between tokens. The fast lane never waits: it raises the
flag and proceeds, because waiting for the slow lane to notice would spend real turn-path
latency to avoid at most one token of overlap.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LaneStats:
    """What the scheduler has actually done, for tests and for the health endpoint."""

    turns: int
    yields: int
    deliberations_started: int
    deliberations_completed: int
    deliberations_preempted: int


@dataclass
class LaneScheduler:
    """Mutual exclusion between the turn path and background deliberation.

    Not a lock. A lock would make the fast lane *wait* for the slow lane, which inverts the
    priority that matters: the buyer is listening and the deliberation is not. This grants
    the fast lane the CPU immediately and asks the slow lane to stand down.
    """

    _turn_depth: int = 0
    _turns: int = 0
    _yields: int = 0
    _started: int = 0
    _completed: int = 0
    _preempted: int = 0
    _idle_waiters: list[asyncio.Future[None]] = field(default_factory=list)

    # -- fast lane ----------------------------------------------------------------------

    @asynccontextmanager
    async def turn(self) -> AsyncIterator[None]:
        """Hold the CPU for one buyer turn.

        Re-entrant by depth so a turn that internally calls another guarded section does
        not release the claim early - releasing halfway through would let the slow lane
        resume while the buyer is still waiting, which is the exact failure this prevents.
        """

        self._turn_depth += 1
        if self._turn_depth == 1:
            self._turns += 1
        try:
            yield
        finally:
            self._turn_depth -= 1
            if self._turn_depth == 0:
                self._release_idle_waiters()

    @property
    def turn_in_flight(self) -> bool:
        return self._turn_depth > 0

    # -- slow lane ----------------------------------------------------------------------

    def should_yield(self) -> bool:
        """Whether the slow lane must stop now. Called between tokens.

        Counting here rather than at the stop site means the statistic reflects how often
        the slow lane actually looked, which is what makes a missing check visible in a
        test instead of merely making the product slower in production.
        """

        if self._turn_depth > 0:
            self._yields += 1
            return True
        return False

    async def wait_until_idle(self, timeout: float | None = None) -> bool:
        """Block until no turn is in flight. Returns whether it became idle.

        The slow lane uses this instead of polling, so an idle conversation costs no CPU
        at all - which matters because the whole point is to leave the cores alone.
        """

        if self._turn_depth == 0:
            return True
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[None] = loop.create_future()
        self._idle_waiters.append(waiter)
        try:
            await asyncio.wait_for(asyncio.shield(waiter), timeout)
        except TimeoutError:
            return False
        finally:
            if waiter in self._idle_waiters:
                self._idle_waiters.remove(waiter)
        return True

    def _release_idle_waiters(self) -> None:
        waiters, self._idle_waiters = self._idle_waiters, []
        for waiter in waiters:
            if not waiter.done():
                waiter.set_result(None)

    # -- bookkeeping --------------------------------------------------------------------

    def deliberation_started(self) -> None:
        self._started += 1

    def deliberation_finished(self, *, preempted: bool) -> None:
        if preempted:
            self._preempted += 1
        else:
            self._completed += 1

    def stats(self) -> LaneStats:
        return LaneStats(
            turns=self._turns,
            yields=self._yields,
            deliberations_started=self._started,
            deliberations_completed=self._completed,
            deliberations_preempted=self._preempted,
        )


class YieldBudget:
    """A stop condition that combines the scheduler with a wall-clock cap.

    The scheduler answers 'must I stop for the buyer'. This also answers 'have I spent
    long enough' - because a deliberation that never finishes is not free even when nobody
    is waiting: it holds the model's memory and keeps the cores warm. Measured, a complete
    site plan is 118 tokens in 9.9 s, so a cap in tens of seconds is generous rather than
    tight.
    """

    def __init__(
        self,
        scheduler: LaneScheduler,
        *,
        max_seconds: float,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_seconds <= 0:
            raise ValueError("max_seconds must be positive")
        self._scheduler = scheduler
        self._max_seconds = max_seconds
        self._monotonic = monotonic
        self._started = monotonic()
        self.preempted_by_turn = False
        self.exhausted = False

    def should_stop(self) -> bool:
        if self._scheduler.should_yield():
            self.preempted_by_turn = True
            return True
        if self._monotonic() - self._started >= self._max_seconds:
            self.exhausted = True
            return True
        return False

    @property
    def elapsed(self) -> float:
        return self._monotonic() - self._started


__all__ = ["LaneScheduler", "LaneStats", "YieldBudget"]
