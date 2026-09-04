"""The slow lane: think about the deal while nobody is waiting for an answer.

This is the half of the two-model design that the fast lane cannot be: it takes 9.9 s to
produce a site plan on Phi-3.5-mini and it is worth every second of that, because the plan
is something no amount of rules can produce - three competitors, a differentiator, and the
pages the buyer's site actually needs.

It runs under three rules, each of which is a measurement rather than a preference.

**Never while the buyer is waiting.** Running alongside the turn path costs that path
3.37x. The generator checks :class:`~pitchbot.deliberation.lanes.YieldBudget` between
tokens and abandons the answer the moment a turn starts. An abandoned deliberation is
discarded whole - there is no partial plan, because a plan with competitors but no pages
is exactly the half-formed conclusion this design exists to avoid. Measured: acting on a
stream at 2.9 s gets you ``competitors`` and neither ``differentiator`` (5.5 s) nor
``pages`` (6.7 s).

**Never twice at once.** One deliberation at a time per briefing. The model adapter
serialises anyway, so a second concurrent request would queue behind the first and finish
against an even staler picture.

**Never authoritative.** A plan is what *we* would propose. It can never fill a
qualification slot, because the buyer did not say it. That distinction is enforced by the
briefing's ownership rule - this module writes only :meth:`Briefing.conclude` and has no
way to write an observation.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from pitchbot.adapters.contracts import StructuredCompletion
from pitchbot.adapters.errors import AdapterError, DeliberationPreempted
from pitchbot.deliberation.briefing import (
    Briefing,
    Deliberation,
    SitePlan,
    Topic,
    describe,
)
from pitchbot.deliberation.lanes import LaneScheduler, YieldBudget

logger = logging.getLogger(__name__)

SCHEMA_NAME = "site-plan-v1"

MAX_PLAN_SECONDS = 45.0
"""Wall-clock cap on one deliberation.

A complete plan measured 9.9 s, so this is roughly four times the observed cost rather than
a tight budget. It exists because an unbounded background generation is not free even with
nobody waiting: it holds the model and keeps cores warm, which is what the whole design is
trying to give back.
"""

MIN_TOPICS = 2
"""How much must be known before thinking is worth the CPU.

With one fact the plan is a generic template that any buyer would get, which is worse than
no plan because it *looks* specific. Two is the point at which the business and at least
one requirement, budget, or deadline are known.
"""

_INSTRUCTION = (
    "You are planning a website for a business. Using only the facts given, name the kinds "
    "of site this business competes with, state in one sentence what would make their site "
    "different, and list the pages the site needs.\n"
    "Do not invent prices, dates, or commitments. Do not address the buyer.\n\n"
    "Facts:\n"
)


class PreemptibleModel(Protocol):
    """A model that can be told to stop between tokens.

    A narrower protocol than ``ModelAdapter`` rather than a widening of it, following the
    same pattern as ``RetunableTranscriber``: only the slow lane needs abandonable
    generation, and requiring it of every model adapter would make the mocks carry a
    capability that nothing else exercises.
    """

    async def complete_structured(
        self,
        instruction: str,
        schema_name: str,
        *,
        should_stop: Callable[[], bool] | None = None,
    ) -> StructuredCompletion: ...


class Deliberator:
    """Runs the slow lane against one briefing, when it is allowed to."""

    def __init__(
        self,
        model: PreemptibleModel,
        scheduler: LaneScheduler,
        *,
        model_id: str = "unknown",
        max_seconds: float = MAX_PLAN_SECONDS,
        min_topics: int = MIN_TOPICS,
    ) -> None:
        if max_seconds <= 0:
            raise ValueError("max_seconds must be positive")
        if min_topics < 1:
            raise ValueError("min_topics must be at least 1")
        self._model = model
        self._scheduler = scheduler
        self._model_id = model_id
        self._max_seconds = max_seconds
        self._min_topics = min_topics
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def should_deliberate(self, briefing: Briefing) -> bool:
        """Whether thinking now would produce anything the briefing does not already have."""

        if self._running or self._scheduler.turn_in_flight:
            return False
        if len(briefing.known_topics()) < self._min_topics:
            return False
        return briefing.current_deliberation() is None

    async def deliberate(self, briefing: Briefing) -> Deliberation | None:
        """Produce a plan for the briefing as it stands, or nothing.

        Returns ``None`` for every ordinary reason a background task does not finish -
        preempted, out of time, model unavailable, model confused. None of them is an error
        the caller must handle, and none of them changes what the buyer hears this turn.
        """

        if self._running:
            return None
        # Read the version *before* generating: the plan describes this picture, and the
        # buyer may add to it while we think. Stamping it afterwards would silently claim
        # the plan accounted for facts it never saw - the misconception this design forbids.
        derived_from = briefing.version
        facts = describe(briefing.observations)
        if not facts:
            return None

        self._running = True
        self._scheduler.deliberation_started()
        budget = YieldBudget(self._scheduler, max_seconds=self._max_seconds)
        try:
            completion = await self._model.complete_structured(
                _INSTRUCTION + facts,
                SCHEMA_NAME,
                should_stop=budget.should_stop,
            )
        except DeliberationPreempted:
            logger.debug("deliberation preempted after %.2fs", budget.elapsed)
            self._scheduler.deliberation_finished(preempted=True)
            return None
        except (AdapterError, RuntimeError, ValueError):
            logger.warning(
                "deliberation failed; the briefing keeps its previous plan", exc_info=True
            )
            self._scheduler.deliberation_finished(preempted=True)
            return None
        finally:
            self._running = False

        plan = _to_plan(completion.value)
        if plan is None:
            self._scheduler.deliberation_finished(preempted=True)
            return None
        deliberation = Deliberation(
            plan=plan,
            derived_from_version=derived_from,
            model_id=self._model_id,
        )
        stored = briefing.conclude(deliberation)
        self._scheduler.deliberation_finished(preempted=False)
        if not stored:
            # Overtaken by a newer plan that finished first. Dropping it is the point of
            # the version check; returning it anyway would let a caller act on the loser.
            logger.debug("deliberation from version %d was overtaken", derived_from)
            return None
        return deliberation


class BackgroundDeliberation:
    """Owns the asyncio task, so the caller never has to.

    Separate from :class:`Deliberator` because task lifetime and generation are different
    concerns and mixing them is how a background task ends up outliving the session that
    started it.
    """

    def __init__(self, deliberator: Deliberator, briefing: Briefing) -> None:
        self._deliberator = deliberator
        self._briefing = briefing
        self._task: asyncio.Task[Deliberation | None] | None = None

    def maybe_start(self) -> bool:
        """Start thinking if it is worth it and nothing is already running."""

        if self._task is not None and not self._task.done():
            return False
        if not self._deliberator.should_deliberate(self._briefing):
            return False
        self._task = asyncio.create_task(self._deliberator.deliberate(self._briefing))
        return True

    async def stop(self, timeout: float = 5.0) -> None:
        """Cancel and await, so no generation outlives the conversation."""

        task = self._task
        if task is None or task.done():
            self._task = None
            return
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout)
        except (asyncio.CancelledError, TimeoutError):
            pass
        finally:
            self._task = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()


def _to_plan(value: object) -> SitePlan | None:
    """Turn the model's answer into a plan, or nothing.

    Constrained decoding guarantees the shape, so this is not defensive parsing - it is the
    boundary where free-text content is bounded before it can reach a buyer. A field the
    model left empty makes the whole plan worthless rather than partially useful, for the
    same reason a partial stream is not usable.
    """

    if not isinstance(value, dict):
        return None
    competitors = _strings(value.get("competitors"))
    pages = _strings(value.get("pages"))
    differentiator = str(value.get("differentiator", "")).strip()
    if not competitors or not pages or not differentiator:
        return None
    return SitePlan(competitors=competitors, differentiator=differentiator, pages=pages)


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def observable_topics() -> frozenset[Topic]:
    """The topics a deliberation reasons about, for the coverage test."""

    return frozenset(Topic)


__all__ = [
    "MAX_PLAN_SECONDS",
    "MIN_TOPICS",
    "SCHEMA_NAME",
    "BackgroundDeliberation",
    "Deliberator",
    "PreemptibleModel",
    "observable_topics",
]
