from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from pitchbot.conversation import (
    ConversationDisposition,
    ConversationEngine,
    ConversationJournal,
    JournalCorruptionError,
    JournaledConversationTurn,
    JournalHistoryUnavailableError,
    JournalOperationConflictError,
    canonical_operation_fingerprint,
)
from pitchbot.domain import AuditEvent, JsonValue, LanguageCode
from pitchbot.storage import (
    AggregateStatus,
    ConcurrencyConflictError,
    EventRepository,
    SqlAlchemyEventRepository,
    SqlAlchemyPrivacyRepository,
)
from pitchbot.storage.models import EventRecord

TURN_DIGEST_KEY = b"journal-test-turn-digest-key-32b"


def _engine() -> ConversationEngine:
    return ConversationEngine(max_goal_changes=2, turn_digest_key=TURN_DIGEST_KEY)


def _process(
    journal: ConversationJournal,
    engine: ConversationEngine,
    session_id: UUID,
    *,
    text: str,
    operation_id: UUID | None = None,
    expected_version: int | None = None,
    occurred_at: datetime | None = None,
) -> JournaledConversationTurn:
    return journal.process_turn(
        engine,
        session_id,
        operation_id=operation_id or uuid4(),
        text=text,
        language=LanguageCode.ENGLISH,
        expected_version=expected_version,
        occurred_at=occurred_at,
    )


def test_journal_restores_incremental_state_without_raw_or_duplicated_turn_text(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    repository = SqlAlchemyEventRepository(session_factory)
    journal = ConversationJournal(repository)
    session_id = uuid4()
    lead_id = uuid4()
    first_engine = _engine()
    first_engine.create_session(session_id, lead_id=lead_id)

    _process(
        journal,
        first_engine,
        session_id,
        text="We sell apparel and need a catalog.",
    )
    _process(journal, first_engine, session_id, text="Please show a demo.")
    _process(journal, first_engine, session_id, text="We need payment instead.")
    expected_snapshot = first_engine.snapshot(session_id)

    raw_events = repository.read(lead_id)
    serialized = [str(event.payload) for event in raw_events]
    assert all("We sell apparel" not in payload for payload in serialized)
    assert all("Please show a demo" not in payload for payload in serialized)
    assert "apparel" not in serialized[1]
    unkeyed = hashlib.sha256(b"we sell apparel and need a catalog").hexdigest()
    assert raw_events[0].payload["turn_digest"] != unkeyed

    restarted_engine = _engine()
    replay = journal.restore_session(restarted_engine, lead_id, session_id)
    assert replay.aggregate_version == 3
    assert restarted_engine.snapshot(session_id) == expected_snapshot

    review = _process(
        journal,
        restarted_engine,
        session_id,
        text="We need inventory instead.",
    )
    assert review.event.result.disposition is ConversationDisposition.REVIEW


def test_exact_retry_is_bound_to_typed_input_and_restores_persisted_state(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    journal = ConversationJournal(SqlAlchemyEventRepository(session_factory))
    session_id = uuid4()
    lead_id = uuid4()
    operation_id = uuid4()
    engine = _engine()
    engine.create_session(session_id, lead_id=lead_id)

    first = _process(
        journal,
        engine,
        session_id,
        text="We sell apparel.",
        operation_id=operation_id,
    )
    retried = _process(
        journal,
        engine,
        session_id,
        text="We sell apparel.",
        operation_id=operation_id,
    )
    assert retried == first
    assert engine.snapshot(session_id).turn_count == 1
    assert (
        journal.find_turn(
            engine,
            lead_id,
            session_id=session_id,
            operation_id=operation_id,
            text="We sell apparel.",
            language=LanguageCode.ENGLISH,
        )
        == first
    )

    with pytest.raises(JournalOperationConflictError):
        _process(
            journal,
            engine,
            session_id,
            text="We sell books.",
            operation_id=operation_id,
        )


class DelegatingRepository:
    def __init__(self, repository: EventRepository) -> None:
        self.repository = repository

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
        return self.repository.append(
            aggregate_id,
            aggregate_type,
            event_type,
            payload,
            expected_version=expected_version,
            occurred_at=occurred_at,
        )

    def read(
        self,
        aggregate_id: UUID,
        *,
        limit: int | None = None,
    ) -> Sequence[AuditEvent]:
        return self.repository.read(aggregate_id, limit=limit)

    def current_version(self, aggregate_id: UUID) -> int:
        return self.repository.current_version(aggregate_id)

    def status(self, aggregate_id: UUID) -> AggregateStatus | None:
        return self.repository.status(aggregate_id)


class AcceptedThenRaisedRepository(DelegatingRepository):
    def __init__(self, repository: EventRepository) -> None:
        super().__init__(repository)
        self.raised = False

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
        event = super().append(
            aggregate_id,
            aggregate_type,
            event_type,
            payload,
            expected_version=expected_version,
            occurred_at=occurred_at,
        )
        if not self.raised:
            self.raised = True
            raise RuntimeError("simulated lost acknowledgement")
        return event


class RejectedRepository(DelegatingRepository):
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
        del aggregate_id, aggregate_type, event_type, payload, expected_version, occurred_at
        raise RuntimeError("simulated persistence failure")


def test_ambiguous_commit_retry_recovers_and_definitive_failure_rolls_back_state(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    base = SqlAlchemyEventRepository(session_factory)
    session_id = uuid4()
    lead_id = uuid4()
    operation_id = uuid4()
    engine = _engine()
    engine.create_session(session_id, lead_id=lead_id)
    journal = ConversationJournal(AcceptedThenRaisedRepository(base))

    with pytest.raises(RuntimeError, match="lost acknowledgement"):
        _process(
            journal,
            engine,
            session_id,
            text="Please show a demo.",
            operation_id=operation_id,
        )
    assert engine.snapshot(session_id).turn_count == 0

    recovered = _process(
        journal,
        engine,
        session_id,
        text="Please show a demo.",
        operation_id=operation_id,
    )
    assert recovered.aggregate_version == 1
    assert engine.snapshot(session_id).turn_count == 1
    assert base.current_version(lead_id) == 1

    failed_session = uuid4()
    failed_engine = _engine()
    failed_engine.create_session(failed_session)
    before = failed_engine.snapshot(failed_session)
    with pytest.raises(RuntimeError, match="persistence failure"):
        _process(
            ConversationJournal(RejectedRepository(base)),
            failed_engine,
            failed_session,
            text="hello",
        )
    assert failed_engine.snapshot(failed_session) == before


def test_stale_version_and_capacity_fail_without_mutating_state(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    journal = ConversationJournal(SqlAlchemyEventRepository(session_factory), max_events=1)
    session_id = uuid4()
    engine = _engine()
    engine.create_session(session_id)
    _process(journal, engine, session_id, text="hello", expected_version=0)
    before = engine.snapshot(session_id)

    with pytest.raises(JournalHistoryUnavailableError, match="capacity"):
        _process(journal, engine, session_id, text="another turn")
    assert engine.snapshot(session_id) == before

    other_session = uuid4()
    other_engine = _engine()
    other_engine.create_session(other_session)
    with pytest.raises(ConcurrencyConflictError, match="Expected version 1, found 0"):
        _process(
            ConversationJournal(SqlAlchemyEventRepository(session_factory)),
            other_engine,
            other_session,
            text="hello",
            expected_version=1,
        )
    assert other_engine.snapshot(other_session).turn_count == 0


def test_stale_live_state_cannot_fork_session_history(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    repository = SqlAlchemyEventRepository(session_factory)
    journal = ConversationJournal(repository)
    lead_id = uuid4()
    session_id = uuid4()
    first_worker = _engine()
    stale_worker = _engine()
    first_worker.create_session(session_id, lead_id=lead_id)
    stale_worker.create_session(session_id, lead_id=lead_id)

    _process(journal, first_worker, session_id, text="first")
    with pytest.raises(ConcurrencyConflictError, match="does not match"):
        _process(journal, stale_worker, session_id, text="competing")
    assert stale_worker.snapshot(session_id).turn_count == 0
    assert repository.current_version(lead_id) == 1

    journal.synchronize_session(stale_worker, lead_id, session_id)
    second = _process(journal, stale_worker, session_id, text="second")
    assert second.event.result.turn_count == 2
    assert repository.current_version(lead_id) == 2


def test_multiple_fact_changes_in_one_turn_remain_persistable(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    journal = ConversationJournal(SqlAlchemyEventRepository(session_factory))
    lead_id = uuid4()
    session_id = uuid4()
    engine = _engine()
    engine.create_session(session_id, lead_id=lead_id)

    _process(
        journal,
        engine,
        session_id,
        text="We sell apparel, need a catalog, and budget is Rs 10000.",
    )
    changed = _process(
        journal,
        engine,
        session_id,
        text="Actually we sell books, need payment, and budget is Rs 20000.",
    )

    assert changed.event.result.disposition is ConversationDisposition.REVIEW
    replay = journal.replay(lead_id, session_id)
    assert replay.checkpoint.goal_change_count == 3


def test_lead_privacy_closes_every_session_and_partial_retention_leaves_no_copies(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    repository = SqlAlchemyEventRepository(session_factory)
    privacy = SqlAlchemyPrivacyRepository(session_factory, repository)
    journal = ConversationJournal(repository)
    lead_id = uuid4()
    old = datetime.now(UTC) - timedelta(days=90)
    recent = datetime.now(UTC)

    first_session = uuid4()
    first_engine = _engine()
    first_engine.create_session(first_session, lead_id=lead_id)
    _process(
        journal,
        first_engine,
        first_session,
        text="We sell apparel.",
        occurred_at=old,
    )
    _process(
        journal,
        first_engine,
        first_session,
        text="Please show a demo.",
        occurred_at=recent,
    )
    payloads = repository.read(lead_id)
    assert "apparel" not in str(payloads[1].payload)

    assert privacy.purge_expired(recent - timedelta(days=1), dry_run=False) == 1
    with pytest.raises(JournalHistoryUnavailableError, match="incomplete"):
        journal.replay(lead_id, first_session)

    anonymized_lead = uuid4()
    for session_number in range(2):
        session_id = UUID(int=session_number + 1)
        engine = _engine()
        engine.create_session(session_id, lead_id=anonymized_lead)
        _process(journal, engine, session_id, text=f"session {session_number}")
    assert privacy.anonymize(anonymized_lead) == 2
    for session_number in range(2):
        with pytest.raises(JournalHistoryUnavailableError, match="anonymized"):
            journal.replay(anonymized_lead, UUID(int=session_number + 1))


class PrivacyRaceRepository(DelegatingRepository):
    def __init__(
        self,
        repository: EventRepository,
        privacy: SqlAlchemyPrivacyRepository,
        lead_id: UUID,
    ) -> None:
        super().__init__(repository)
        self.privacy = privacy
        self.lead_id = lead_id
        self.status_calls = 0

    def status(self, aggregate_id: UUID) -> AggregateStatus | None:
        self.status_calls += 1
        if self.status_calls == 2:
            self.privacy.anonymize(self.lead_id)
        return super().status(aggregate_id)


def test_replay_detects_privacy_change_during_load(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    repository = SqlAlchemyEventRepository(session_factory)
    privacy = SqlAlchemyPrivacyRepository(session_factory, repository)
    lead_id = uuid4()
    session_id = uuid4()
    engine = _engine()
    engine.create_session(session_id, lead_id=lead_id)
    _process(ConversationJournal(repository), engine, session_id, text="hello")

    racing = ConversationJournal(PrivacyRaceRepository(repository, privacy, lead_id))
    with pytest.raises(JournalHistoryUnavailableError, match="anonymized"):
        racing.replay(lead_id, session_id)


def test_unknown_malformed_and_oversized_conversation_events_fail_closed(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    repository = SqlAlchemyEventRepository(session_factory)

    unrelated_id = uuid4()
    repository.append(unrelated_id, "lead", "lead.created", {}, expected_version=0)
    with pytest.raises(LookupError):
        ConversationJournal(repository).replay(unrelated_id, uuid4())

    unknown_id = uuid4()
    repository.append(
        unknown_id,
        "lead",
        "conversation.unknown.v1",
        {},
        expected_version=0,
    )
    with pytest.raises(JournalCorruptionError, match="unsupported"):
        ConversationJournal(repository).replay(unknown_id, uuid4())

    malformed_id = uuid4()
    repository.append(
        malformed_id,
        "lead",
        "conversation.turn-accepted.v1",
        {"event_schema_version": "1"},
        expected_version=0,
    )
    with pytest.raises(JournalCorruptionError, match="payload"):
        ConversationJournal(repository).replay(malformed_id, uuid4())

    oversized_id = uuid4()
    repository.append(
        oversized_id,
        "lead",
        "conversation.turn-accepted.v1",
        {"padding": "x" * (2 * 1024 * 1024)},
        expected_version=0,
    )
    with pytest.raises(JournalHistoryUnavailableError, match="size"):
        ConversationJournal(repository).replay(oversized_id, uuid4())

    gapped_id = uuid4()
    gapped_session = uuid4()
    gapped_engine = _engine()
    gapped_engine.create_session(gapped_session, lead_id=gapped_id)
    gapped_journal = ConversationJournal(repository)
    _process(gapped_journal, gapped_engine, gapped_session, text="first")
    _process(gapped_journal, gapped_engine, gapped_session, text="second")
    with session_factory() as database_session:
        database_session.execute(
            update(EventRecord)
            .where(
                EventRecord.aggregate_id == str(gapped_id),
                EventRecord.aggregate_version == 2,
            )
            .values(aggregate_version=3)
        )
        database_session.commit()
    with pytest.raises(JournalCorruptionError, match="not contiguous"):
        gapped_journal.replay(gapped_id, gapped_session)


def test_replay_requires_matching_digest_key_and_cannot_overwrite_live_session(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    journal = ConversationJournal(SqlAlchemyEventRepository(session_factory))
    lead_id = uuid4()
    session_id = uuid4()
    engine = _engine()
    engine.create_session(session_id, lead_id=lead_id)
    _process(journal, engine, session_id, text="hello")

    wrong_key_engine = ConversationEngine(turn_digest_key=b"x" * 32)
    with pytest.raises(ValueError, match="different digest key"):
        journal.restore_session(wrong_key_engine, lead_id, session_id)

    live_engine = _engine()
    live_engine.create_session(session_id, lead_id=lead_id)
    with pytest.raises(ValueError, match="already exists"):
        journal.restore_session(live_engine, lead_id, session_id)


def test_fingerprint_is_canonical_bounded_and_finite() -> None:
    engine = _engine()
    session_id = uuid4()
    first = canonical_operation_fingerprint(
        engine,
        session_id,
        {"text": "नमस्ते", "language": "hi"},
    )
    second = canonical_operation_fingerprint(
        engine,
        session_id,
        {"language": "hi", "text": "नमस्ते"},
    )
    assert first == second
    assert len(first) == 64
    assert first != canonical_operation_fingerprint(
        engine,
        uuid4(),
        {"text": "नमस्ते", "language": "hi"},
    )
    assert first != canonical_operation_fingerprint(
        ConversationEngine(turn_digest_key=b"y" * 32),
        session_id,
        {"text": "नमस्ते", "language": "hi"},
    )

    with pytest.raises(ValueError, match="size"):
        canonical_operation_fingerprint(
            engine,
            session_id,
            {"text": "x" * (64 * 1024)},
        )
    with pytest.raises(ValueError, match="finite"):
        canonical_operation_fingerprint(engine, session_id, {"score": float("nan")})


def test_journal_capacity_leaves_room_for_overflow_detection(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    repository = SqlAlchemyEventRepository(session_factory)

    ConversationJournal(repository, max_events=9_999)
    with pytest.raises(ValueError, match="9999"):
        ConversationJournal(repository, max_events=10_000)

    lead_id = uuid4()
    repository.append(lead_id, "lead", "lead.created", {}, expected_version=0)
    session_id = uuid4()
    engine = _engine()
    engine.create_session(session_id, lead_id=lead_id)
    with pytest.raises(JournalHistoryUnavailableError, match="capacity"):
        _process(
            ConversationJournal(repository, max_events=1),
            engine,
            session_id,
            text="hello",
        )
    assert repository.current_version(lead_id) == 1
