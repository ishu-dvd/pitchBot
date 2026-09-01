from __future__ import annotations

from collections.abc import Callable
from uuid import UUID, uuid4

import pytest
from clocks import ScriptedClock, SteppingClock
from sqlalchemy.orm import Session, sessionmaker

from pitchbot.conversation import ConversationEngine, ConversationJournal
from pitchbot.domain import LanguageCode
from pitchbot.knowledge import (
    FactClaimStatus,
    KnowledgeGraphDeadlineExceededError,
    LeadKnowledgeBm25Retriever,
    TemporalKnowledgeGraphBuilder,
)
from pitchbot.retrieval import RetrievalDeadline
from pitchbot.storage import SqlAlchemyEventRepository, SqlAlchemyPrivacyRepository

TURN_DIGEST_KEY = b"knowledge-retrieval-test-key-safe!"


def _setup(
    session_factory: sessionmaker[Session],
) -> tuple[
    SqlAlchemyEventRepository,
    ConversationJournal,
    ConversationEngine,
    UUID,
]:
    repository = SqlAlchemyEventRepository(session_factory)
    journal = ConversationJournal(repository)
    return repository, journal, ConversationEngine(turn_digest_key=TURN_DIGEST_KEY), uuid4()


def _turn(
    journal: ConversationJournal,
    engine: ConversationEngine,
    session_id: UUID,
    text: str,
    language: LanguageCode = LanguageCode.ENGLISH,
) -> None:
    journal.process_turn(
        engine,
        session_id,
        operation_id=uuid4(),
        text=text,
        language=language,
    )


def _count_validations(
    builder: TemporalKnowledgeGraphBuilder,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[], int]:
    calls = 0
    original_validate_version = builder.validate_version

    def track_validate_version(lead: UUID, aggregate_version: int) -> None:
        nonlocal calls
        calls += 1
        original_validate_version(lead, aggregate_version)

    monkeypatch.setattr(builder, "validate_version", track_validate_version)
    return lambda: calls


def test_lead_retrieval_excludes_superseded_and_preserves_conflicts(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    _, journal, engine, lead_id = _setup(session_factory)
    first_session = uuid4()
    second_session = uuid4()
    engine.create_session(first_session, lead_id=lead_id)
    engine.create_session(second_session, lead_id=lead_id)
    _turn(journal, engine, first_session, "We sell apparel.")
    _turn(journal, engine, first_session, "We sell toys instead.")
    _turn(journal, engine, second_session, "We sell books.")

    retriever = LeadKnowledgeBm25Retriever(TemporalKnowledgeGraphBuilder(journal))
    response = retriever.search(lead_id, "toys books business", top_k=5)

    assert response.timed_out is False
    assert response.indexed_claim_count == 2
    assert {item.claim.fact.value for item in response.results} == {"toys", "books"}
    assert {item.claim.status for item in response.results} == {FactClaimStatus.CONFLICTING}
    assert {item.claim.session_id for item in response.results} == {
        first_session,
        second_session,
    }
    assert retriever.search(lead_id, "apparel").results == ()


def test_equal_cross_session_claims_and_multilingual_values_remain_retrievable(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    _, journal, engine, lead_id = _setup(session_factory)
    for language, text in (
        (LanguageCode.ENGLISH, "We sell books."),
        (LanguageCode.HINDI, "हम books बेचते हैं।"),
    ):
        session_id = uuid4()
        engine.create_session(session_id, lead_id=lead_id)
        _turn(journal, engine, session_id, text, language)

    response = LeadKnowledgeBm25Retriever(TemporalKnowledgeGraphBuilder(journal)).search(
        lead_id,
        "books",
    )

    assert len(response.results) == 2
    assert {item.claim.status for item in response.results} == {FactClaimStatus.CURRENT}
    assert {item.claim.language for item in response.results} == {
        LanguageCode.ENGLISH,
        LanguageCode.HINDI,
    }


def test_lead_retrieval_revalidates_timeout_and_rejects_invalid_input_first(
    migrated_database: tuple[str, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, session_factory = migrated_database
    _, journal, engine, lead_id = _setup(session_factory)
    session_id = uuid4()
    engine.create_session(session_id, lead_id=lead_id)
    _turn(journal, engine, session_id, "We sell food.")
    builder = TemporalKnowledgeGraphBuilder(journal)
    validations = _count_validations(builder, monkeypatch)
    response = LeadKnowledgeBm25Retriever(
        builder,
        clock=ScriptedClock(0, 201_000_000, 202_000_000),
    ).search(
        lead_id,
        "food",
        deadline_ms=200,
    )

    assert response.timed_out is True
    assert response.aggregate_version == 1
    assert response.indexed_claim_count == 0
    assert response.results == ()
    assert validations() == 1

    def unexpected_build(_: UUID, **__: object) -> None:
        raise AssertionError("invalid input reached the graph")

    monkeypatch.setattr(builder, "build", unexpected_build)
    with pytest.raises(ValueError, match="blank"):
        LeadKnowledgeBm25Retriever(builder).search(lead_id, " ")


def test_lead_retrieval_rejects_concurrent_change_and_anonymization(
    migrated_database: tuple[str, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, session_factory = migrated_database
    repository, journal, engine, lead_id = _setup(session_factory)
    session_id = uuid4()
    engine.create_session(session_id, lead_id=lead_id)
    _turn(journal, engine, session_id, "We sell plastics.")
    builder = TemporalKnowledgeGraphBuilder(journal)
    original_validate = builder.validate

    def change_before_validation(graph: object) -> None:
        repository.append(
            lead_id,
            "lead",
            "lead.note-added",
            {},
            expected_version=1,
        )
        original_validate(graph)  # type: ignore[arg-type]

    monkeypatch.setattr(builder, "validate", change_before_validation)
    with pytest.raises(RuntimeError, match="changed during knowledge retrieval"):
        LeadKnowledgeBm25Retriever(builder).search(lead_id, "plastics")

    monkeypatch.setattr(builder, "validate", original_validate)
    SqlAlchemyPrivacyRepository(session_factory, repository).anonymize(lead_id)
    with pytest.raises(RuntimeError, match="anonymized"):
        LeadKnowledgeBm25Retriever(builder).search(lead_id, "plastics")


def test_lead_retrieval_times_out_inside_graph_projection_without_partial_results(
    migrated_database: tuple[str, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, session_factory = migrated_database
    _, journal, engine, lead_id = _setup(session_factory)
    session_id = uuid4()
    engine.create_session(session_id, lead_id=lead_id)
    _turn(journal, engine, session_id, "We sell books.")
    builder = TemporalKnowledgeGraphBuilder(journal)

    with pytest.raises(KnowledgeGraphDeadlineExceededError) as exceeded:
        builder.build(
            lead_id,
            deadline=RetrievalDeadline.start(50, clock=SteppingClock(60_000_000)),
        )

    assert exceeded.value.lead_id == lead_id
    assert exceeded.value.aggregate_version == 1

    validations = _count_validations(builder, monkeypatch)
    response = LeadKnowledgeBm25Retriever(
        builder,
        clock=SteppingClock(60_000_000),
    ).search(lead_id, "books", deadline_ms=50)

    assert response.timed_out is True
    assert response.aggregate_version == 1
    assert response.indexed_claim_count == 0
    assert response.results == ()
    assert validations() == 1


def test_lead_retrieval_times_out_after_projection_and_revalidates_once(
    migrated_database: tuple[str, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, session_factory = migrated_database
    _, journal, engine, lead_id = _setup(session_factory)
    for text in ("We sell books.", "Our budget is 50000 rupees.", "We need 200 units."):
        session_id = uuid4()
        engine.create_session(session_id, lead_id=lead_id)
        _turn(journal, engine, session_id, text)
    builder = TemporalKnowledgeGraphBuilder(journal)
    validations = _count_validations(builder, monkeypatch)

    response = LeadKnowledgeBm25Retriever(
        builder,
        clock=SteppingClock(12_000_000),
    ).search(lead_id, "books", deadline_ms=100)

    assert response.timed_out is True
    assert response.aggregate_version == 3
    assert response.indexed_claim_count == 0
    assert response.results == ()
    assert validations() == 1


def test_lead_retrieval_completes_within_a_generous_budget(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    _, journal, engine, lead_id = _setup(session_factory)
    session_id = uuid4()
    engine.create_session(session_id, lead_id=lead_id)
    _turn(journal, engine, session_id, "We sell books.")

    response = LeadKnowledgeBm25Retriever(
        TemporalKnowledgeGraphBuilder(journal),
        clock=SteppingClock(0),
    ).search(lead_id, "books", deadline_ms=200)

    assert response.timed_out is False
    assert response.duration_ms == 0
    assert [item.claim.fact.value for item in response.results] == ["books"]
