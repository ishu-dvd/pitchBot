from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from pitchbot.domain import (
    AuditEvent,
    JsonValue,
    PrivacyOperation,
    PrivacyOperationType,
    utc_now,
)
from pitchbot.storage.models import (
    AggregateRecord,
    EventRecord,
    PrivacyOperationRecord,
    SuppressionRecord,
)
from pitchbot.storage.privacy import redact_json

PROTECTED_RETENTION_EVENT_TYPES = frozenset({"consent.revoked", "lead.opted_out"})


@dataclass(frozen=True, slots=True)
class AggregateStatus:
    aggregate_type: str
    current_version: int
    privacy_state: str


class ConcurrencyConflictError(RuntimeError):
    pass


class AggregateTypeConflictError(RuntimeError):
    pass


class AggregateClosedError(RuntimeError):
    pass


class EventRepository(Protocol):
    def append(
        self,
        aggregate_id: UUID,
        aggregate_type: str,
        event_type: str,
        payload: dict[str, JsonValue],
        *,
        expected_version: int | None = None,
        occurred_at: datetime | None = None,
    ) -> AuditEvent: ...

    def read(
        self,
        aggregate_id: UUID,
        *,
        limit: int | None = None,
    ) -> Sequence[AuditEvent]: ...

    def current_version(self, aggregate_id: UUID) -> int: ...

    def status(self, aggregate_id: UUID) -> AggregateStatus | None: ...


class SuppressionRepository(Protocol):
    def record(
        self,
        lead_id: UUID,
        channel: str,
        suppressed: bool,
        reason: str,
        *,
        occurred_at: datetime | None = None,
    ) -> UUID: ...

    def is_suppressed(self, lead_id: UUID, channel: str) -> bool: ...


class PrivacyRepository(Protocol):
    def export_redacted(self, aggregate_id: UUID) -> list[dict[str, JsonValue]]: ...

    def anonymize(self, aggregate_id: UUID) -> int: ...

    def hard_delete(self, aggregate_id: UUID) -> tuple[int, int]: ...

    def purge_expired(self, cutoff: datetime, *, dry_run: bool = True) -> int: ...

    def history(self, aggregate_id: UUID) -> Sequence[PrivacyOperation]: ...


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SqlAlchemyEventRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def append(
        self,
        aggregate_id: UUID,
        aggregate_type: str,
        event_type: str,
        payload: dict[str, JsonValue],
        *,
        expected_version: int | None = None,
        occurred_at: datetime | None = None,
    ) -> AuditEvent:
        with self._session_factory() as session:
            aggregate = session.get(AggregateRecord, str(aggregate_id))
            current_version = aggregate.current_version if aggregate is not None else 0
            if aggregate is not None and aggregate.aggregate_type != aggregate_type:
                raise AggregateTypeConflictError(
                    f"Aggregate {aggregate_id} is {aggregate.aggregate_type}, not {aggregate_type}"
                )
            if aggregate is not None and aggregate.privacy_state != "active":
                raise AggregateClosedError(
                    f"Aggregate {aggregate_id} is closed for privacy state "
                    f"{aggregate.privacy_state}"
                )
            if expected_version is not None and expected_version != current_version:
                raise ConcurrencyConflictError(
                    f"Expected version {expected_version}, found {current_version}"
                )

            event_time = occurred_at or utc_now()
            event = AuditEvent(
                aggregate_id=aggregate_id,
                aggregate_type=aggregate_type,
                aggregate_version=current_version + 1,
                event_type=event_type,
                payload=payload,
                occurred_at=event_time,
            )
            if aggregate is None:
                aggregate = AggregateRecord(
                    aggregate_id=str(aggregate_id),
                    aggregate_type=aggregate_type,
                    current_version=event.aggregate_version,
                    privacy_state="active",
                    created_at=event_time,
                    updated_at=event_time,
                )
                session.add(aggregate)
            else:
                update_result = session.execute(
                    update(AggregateRecord)
                    .where(
                        AggregateRecord.aggregate_id == str(aggregate_id),
                        AggregateRecord.aggregate_type == aggregate_type,
                        AggregateRecord.current_version == current_version,
                        AggregateRecord.privacy_state == "active",
                    )
                    .values(
                        current_version=event.aggregate_version,
                        updated_at=event_time,
                    )
                )
                if int(cast(CursorResult[Any], update_result).rowcount or 0) != 1:
                    session.rollback()
                    refreshed = self.status(aggregate_id)
                    if refreshed is not None and refreshed.privacy_state != "active":
                        raise AggregateClosedError(
                            f"Aggregate {aggregate_id} is closed for privacy state "
                            f"{refreshed.privacy_state}"
                        )
                    raise ConcurrencyConflictError(
                        f"Concurrent update for aggregate {aggregate_id}"
                    )
            session.add(
                EventRecord(
                    event_id=str(event.event_id),
                    aggregate_id=str(event.aggregate_id),
                    aggregate_type=event.aggregate_type,
                    aggregate_version=event.aggregate_version,
                    event_type=event.event_type,
                    payload=cast(dict[str, object], event.payload),
                    occurred_at=event.occurred_at,
                )
            )
            try:
                session.commit()
            except IntegrityError as error:
                session.rollback()
                raise ConcurrencyConflictError(
                    f"Concurrent update for aggregate {aggregate_id}"
                ) from error
            return event

    def read(
        self,
        aggregate_id: UUID,
        *,
        limit: int | None = None,
    ) -> Sequence[AuditEvent]:
        if limit is not None and not 1 <= limit <= 10_000:
            raise ValueError("event read limit must be between 1 and 10000")
        with self._session_factory() as session:
            statement = (
                select(EventRecord)
                .where(EventRecord.aggregate_id == str(aggregate_id))
                .order_by(EventRecord.aggregate_version.asc())
            )
            if limit is not None:
                statement = statement.limit(limit)
            rows = session.scalars(statement).all()
            return [self._to_domain(row) for row in rows]

    def current_version(self, aggregate_id: UUID) -> int:
        status = self.status(aggregate_id)
        return status.current_version if status is not None else 0

    def status(self, aggregate_id: UUID) -> AggregateStatus | None:
        with self._session_factory() as session:
            row = session.execute(
                select(
                    AggregateRecord.aggregate_type,
                    AggregateRecord.current_version,
                    AggregateRecord.privacy_state,
                ).where(AggregateRecord.aggregate_id == str(aggregate_id))
            ).one_or_none()
            if row is None:
                return None
            return AggregateStatus(
                aggregate_type=str(row.aggregate_type),
                current_version=int(row.current_version),
                privacy_state=str(row.privacy_state),
            )

    @staticmethod
    def _to_domain(row: EventRecord) -> AuditEvent:
        return AuditEvent(
            event_id=UUID(row.event_id),
            aggregate_id=UUID(row.aggregate_id),
            aggregate_type=row.aggregate_type,
            aggregate_version=row.aggregate_version,
            event_type=row.event_type,
            payload=cast(dict[str, JsonValue], row.payload),
            occurred_at=_as_utc(row.occurred_at),
        )


class SqlAlchemySuppressionRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def record(
        self,
        lead_id: UUID,
        channel: str,
        suppressed: bool,
        reason: str,
        *,
        occurred_at: datetime | None = None,
    ) -> UUID:
        channel = channel.strip().casefold()
        reason = reason.strip()
        if not channel:
            raise ValueError("channel must not be empty")
        if not reason:
            raise ValueError("reason must not be empty")

        suppression_id = uuid4()
        with self._session_factory() as session:
            session.add(
                SuppressionRecord(
                    suppression_id=str(suppression_id),
                    lead_id=str(lead_id),
                    channel=channel,
                    suppressed=suppressed,
                    reason=reason,
                    occurred_at=occurred_at or utc_now(),
                )
            )
            session.commit()
        return suppression_id

    def is_suppressed(self, lead_id: UUID, channel: str) -> bool:
        channel = channel.strip().casefold()
        if not channel:
            raise ValueError("channel must not be empty")

        with self._session_factory() as session:
            global_state = self._latest_state(session, lead_id, "all")
            channel_state = self._latest_state(session, lead_id, channel)
            return global_state is True or channel_state is True

    @staticmethod
    def _latest_state(session: Session, lead_id: UUID, channel: str) -> bool | None:
        value = session.scalar(
            select(SuppressionRecord.suppressed)
            .where(
                SuppressionRecord.lead_id == str(lead_id),
                SuppressionRecord.channel == channel,
            )
            .order_by(SuppressionRecord.sequence.desc())
            .limit(1)
        )
        return value


class SqlAlchemyPrivacyRepository:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        event_repository: EventRepository,
    ) -> None:
        self._session_factory = session_factory
        self._event_repository = event_repository

    def export_redacted(self, aggregate_id: UUID) -> list[dict[str, JsonValue]]:
        return [
            {
                "event_id": str(event.event_id),
                "aggregate_id": str(event.aggregate_id),
                "aggregate_type": event.aggregate_type,
                "aggregate_version": event.aggregate_version,
                "event_type": event.event_type,
                "payload": redact_json(event.payload),
                "occurred_at": event.occurred_at.isoformat(),
            }
            for event in self._event_repository.read(aggregate_id)
        ]

    def anonymize(self, aggregate_id: UUID) -> int:
        anonymized_at = utc_now()
        with self._session_factory() as session:
            aggregate_result = session.execute(
                update(AggregateRecord)
                .where(
                    AggregateRecord.aggregate_id == str(aggregate_id),
                    AggregateRecord.privacy_state == "active",
                )
                .values(privacy_state="anonymized", updated_at=anonymized_at)
            )
            aggregate_count = int(cast(CursorResult[Any], aggregate_result).rowcount or 0)
            affected = 0
            if aggregate_count:
                result = session.execute(
                    update(EventRecord)
                    .where(
                        EventRecord.aggregate_id == str(aggregate_id),
                        EventRecord.anonymized_at.is_(None),
                    )
                    .values(payload={"anonymized": True}, anonymized_at=anonymized_at)
                )
                affected = int(cast(CursorResult[Any], result).rowcount or 0)
                session.add(
                    PrivacyOperationRecord(
                        operation_id=str(uuid4()),
                        aggregate_id=str(aggregate_id),
                        operation=PrivacyOperationType.ANONYMIZED.value,
                        affected_event_count=affected,
                        occurred_at=anonymized_at,
                    )
                )
            session.commit()
            return affected

    def hard_delete(self, aggregate_id: UUID) -> tuple[int, int]:
        deleted_at = utc_now()
        with self._session_factory() as session:
            aggregate_result = session.execute(
                update(AggregateRecord)
                .where(
                    AggregateRecord.aggregate_id == str(aggregate_id),
                    AggregateRecord.privacy_state != "hard-deleted",
                )
                .values(privacy_state="hard-deleted", updated_at=deleted_at)
            )
            aggregate_count = int(cast(CursorResult[Any], aggregate_result).rowcount or 0)
            event_count = 0
            if aggregate_count:
                event_result = session.execute(
                    delete(EventRecord).where(EventRecord.aggregate_id == str(aggregate_id))
                )
                event_count = int(cast(CursorResult[Any], event_result).rowcount or 0)
                session.add(
                    PrivacyOperationRecord(
                        operation_id=str(uuid4()),
                        aggregate_id=str(aggregate_id),
                        operation=PrivacyOperationType.HARD_DELETED.value,
                        affected_event_count=event_count,
                        occurred_at=deleted_at,
                    )
                )
            session.commit()
            return event_count, aggregate_count

    def purge_expired(self, cutoff: datetime, *, dry_run: bool = True) -> int:
        cutoff = _as_utc(cutoff)
        eligible = (
            EventRecord.occurred_at < cutoff,
            EventRecord.event_type.not_in(PROTECTED_RETENTION_EVENT_TYPES),
        )
        with self._session_factory() as session:
            count = int(
                session.scalar(select(func.count()).select_from(EventRecord).where(*eligible)) or 0
            )
            if not dry_run and count:
                session.execute(delete(EventRecord).where(*eligible))
                session.commit()
            return count

    def history(self, aggregate_id: UUID) -> Sequence[PrivacyOperation]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(PrivacyOperationRecord)
                .where(PrivacyOperationRecord.aggregate_id == str(aggregate_id))
                .order_by(PrivacyOperationRecord.sequence.asc())
            ).all()
            return [
                PrivacyOperation(
                    operation_id=UUID(row.operation_id),
                    aggregate_id=UUID(row.aggregate_id),
                    operation=PrivacyOperationType(row.operation),
                    affected_event_count=row.affected_event_count,
                    occurred_at=_as_utc(row.occurred_at),
                )
                for row in rows
            ]
