from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from clocks import SteppingClock
from sqlalchemy.orm import Session, sessionmaker

from pitchbot.adapters import FakeClock
from pitchbot.conversation import (
    ConversationEngine,
    ConversationJournal,
    ConversationReplay,
    JournaledConversationTurn,
    JournalHistoryDeadlineExceededError,
    JournalHistoryUnavailableError,
    LeadKnowledgeSourceSnapshot,
)
from pitchbot.domain import AuditEvent, JsonValue, LanguageCode
from pitchbot.simulator.models import CreateSessionRequest, TurnRequest
from pitchbot.simulator.service import SimulatorService
from pitchbot.storage import AggregateStatus, EventRepository, SqlAlchemyEventRepository

TURN_DIGEST_KEY = b"journal-bounds-test-digest-key32"

# Distinct statements per turn, so no turn repeats a digest and every session builds the
# same shape of history the projection has to replay.
SESSION_TURNS = (
    "We sell apparel and need a catalog.",
    "Our budget is 80000 rupees for a restaurant.",
    "Please show a demo next week.",
    "We need delivery by March for two stores.",
)


@dataclass(slots=True)
class _ProjectionCounters:
    """Deterministic work counters for one journal, shared by its bounded views."""

    decodes: int = 0
    events_scanned: int = 0
    session_replays: int = 0
    projections_started: int = 0
    projections_finished: int = 0
    projection_decodes: list[int] = field(default_factory=list)
    projection_threads: set[str] = field(default_factory=set)


class _CountingJournal(ConversationJournal):
    """A journal that counts the work each projection actually performs.

    Counting the events handed to every replay is the deterministic way to observe the
    complexity of `knowledge_source`: replaying each session against the whole history
    reports sessions x events, while one grouping pass reports events.
    """

    def __init__(
        self,
        repository: EventRepository,
        *,
        counters: _ProjectionCounters | None = None,
        **options: Any,
    ) -> None:
        super().__init__(repository, **options)
        self.counters = counters if counters is not None else _ProjectionCounters()
        self._options = options

    def with_history_bounds(
        self,
        *,
        max_history_events: int,
        history_deadline_ms: int | None = None,
    ) -> ConversationJournal:
        return _CountingJournal(
            self._repository,
            counters=self.counters,
            **{
                **self._options,
                "max_history_events": max_history_events,
                "history_deadline_ms": history_deadline_ms,
            },
        )

    def knowledge_source(
        self,
        lead_id: UUID,
        *,
        max_sessions: int = 1_000,
        max_facts: int = 1_000,
        max_revisions: int = 1_000,
    ) -> LeadKnowledgeSourceSnapshot:
        self.counters.projections_started += 1
        self.counters.projection_threads.add(threading.current_thread().name)
        decoded_before = self.counters.decodes
        try:
            return super().knowledge_source(
                lead_id,
                max_sessions=max_sessions,
                max_facts=max_facts,
                max_revisions=max_revisions,
            )
        finally:
            self.counters.projections_finished += 1
            self.counters.projection_decodes.append(self.counters.decodes - decoded_before)

    def _parse_event(self, event: AuditEvent, lead_id: UUID) -> JournaledConversationTurn:
        self.counters.decodes += 1
        return super()._parse_event(event, lead_id)

    def _replay_loaded(
        self,
        lead_id: UUID,
        session_id: UUID,
        events: list[JournaledConversationTurn],
        status: AggregateStatus | None,
    ) -> ConversationReplay:
        self.counters.events_scanned += len(events)
        return super()._replay_loaded(lead_id, session_id, events, status)

    def _replay_session_events(
        self,
        lead_id: UUID,
        session_id: UUID,
        session_events: list[JournaledConversationTurn],
        status: AggregateStatus | None,
        *,
        budget: Any = None,
    ) -> ConversationReplay:
        replay = super()._replay_session_events(
            lead_id,
            session_id,
            session_events,
            status,
            budget=budget,
        )
        # Counted after the call so an aborted replay is not reported as a completed one.
        self.counters.events_scanned += len(session_events)
        self.counters.session_replays += 1
        return replay


class _RecordingRepository:
    """An event repository that records the read limits the journal actually asks for."""

    def __init__(self, repository: EventRepository) -> None:
        self._repository = repository
        self.read_limits: list[int | None] = []

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
        return self._repository.append(
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
        self.read_limits.append(limit)
        return self._repository.read(aggregate_id, limit=limit)

    def current_version(self, aggregate_id: UUID) -> int:
        return self._repository.current_version(aggregate_id)

    def status(self, aggregate_id: UUID) -> AggregateStatus | None:
        return self._repository.status(aggregate_id)


def _build_lead_history(
    journal: ConversationJournal,
    lead_id: UUID,
    *,
    sessions: int,
    turns_per_session: int,
) -> tuple[UUID, ...]:
    session_ids: list[UUID] = []
    for _ in range(sessions):
        session_id = uuid4()
        engine = ConversationEngine(turn_digest_key=TURN_DIGEST_KEY)
        engine.create_session(session_id, lead_id=lead_id)
        for turn in range(turns_per_session):
            journal.process_turn(
                engine,
                session_id,
                operation_id=uuid4(),
                text=SESSION_TURNS[turn % len(SESSION_TURNS)],
                language=LanguageCode.ENGLISH,
            )
        session_ids.append(session_id)
    return tuple(session_ids)


def _bounded_service(
    session_factory: sessionmaker[Session],
    *,
    journal: ConversationJournal,
    max_history_events_per_lead: int = 500,
    recall_deadline_ms: int = 150,
    recall_failure_budget: int = 3,
) -> SimulatorService:
    del session_factory
    return SimulatorService(
        clock=FakeClock(datetime(2026, 1, 1, tzinfo=UTC)),
        conversation_engine=ConversationEngine(turn_digest_key=TURN_DIGEST_KEY),
        conversation_journal=journal,
        max_history_events_per_lead=max_history_events_per_lead,
        recall_deadline_ms=recall_deadline_ms,
        recall_failure_budget=recall_failure_budget,
    )


def test_knowledge_source_replays_each_event_once_across_many_sessions(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    counters = _ProjectionCounters()
    journal = _CountingJournal(SqlAlchemyEventRepository(session_factory), counters=counters)
    lead_id = uuid4()
    sessions = 12
    turns_per_session = 4
    total_events = sessions * turns_per_session
    session_ids = _build_lead_history(
        journal,
        lead_id,
        sessions=sessions,
        turns_per_session=turns_per_session,
    )

    counters.events_scanned = 0
    counters.session_replays = 0
    snapshot = journal.knowledge_source(lead_id, max_facts=10_000, max_revisions=10_000)

    assert snapshot.aggregate_version == total_events
    assert set(snapshot.session_ids) == set(session_ids)
    assert counters.session_replays == sessions
    # Replaying each session against the whole history would scan sessions x events; one
    # grouping pass scans each event exactly once.
    assert counters.events_scanned == total_events
    assert counters.events_scanned < sessions * total_events


def test_knowledge_source_replay_matches_a_per_session_replay(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    journal = ConversationJournal(SqlAlchemyEventRepository(session_factory))
    lead_id = uuid4()
    session_ids = _build_lead_history(journal, lead_id, sessions=3, turns_per_session=3)

    snapshot = journal.knowledge_source(lead_id)

    assert snapshot.facts
    # Grouping must preserve the ordering, supersession and provenance the per-session
    # replay produces, so both routes agree on every session's final state.
    for session_id in session_ids:
        replay = journal.replay(lead_id, session_id)
        assert replay.checkpoint.facts
        assert replay.aggregate_version == snapshot.aggregate_version
        assert {fact.key for fact in replay.checkpoint.facts} == {
            item.fact.key for item in snapshot.facts if item.session_id == session_id
        }
    assert [item.aggregate_version for item in snapshot.facts] == sorted(
        item.aggregate_version for item in snapshot.facts
    )
    assert journal.knowledge_source(lead_id) == snapshot


def test_a_history_over_its_bound_is_refused_rather_than_truncated(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    repository = _RecordingRepository(SqlAlchemyEventRepository(session_factory))
    journal = ConversationJournal(repository)
    lead_id = uuid4()
    _build_lead_history(journal, lead_id, sessions=2, turns_per_session=2)

    within_bound = journal.with_history_bounds(max_history_events=4)
    repository.read_limits.clear()
    complete = within_bound.knowledge_source(lead_id)

    assert complete.aggregate_version == 4
    assert len(complete.session_ids) == 2
    # The bound reaches the repository, so an over-long history cannot be materialized.
    assert repository.read_limits == [5]

    over_bound = journal.with_history_bounds(max_history_events=3)
    repository.read_limits.clear()
    with pytest.raises(JournalHistoryUnavailableError, match="history bound"):
        over_bound.knowledge_source(lead_id)

    # Refused before a single row was read, and with no partial snapshot returned.
    assert repository.read_limits == []
    # The bound governs the projection only: the turn path stays usable for a lead whose
    # history has outgrown it, on the bounded view as well as the original journal.
    assert len(over_bound.read_turns(lead_id, complete.session_ids[0], limit=10)) == 2
    assert len(journal.read_turns(lead_id, complete.session_ids[0], limit=10)) == 2


def test_the_projection_deadline_is_observed_while_events_are_decoded(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    repository = SqlAlchemyEventRepository(session_factory)
    lead_id = uuid4()
    _build_lead_history(ConversationJournal(repository), lead_id, sessions=3, turns_per_session=2)

    counters = _ProjectionCounters()
    # Each clock read advances 0.25 ms, so a 1 ms budget expires on the fourth read: the
    # load attempt is admitted, two events are decoded, and four are never touched.
    journal = _CountingJournal(
        repository,
        counters=counters,
        history_deadline_ms=1,
        clock=SteppingClock(step_ns=250_000),
    )

    with pytest.raises(JournalHistoryDeadlineExceededError):
        journal.knowledge_source(lead_id)

    assert counters.decodes == 2
    assert counters.session_replays == 0


def test_the_projection_deadline_is_observed_while_sessions_are_replayed(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    repository = SqlAlchemyEventRepository(session_factory)
    lead_id = uuid4()
    _build_lead_history(ConversationJournal(repository), lead_id, sessions=3, turns_per_session=2)

    counters = _ProjectionCounters()
    # 0.28 ms per clock read against a 3 ms budget: the load attempt, all six decodes and
    # the grouping pass fit, and the budget expires on the third session's replay rather
    # than after every session has already been replayed.
    journal = _CountingJournal(
        repository,
        counters=counters,
        history_deadline_ms=3,
        clock=SteppingClock(step_ns=280_000),
    )

    with pytest.raises(JournalHistoryDeadlineExceededError):
        journal.knowledge_source(lead_id)

    assert counters.decodes == 6
    assert counters.session_replays == 2


@pytest.mark.asyncio
async def test_the_history_bound_reaches_the_journal_the_simulator_recalls_from(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    journal = ConversationJournal(SqlAlchemyEventRepository(session_factory))
    service = _bounded_service(
        session_factory,
        journal=journal,
        max_history_events_per_lead=2,
    )
    session = service.create_session(CreateSessionRequest(lead_ref="bounded-recall"))

    recalls = []
    for turn in range(4):
        result = await service.process_turn(
            session.session_id,
            TurnRequest(
                text=SESSION_TURNS[turn % len(SESSION_TURNS)],
                language=LanguageCode.ENGLISH,
            ),
        )
        recalls.append(result.recall)

    # Recall runs after the commit, so turn N projects N events: the third turn is the
    # first to exceed a bound of two, and every later turn stays refused.
    assert [recall is not None for recall in recalls] == [True, True, False, False]
    # The refusal never fails the turn it followed.
    assert len(service.get_durable_history(session.session_id, limit=10).turns) == 4


@pytest.mark.asyncio
async def test_an_over_budget_recall_stops_its_own_worker_without_accumulating_threads(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    counters = _ProjectionCounters()
    journal = _CountingJournal(
        SqlAlchemyEventRepository(session_factory),
        counters=counters,
        clock=SteppingClock(step_ns=1_000_000),
    )
    service = _bounded_service(
        session_factory,
        journal=journal,
        recall_deadline_ms=1,
        # Raised above the default so every turn attempts a recall: this test is about
        # what an attempted, over-budget read leaves behind, not about the self-disable.
        recall_failure_budget=10,
    )
    session = service.create_session(CreateSessionRequest(lead_ref="worker-bound"))

    for turn in range(5):
        result = await service.process_turn(
            session.session_id,
            TurnRequest(
                text=SESSION_TURNS[turn % len(SESSION_TURNS)],
                language=LanguageCode.ENGLISH,
            ),
        )
        assert result.recall is None
        assert result.reply
        # Checked at the instant the turn returns: an over-budget projection stops itself
        # rather than being abandoned, so no worker is still running behind this reply.
        # A caller-side timeout that walked away from `to_thread` would leave the started
        # count ahead of the finished one here.
        assert counters.projections_started == turn + 1
        assert counters.projections_finished == turn + 1

    assert len(service.get_durable_history(session.session_id, limit=10).turns) == 5
    # Each projection refused before decoding a single event, and none of them ran on the
    # event loop that also serves the latency-critical audio socket.
    assert counters.projection_decodes == [0, 0, 0, 0, 0]
    assert counters.projection_threads
    assert threading.main_thread().name not in counters.projection_threads


def test_history_bounds_are_validated(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    repository = SqlAlchemyEventRepository(session_factory)

    with pytest.raises(ValueError, match="history bound"):
        ConversationJournal(repository, max_history_events=0)
    with pytest.raises(ValueError, match="history bound"):
        ConversationJournal(repository, max_history_events=10_000)
    with pytest.raises(ValueError, match="history deadline"):
        ConversationJournal(repository, history_deadline_ms=0)
