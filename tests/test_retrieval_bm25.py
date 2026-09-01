from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from clocks import ScriptedClock, SteppingClock
from sqlalchemy.orm import Session, sessionmaker

from pitchbot.conversation import ConversationEngine, ConversationJournal
from pitchbot.domain import LanguageCode
from pitchbot.retrieval import (
    Bm25Index,
    FactProvenance,
    JournalBm25Retriever,
    LexicalDocument,
    RetrievalDeadline,
    RetrievalDeadlineExceededError,
    RetrievalScope,
    tokenize,
)
from pitchbot.retrieval.bm25 import MAX_DOCUMENT_BYTES
from pitchbot.storage import SqlAlchemyEventRepository, SqlAlchemyPrivacyRepository

TURN_DIGEST_KEY = b"retrieval-test-turn-digest-key!!"


def _document(
    key: str,
    value: str,
    *,
    fact_id: UUID,
    lead_id: UUID,
    session_id: UUID,
    language: LanguageCode = LanguageCode.ENGLISH,
) -> LexicalDocument:
    return LexicalDocument(
        key=key,
        value=value,
        language=language,
        provenance=FactProvenance(
            lead_id=lead_id,
            session_id=session_id,
            aggregate_version=1,
            fact_id=fact_id,
            source_span_ids=(uuid4(),),
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )


def _journal(
    session_factory: sessionmaker[Session],
) -> tuple[SqlAlchemyEventRepository, ConversationJournal, ConversationEngine, UUID, UUID]:
    repository = SqlAlchemyEventRepository(session_factory)
    journal = ConversationJournal(repository)
    engine = ConversationEngine(turn_digest_key=TURN_DIGEST_KEY)
    lead_id = uuid4()
    session_id = uuid4()
    engine.create_session(session_id, lead_id=lead_id)
    return repository, journal, engine, lead_id, session_id


def test_unicode_tokenization_preserves_hindi_and_splits_structured_keys() -> None:
    assert tokenize("Requested_Features: Catalog, भुगतान") == (
        "requested",
        "features",
        "catalog",
        "भुगतान",
    )
    assert tokenize("CATALOG catalog") == ("catalog", "catalog")


def test_bm25_ranking_is_deterministic_and_lead_scoped() -> None:
    lead_id = uuid4()
    session_id = uuid4()
    first_id = UUID("10000000-0000-0000-0000-000000000001")
    second_id = UUID("20000000-0000-0000-0000-000000000002")
    documents = (
        _document(
            "requested_features",
            "catalog,payments",
            fact_id=first_id,
            lead_id=lead_id,
            session_id=session_id,
        ),
        _document(
            "business_type",
            "books",
            fact_id=second_id,
            lead_id=lead_id,
            session_id=session_id,
        ),
    )
    index = Bm25Index(documents)

    first, _, timed_out = index.search("catalog payments")
    second, _, _ = index.search("catalog payments")

    assert timed_out is False
    assert first == second
    assert [item.document.provenance.fact_id for item in first] == [first_id]
    assert first[0].matched_terms == ("catalog", "payments")

    other_lead = _document(
        "timeline",
        "this month",
        fact_id=uuid4(),
        lead_id=uuid4(),
        session_id=session_id,
    )
    with pytest.raises(ValueError, match="one lead and session"):
        Bm25Index((*documents, other_lead))


def test_explicit_lead_scope_allows_only_same_lead_cross_session_documents() -> None:
    lead_id = uuid4()
    first = _document(
        "business_type",
        "books",
        fact_id=uuid4(),
        lead_id=lead_id,
        session_id=uuid4(),
    )
    second = _document(
        "requested_features",
        "catalog",
        fact_id=uuid4(),
        lead_id=lead_id,
        session_id=uuid4(),
    )

    with pytest.raises(ValueError, match="one lead and session"):
        Bm25Index((first, second))
    with pytest.raises(ValueError, match="one lead and session"):
        Bm25Index((first, second), scope="session")
    assert Bm25Index((first, second), scope=RetrievalScope.LEAD).document_count == 2
    assert Bm25Index((first, second), scope="lead").document_count == 2
    with pytest.raises(ValueError, match="scope must be session or lead"):
        Bm25Index((first, second), scope="invalid")

    other_lead = second.model_copy(
        update={
            "provenance": second.provenance.model_copy(update={"lead_id": uuid4()}),
        }
    )
    with pytest.raises(ValueError, match="one lead and session"):
        Bm25Index((first, other_lead), scope=RetrievalScope.LEAD)


def test_repeated_terms_count_once_toward_document_frequency() -> None:
    lead_id = uuid4()
    session_id = uuid4()
    repeated_id = UUID("10000000-0000-0000-0000-000000000001")
    index = Bm25Index(
        (
            _document(
                "notes",
                "catalog catalog catalog catalog",
                fact_id=repeated_id,
                lead_id=lead_id,
                session_id=session_id,
            ),
            _document(
                "requested_features",
                "payments",
                fact_id=uuid4(),
                lead_id=lead_id,
                session_id=session_id,
            ),
        )
    )

    results, _, _ = index.search("catalog")

    assert [item.document.provenance.fact_id for item in results] == [repeated_id]
    assert results[0].score > 0


def test_bm25_bounds_and_timeout_fail_without_partial_results() -> None:
    lead_id = uuid4()
    session_id = uuid4()
    index = Bm25Index(
        (
            _document(
                "requested_features",
                "catalog",
                fact_id=uuid4(),
                lead_id=lead_id,
                session_id=session_id,
            ),
        )
    )
    ticks = iter((0, 1_000_000, 2_000_000))

    results, duration_ms, timed_out = index.search(
        "catalog",
        deadline_ms=1,
        clock=lambda: next(ticks),
    )

    assert results == ()
    assert duration_ms == 2
    assert timed_out is True
    with pytest.raises(ValueError, match="blank"):
        index.search(" ")
    with pytest.raises(ValueError, match="size limit"):
        index.search("x" * 4_097)
    with pytest.raises(ValueError, match="top_k"):
        index.search("catalog", top_k=21)
    with pytest.raises(ValueError, match="deadline"):
        index.search("catalog", deadline_ms=201)


def test_journal_retrieval_returns_current_fact_with_turn_provenance(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    _, journal, engine, lead_id, session_id = _journal(session_factory)
    journal.process_turn(
        engine,
        session_id,
        operation_id=uuid4(),
        text="We sell apparel and need a catalog.",
        language=LanguageCode.ENGLISH,
    )
    journal.process_turn(
        engine,
        session_id,
        operation_id=uuid4(),
        text="We sell toys instead.",
        language=LanguageCode.ENGLISH,
    )

    response = JournalBm25Retriever(journal).search(
        lead_id,
        session_id,
        "toys business",
    )

    assert response.timed_out is False
    assert response.aggregate_version == 2
    assert response.indexed_document_count == 2
    assert response.results[0].document.key == "business_type"
    assert response.results[0].document.value == "toys"
    assert response.results[0].document.provenance.aggregate_version == 2
    assert response.results[0].document.provenance.source_span_ids
    assert all(item.document.value != "apparel" for item in response.results)


def test_journal_retrieval_revalidates_privacy_and_version(
    migrated_database: tuple[str, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, session_factory = migrated_database
    repository, journal, engine, lead_id, session_id = _journal(session_factory)
    journal.process_turn(
        engine,
        session_id,
        operation_id=uuid4(),
        text="We sell books and need search.",
        language=LanguageCode.ENGLISH,
    )
    snapshot = journal.facts_for_retrieval(lead_id, session_id)
    repository.append(lead_id, "lead", "lead.note-added", {}, expected_version=1)
    with pytest.raises(RuntimeError, match="changed during retrieval"):
        journal.validate_fact_snapshot(snapshot)

    current_snapshot = journal.facts_for_retrieval(lead_id, session_id)
    validations = 0
    original_validate = journal.validate_fact_snapshot

    def track_validation(snapshot_to_validate: object) -> None:
        nonlocal validations
        validations += 1
        original_validate(snapshot_to_validate)  # type: ignore[arg-type]

    monkeypatch.setattr(journal, "validate_fact_snapshot", track_validation)
    ticks = iter((0, 200_000_000, 201_000_000))
    timeout = JournalBm25Retriever(journal, clock=lambda: next(ticks)).search(
        lead_id,
        session_id,
        "books",
        deadline_ms=200,
    )
    assert timeout.timed_out is True
    assert timeout.results == ()
    assert validations == 1
    original_validate(current_snapshot)

    SqlAlchemyPrivacyRepository(session_factory, repository).anonymize(lead_id)
    with pytest.raises(RuntimeError, match="anonymized"):
        JournalBm25Retriever(journal).search(lead_id, session_id, "books")


def test_journal_retrieval_cannot_cross_session_or_lead(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    _, journal, engine, lead_id, session_id = _journal(session_factory)
    journal.process_turn(
        engine,
        session_id,
        operation_id=uuid4(),
        text="We sell food and need delivery tracking.",
        language=LanguageCode.ENGLISH,
    )

    with pytest.raises(LookupError):
        JournalBm25Retriever(journal).search(lead_id, uuid4(), "food")
    with pytest.raises(LookupError):
        JournalBm25Retriever(journal).search(uuid4(), session_id, "food")


def test_retrieval_deadline_rejects_invalid_budget_and_guards_iteration() -> None:
    with pytest.raises(ValueError, match="at least one millisecond"):
        RetrievalDeadline.start(0, clock=ScriptedClock(0))

    budget = RetrievalDeadline.start(5, clock=ScriptedClock(0, 1_000_000, 9_000_000))
    guarded = budget.guard(range(64), interval=1)

    assert next(guarded) == 0
    with pytest.raises(RetrievalDeadlineExceededError, match="deadline exceeded"):
        next(guarded)
    with pytest.raises(ValueError, match="interval must be positive"):
        next(budget.guard(range(2), interval=0))


def test_index_construction_stops_before_tokenizing_an_over_budget_corpus() -> None:
    lead_id = uuid4()
    session_id = uuid4()
    documents = tuple(
        _document(
            f"key_{index}",
            f"catalog value {index}",
            fact_id=uuid4(),
            lead_id=lead_id,
            session_id=session_id,
        )
        for index in range(64)
    )

    with pytest.raises(RetrievalDeadlineExceededError):
        Bm25Index(
            documents,
            deadline=RetrievalDeadline.start(1, clock=ScriptedClock(0, 5_000_000)),
        )

    index = Bm25Index(documents, deadline=RetrievalDeadline.start(200, clock=SteppingClock(0)))
    assert index.document_count == 64


def test_corrupt_document_beyond_the_guard_stride_is_never_downgraded_to_a_timeout() -> None:
    lead_id = uuid4()
    session_id = uuid4()
    documents = tuple(
        _document(
            f"key_{index}",
            "x" * (MAX_DOCUMENT_BYTES + 1) if index == 40 else f"catalog value {index}",
            fact_id=uuid4(),
            lead_id=lead_id,
            session_id=session_id,
        )
        for index in range(64)
    )

    with pytest.raises(ValueError, match="exceeds size limit"):
        Bm25Index(
            documents,
            deadline=RetrievalDeadline.start(1, clock=ScriptedClock(0, 5_000_000)),
        )


def test_search_shares_one_budget_with_the_caller_and_skips_late_ranking() -> None:
    lead_id = uuid4()
    session_id = uuid4()
    documents = tuple(
        _document(
            f"key_{index}",
            f"catalog value {index}",
            fact_id=uuid4(),
            lead_id=lead_id,
            session_id=session_id,
        )
        for index in range(4)
    )
    index = Bm25Index(documents)
    budget = RetrievalDeadline.start(10, clock=SteppingClock(4_000_000))

    results, duration_ms, timed_out = index.search("catalog", deadline=budget)

    assert results == ()
    assert timed_out is True
    assert duration_ms >= 10


def test_corpus_invariants_are_reported_even_when_the_budget_is_gone() -> None:
    session_id = uuid4()
    documents = (
        _document(
            "business_type",
            "apparel",
            fact_id=uuid4(),
            lead_id=uuid4(),
            session_id=session_id,
        ),
        _document(
            "business_type",
            "toys",
            fact_id=uuid4(),
            lead_id=uuid4(),
            session_id=session_id,
        ),
    )

    with pytest.raises(ValueError, match="one lead and session"):
        Bm25Index(
            documents,
            scope=RetrievalScope.LEAD,
            deadline=RetrievalDeadline.start(1, clock=ScriptedClock(0, 5_000_000)),
        )
