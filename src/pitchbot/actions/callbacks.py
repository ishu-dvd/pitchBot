from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

from pitchbot.actions.models import (
    ActionAuthorizationContext,
    BlockReason,
    CallbackRecord,
    CallbackRequest,
    CallbackStatus,
)
from pitchbot.actions.policy import ActionPolicy
from pitchbot.adapters import Clock, SchedulerAdapter, TelephonyAdapter
from pitchbot.domain import ActionType


class CallbackConflictError(RuntimeError):
    pass


class CallbackService:
    def __init__(
        self,
        *,
        scheduler: SchedulerAdapter,
        telephony: TelephonyAdapter,
        policy: ActionPolicy,
        clock: Clock,
        max_callbacks: int = 100,
        max_future: timedelta = timedelta(days=90),
    ) -> None:
        if max_callbacks < 1 or max_future.total_seconds() <= 0:
            raise ValueError("Callback limits must be positive")
        self._scheduler = scheduler
        self._telephony = telephony
        self._policy = policy
        self._clock = clock
        self._max_callbacks = max_callbacks
        self._max_future = max_future
        self._records: dict[str, CallbackRecord] = {}
        self._operation_fingerprints: dict[str, tuple[str, str]] = {}
        self._operation_results: dict[str, CallbackRecord] = {}

    async def schedule(
        self,
        request: CallbackRequest,
        context: ActionAuthorizationContext,
    ) -> CallbackRecord:
        fingerprint = ("schedule", request.model_dump_json())
        replay = self._check_operation(request.idempotency_key, fingerprint)
        if replay is not None:
            return replay
        if (
            request.run_at <= self._clock.now()
            or request.run_at > self._clock.now() + self._max_future
        ):
            return self._blocked(request, BlockReason.CALLBACK_TIME_INVALID)
        existing = self._records.get(request.callback_id)
        active_count = sum(
            record.status is CallbackStatus.SCHEDULED for record in self._records.values()
        )
        if existing is None and active_count >= self._max_callbacks:
            raise RuntimeError("Callback capacity reached")
        if existing is not None and existing.status is CallbackStatus.SCHEDULED:
            raise CallbackConflictError("Callback is already scheduled; cancel before rescheduling")

        decision = self._policy.authorize(ActionType.CALLBACK_SCHEDULE, context)
        if decision.reasons:
            return self._blocked(request, *decision.reasons)
        result = await self._scheduler.schedule(
            request.callback_id,
            request.run_at,
            {
                "timezone": request.timezone,
                "agenda": request.agenda.value,
            },
            request.idempotency_key,
        )
        record = CallbackRecord(
            request=request,
            status=CallbackStatus.SCHEDULED,
            provider_reference=result.provider_reference,
            updated_at=self._clock.now(),
        )
        self._store_operation(request.idempotency_key, fingerprint, record)
        return record

    async def cancel(self, callback_id: str, *, idempotency_key: str) -> CallbackRecord:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key must not be empty")
        fingerprint = ("cancel", callback_id)
        replay = self._check_operation(idempotency_key, fingerprint)
        if replay is not None:
            return replay
        try:
            existing = self._records[callback_id]
        except KeyError as error:
            raise LookupError("Callback not found") from error
        if existing.status is not CallbackStatus.SCHEDULED:
            raise CallbackConflictError("Only a scheduled callback can be canceled")
        result = await self._scheduler.cancel(callback_id, idempotency_key)
        record = existing.model_copy(
            update={
                "status": CallbackStatus.CANCELED,
                "provider_reference": result.provider_reference,
                "updated_at": self._clock.now(),
            }
        )
        self._store_operation(idempotency_key, fingerprint, record)
        return record

    async def dispatch_due(
        self,
        context_for: Callable[[CallbackRequest], ActionAuthorizationContext],
    ) -> tuple[CallbackRecord, ...]:
        dispatched: list[CallbackRecord] = []
        due = sorted(
            (
                record
                for record in self._records.values()
                if record.status is CallbackStatus.SCHEDULED
                and record.request.run_at <= self._clock.now()
            ),
            key=lambda item: (item.request.run_at, item.request.callback_id),
        )
        for record in due:
            context = context_for(record.request)
            decision = self._policy.authorize(ActionType.CALLBACK_SCHEDULE, context)
            if decision.reasons:
                updated = record.model_copy(
                    update={
                        "status": CallbackStatus.BLOCKED,
                        "block_reasons": (BlockReason.POLICY_CHANGED, *decision.reasons),
                        "updated_at": self._clock.now(),
                    }
                )
            else:
                operation_key = f"dispatch:{record.request.idempotency_key}"
                result = await self._telephony.dial(f"lead:{record.request.lead_id}", operation_key)
                updated = record.model_copy(
                    update={
                        "status": CallbackStatus.DISPATCHED,
                        "provider_reference": result.provider_reference,
                        "updated_at": self._clock.now(),
                    }
                )
            self._records[record.request.callback_id] = updated
            dispatched.append(updated)
        return tuple(dispatched)

    def get(self, callback_id: str) -> CallbackRecord:
        try:
            return self._records[callback_id]
        except KeyError as error:
            raise LookupError("Callback not found") from error

    def _blocked(self, request: CallbackRequest, *reasons: BlockReason) -> CallbackRecord:
        record = CallbackRecord(
            request=request,
            status=CallbackStatus.BLOCKED,
            block_reasons=tuple(dict.fromkeys(reasons)),
            updated_at=self._clock.now(),
        )
        fingerprint = ("schedule", request.model_dump_json())
        self._store_operation(request.idempotency_key, fingerprint, record)
        return record

    def _check_operation(
        self, idempotency_key: str, fingerprint: tuple[str, str]
    ) -> CallbackRecord | None:
        previous = self._operation_fingerprints.get(idempotency_key)
        if previous is not None and previous != fingerprint:
            raise CallbackConflictError("Idempotency key reused with different callback input")
        if previous is None:
            return None
        try:
            return self._operation_results[idempotency_key]
        except KeyError as error:
            raise RuntimeError("Callback idempotency state is inconsistent") from error

    def _store_operation(
        self,
        idempotency_key: str,
        fingerprint: tuple[str, str],
        record: CallbackRecord,
    ) -> None:
        self._operation_fingerprints[idempotency_key] = fingerprint
        self._operation_results[idempotency_key] = record
        self._records[record.request.callback_id] = record
