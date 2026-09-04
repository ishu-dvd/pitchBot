"""The slow lane, reached the way a deployment reaches it.

Everything in ``test_deliberation_worker.py`` drives the deliberator directly. These tests
go through :class:`SimulatorService`, which is what actually runs in a deployment, and they
exist because two of the defects they cover were only visible from here: a background task
that nothing ever started, and per-session state that nothing ever freed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import pytest

from pitchbot.adapters.contracts import StructuredCompletion
from pitchbot.adapters.mocks import MockModelAdapter
from pitchbot.conversation import ConversationEngine
from pitchbot.deliberation import Topic
from pitchbot.domain import LanguageCode
from pitchbot.simulator.models import CreateSessionRequest, TurnRequest
from pitchbot.simulator.service import SimulatorService

PLAN = {
    "competitors": ["template shops", "marketplaces", "custom agencies"],
    "differentiator": "Wholesale-first ordering for boutiques.",
    "pages": ["Home", "Catalogue", "Reorder"],
}


class SlowLaneModel:
    """Stands in for the background model, and records that it was asked."""

    def __init__(self, *, block: asyncio.Event | None = None) -> None:
        self.calls = 0
        self._block = block

    async def complete_structured(
        self,
        instruction: str,
        schema_name: str,
        *,
        should_stop: Callable[[], bool] | None = None,
    ) -> StructuredCompletion:
        self.calls += 1
        if self._block is not None:
            await self._block.wait()
        value: Any = PLAN
        return StructuredCompletion(value=value, model_version="fake")


async def _qualified(service: SimulatorService) -> Any:
    """A session that has said enough for a plan to be worth drawing."""

    session = service.create_session(CreateSessionRequest(lead_ref="deliberation"))
    for text in (
        "We run a clothing store selling shirts.",
        "We need online payments and a cart.",
    ):
        await service.process_turn(
            session.session_id,
            TurnRequest(text=text, language=LanguageCode.ENGLISH),
        )
    return session


# --------------------------------------------------------------------------------------
# It is actually reachable
# --------------------------------------------------------------------------------------


def test_no_deliberation_model_means_no_slow_lane() -> None:
    """The default is off, and says so rather than pretending to have planned."""

    service = SimulatorService()

    assert not service.deliberation_available
    assert service.site_plan(uuid4()) is None


@pytest.mark.asyncio
async def test_a_configured_slow_lane_is_asked_after_a_turn() -> None:
    """The defect this catches: a deliberator that nothing in the product ever started."""

    model = SlowLaneModel()
    service = SimulatorService(deliberation_model=model, deliberation_model_id="fake")
    session = await _qualified(service)

    for _ in range(50):
        await asyncio.sleep(0.01)
        if model.calls:
            break

    assert model.calls >= 1
    assert service.site_plan(session.session_id) is not None


@pytest.mark.asyncio
async def test_the_plan_renders_as_an_outline_and_as_slides() -> None:
    model = SlowLaneModel()
    service = SimulatorService(deliberation_model=model, deliberation_model_id="fake")
    session = await _qualified(service)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if service.site_plan(session.session_id) is not None:
            break

    outline = service.site_outline(session.session_id, LanguageCode.ENGLISH)
    slides = service.deck_preview_slides(session.session_id, LanguageCode.ENGLISH)

    assert outline is not None and "Catalogue" in outline
    assert slides is not None and len(slides) == 3


def test_an_unplanned_session_renders_nothing_rather_than_an_empty_deck() -> None:
    service = SimulatorService()

    assert service.site_outline(uuid4(), LanguageCode.ENGLISH) is None
    assert service.deck_preview_slides(uuid4(), LanguageCode.ENGLISH) is None


# --------------------------------------------------------------------------------------
# It cannot be wired into a shape that is worse than not having it
# --------------------------------------------------------------------------------------


def test_sharing_one_adapter_between_the_lanes_is_refused() -> None:
    """A model adapter serialises behind one lock.

    Sharing it would make every buyer turn queue behind a ten-second deliberation - worse
    than the 3.37x contention the two-lane design exists to avoid, and invisible until a
    deliberation happened to be running when someone spoke.
    """

    shared = MockModelAdapter([])

    with pytest.raises(ValueError, match="different adapter instance"):
        SimulatorService(language_model=shared, deliberation_model=shared)


@pytest.mark.asyncio
async def test_a_turn_claims_the_cpu_for_the_whole_turn() -> None:
    """Not just for the model call: the contention penalty applies to any work in the turn."""

    service = SimulatorService()
    session = service.create_session(CreateSessionRequest(lead_ref="claims"))

    await service.process_turn(
        session.session_id,
        TurnRequest(text="We run a clothing store.", language=LanguageCode.ENGLISH),
    )

    assert service.lane_stats().turns == 1


# --------------------------------------------------------------------------------------
# Nothing outlives the session
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_closing_a_session_frees_its_briefing() -> None:
    """The leak this catches is invisible to any test that uses one session."""

    engine = ConversationEngine()
    service = SimulatorService(conversation_engine=engine)
    session = service.create_session(CreateSessionRequest(lead_ref="leak"))
    await service.process_turn(
        session.session_id,
        TurnRequest(text="We run a clothing store selling shirts.", language=LanguageCode.ENGLISH),
    )
    assert Topic.BUSINESS_TYPE in engine.briefing(session.session_id).known_topics()

    await service.close_session(session.session_id)

    assert engine.briefing(session.session_id).known_topics() == frozenset()


@pytest.mark.asyncio
async def test_closing_a_session_waits_for_its_deliberation_to_stop() -> None:
    """A generation left running would hold the model and the CPU the next session needs."""

    release = asyncio.Event()
    model = SlowLaneModel(block=release)
    service = SimulatorService(deliberation_model=model, deliberation_model_id="fake")
    session = await _qualified(service)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if model.calls:
            break

    closing = asyncio.create_task(service.close_session(session.session_id))
    await asyncio.sleep(0.05)
    release.set()
    await asyncio.wait_for(closing, timeout=5.0)

    assert service.site_plan(session.session_id) is None
