from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from pitchbot.actions.models import (
    ActionAuthorizationContext,
    BlockReason,
    CallbackRecord,
    CallbackRequest,
    CallbackStatus,
)
from pitchbot.actions.policy import ActionPolicy
from pitchbot.adapters import (
    Clock,
    EphemeralOperationStore,
    PermanentAdapterError,
    SchedulerAdapter,
    TelephonyAdapter,
)
from pitchbot.domain import ActionType


class CallbackConflictError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _PendingSchedule:
    request: CallbackRequest
    context: ActionAuthorizationContext
    fingerprint: tuple[str, str]
    incarnation: int


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
        self._pending_schedules: dict[str, _PendingSchedule] = {}
        self._pending_cancellations: dict[str, str] = {}
        self._pending_schedule_cancellations: dict[str, str] = {}
        self._failed_cancellations: dict[str, str] = {}
        self._callback_incarnations: dict[str, int] = {}
        self._cleanup_attempts: dict[tuple[str, int], int] = {}
        self._incarnation_sequence = 0
        self._lock = asyncio.Lock()

    async def schedule(
        self,
        request: CallbackRequest,
        context: ActionAuthorizationContext,
    ) -> CallbackRecord:
        async with self._lock:
            return await self._schedule(request, context)

    async def _schedule(
        self,
        request: CallbackRequest,
        context: ActionAuthorizationContext,
    ) -> CallbackRecord:
        fingerprint = ("schedule", request.model_dump_json())
        replay = self._check_operation(request.idempotency_key, fingerprint)
        if replay is not None:
            return replay
        pending = self._pending_schedules.get(request.idempotency_key)
        if pending is not None:
            if pending.fingerprint != fingerprint:
                raise CallbackConflictError("Idempotency key reused with different callback input")
            context = pending.context
        else:
            if (
                request.run_at <= self._clock.now()
                or request.run_at > self._clock.now() + self._max_future
            ):
                return self._blocked(request, BlockReason.CALLBACK_TIME_INVALID)
            existing = self._records.get(request.callback_id)
            active_callback_ids = {
                record.request.callback_id
                for record in self._records.values()
                if record.status
                in {
                    CallbackStatus.SCHEDULED,
                    CallbackStatus.CANCELLATION_PENDING,
                    CallbackStatus.CANCELLATION_REQUIRED,
                }
            }
            active_callback_ids.update(
                item.request.callback_id for item in self._pending_schedules.values()
            )
            if (
                request.callback_id not in active_callback_ids
                and len(active_callback_ids) >= self._max_callbacks
            ):
                raise RuntimeError("Callback capacity reached")
            if existing is not None and existing.status in {
                CallbackStatus.SCHEDULED,
                CallbackStatus.CANCELLATION_PENDING,
                CallbackStatus.CANCELLATION_REQUIRED,
            }:
                raise CallbackConflictError(
                    "Callback is already scheduled or cancellation is pending"
                )
            if request.callback_id in active_callback_ids:
                raise CallbackConflictError("Callback scheduling outcome is pending")

            decision = self._policy.authorize(ActionType.CALLBACK_SCHEDULE, context)
            if decision.reasons:
                return self._blocked(request, *decision.reasons)
            self._incarnation_sequence += 1
            pending = _PendingSchedule(
                request=request,
                context=context,
                fingerprint=fingerprint,
                incarnation=self._incarnation_sequence,
            )
            self._pending_schedules[request.idempotency_key] = pending
        try:
            result = await self._scheduler.schedule(
                request.callback_id,
                request.run_at,
                {
                    "timezone": request.timezone,
                    "agenda": request.agenda.value,
                },
                request.idempotency_key,
            )
        except PermanentAdapterError:
            self._pending_schedules.pop(request.idempotency_key, None)
            raise
        record = CallbackRecord(
            request=request,
            status=CallbackStatus.SCHEDULED,
            provider_reference=result.provider_reference,
            updated_at=self._clock.now(),
        )
        self._pending_schedules.pop(request.idempotency_key, None)
        self._callback_incarnations[request.callback_id] = pending.incarnation
        self._store_operation(request.idempotency_key, fingerprint, record)
        return record

    async def cancel(self, callback_id: str, *, idempotency_key: str) -> CallbackRecord:
        async with self._lock:
            return await self._cancel(callback_id, idempotency_key=idempotency_key)

    async def _cancel(self, callback_id: str, *, idempotency_key: str) -> CallbackRecord:
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
        pending_key = self._pending_cancellations.get(callback_id)
        if existing.status is CallbackStatus.CANCELLATION_PENDING:
            if pending_key != idempotency_key:
                raise CallbackConflictError("Callback cancellation outcome is pending")
        elif existing.status in {
            CallbackStatus.SCHEDULED,
            CallbackStatus.CANCELLATION_REQUIRED,
        }:
            self._records[callback_id] = existing.model_copy(
                update={
                    "status": CallbackStatus.CANCELLATION_PENDING,
                    "updated_at": self._clock.now(),
                }
            )
            self._pending_cancellations[callback_id] = idempotency_key
        else:
            raise CallbackConflictError("Only a scheduled callback can be canceled")
        try:
            result = await self._scheduler.cancel(callback_id, idempotency_key)
        except PermanentAdapterError:
            self._records[callback_id] = existing.model_copy(
                update={
                    "status": CallbackStatus.CANCELLATION_REQUIRED,
                    "updated_at": self._clock.now(),
                }
            )
            self._pending_cancellations.pop(callback_id, None)
            self._record_failed_cancellation(idempotency_key, callback_id)
            raise
        record = existing.model_copy(
            update={
                "status": CallbackStatus.CANCELED,
                "provider_reference": result.provider_reference,
                "updated_at": self._clock.now(),
            }
        )
        self._pending_cancellations.pop(callback_id, None)
        self._store_operation(idempotency_key, fingerprint, record)
        return record

    async def dispatch_due(
        self,
        context_for: Callable[[CallbackRequest], ActionAuthorizationContext],
    ) -> tuple[CallbackRecord, ...]:
        async with self._lock:
            return await self._dispatch_due(context_for)

    async def _dispatch_due(
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

    async def remove_by_prefix(self, callback_id_prefix: str, operation_key_prefix: str) -> None:
        async with self._lock:
            pending_schedule_keys = tuple(
                key
                for key, pending in self._pending_schedules.items()
                if pending.request.callback_id.startswith(callback_id_prefix)
            )
            for key in pending_schedule_keys:
                pending = self._pending_schedules[key]
                cancellation_key = self._pending_schedule_cancellations.get(key)
                if cancellation_key is None:
                    cancellation_key = self._next_cleanup_key(
                        pending.request.callback_id,
                        pending.incarnation,
                    )
                    self._pending_schedule_cancellations[key] = cancellation_key
                try:
                    await self._scheduler.cancel(
                        pending.request.callback_id,
                        cancellation_key,
                    )
                except PermanentAdapterError:
                    self._pending_schedule_cancellations.pop(key, None)
                    self._record_failed_cancellation(
                        cancellation_key,
                        pending.request.callback_id,
                    )
                    raise
                self._pending_schedules.pop(key, None)
                self._pending_schedule_cancellations.pop(key, None)
                self._cleanup_attempts.pop(
                    (pending.request.callback_id, pending.incarnation),
                    None,
                )
            callback_ids = tuple(
                callback_id
                for callback_id in self._records
                if callback_id.startswith(callback_id_prefix)
            )
            for callback_id in callback_ids:
                record = self._records[callback_id]
                if record.status in {
                    CallbackStatus.SCHEDULED,
                    CallbackStatus.CANCELLATION_REQUIRED,
                }:
                    cancellation_key = self._next_cleanup_key(
                        callback_id,
                        self._callback_incarnations[callback_id],
                    )
                    self._records[callback_id] = record.model_copy(
                        update={
                            "status": CallbackStatus.CANCELLATION_PENDING,
                            "updated_at": self._clock.now(),
                        }
                    )
                    self._pending_cancellations[callback_id] = cancellation_key
                    try:
                        await self._scheduler.cancel(callback_id, cancellation_key)
                    except PermanentAdapterError:
                        self._records[callback_id] = record.model_copy(
                            update={
                                "status": CallbackStatus.CANCELLATION_REQUIRED,
                                "updated_at": self._clock.now(),
                            }
                        )
                        self._pending_cancellations.pop(callback_id, None)
                        self._record_failed_cancellation(cancellation_key, callback_id)
                        raise
                elif record.status is CallbackStatus.CANCELLATION_PENDING:
                    cancellation_key = self._pending_cancellations[callback_id]
                    try:
                        await self._scheduler.cancel(callback_id, cancellation_key)
                    except PermanentAdapterError:
                        self._records[callback_id] = record.model_copy(
                            update={
                                "status": CallbackStatus.CANCELLATION_REQUIRED,
                                "updated_at": self._clock.now(),
                            }
                        )
                        self._pending_cancellations.pop(callback_id, None)
                        self._record_failed_cancellation(cancellation_key, callback_id)
                        raise
                elif record.status is CallbackStatus.DISPATCHED and isinstance(
                    self._scheduler, EphemeralOperationStore
                ):
                    await self._scheduler.cancel(
                        callback_id,
                        self._next_cleanup_key(
                            callback_id,
                            self._callback_incarnations[callback_id],
                        ),
                    )
                self._pending_cancellations.pop(callback_id, None)
                operation_keys = tuple(
                    key
                    for key, result in self._operation_results.items()
                    if result.request.callback_id == callback_id
                )
                for key in operation_keys:
                    self._operation_fingerprints.pop(key, None)
                    self._operation_results.pop(key, None)
                incarnation = self._callback_incarnations.pop(callback_id, None)
                if incarnation is not None:
                    self._cleanup_attempts.pop((callback_id, incarnation), None)
                self._records.pop(callback_id, None)
            self._release_failed_cancellations(callback_id_prefix)
            if isinstance(self._scheduler, EphemeralOperationStore):
                self._scheduler.clear_operations(operation_key_prefix)
                self._scheduler.clear_operations(f"cleanup:{callback_id_prefix}")
            if isinstance(self._telephony, EphemeralOperationStore):
                self._telephony.clear_operations(f"dispatch:{operation_key_prefix}")

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
        if self._failed_cancellations.get(idempotency_key) is not None:
            raise CallbackConflictError(
                "Callback cancellation operation permanently failed; use a new idempotency key"
            )
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

    def _next_cleanup_key(self, callback_id: str, incarnation: int) -> str:
        attempt_key = (callback_id, incarnation)
        attempt = self._cleanup_attempts.get(attempt_key, 0) + 1
        self._cleanup_attempts[attempt_key] = attempt
        return f"cleanup:{callback_id}:{incarnation}:{attempt}"

    def _record_failed_cancellation(self, idempotency_key: str, callback_id: str) -> None:
        self._operation_fingerprints[idempotency_key] = ("cancel", callback_id)
        self._failed_cancellations[idempotency_key] = callback_id

    def _release_failed_cancellations(self, callback_id_prefix: str) -> None:
        # A tombstone exists so a permanently-rejected cancellation key cannot be replayed
        # against the callback it failed to cancel. That guarantee is only meaningful while
        # the callback is still live, and cleanup keys are minted per session, so retaining
        # the entry past teardown leaks one permanent pair per occurrence for the lifetime of
        # the process. Reclaim only keys whose callback is absent from every live map, so a
        # tombstone still outlives any resource it protects.
        live_callback_ids = set(self._records)
        live_callback_ids.update(
            pending.request.callback_id for pending in self._pending_schedules.values()
        )
        reclaimable = tuple(
            key
            for key, callback_id in self._failed_cancellations.items()
            if callback_id.startswith(callback_id_prefix) and callback_id not in live_callback_ids
        )
        for key in reclaimable:
            self._failed_cancellations.pop(key, None)
            self._operation_fingerprints.pop(key, None)
            self._operation_results.pop(key, None)
