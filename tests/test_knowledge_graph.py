from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from pitchbot.conversation import ConversationEngine, ConversationJournal
from pitchbot.domain import LanguageCode
from pitchbot.knowledge import (
    FactClaimStatus,
    KnowledgeRelationType,
    TemporalKnowledgeGraphBuilder,
)
from pitchbot.storage import SqlAlchemyEventRepository, SqlAlchemyPrivacyRepository
from pitchbot.storage.models import EventRecord

TURN_DIGEST_KEY = b"knowledge-graph-test-digest-key!"


def _process(
    journal: ConversationJournal,
    engine: ConversationEngine,
    session_id: UUID,
    text: str,
    language: LanguageCode = LanguageCode.ENGLISH,
    occurred_at: datetime | None = None,
) -> None:
    journal.process_turn(
        engine,
        session_id,
        operation_id=uuid4(),
        text=text,
        language=language,
        occurred_at=occurred_at,
    )


def _setup(
    session_factory: sessionmaker[Session],
) -> tuple[SqlAlchemyEventRepository, ConversationJournal, ConversationEngine, UUID]:
    repository = SqlAlchemyEventRepository(session_factory)
    journal = ConversationJournal(repository)
    engine = ConversationEngine(turn_digest_key=TURN_DIGEST_KEY)
    return repository, journal, engine, uuid4()


def test_explicit_revision_creates_a_bounded_validity_interval(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    _, journal, engine, lead_id = _setup(session_factory)
    session_id = uuid4()
    engine.create_session(session_id, lead_id=lead_id)
    _process(journal, engine, session_id, "We sell apparel.")
    _process(journal, engine, session_id, "We sell toys instead.")

    graph = TemporalKnowledgeGraphBuilder(journal).build(lead_id)
    claims = {str(claim.fact.value): claim for claim in graph.claims}

    assert graph.aggregate_version == 2
    assert claims["apparel"].status is FactClaimStatus.SUPERSEDED
    assert claims["apparel"].valid_to_version == 2
    assert claims["apparel"].superseded_by_fact_id == claims["toys"].fact.fact_id
    assert claims["toys"].status is FactClaimStatus.CURRENT
    assert any(
        relation.relation is KnowledgeRelationType.SUPERSEDED_BY
        and relation.source_id == claims["apparel"].fact.fact_id
        and relation.target_id == claims["toys"].fact.fact_id
        for relation in graph.relations
    )
    assert "We sell" not in graph.model_dump_json()
    assert TemporalKnowledgeGraphBuilder(journal).build(lead_id) == graph


def test_cross_session_differences_remain_conflicting_claims(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    _, journal, engine, lead_id = _setup(session_factory)
    english_session = uuid4()
    hindi_session = uuid4()
    engine.create_session(english_session, lead_id=lead_id)
    engine.create_session(hindi_session, lead_id=lead_id)
    _process(journal, engine, english_session, "We sell apparel.")
    _process(
        journal,
        engine,
        hindi_session,
        "हम खिलौने बेचते हैं।",
        LanguageCode.HINDI,
    )

    graph = TemporalKnowledgeGraphBuilder(journal).build(lead_id)
    business_claims = [claim for claim in graph.claims if claim.fact.key == "business_type"]

    assert {claim.fact.value for claim in business_claims} == {"apparel", "toys"}
    assert {claim.status for claim in business_claims} == {FactClaimStatus.CONFLICTING}
    assert {claim.language for claim in business_claims} == {
        LanguageCode.ENGLISH,
        LanguageCode.HINDI,
    }
    assert set(graph.session_ids) == {english_session, hindi_session}


def test_same_cross_session_value_is_not_a_conflict(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    _, journal, engine, lead_id = _setup(session_factory)
    for _ in range(2):
        session_id = uuid4()
        engine.create_session(session_id, lead_id=lead_id)
        _process(journal, engine, session_id, "We sell books.")

    graph = TemporalKnowledgeGraphBuilder(journal).build(lead_id)

    assert len(graph.claims) == 2
    assert {claim.status for claim in graph.claims} == {FactClaimStatus.CURRENT}


def test_graph_capacity_and_unknown_lead_fail_closed(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    _, journal, engine, lead_id = _setup(session_factory)
    session_id = uuid4()
    engine.create_session(session_id, lead_id=lead_id)
    _process(journal, engine, session_id, "We sell apparel and need a catalog.")

    with pytest.raises(RuntimeError, match="fact capacity"):
        TemporalKnowledgeGraphBuilder(journal, max_claims=1).build(lead_id)
    with pytest.raises(LookupError):
        TemporalKnowledgeGraphBuilder(journal).build(uuid4())
    with pytest.raises(ValueError, match="positive"):
        TemporalKnowledgeGraphBuilder(journal, max_relations=0)


def test_graph_revalidates_concurrent_change_and_privacy(
    migrated_database: tuple[str, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, session_factory = migrated_database
    repository, journal, engine, lead_id = _setup(session_factory)
    session_id = uuid4()
    engine.create_session(session_id, lead_id=lead_id)
    _process(journal, engine, session_id, "We sell food.")
    original_validate = journal.validate_knowledge_source

    def change_before_validation(source: object) -> None:
        repository.append(
            lead_id,
            "lead",
            "lead.note-added",
            {},
            expected_version=1,
        )
        original_validate(source)  # type: ignore[arg-type]

    monkeypatch.setattr(journal, "validate_knowledge_source", change_before_validation)
    with pytest.raises(RuntimeError, match="changed during projection"):
        TemporalKnowledgeGraphBuilder(journal).build(lead_id)

    monkeypatch.setattr(journal, "validate_knowledge_source", original_validate)
    SqlAlchemyPrivacyRepository(session_factory, repository).anonymize(lead_id)
    with pytest.raises(RuntimeError, match="anonymized"):
        TemporalKnowledgeGraphBuilder(journal).build(lead_id)


def test_corrupt_self_revision_is_rejected(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    _, journal, engine, lead_id = _setup(session_factory)
    session_id = uuid4()
    engine.create_session(session_id, lead_id=lead_id)
    _process(journal, engine, session_id, "We sell apparel.")
    _process(journal, engine, session_id, "We sell plastics instead.")
    with session_factory() as database_session:
        row = database_session.scalar(select(EventRecord).where(EventRecord.aggregate_version == 2))
        assert row is not None
        payload = deepcopy(row.payload)
        revision = payload["result"]["revisions"][0]  # type: ignore[index]
        revision["previous_fact_id"] = revision["replacement_fact_id"]
        database_session.execute(
            update(EventRecord).where(EventRecord.sequence == row.sequence).values(payload=payload)
        )
        database_session.commit()

    with pytest.raises(RuntimeError, match="fact capacity"):
        TemporalKnowledgeGraphBuilder(journal, max_claims=1).build(lead_id)
    with pytest.raises(RuntimeError, match="revision source"):
        TemporalKnowledgeGraphBuilder(journal).build(lead_id)


def test_revision_capacity_is_configured_independently(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    _, journal, engine, lead_id = _setup(session_factory)
    session_id = uuid4()
    engine.create_session(session_id, lead_id=lead_id)
    _process(journal, engine, session_id, "We sell apparel.")
    _process(journal, engine, session_id, "We sell toys instead.")
    _process(journal, engine, session_id, "We sell books instead.")

    with pytest.raises(RuntimeError, match="revision capacity"):
        TemporalKnowledgeGraphBuilder(
            journal,
            max_claims=3,
            max_revisions=1,
        ).build(lead_id)


def test_backdated_revision_is_reported_as_journal_corruption(
    migrated_database: tuple[str, sessionmaker[Session]],
) -> None:
    _, session_factory = migrated_database
    _, journal, engine, lead_id = _setup(session_factory)
    session_id = uuid4()
    engine.create_session(session_id, lead_id=lead_id)
    _process(
        journal,
        engine,
        session_id,
        "We sell apparel.",
        occurred_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    _process(
        journal,
        engine,
        session_id,
        "We sell toys instead.",
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    with pytest.raises(RuntimeError, match="timestamp precedes"):
        TemporalKnowledgeGraphBuilder(journal).build(lead_id)


def test_temporal_claim_rejects_backwards_time() -> None:
    from pitchbot.domain import RequirementFact
    from pitchbot.knowledge import TemporalFactClaim

    lead_id = uuid4()
    fact = RequirementFact(
        lead_id=lead_id,
        key="business_type",
        value="books",
        confidence=0.85,
        captured_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="time cannot decrease"):
        TemporalFactClaim(
            fact=fact,
            status=FactClaimStatus.SUPERSEDED,
            session_id=uuid4(),
            language=LanguageCode.ENGLISH,
            valid_from_version=1,
            valid_from=datetime(2026, 1, 2, tzinfo=UTC),
            valid_to_version=2,
            valid_to=datetime(2026, 1, 1, tzinfo=UTC),
            superseded_by_fact_id=uuid4(),
        )
