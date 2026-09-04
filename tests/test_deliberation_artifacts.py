"""The artifacts a buyer sees, and the wiring that reaches them from a conversation.

Two things are tested here that are easy to get wrong in opposite directions: an artifact
that invents something the model never said, and a plan that reaches the buyer while it is
describing a business they have since corrected.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from pitchbot.conversation.engine import ConversationEngine
from pitchbot.conversation.planning import supported_languages
from pitchbot.deliberation import (
    Briefing,
    Deliberation,
    SitePlan,
    Topic,
    artifact_languages,
    deck_slides,
    phrases_for,
    site_content,
)
from pitchbot.domain import LanguageCode

PLAN = SitePlan(
    competitors=("template shops", "marketplaces", "custom agencies"),
    differentiator="Wholesale-first ordering built for boutique buyers.",
    pages=("Home", "Catalogue", "Wholesale pricing", "Reorder"),
)


# --------------------------------------------------------------------------------------
# Artifacts contain what was concluded, and nothing else
# --------------------------------------------------------------------------------------


def test_every_language_the_agent_replies_in_can_render_an_artifact() -> None:
    """A buyer who is being answered in Hindi must not receive an English-only draft label."""

    assert set(supported_languages()) <= artifact_languages()


def test_the_outline_contains_every_part_of_the_plan() -> None:
    rendered = site_content(PLAN)

    assert PLAN.differentiator in rendered
    for page in PLAN.pages:
        assert page in rendered
    for competitor in PLAN.competitors:
        assert competitor in rendered


def test_the_outline_says_it_is_a_draft_in_the_buyers_language() -> None:
    for language in artifact_languages():
        assert phrases_for(language).draft_notice in site_content(PLAN, language)


@pytest.mark.parametrize("language", sorted(artifact_languages()))
def test_an_artifact_never_states_a_price_or_a_date(language: LanguageCode) -> None:
    """Absent by construction: the scaffolding has no slot for either."""

    scaffolding = phrases_for(language)
    joined = " ".join(
        (
            scaffolding.draft_notice,
            scaffolding.competitors_title,
            scaffolding.differentiator_title,
            scaffolding.pages_title,
            scaffolding.content_heading,
            scaffolding.next_step,
        )
    )
    assert not any(character.isdigit() for character in joined)


def test_the_deck_has_one_slide_per_thing_the_plan_knows() -> None:
    slides = deck_slides(PLAN)

    assert len(slides) == 3
    assert slides[1].bullets == PLAN.pages
    assert slides[2].bullets == PLAN.competitors


def test_the_deck_carries_the_draft_notice_where_a_reader_will_see_it() -> None:
    slides = deck_slides(PLAN, LanguageCode.HINDI)

    assert phrases_for(LanguageCode.HINDI).draft_notice in slides[0].bullets


def test_an_unknown_language_falls_back_rather_than_failing() -> None:
    assert site_content(PLAN, LanguageCode.UNKNOWN)


# --------------------------------------------------------------------------------------
# Reaching an artifact from a conversation
# --------------------------------------------------------------------------------------


def test_a_turn_records_what_the_buyer_said_into_the_briefing() -> None:
    engine = ConversationEngine()
    session = uuid4()
    engine.create_session(session)

    engine.process_turn(
        session, text="We run a clothing store selling shirts.", language=LanguageCode.ENGLISH
    )

    briefing = engine.briefing(session)
    assert Topic.BUSINESS_TYPE in briefing.known_topics()


def test_a_session_with_no_plan_offers_none_rather_than_an_empty_one() -> None:
    engine = ConversationEngine()
    session = uuid4()
    engine.create_session(session)

    engine.process_turn(
        session, text="We run a clothing store selling shirts.", language=LanguageCode.ENGLISH
    )

    assert engine.site_plan(session) is None


def test_a_plan_is_offered_once_it_describes_the_buyer_we_are_talking_to() -> None:
    engine = ConversationEngine()
    session = uuid4()
    engine.create_session(session)
    engine.process_turn(
        session, text="We run a clothing store selling shirts.", language=LanguageCode.ENGLISH
    )
    briefing = engine.briefing(session)

    briefing.conclude(
        Deliberation(plan=PLAN, derived_from_version=briefing.version, model_id="test")
    )

    assert engine.site_plan(session) is PLAN


def test_a_plan_stops_being_offered_when_the_buyer_adds_something() -> None:
    """The buyer has corrected the picture; showing the old plan is the misconception."""

    engine = ConversationEngine()
    session = uuid4()
    engine.create_session(session)
    engine.process_turn(
        session, text="We run a clothing store selling shirts.", language=LanguageCode.ENGLISH
    )
    briefing = engine.briefing(session)
    briefing.conclude(
        Deliberation(plan=PLAN, derived_from_version=briefing.version, model_id="test")
    )

    engine.process_turn(
        session, text="Our budget is around 200000 rupees.", language=LanguageCode.ENGLISH
    )

    assert engine.site_plan(session) is None


def test_an_unknown_session_has_a_briefing_but_no_plan() -> None:
    engine = ConversationEngine()

    assert isinstance(engine.briefing(uuid4()), Briefing)
    assert engine.site_plan(uuid4()) is None
