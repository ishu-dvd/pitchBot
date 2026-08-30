from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from pitchbot.domain import PrivacyOperationType
from pitchbot.storage import (
    AggregateClosedError,
    AggregateTypeConflictError,
    ConcurrencyConflictError,
    SqlAlchemyEventRepository,
    SqlAlchemyPrivacyRepository,
    SqlAlchemySuppressionRepository,
)


def repositories(
    session_factory: sessionmaker[Session],
) -> tuple[
    SqlAlchemyEventRepository,
    SqlAlchemySuppressionRepository,
    SqlAlchemyPrivacyRepository,
]:
    events = SqlAlchemyEventRepository(session_factory)
    suppressions = SqlAlchemySuppressionRepository(session_factory)
    privacy = SqlAlchemyPrivacyRepository(session_factory, events)
    return events, suppressions, privacy


def test_append_only_journey_preserves_order_and_versions(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    events, _, _ = repositories(session_factory)
    lead_id = uuid4()

    first = events.append(
        lead_id,
        "lead",
        "lead.created",
        {"display_name": "Synthetic buyer"},
        expected_version=0,
    )
    second = events.append(
        lead_id,
        "lead",
        "requirement.captured",
        {"key": "budget", "value": "under review"},
        expected_version=1,
    )

    journey = events.read(lead_id)
    assert first.aggregate_version == 1
    assert second.aggregate_version == 2
    assert [event.event_type for event in journey] == [
        "lead.created",
        "requirement.captured",
    ]
    assert events.current_version(lead_id) == 2


def test_stale_expected_version_is_rejected(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    events, _, _ = repositories(session_factory)
    lead_id = uuid4()
    events.append(lead_id, "lead", "lead.created", {}, expected_version=0)

    with pytest.raises(ConcurrencyConflictError, match="Expected version 0, found 1"):
        events.append(lead_id, "lead", "lead.updated", {}, expected_version=0)


def test_aggregate_type_cannot_change(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    events, _, _ = repositories(session_factory)
    aggregate_id = uuid4()
    events.append(aggregate_id, "lead", "lead.created", {})

    with pytest.raises(AggregateTypeConflictError):
        events.append(aggregate_id, "session", "session.created", {})


def test_global_and_channel_suppression_fail_closed(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    _, suppressions, _ = repositories(session_factory)
    lead_id = uuid4()

    assert suppressions.is_suppressed(lead_id, "whatsapp") is False
    suppressions.record(lead_id, "all", True, "customer opted out")
    suppressions.record(lead_id, "whatsapp", False, "channel correction")

    assert suppressions.is_suppressed(lead_id, "whatsapp") is True
    assert suppressions.is_suppressed(lead_id, "telephony") is True


def test_suppression_rejects_empty_channel_and_reason(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    _, suppressions, _ = repositories(session_factory)
    lead_id = uuid4()

    with pytest.raises(ValueError, match="channel"):
        suppressions.record(lead_id, " ", True, "opted out")
    with pytest.raises(ValueError, match="reason"):
        suppressions.record(lead_id, "all", True, " ")
    with pytest.raises(ValueError, match="channel"):
        suppressions.is_suppressed(lead_id, " ")


def test_redacted_export_and_anonymization(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    events, _, privacy = repositories(session_factory)
    lead_id = uuid4()
    events.append(
        lead_id,
        "lead",
        "lead.created",
        {
            "display_name": "Synthetic buyer",
            "business": {"email": "buyer@example.invalid", "category": "apparel"},
        },
    )

    exported = privacy.export_redacted(lead_id)
    payload = exported[0]["payload"]
    assert isinstance(payload, dict)
    assert payload["display_name"] == "[REDACTED]"
    assert payload["business"] == {
        "email": "[REDACTED]",
        "category": "apparel",
    }

    assert privacy.anonymize(lead_id) == 1
    anonymized = events.read(lead_id)
    assert anonymized[0].payload == {"anonymized": True}
    assert anonymized[0].aggregate_version == 1
    history = privacy.history(lead_id)
    assert len(history) == 1
    assert history[0].operation is PrivacyOperationType.ANONYMIZED
    assert history[0].affected_event_count == 1
    assert privacy.anonymize(lead_id) == 0
    assert len(privacy.history(lead_id)) == 1
    with pytest.raises(AggregateClosedError):
        events.append(lead_id, "lead", "lead.updated", {})


def test_hard_delete_removes_journey_but_preserves_suppression(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    events, suppressions, privacy = repositories(session_factory)
    lead_id = uuid4()
    events.append(lead_id, "lead", "lead.created", {})
    suppressions.record(lead_id, "all", True, "customer opted out")

    assert privacy.hard_delete(lead_id) == (1, 1)
    assert events.read(lead_id) == []
    assert events.current_version(lead_id) == 1
    assert suppressions.is_suppressed(lead_id, "telephony") is True
    history = privacy.history(lead_id)
    assert len(history) == 1
    assert history[0].operation is PrivacyOperationType.HARD_DELETED
    assert privacy.hard_delete(lead_id) == (0, 0)
    assert len(privacy.history(lead_id)) == 1
    with pytest.raises(AggregateClosedError):
        events.append(lead_id, "lead", "lead.recreated", {})


def test_retention_is_dry_run_by_default_and_protects_opt_out_events(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    events, _, privacy = repositories(session_factory)
    lead_id = uuid4()
    old = datetime.now(UTC) - timedelta(days=90)
    cutoff = datetime.now(UTC) - timedelta(days=30)
    events.append(lead_id, "lead", "lead.created", {}, occurred_at=old)
    events.append(lead_id, "lead", "lead.opted_out", {}, occurred_at=old)

    assert privacy.purge_expired(cutoff) == 1
    assert len(events.read(lead_id)) == 2
    assert privacy.purge_expired(cutoff, dry_run=False) == 1
    assert [event.event_type for event in events.read(lead_id)] == ["lead.opted_out"]
    assert events.current_version(lead_id) == 2

    next_event = events.append(
        lead_id,
        "lead",
        "consent.revoked",
        {},
        expected_version=2,
    )
    assert next_event.aggregate_version == 3
