from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from pitchbot.actions import (
    ActionAuthorizationContext,
    ActionPolicy,
    AuthorizationStatus,
    BlockReason,
    CallbackAgenda,
    CallbackConflictError,
    CallbackRequest,
    CallbackService,
    CallbackStatus,
    DeckIndustry,
    DeckRequest,
    DeckService,
    build_follow_up,
)
from pitchbot.adapters import FakeClock
from pitchbot.adapters.mocks import (
    MockArtifactAdapter,
    MockSchedulerAdapter,
    MockTelephonyAdapter,
)
from pitchbot.domain import ActionType, ContactPolicy, LanguageCode, LeadTemperature


def eligible_context(**changes: object) -> ActionAuthorizationContext:
    values: dict[str, object] = {
        "disclosure_delivered": True,
        "consent_granted": True,
        "contact_policy": ContactPolicy(
            outreach_allowed=True,
            allowlisted=True,
            dnd_check_passed=True,
            calling_hours_check_passed=True,
        ),
        "temperature": LeadTemperature.WARM,
        "conversation_disposition": "continue",
    }
    values.update(changes)
    return ActionAuthorizationContext.model_validate(values)


def test_policy_denies_unknown_state_and_reports_all_reasons() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    decision = ActionPolicy(clock=clock).authorize(
        ActionType.WHATSAPP_PREVIEW,
        ActionAuthorizationContext(conversation_disposition="review"),
    )

    assert decision.status is AuthorizationStatus.BLOCKED
    assert set(decision.reasons) >= {
        BlockReason.DISCLOSURE_MISSING,
        BlockReason.CONSENT_MISSING,
        BlockReason.OUTREACH_NOT_ALLOWED,
        BlockReason.NOT_ALLOWLISTED,
        BlockReason.DND_NOT_PASSED,
        BlockReason.CALLING_HOURS_NOT_PASSED,
        BlockReason.CONVERSATION_NOT_ELIGIBLE,
        BlockReason.CLASSIFICATION_REVIEW,
    }
    assert decision.decided_at == clock.now()


def test_opt_out_and_quota_fail_closed() -> None:
    context = eligible_context(
        contact_policy=ContactPolicy(
            outreach_allowed=True,
            allowlisted=True,
            dnd_check_passed=True,
            calling_hours_check_passed=True,
            opted_out=True,
        ),
        used_actions=3,
        max_actions=3,
    )

    decision = ActionPolicy().authorize(ActionType.ARTIFACT_PREVIEW, context)

    assert BlockReason.OPTED_OUT in decision.reasons
    assert BlockReason.QUOTA_EXCEEDED in decision.reasons


def test_cold_classification_is_not_action_eligible() -> None:
    decision = ActionPolicy().authorize(
        ActionType.WHATSAPP_PREVIEW,
        eligible_context(temperature=LeadTemperature.COLD),
    )

    assert decision.status is AuthorizationStatus.BLOCKED
    assert decision.reasons == (BlockReason.CLASSIFICATION_INELIGIBLE,)


def test_follow_up_uses_only_allowlisted_minimized_facts() -> None:
    lead_id = uuid4()
    summary = build_follow_up(
        lead_id=lead_id,
        language=LanguageCode.MIXED,
        facts={
            "business_type": "apparel",
            "requested_features": "catalog,online-payments",
            "timeline": "within 3 weeks",
            "phone": "+91-0000000000",
            "raw_transcript": "private transcript",
        },
        next_steps=(" Review synthetic deck ",),
    )

    dumped = summary.model_dump_json()
    assert summary.lead_id == lead_id
    assert summary.requested_features == ("catalog", "online-payments")
    assert "+91" not in dumped
    assert "private transcript" not in dumped


@pytest.mark.asyncio
async def test_fake_time_callback_dispatches_only_when_due() -> None:
    clock = FakeClock(datetime(2026, 1, 1, 10, tzinfo=UTC))
    scheduler = MockSchedulerAdapter()
    telephony = MockTelephonyAdapter()
    service = CallbackService(
        scheduler=scheduler,
        telephony=telephony,
        policy=ActionPolicy(clock=clock),
        clock=clock,
    )
    request = CallbackRequest(
        lead_id=uuid4(),
        callback_id="callback-1",
        run_at=clock.now() + timedelta(minutes=2),
        timezone="UTC",
        agenda=CallbackAgenda.WEBSITE_DISCOVERY,
        idempotency_key="schedule-1",
    )

    scheduled = await service.schedule(request, eligible_context())
    assert scheduled.status is CallbackStatus.SCHEDULED
    assert await service.dispatch_due(lambda _: eligible_context()) == ()

    clock.advance(timedelta(minutes=2))
    dispatched = await service.dispatch_due(lambda _: eligible_context())
    assert dispatched[0].status is CallbackStatus.DISPATCHED
    assert len(telephony.actions) == 1
    assert await service.dispatch_due(lambda _: eligible_context()) == ()


@pytest.mark.asyncio
async def test_callback_rechecks_opt_out_before_dispatch() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    telephony = MockTelephonyAdapter()
    service = CallbackService(
        scheduler=MockSchedulerAdapter(),
        telephony=telephony,
        policy=ActionPolicy(clock=clock),
        clock=clock,
    )
    request = CallbackRequest(
        lead_id=uuid4(),
        callback_id="callback-opt-out",
        run_at=clock.now() + timedelta(minutes=1),
        timezone="UTC",
        agenda=CallbackAgenda.WEBSITE_DISCOVERY,
        idempotency_key="schedule-opt-out",
    )
    await service.schedule(request, eligible_context())
    clock.advance(timedelta(minutes=1))

    blocked = await service.dispatch_due(
        lambda _: eligible_context(
            contact_policy=ContactPolicy(
                outreach_allowed=True,
                allowlisted=True,
                dnd_check_passed=True,
                calling_hours_check_passed=True,
                opted_out=True,
            )
        )
    )

    assert blocked[0].status is CallbackStatus.BLOCKED
    assert BlockReason.POLICY_CHANGED in blocked[0].block_reasons
    assert BlockReason.OPTED_OUT in blocked[0].block_reasons
    assert not telephony.actions


@pytest.mark.asyncio
async def test_callback_idempotency_cancel_and_reschedule() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    scheduler = MockSchedulerAdapter()
    service = CallbackService(
        scheduler=scheduler,
        telephony=MockTelephonyAdapter(),
        policy=ActionPolicy(clock=clock),
        clock=clock,
    )
    request = CallbackRequest(
        lead_id=uuid4(),
        callback_id="callback-retry",
        run_at=clock.now() + timedelta(minutes=5),
        timezone="UTC",
        agenda=CallbackAgenda.WEBSITE_DISCOVERY,
        idempotency_key="schedule-retry",
    )

    first = await service.schedule(request, eligible_context())
    assert await service.schedule(request, eligible_context()) == first
    assert len(scheduler.actions) == 1
    with pytest.raises(CallbackConflictError, match="already scheduled"):
        await service.schedule(
            request.model_copy(update={"idempotency_key": "schedule-conflict"}),
            eligible_context(),
        )

    canceled = await service.cancel("callback-retry", idempotency_key="cancel-1")
    assert canceled.status is CallbackStatus.CANCELED
    assert await service.schedule(request, eligible_context()) == first
    assert await service.cancel("callback-retry", idempotency_key="cancel-1") == canceled
    with pytest.raises(CallbackConflictError, match="Only a scheduled"):
        await service.cancel("callback-retry", idempotency_key="cancel-2")
    rescheduled = await service.schedule(
        request.model_copy(
            update={
                "run_at": clock.now() + timedelta(minutes=10),
                "idempotency_key": "schedule-2",
            }
        ),
        eligible_context(),
    )
    assert rescheduled.status is CallbackStatus.SCHEDULED


@pytest.mark.asyncio
async def test_invalid_callback_window_is_blocked_without_scheduler_action() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    scheduler = MockSchedulerAdapter()
    service = CallbackService(
        scheduler=scheduler,
        telephony=MockTelephonyAdapter(),
        policy=ActionPolicy(clock=clock),
        clock=clock,
    )
    request = CallbackRequest(
        lead_id=uuid4(),
        callback_id="callback-past",
        run_at=clock.now(),
        timezone="UTC",
        agenda=CallbackAgenda.WEBSITE_DISCOVERY,
        idempotency_key="past-1",
    )

    blocked = await service.schedule(request, eligible_context())
    assert blocked.status is CallbackStatus.BLOCKED
    assert blocked.block_reasons == (BlockReason.CALLBACK_TIME_INVALID,)
    assert not scheduler.actions


@pytest.mark.asyncio
async def test_blocked_callbacks_do_not_consume_active_capacity() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    service = CallbackService(
        scheduler=MockSchedulerAdapter(),
        telephony=MockTelephonyAdapter(),
        policy=ActionPolicy(clock=clock),
        clock=clock,
        max_callbacks=1,
    )
    blocked_request = CallbackRequest(
        lead_id=uuid4(),
        callback_id="blocked-capacity",
        run_at=clock.now(),
        timezone="UTC",
        agenda=CallbackAgenda.WEBSITE_DISCOVERY,
        idempotency_key="blocked-capacity-operation",
    )
    allowed_request = CallbackRequest(
        lead_id=uuid4(),
        callback_id="allowed-capacity",
        run_at=clock.now() + timedelta(minutes=1),
        timezone="UTC",
        agenda=CallbackAgenda.WEBSITE_DISCOVERY,
        idempotency_key="allowed-capacity-operation",
    )

    assert (
        await service.schedule(blocked_request, eligible_context())
    ).status is CallbackStatus.BLOCKED
    assert (
        await service.schedule(allowed_request, eligible_context())
    ).status is CallbackStatus.SCHEDULED


@pytest.mark.asyncio
async def test_all_industry_decks_are_bounded_and_idempotent() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    adapter = MockArtifactAdapter()
    service = DeckService(artifact_adapter=adapter, clock=clock, max_decks=6)

    for index, industry in enumerate(DeckIndustry):
        request = DeckRequest(
            lead_id=uuid4(),
            deck_id=f"deck-{index}",
            industry=industry,
            language=LanguageCode.ENGLISH,
            requested_features=("catalog", "unknown-buyer-text"),
            idempotency_key=f"deck-operation-{index}",
        )
        preview = await service.create(request)
        assert len(preview.slides) == 3
        assert "unknown-buyer-text" not in preview.model_dump_json()
        assert await service.create(request) == preview

    assert len(adapter.actions) == 6
    assert all(action.payload["field_count"] == 4 for action in adapter.actions)
