from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from pitchbot.actions import (
    ActionAuthorizationContext,
    ActionPolicy,
    ActionWorkflowService,
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
from pitchbot.adapters import ActionResult, AdapterTimeoutError, FakeClock, PermanentAdapterError
from pitchbot.adapters.mocks import (
    MockArtifactAdapter,
    MockSchedulerAdapter,
    MockTelephonyAdapter,
    MockWhatsAppAdapter,
)
from pitchbot.domain import ActionType, ContactPolicy, JsonValue, LanguageCode, LeadTemperature


class ConcurrencyProbeScheduleAdapter(MockSchedulerAdapter):
    """Counts how many schedule calls are inside the adapter at the same moment.

    The adapter is where the network latency lives, so overlap here is the only thing
    that distinguishes a service whose callers wait for each other from one whose callers
    do not.
    """

    def __init__(self, *, hold: float = 0.05) -> None:
        super().__init__()
        self._hold = hold
        self.concurrent = 0
        self.peak_concurrent = 0

    async def schedule(
        self,
        job_key: str,
        run_at: datetime,
        payload: Mapping[str, JsonValue],
        idempotency_key: str,
    ) -> ActionResult:
        self.concurrent += 1
        self.peak_concurrent = max(self.peak_concurrent, self.concurrent)
        try:
            await asyncio.sleep(self._hold)
            return await super().schedule(job_key, run_at, payload, idempotency_key)
        finally:
            self.concurrent -= 1


class SlowDialTelephonyAdapter(MockTelephonyAdapter):
    def __init__(self, *, hold: float = 0.05) -> None:
        super().__init__()
        self._hold = hold
        self.dialing = asyncio.Event()

    async def dial(self, contact_ref: str, idempotency_key: str) -> ActionResult:
        self.dialing.set()
        await asyncio.sleep(self._hold)
        return await super().dial(contact_ref, idempotency_key)


class BlockingScheduleAdapter(MockSchedulerAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.schedule_calls = 0

    async def schedule(
        self,
        job_key: str,
        run_at: datetime,
        payload: Mapping[str, JsonValue],
        idempotency_key: str,
    ) -> ActionResult:
        self.schedule_calls += 1
        self.started.set()
        await self.release.wait()
        return await super().schedule(job_key, run_at, payload, idempotency_key)


class AcceptedThenBlockingScheduleAdapter(MockSchedulerAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.accepted = asyncio.Event()
        self.release = asyncio.Event()
        self.schedule_calls = 0

    async def schedule(
        self,
        job_key: str,
        run_at: datetime,
        payload: Mapping[str, JsonValue],
        idempotency_key: str,
    ) -> ActionResult:
        self.schedule_calls += 1
        result = await super().schedule(job_key, run_at, payload, idempotency_key)
        if self.schedule_calls == 1:
            self.accepted.set()
            await self.release.wait()
        return result


class BlockingCancelAdapter(MockSchedulerAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.cancel_started = asyncio.Event()
        self.release_cancel = asyncio.Event()

    async def cancel(self, job_key: str, idempotency_key: str) -> ActionResult:
        self.cancel_started.set()
        await self.release_cancel.wait()
        return await super().cancel(job_key, idempotency_key)


class AcceptedThenTimeoutCancelAdapter(MockSchedulerAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.timeout_once = True

    async def cancel(self, job_key: str, idempotency_key: str) -> ActionResult:
        result = await super().cancel(job_key, idempotency_key)
        if self.timeout_once:
            self.timeout_once = False
            raise AdapterTimeoutError("ambiguous cancellation timeout")
        return result


class AcceptedThenPermanentCancelAdapter(AcceptedThenBlockingScheduleAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.cancel_keys: list[str] = []
        self.fail_cancel_once = True

    async def cancel(self, job_key: str, idempotency_key: str) -> ActionResult:
        self.cancel_keys.append(idempotency_key)
        if self.fail_cancel_once:
            self.fail_cancel_once = False
            raise PermanentAdapterError("definitive pending cleanup failure")
        return await super().cancel(job_key, idempotency_key)


class RetainingMockSchedulerAdapter(MockSchedulerAdapter):
    def clear_operations(self, idempotency_key_prefix: str) -> None:
        pass


class BlockingArtifactAdapter(MockArtifactAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.create_calls = 0

    async def create(
        self,
        artifact_key: str,
        payload: Mapping[str, JsonValue],
        idempotency_key: str,
    ) -> ActionResult:
        self.create_calls += 1
        self.started.set()
        await self.release.wait()
        return await super().create(artifact_key, payload, idempotency_key)


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


def _eligible_policy(**changes: bool) -> ContactPolicy:
    values = {
        "outreach_allowed": True,
        "allowlisted": True,
        "dnd_check_passed": True,
        "calling_hours_check_passed": True,
    }
    values.update(changes)
    return ContactPolicy(**values)


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


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"disclosure_delivered": False}, BlockReason.DISCLOSURE_MISSING),
        ({"contact_policy": _eligible_policy(allowlisted=False)}, BlockReason.NOT_ALLOWLISTED),
        ({"contact_policy": _eligible_policy(dnd_check_passed=False)}, BlockReason.DND_NOT_PASSED),
        (
            {"contact_policy": _eligible_policy(calling_hours_check_passed=False)},
            BlockReason.CALLING_HOURS_NOT_PASSED,
        ),
    ],
)
def test_disclosure_allowlist_dnd_and_calling_hours_gates_are_unconditional(
    override: dict[str, object], expected: BlockReason
) -> None:
    # This is a lock, not a failing-first regression: the behaviour is unchanged
    # and this passes before and after PR 30. ActionPolicy takes no Settings and
    # reads none, so each of these four gates blocks whenever its input fails,
    # with no configuration path that can turn it off. PR 30 removed the
    # never-wired require_ai_disclosure/require_dnd_check/require_calling_hours/
    # allowlist_enabled knobs precisely so this stays true; the test fails if
    # someone later reintroduces a config switch that lets one of these mandatory
    # safety gates be disabled.
    decision = ActionPolicy().authorize(ActionType.WHATSAPP_PREVIEW, eligible_context(**override))

    assert decision.status is AuthorizationStatus.BLOCKED
    assert expected in decision.reasons


def test_action_policy_layer_consults_no_configuration() -> None:
    # The four gates above are unconditional because the policy layer never reads
    # Settings. Wiring settings in to add a require_*/allowlist_enabled switch is
    # exactly the regression PR 30 forecloses, so catch it at the source: the
    # policy module must not import the config singleton or its type.
    import inspect

    from pitchbot.actions import policy as policy_module

    source = inspect.getsource(policy_module)
    assert "pitchbot.config" not in source
    assert "settings" not in source


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
async def test_canceled_schedule_retry_reconciles_original_request_after_due_time() -> None:
    clock = FakeClock(datetime(2026, 1, 1, 10, tzinfo=UTC))
    scheduler = AcceptedThenBlockingScheduleAdapter()
    service = CallbackService(
        scheduler=scheduler,
        telephony=MockTelephonyAdapter(),
        policy=ActionPolicy(clock=clock),
        clock=clock,
    )
    request = CallbackRequest(
        lead_id=uuid4(),
        callback_id="callback-canceled-schedule",
        run_at=clock.now() + timedelta(minutes=1),
        timezone="UTC",
        agenda=CallbackAgenda.WEBSITE_DISCOVERY,
        idempotency_key="schedule-canceled-operation",
    )

    pending = asyncio.create_task(service.schedule(request, eligible_context()))
    await scheduler.accepted.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    clock.advance(timedelta(minutes=2))
    scheduler.release.set()
    reconciled = await service.schedule(request, eligible_context())

    assert reconciled.status is CallbackStatus.SCHEDULED
    assert reconciled.request == request
    assert scheduler.schedule_calls == 2
    assert len(scheduler.actions) == 1


@pytest.mark.asyncio
async def test_invalid_callback_time_produces_blocked_preview_decision() -> None:
    clock = FakeClock(datetime(2026, 1, 1, 10, tzinfo=UTC))
    policy = ActionPolicy(clock=clock)
    workflows = ActionWorkflowService(
        policy=policy,
        callbacks=CallbackService(
            scheduler=MockSchedulerAdapter(),
            telephony=MockTelephonyAdapter(),
            policy=policy,
            clock=clock,
        ),
        decks=DeckService(artifact_adapter=MockArtifactAdapter(), clock=clock),
        whatsapp=MockWhatsAppAdapter(),
        clock=clock,
    )

    preview = await workflows.preview_callback(
        session_id=uuid4(),
        lead_id=uuid4(),
        delay_minutes=1,
        context=eligible_context(),
        operation_id=uuid4(),
        requested_at=clock.now() - timedelta(minutes=2),
    )

    assert preview.decision.status is AuthorizationStatus.BLOCKED
    assert preview.decision.reasons == (BlockReason.CALLBACK_TIME_INVALID,)
    assert preview.label == "Callback preview blocked."
    assert preview.callback is not None
    assert preview.callback.status is CallbackStatus.BLOCKED


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
async def test_inactive_callback_cannot_be_rescheduled_when_capacity_is_full() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    service = CallbackService(
        scheduler=MockSchedulerAdapter(),
        telephony=MockTelephonyAdapter(),
        policy=ActionPolicy(clock=clock),
        clock=clock,
        max_callbacks=1,
    )
    first = CallbackRequest(
        lead_id=uuid4(),
        callback_id="inactive-callback",
        run_at=clock.now() + timedelta(minutes=1),
        timezone="UTC",
        idempotency_key="inactive-schedule-1",
    )
    second = first.model_copy(
        update={
            "callback_id": "active-callback",
            "idempotency_key": "active-schedule",
        }
    )
    await service.schedule(first, eligible_context())
    await service.cancel(first.callback_id, idempotency_key="inactive-cancel")
    await service.schedule(second, eligible_context())

    with pytest.raises(RuntimeError, match="capacity"):
        await service.schedule(
            first.model_copy(
                update={
                    "run_at": clock.now() + timedelta(minutes=2),
                    "idempotency_key": "inactive-schedule-2",
                }
            ),
            eligible_context(),
        )


@pytest.mark.asyncio
async def test_definitive_schedule_failure_releases_pending_capacity() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    scheduler = MockSchedulerAdapter(
        failures=[PermanentAdapterError("definitive schedule failure")]
    )
    service = CallbackService(
        scheduler=scheduler,
        telephony=MockTelephonyAdapter(),
        policy=ActionPolicy(clock=clock),
        clock=clock,
        max_callbacks=1,
    )
    failed = CallbackRequest(
        lead_id=uuid4(),
        callback_id="definitive-failure",
        run_at=clock.now() + timedelta(minutes=1),
        timezone="UTC",
        idempotency_key="definitive-failure-operation",
    )
    replacement = failed.model_copy(
        update={
            "callback_id": "replacement-after-failure",
            "idempotency_key": "replacement-after-failure-operation",
        }
    )

    with pytest.raises(PermanentAdapterError, match="definitive"):
        await service.schedule(failed, eligible_context())

    assert (await service.schedule(replacement, eligible_context())).status is (
        CallbackStatus.SCHEDULED
    )


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
        assert len(preview.slides) == 4
        assert "unknown-buyer-text" not in preview.model_dump_json()
        assert await service.create(request) == preview

    assert len(adapter.actions) == 6
    assert all(action.payload["field_count"] == 4 for action in adapter.actions)


@pytest.mark.asyncio
async def test_concurrent_callback_admission_cannot_exceed_capacity() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    scheduler = BlockingScheduleAdapter()
    service = CallbackService(
        scheduler=scheduler,
        telephony=MockTelephonyAdapter(),
        policy=ActionPolicy(clock=clock),
        clock=clock,
        max_callbacks=1,
    )

    def request(identifier: str) -> CallbackRequest:
        return CallbackRequest(
            lead_id=uuid4(),
            callback_id=identifier,
            run_at=clock.now() + timedelta(minutes=1),
            timezone="UTC",
            idempotency_key=f"schedule-{identifier}",
        )

    first = asyncio.create_task(service.schedule(request("first"), eligible_context()))
    await scheduler.started.wait()
    second = asyncio.create_task(service.schedule(request("second"), eligible_context()))
    await asyncio.sleep(0)
    assert scheduler.schedule_calls == 1

    scheduler.release.set()
    assert (await first).status is CallbackStatus.SCHEDULED
    with pytest.raises(RuntimeError, match="capacity"):
        await second
    assert len(scheduler.actions) == 1


@pytest.mark.asyncio
async def test_unrelated_callbacks_are_scheduled_concurrently() -> None:
    """Two sessions scheduling different callbacks must not wait for each other.

    Every adapter call in this service is a network call in production. Measured with a
    200 ms adapter (`probe_callback_contention.py`), ten sessions each scheduling once took
    2,057 ms - exactly ten times one call - because a single service-wide lock was held
    across the adapter. Nothing about two different callbacks requires that.
    """

    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    scheduler = ConcurrencyProbeScheduleAdapter()
    service = CallbackService(
        scheduler=scheduler,
        telephony=MockTelephonyAdapter(),
        policy=ActionPolicy(clock=clock),
        clock=clock,
    )

    def request(identifier: str) -> CallbackRequest:
        return CallbackRequest(
            lead_id=uuid4(),
            callback_id=identifier,
            run_at=clock.now() + timedelta(minutes=1),
            timezone="UTC",
            idempotency_key=f"schedule-{identifier}",
        )

    results = await asyncio.gather(
        *(service.schedule(request(f"lead-{index}"), eligible_context()) for index in range(5))
    )

    assert all(record.status is CallbackStatus.SCHEDULED for record in results)
    assert scheduler.peak_concurrent == 5


@pytest.mark.asyncio
async def test_dispatching_due_callbacks_does_not_block_an_unrelated_session() -> None:
    """A dispatch batch holds no claim over callbacks that are not in it.

    The batch dials once per due callback. Holding a service-wide lock for the whole batch
    made an unrelated session's `schedule` wait for every dial in it: measured at 2,241 ms
    behind ten 200 ms dials, and a real telephony dial is far slower than 200 ms.
    """

    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    telephony = SlowDialTelephonyAdapter()
    service = CallbackService(
        scheduler=MockSchedulerAdapter(),
        telephony=telephony,
        policy=ActionPolicy(clock=clock),
        clock=clock,
    )
    for index in range(3):
        await service.schedule(
            CallbackRequest(
                lead_id=uuid4(),
                callback_id=f"due-{index}",
                run_at=clock.now() + timedelta(minutes=1),
                timezone="UTC",
                idempotency_key=f"schedule-due-{index}",
            ),
            eligible_context(),
        )
    clock.advance(timedelta(minutes=1))

    dispatch = asyncio.create_task(service.dispatch_due(lambda _: eligible_context()))
    await telephony.dialing.wait()

    # The batch is mid-dial. A different session arriving now is not part of it.
    newcomer = await asyncio.wait_for(
        service.schedule(
            CallbackRequest(
                lead_id=uuid4(),
                callback_id="unrelated",
                run_at=clock.now() + timedelta(minutes=5),
                timezone="UTC",
                idempotency_key="schedule-unrelated",
            ),
            eligible_context(),
        ),
        timeout=0.02,
    )

    assert newcomer.status is CallbackStatus.SCHEDULED
    assert len(await dispatch) == 3


@pytest.mark.asyncio
async def test_a_callback_canceled_mid_batch_is_not_dispatched_anyway() -> None:
    """A dispatch batch is not a claim over the callbacks it has not reached yet.

    This is the race that per-callback locking introduces and must therefore close. The
    batch dials one callback at a time; while it is dialing the first, a cancel for the
    second is free to run, because they are different callbacks and no longer share a lock.
    Dispatching the second from the batch's stale snapshot would silently undo that cancel
    and place a call the buyer asked not to receive.
    """

    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    telephony = SlowDialTelephonyAdapter()
    service = CallbackService(
        scheduler=MockSchedulerAdapter(),
        telephony=telephony,
        policy=ActionPolicy(clock=clock),
        clock=clock,
    )
    for identifier in ("aaa-first", "zzz-second"):
        await service.schedule(
            CallbackRequest(
                lead_id=uuid4(),
                callback_id=identifier,
                run_at=clock.now() + timedelta(minutes=1),
                timezone="UTC",
                idempotency_key=f"schedule-{identifier}",
            ),
            eligible_context(),
        )
    clock.advance(timedelta(minutes=1))

    # Both are due, and the batch dials them in callback-id order.
    dispatch = asyncio.create_task(service.dispatch_due(lambda _: eligible_context()))
    await telephony.dialing.wait()

    canceled = await service.cancel("zzz-second", idempotency_key="cancel-mid-batch")
    assert canceled.status is CallbackStatus.CANCELED

    dispatched = await dispatch
    assert [record.request.callback_id for record in dispatched] == ["aaa-first"]
    assert service.get("zzz-second").status is CallbackStatus.CANCELED
    assert len(telephony.actions) == 1, "the canceled callback must not have been dialed"


@pytest.mark.asyncio
async def test_cancel_claim_prevents_concurrent_due_dispatch() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    scheduler = BlockingCancelAdapter()
    telephony = MockTelephonyAdapter()
    service = CallbackService(
        scheduler=scheduler,
        telephony=telephony,
        policy=ActionPolicy(clock=clock),
        clock=clock,
    )
    request = CallbackRequest(
        lead_id=uuid4(),
        callback_id="cancel-race",
        run_at=clock.now() + timedelta(minutes=1),
        timezone="UTC",
        idempotency_key="schedule-cancel-race",
    )
    await service.schedule(request, eligible_context())
    clock.advance(timedelta(minutes=1))

    cancellation = asyncio.create_task(
        service.cancel(request.callback_id, idempotency_key="cancel-race-operation")
    )
    await scheduler.cancel_started.wait()
    dispatch = asyncio.create_task(service.dispatch_due(lambda _: eligible_context()))
    await asyncio.sleep(0)
    assert not telephony.actions

    scheduler.release_cancel.set()
    assert (await cancellation).status is CallbackStatus.CANCELED
    assert await dispatch == ()
    assert not telephony.actions


@pytest.mark.asyncio
async def test_ambiguous_cancellation_remains_non_dispatchable_until_reconciled() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    scheduler = AcceptedThenTimeoutCancelAdapter()
    telephony = MockTelephonyAdapter()
    service = CallbackService(
        scheduler=scheduler,
        telephony=telephony,
        policy=ActionPolicy(clock=clock),
        clock=clock,
    )
    request = CallbackRequest(
        lead_id=uuid4(),
        callback_id="ambiguous-cancel",
        run_at=clock.now() + timedelta(minutes=1),
        timezone="UTC",
        idempotency_key="schedule-ambiguous-cancel",
    )
    await service.schedule(request, eligible_context())
    clock.advance(timedelta(minutes=1))

    with pytest.raises(AdapterTimeoutError, match="ambiguous"):
        await service.cancel(request.callback_id, idempotency_key="cancel-ambiguous")

    assert service.get(request.callback_id).status is CallbackStatus.CANCELLATION_PENDING
    assert await service.dispatch_due(lambda _: eligible_context()) == ()
    assert not telephony.actions
    reconciled = await service.cancel(request.callback_id, idempotency_key="cancel-ambiguous")
    assert reconciled.status is CallbackStatus.CANCELED


@pytest.mark.asyncio
async def test_permanent_cancellation_requires_new_key_and_blocks_dispatch() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    scheduler = MockSchedulerAdapter(
        failures=[None, PermanentAdapterError("definitive cancellation failure"), None]
    )
    telephony = MockTelephonyAdapter()
    service = CallbackService(
        scheduler=scheduler,
        telephony=telephony,
        policy=ActionPolicy(clock=clock),
        clock=clock,
        max_callbacks=1,
    )
    request = CallbackRequest(
        lead_id=uuid4(),
        callback_id="permanent-cancel",
        run_at=clock.now() + timedelta(minutes=1),
        timezone="UTC",
        idempotency_key="schedule-permanent-cancel",
    )
    await service.schedule(request, eligible_context())
    clock.advance(timedelta(minutes=1))

    with pytest.raises(PermanentAdapterError, match="definitive"):
        await service.cancel(request.callback_id, idempotency_key="cancel-permanent")

    assert service.get(request.callback_id).status is CallbackStatus.CANCELLATION_REQUIRED
    assert request.callback_id in scheduler.jobs
    assert await service.dispatch_due(lambda _: eligible_context()) == ()
    assert not telephony.actions
    with pytest.raises(CallbackConflictError, match="permanently failed"):
        await service.cancel(request.callback_id, idempotency_key="cancel-permanent")
    with pytest.raises(RuntimeError, match="capacity"):
        await service.schedule(
            request.model_copy(
                update={
                    "callback_id": "blocked-by-reconciliation",
                    "run_at": clock.now() + timedelta(minutes=1),
                    "idempotency_key": "schedule-blocked-by-reconciliation",
                }
            ),
            eligible_context(),
        )

    reconciled = await service.cancel(
        request.callback_id,
        idempotency_key="cancel-permanent-reconcile",
    )
    assert reconciled.status is CallbackStatus.CANCELED
    assert request.callback_id not in scheduler.jobs
    replacement = request.model_copy(
        update={
            "callback_id": "replacement-after-reconciliation",
            "run_at": clock.now() + timedelta(minutes=1),
            "idempotency_key": "schedule-replacement-after-reconciliation",
        }
    )
    assert (await service.schedule(replacement, eligible_context())).status is (
        CallbackStatus.SCHEDULED
    )


@pytest.mark.asyncio
async def test_concurrent_deck_admission_cannot_exceed_capacity() -> None:
    adapter = BlockingArtifactAdapter()
    service = DeckService(artifact_adapter=adapter, max_decks=1)

    def request(identifier: str) -> DeckRequest:
        return DeckRequest(
            lead_id=uuid4(),
            deck_id=identifier,
            industry=DeckIndustry.APPAREL,
            language=LanguageCode.ENGLISH,
            idempotency_key=f"create-{identifier}",
        )

    first = asyncio.create_task(service.create(request("first")))
    await adapter.started.wait()
    second = asyncio.create_task(service.create(request("second")))
    await asyncio.sleep(0)
    assert adapter.create_calls == 1

    adapter.release.set()
    assert (await first).deck_id == "first"
    with pytest.raises(RuntimeError, match="capacity"):
        await second
    assert len(adapter.actions) == 1


@pytest.mark.asyncio
async def test_resource_cleanup_reclaims_callback_and_deck_capacity() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    scheduler = MockSchedulerAdapter()
    telephony = MockTelephonyAdapter()
    callbacks = CallbackService(
        scheduler=scheduler,
        telephony=telephony,
        policy=ActionPolicy(clock=clock),
        clock=clock,
        max_callbacks=1,
    )
    decks = DeckService(artifact_adapter=MockArtifactAdapter(), max_decks=1)
    callback = CallbackRequest(
        lead_id=uuid4(),
        callback_id="owned-callback-1",
        run_at=clock.now() + timedelta(minutes=1),
        timezone="UTC",
        idempotency_key="owned-callback-operation-1",
    )
    deck = DeckRequest(
        lead_id=uuid4(),
        deck_id="owned-deck-1",
        industry=DeckIndustry.BOOKS,
        language=LanguageCode.ENGLISH,
        idempotency_key="owned-deck-operation-1",
    )
    await callbacks.schedule(callback, eligible_context())
    await decks.create(deck)
    clock.advance(timedelta(minutes=1))
    assert await callbacks.dispatch_due(lambda _: eligible_context())
    assert telephony.actions

    await callbacks.remove_by_prefix("owned-callback-", "owned-callback-operation-")
    await decks.remove_by_prefix("owned-deck-", "owned-deck-operation-")

    assert not scheduler.jobs
    assert not scheduler.actions
    assert not telephony.actions
    with pytest.raises(LookupError):
        callbacks.get(callback.callback_id)
    with pytest.raises(LookupError):
        decks.get(deck.deck_id)
    assert (
        await callbacks.schedule(
            callback.model_copy(
                update={
                    "callback_id": "replacement-callback",
                    "run_at": clock.now() + timedelta(minutes=1),
                    "idempotency_key": "replacement-callback-operation",
                }
            ),
            eligible_context(),
        )
    ).status is CallbackStatus.SCHEDULED
    assert (
        await decks.create(
            deck.model_copy(
                update={
                    "deck_id": "replacement-deck",
                    "idempotency_key": "replacement-deck-operation",
                }
            )
        )
    ).deck_id == "replacement-deck"


@pytest.mark.asyncio
async def test_failed_cleanup_cancellation_leaves_callback_non_dispatchable() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    scheduler = AcceptedThenTimeoutCancelAdapter()
    telephony = MockTelephonyAdapter()
    service = CallbackService(
        scheduler=scheduler,
        telephony=telephony,
        policy=ActionPolicy(clock=clock),
        clock=clock,
    )
    request = CallbackRequest(
        lead_id=uuid4(),
        callback_id="cleanup-failure-callback",
        run_at=clock.now() + timedelta(minutes=1),
        timezone="UTC",
        idempotency_key="cleanup-failure-operation",
    )
    await service.schedule(request, eligible_context())
    clock.advance(timedelta(minutes=1))

    with pytest.raises(AdapterTimeoutError, match="ambiguous"):
        await service.remove_by_prefix("cleanup-failure-", "cleanup-failure-operation")

    assert request.callback_id not in scheduler.jobs
    assert service.get(request.callback_id).status is CallbackStatus.CANCELLATION_PENDING
    assert await service.dispatch_due(lambda _: eligible_context()) == ()
    assert not telephony.actions


@pytest.mark.asyncio
async def test_cleanup_reconciles_permanent_cancellation_with_distinct_key() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    scheduler = MockSchedulerAdapter(
        failures=[None, PermanentAdapterError("definitive cleanup failure"), None]
    )
    service = CallbackService(
        scheduler=scheduler,
        telephony=MockTelephonyAdapter(),
        policy=ActionPolicy(clock=clock),
        clock=clock,
        max_callbacks=1,
    )
    request = CallbackRequest(
        lead_id=uuid4(),
        callback_id="cleanup-permanent-callback",
        run_at=clock.now() + timedelta(minutes=1),
        timezone="UTC",
        idempotency_key="cleanup-permanent-operation",
    )
    await service.schedule(request, eligible_context())

    with pytest.raises(PermanentAdapterError, match="definitive"):
        await service.remove_by_prefix("cleanup-permanent-", "cleanup-permanent-operation")

    assert service.get(request.callback_id).status is CallbackStatus.CANCELLATION_REQUIRED
    assert request.callback_id in scheduler.jobs

    await service.remove_by_prefix("cleanup-permanent-", "cleanup-permanent-operation")

    assert request.callback_id not in scheduler.jobs
    with pytest.raises(LookupError):
        service.get(request.callback_id)
    replacement = request.model_copy(
        update={
            "callback_id": "cleanup-capacity-recovered",
            "idempotency_key": "cleanup-capacity-recovered-operation",
        }
    )
    assert (await service.schedule(replacement, eligible_context())).status is (
        CallbackStatus.SCHEDULED
    )


@pytest.mark.asyncio
async def test_pending_schedule_cleanup_advances_only_after_permanent_failure() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    scheduler = AcceptedThenPermanentCancelAdapter()
    service = CallbackService(
        scheduler=scheduler,
        telephony=MockTelephonyAdapter(),
        policy=ActionPolicy(clock=clock),
        clock=clock,
    )
    request = CallbackRequest(
        lead_id=uuid4(),
        callback_id="pending-cleanup-callback",
        run_at=clock.now() + timedelta(minutes=1),
        timezone="UTC",
        idempotency_key="pending-cleanup-operation",
    )
    pending = asyncio.create_task(service.schedule(request, eligible_context()))
    await scheduler.accepted.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    with pytest.raises(PermanentAdapterError, match="definitive"):
        await service.remove_by_prefix("pending-cleanup-", "pending-cleanup-operation")

    failed_key = scheduler.cancel_keys[0]
    with pytest.raises(CallbackConflictError, match="permanently failed"):
        await service.cancel(request.callback_id, idempotency_key=failed_key)

    await service.remove_by_prefix("pending-cleanup-", "pending-cleanup-operation")

    assert len(scheduler.cancel_keys) == 2
    assert len(set(scheduler.cancel_keys)) == 2
    assert request.callback_id not in scheduler.jobs


@pytest.mark.asyncio
async def test_cleanup_key_is_unique_across_callback_id_reuse() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    scheduler = RetainingMockSchedulerAdapter()
    service = CallbackService(
        scheduler=scheduler,
        telephony=MockTelephonyAdapter(),
        policy=ActionPolicy(clock=clock),
        clock=clock,
    )
    request = CallbackRequest(
        lead_id=uuid4(),
        callback_id="reused-cleanup-callback",
        run_at=clock.now() + timedelta(minutes=1),
        timezone="UTC",
        idempotency_key="reused-cleanup-schedule-1",
    )
    await service.schedule(request, eligible_context())
    await service.remove_by_prefix("reused-cleanup-", "reused-cleanup-schedule-")
    await service.schedule(
        request.model_copy(
            update={
                "idempotency_key": "reused-cleanup-schedule-2",
                "run_at": clock.now() + timedelta(minutes=2),
            }
        ),
        eligible_context(),
    )
    await service.remove_by_prefix("reused-cleanup-", "reused-cleanup-schedule-")

    cancellation_keys = [
        action.idempotency_key for action in scheduler.actions if action.operation == "cancel"
    ]
    assert cancellation_keys == [
        "cleanup:reused-cleanup-callback:1:1",
        "cleanup:reused-cleanup-callback:2:1",
    ]


@pytest.mark.asyncio
async def test_failed_cancellation_tombstone_is_reclaimed_with_the_callback() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    scheduler = MockSchedulerAdapter(
        failures=[None, PermanentAdapterError("definitive cancellation failure"), None]
    )
    service = CallbackService(
        scheduler=scheduler,
        telephony=MockTelephonyAdapter(),
        policy=ActionPolicy(clock=clock),
        clock=clock,
    )
    request = CallbackRequest(
        lead_id=uuid4(),
        callback_id="tombstone-callback",
        run_at=clock.now() + timedelta(minutes=1),
        timezone="UTC",
        idempotency_key="tombstone-schedule",
    )
    await service.schedule(request, eligible_context())

    with pytest.raises(PermanentAdapterError, match="definitive"):
        await service.cancel(request.callback_id, idempotency_key="tombstone-cancel")

    assert service._failed_cancellations == {"tombstone-cancel": "tombstone-callback"}
    with pytest.raises(CallbackConflictError, match="permanently failed"):
        await service.cancel(request.callback_id, idempotency_key="tombstone-cancel")

    await service.remove_by_prefix("tombstone-", "tombstone-")

    assert service._records == {}
    assert service._failed_cancellations == {}
    assert service._operation_fingerprints == {}
    assert service._operation_results == {}


@pytest.mark.asyncio
async def test_failed_cancellation_tombstone_survives_while_its_callback_does() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    scheduler = MockSchedulerAdapter(
        failures=[None, PermanentAdapterError("definitive cancellation failure"), None]
    )
    service = CallbackService(
        scheduler=scheduler,
        telephony=MockTelephonyAdapter(),
        policy=ActionPolicy(clock=clock),
        clock=clock,
    )
    request = CallbackRequest(
        lead_id=uuid4(),
        callback_id="retained-tombstone-callback",
        run_at=clock.now() + timedelta(minutes=1),
        timezone="UTC",
        idempotency_key="retained-tombstone-schedule",
    )
    await service.schedule(request, eligible_context())
    with pytest.raises(PermanentAdapterError, match="definitive"):
        await service.cancel(request.callback_id, idempotency_key="retained-tombstone-cancel")

    # A teardown of an unrelated session must not reclaim a tombstone whose callback is live.
    await service.remove_by_prefix("unrelated-callback-", "unrelated-operation-")

    assert service._failed_cancellations == {
        "retained-tombstone-cancel": "retained-tombstone-callback"
    }
    with pytest.raises(CallbackConflictError, match="permanently failed"):
        await service.cancel(request.callback_id, idempotency_key="retained-tombstone-cancel")
    assert service.get(request.callback_id).status is CallbackStatus.CANCELLATION_REQUIRED
    assert await service.dispatch_due(lambda _: eligible_context()) == ()


@pytest.mark.asyncio
async def test_pending_schedule_cancellation_tombstone_is_reclaimed_with_the_callback() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    scheduler = AcceptedThenPermanentCancelAdapter()
    service = CallbackService(
        scheduler=scheduler,
        telephony=MockTelephonyAdapter(),
        policy=ActionPolicy(clock=clock),
        clock=clock,
    )
    request = CallbackRequest(
        lead_id=uuid4(),
        callback_id="pending-tombstone-callback",
        run_at=clock.now() + timedelta(minutes=1),
        timezone="UTC",
        idempotency_key="pending-tombstone-operation",
    )
    pending = asyncio.create_task(service.schedule(request, eligible_context()))
    await scheduler.accepted.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    with pytest.raises(PermanentAdapterError, match="definitive"):
        await service.remove_by_prefix("pending-tombstone-", "pending-tombstone-operation")

    assert service._failed_cancellations
    await service.remove_by_prefix("pending-tombstone-", "pending-tombstone-operation")

    assert service._pending_schedules == {}
    assert service._failed_cancellations == {}
    assert service._operation_fingerprints == {}
    assert service._operation_results == {}


@pytest.mark.asyncio
async def test_callback_bookkeeping_does_not_grow_with_session_count() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    scheduler = MockSchedulerAdapter(
        failures=[None, PermanentAdapterError("definitive cancellation failure"), None] * 6
    )
    service = CallbackService(
        scheduler=scheduler,
        telephony=MockTelephonyAdapter(),
        policy=ActionPolicy(clock=clock),
        clock=clock,
        max_callbacks=1,
    )
    for session in range(6):
        prefix = f"session-{session}-"
        request = CallbackRequest(
            lead_id=uuid4(),
            callback_id=f"{prefix}callback",
            run_at=clock.now() + timedelta(minutes=1),
            timezone="UTC",
            idempotency_key=f"{prefix}schedule",
        )
        await service.schedule(request, eligible_context())
        with pytest.raises(PermanentAdapterError, match="definitive"):
            await service.cancel(request.callback_id, idempotency_key=f"{prefix}cancel")
        await service.remove_by_prefix(prefix, prefix)

        assert service._records == {}
        assert service._failed_cancellations == {}
        assert service._operation_fingerprints == {}
        assert service._operation_results == {}
        assert service._pending_schedules == {}
        assert service._pending_cancellations == {}
        assert service._pending_schedule_cancellations == {}
        assert service._callback_incarnations == {}
        assert service._cleanup_attempts == {}


@pytest.mark.asyncio
async def test_a_deck_carries_what_the_buyer_actually_said() -> None:
    """A buyer opens a deck to find out whether they were listened to.

    Before PR 54 the deck received only the feature list, so the budget and the timing -
    the two facts that decide whether a proposal is worth reading - were captured by the
    conversation and then dropped. The title was the literal string "Sample Business".
    """

    service = DeckService(
        artifact_adapter=MockArtifactAdapter(),
        clock=FakeClock(datetime(2026, 1, 1, tzinfo=UTC)),
    )

    preview = await service.create(
        DeckRequest(
            lead_id=uuid4(),
            deck_id="deck-heard",
            industry=DeckIndustry.APPAREL,
            language=LanguageCode.ENGLISH,
            requested_features=("catalog", "online-payments"),
            budget_summary="budget is 150000",
            timeline_summary="3 months",
            idempotency_key="deck-heard-1",
        )
    )

    rendered = preview.model_dump_json()
    assert "Sample Business" not in rendered
    assert "150000" in rendered
    assert "3 months" in rendered
    # The cue word the extractor keeps must not survive onto a slide already labelled
    # "Budget", or the buyer reads "Budget: budget is 150000".
    assert "budget is 150000" not in rendered
    assert preview.slides[0].title == "What you told us"


@pytest.mark.asyncio
async def test_a_deck_says_so_when_a_commercial_was_never_discussed() -> None:
    """Silence is reported as silence rather than invented or omitted."""

    service = DeckService(
        artifact_adapter=MockArtifactAdapter(),
        clock=FakeClock(datetime(2026, 1, 1, tzinfo=UTC)),
    )

    preview = await service.create(
        DeckRequest(
            lead_id=uuid4(),
            deck_id="deck-silent",
            industry=DeckIndustry.BOOKS,
            language=LanguageCode.ENGLISH,
            requested_features=("catalog",),
            idempotency_key="deck-silent-1",
        )
    )

    heard = preview.slides[0].bullets
    assert any(bullet.startswith("Budget: not discussed yet") for bullet in heard)
    assert any(bullet.startswith("Timeline: not discussed yet") for bullet in heard)


@pytest.mark.asyncio
async def test_a_deck_is_written_in_the_language_it_was_asked_for() -> None:
    """`DeckRequest` has always insisted the language be explicit, then ignored it.

    Every deck was byte-identical in English, Hindi and Telugu, which for a product whose
    whole premise is selling in the buyer's language is the defect that matters most.
    """

    service = DeckService(
        artifact_adapter=MockArtifactAdapter(),
        clock=FakeClock(datetime(2026, 1, 1, tzinfo=UTC)),
    )

    rendered: dict[LanguageCode, str] = {}
    for language in (LanguageCode.ENGLISH, LanguageCode.HINDI, LanguageCode.TELUGU):
        preview = await service.create(
            DeckRequest(
                lead_id=uuid4(),
                deck_id=f"deck-{language.value}",
                industry=DeckIndustry.APPAREL,
                language=language,
                requested_features=("catalog",),
                idempotency_key=f"deck-language-{language.value}",
            )
        )
        rendered[language] = (
            preview.title
            + " "
            + " ".join(bullet for slide in preview.slides for bullet in slide.bullets)
        )

    assert len(set(rendered.values())) == 3
    assert "कपड़ों" in rendered[LanguageCode.HINDI]
    assert "దుస్తుల" in rendered[LanguageCode.TELUGU]


def test_every_deck_language_covers_the_whole_catalogue() -> None:
    """A missing industry or feature would be a KeyError in front of a customer.

    `DeckPhrases.__post_init__` enforces this at construction, so importing the module is
    the check. Asserting it here states the guarantee where a reader looks for it, and
    catches a language added to the enum without deck copy.
    """

    from pitchbot.actions.deck_content import deck_languages, phrases_for
    from pitchbot.domain import business_types
    from pitchbot.domain import features as catalog_features

    assert LanguageCode.UNKNOWN not in deck_languages()
    for language in deck_languages():
        phrases = phrases_for(language)
        assert set(phrases.industry_bullets) == set(business_types())
        assert set(phrases.feature_label) == set(catalog_features())

    # UNKNOWN falls back rather than raising, matching the planner.
    assert phrases_for(LanguageCode.UNKNOWN) is phrases_for(LanguageCode.ENGLISH)
