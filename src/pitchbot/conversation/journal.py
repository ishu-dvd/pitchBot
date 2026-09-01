from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from pitchbot.conversation.engine import ConversationEngine
from pitchbot.conversation.models import (
    ConversationDisposition,
    ConversationPhase,
    ConversationResult,
    ConversationStateCheckpoint,
)
from pitchbot.conversation.state import ConversationState
from pitchbot.domain import (
    AuditEvent,
    Classification,
    IntentEvidence,
    JsonValue,
    LanguageCode,
    RequirementFact,
    RequirementRevision,
)
from pitchbot.storage import (
    AggregateStatus,
    ConcurrencyConflictError,
    EventRepository,
)

CONVERSATION_AGGREGATE_TYPE = "lead"
TURN_ACCEPTED_EVENT_TYPE = "conversation.turn-accepted.v1"
MAX_FINGERPRINT_INPUT_BYTES = 64 * 1024
MAX_EVENT_PAYLOAD_BYTES = 2 * 1024 * 1024


class ConversationJournalError(RuntimeError):
    pass


class JournalOperationConflictError(ConversationJournalError):
    pass


class JournalHistoryUnavailableError(ConversationJournalError):
    pass


class JournalCorruptionError(ConversationJournalError):
    pass


class ConversationTurnEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_schema_version: Literal["1"]
    session_id: UUID
    operation_id: UUID
    operation_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    turn_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    max_turns: int = Field(ge=1, le=10_000)
    max_facts: int = Field(ge=1, le=10_000)
    max_evidence: int = Field(ge=1, le=10_000)
    max_classifications: int = Field(ge=1, le=10_000)
    max_goal_changes: int = Field(ge=1, le=10_000)
    digest_key_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    abuse_redirected: bool
    stopped: bool
    goal_change_count: int = Field(ge=0)
    result: ConversationResult

    @model_validator(mode="after")
    def validate_consistency(self) -> ConversationTurnEvent:
        if self.stopped is not (self.result.disposition is ConversationDisposition.STOP):
            raise ValueError("journal result disposition must agree with stopped state")
        if self.stopped is not (self.result.phase is ConversationPhase.CLOSED):
            raise ValueError("journal result phase must agree with stopped state")
        result_fact_ids = {fact.fact_id for fact in self.result.facts}
        if any(
            revision.replacement_fact_id not in result_fact_ids
            for revision in self.result.revisions
        ):
            raise ValueError("journal revisions must reference facts from the same turn")
        return self


class JournaledConversationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    aggregate_version: int = Field(ge=1)
    occurred_at: AwareDatetime
    event: ConversationTurnEvent


class ConversationReplay(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lead_id: UUID
    session_id: UUID
    aggregate_version: int = Field(ge=1)
    checkpoint: ConversationStateCheckpoint
    last_result: ConversationResult


class JournaledConversationFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fact: RequirementFact
    aggregate_version: int = Field(ge=1)
    session_id: UUID
    language: LanguageCode
    occurred_at: AwareDatetime


class ConversationFactSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lead_id: UUID
    session_id: UUID
    aggregate_version: int = Field(ge=1)
    facts: tuple[JournaledConversationFact, ...]


class JournaledConversationRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: RequirementRevision
    aggregate_version: int = Field(ge=1)
    session_id: UUID
    occurred_at: AwareDatetime


class LeadKnowledgeSourceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lead_id: UUID
    aggregate_version: int = Field(ge=1)
    session_ids: tuple[UUID, ...] = Field(max_length=1_000)
    facts: tuple[JournaledConversationFact, ...] = Field(max_length=10_000)
    revisions: tuple[JournaledConversationRevision, ...] = Field(max_length=10_000)


@dataclass(frozen=True, slots=True)
class ConversationTurnPreparation:
    lead_id: UUID
    session_id: UUID
    operation_id: UUID
    operation_fingerprint: str
    aggregate_version: int
    rollback_state: ConversationState
    existing: JournaledConversationTurn | None = None


def canonical_operation_fingerprint(
    engine: ConversationEngine,
    session_id: UUID,
    payload: dict[str, JsonValue],
) -> str:
    encoded = _canonical_json(payload, size_limit=MAX_FINGERPRINT_INPUT_BYTES)
    return engine.operation_fingerprint(session_id, encoded)


class ConversationJournal:
    def __init__(
        self,
        repository: EventRepository,
        *,
        max_events: int = 1_000,
        load_attempts: int = 3,
    ) -> None:
        if not 1 <= max_events <= 9_999:
            raise ValueError("journal event capacity must be between 1 and 9999")
        if not 1 <= load_attempts <= 10:
            raise ValueError("journal load attempts must be between 1 and 10")
        self._repository = repository
        self._max_events = max_events
        self._load_attempts = load_attempts

    def process_turn(
        self,
        engine: ConversationEngine,
        session_id: UUID,
        *,
        operation_id: UUID,
        text: str,
        language: LanguageCode,
        source_span_id: UUID | None = None,
        expected_version: int | None = None,
        occurred_at: datetime | None = None,
        operation_context: dict[str, JsonValue] | None = None,
    ) -> JournaledConversationTurn:
        preparation = self.prepare_turn(
            engine,
            session_id,
            operation_id=operation_id,
            text=text,
            language=language,
            source_span_id=source_span_id,
            expected_version=expected_version,
            operation_context=operation_context,
        )
        if preparation.existing is not None:
            return preparation.existing
        try:
            result = engine.process_turn(
                session_id,
                text=text,
                language=language,
                source_span_id=source_span_id,
            )
        except Exception:
            engine.restore(session_id, preparation.rollback_state)
            raise
        return self.commit_turn(
            engine,
            preparation,
            result,
            occurred_at=occurred_at,
        )

    def prepare_turn(
        self,
        engine: ConversationEngine,
        session_id: UUID,
        *,
        operation_id: UUID,
        text: str,
        language: LanguageCode,
        source_span_id: UUID | None = None,
        expected_version: int | None = None,
        operation_context: dict[str, JsonValue] | None = None,
    ) -> ConversationTurnPreparation:
        operation_fingerprint = self._request_fingerprint(
            engine,
            session_id,
            text=text,
            language=language,
            source_span_id=source_span_id,
            operation_context=operation_context,
        )
        before = engine.checkpoint(session_id)
        before_checkpoint = engine.export_checkpoint(session_id)
        lead_id = before.lead_id
        events, status = self._load_events(lead_id)
        existing = self._find_operation(
            events,
            session_id=session_id,
            operation_id=operation_id,
            operation_fingerprint=operation_fingerprint,
        )
        if existing is not None:
            replay = self._replay_loaded(lead_id, session_id, events, status)
            engine.replace_checkpoint(session_id, replay.checkpoint)
            return ConversationTurnPreparation(
                lead_id=lead_id,
                session_id=session_id,
                operation_id=operation_id,
                operation_fingerprint=operation_fingerprint,
                aggregate_version=existing.aggregate_version,
                rollback_state=before,
                existing=existing,
            )
        session_events = [item for item in events if item.event.session_id == session_id]
        if session_events:
            durable = self._replay_loaded(lead_id, session_id, events, status)
            if before_checkpoint != durable.checkpoint:
                raise ConcurrencyConflictError(
                    "Live conversation state does not match durable history"
                )
        elif before_checkpoint.turn_count != 0:
            raise ConcurrencyConflictError("Live conversation state has unpersisted turns")
        current_version = status.current_version if status is not None else 0
        if current_version >= self._max_events:
            raise JournalHistoryUnavailableError("conversation journal event capacity reached")
        if expected_version is not None and expected_version != current_version:
            raise ConcurrencyConflictError(
                f"Expected version {expected_version}, found {current_version}"
            )
        return ConversationTurnPreparation(
            lead_id=lead_id,
            session_id=session_id,
            operation_id=operation_id,
            operation_fingerprint=operation_fingerprint,
            aggregate_version=current_version,
            rollback_state=before,
        )

    def commit_turn(
        self,
        engine: ConversationEngine,
        preparation: ConversationTurnPreparation,
        result: ConversationResult,
        *,
        occurred_at: datetime | None = None,
    ) -> JournaledConversationTurn:
        if preparation.existing is not None:
            raise ValueError("Persisted turn preparations cannot be committed again")
        try:
            checkpoint = engine.export_checkpoint(preparation.session_id)
            if checkpoint.lead_id != preparation.lead_id:
                raise ValueError("Conversation lead changed after journal preparation")
            proposed = self._event_from_state(
                session_id=preparation.session_id,
                operation_id=preparation.operation_id,
                operation_fingerprint=preparation.operation_fingerprint,
                checkpoint=checkpoint,
                result=result,
            )
            persisted = self._repository.append(
                preparation.lead_id,
                CONVERSATION_AGGREGATE_TYPE,
                TURN_ACCEPTED_EVENT_TYPE,
                self._serialize_event(proposed),
                expected_version=preparation.aggregate_version,
                occurred_at=occurred_at,
            )
        except ConcurrencyConflictError:
            engine.restore(preparation.session_id, preparation.rollback_state)
            reconciled_events, reconciled_status = self._load_events(preparation.lead_id)
            reconciled = self._find_operation(
                reconciled_events,
                session_id=preparation.session_id,
                operation_id=preparation.operation_id,
                operation_fingerprint=preparation.operation_fingerprint,
            )
            if reconciled is not None:
                replay = self._replay_loaded(
                    preparation.lead_id,
                    preparation.session_id,
                    reconciled_events,
                    reconciled_status,
                )
                engine.replace_checkpoint(preparation.session_id, replay.checkpoint)
                return reconciled
            self._synchronize_after_conflict(
                engine,
                preparation,
                reconciled_events,
                reconciled_status,
            )
            raise
        except Exception:
            engine.restore(preparation.session_id, preparation.rollback_state)
            raise
        return JournaledConversationTurn(
            aggregate_version=persisted.aggregate_version,
            occurred_at=persisted.occurred_at,
            event=proposed,
        )

    def find_turn(
        self,
        engine: ConversationEngine,
        lead_id: UUID,
        *,
        session_id: UUID,
        operation_id: UUID,
        text: str,
        language: LanguageCode,
        source_span_id: UUID | None = None,
        operation_context: dict[str, JsonValue] | None = None,
    ) -> JournaledConversationTurn | None:
        operation_fingerprint = self._request_fingerprint(
            engine,
            session_id,
            text=text,
            language=language,
            source_span_id=source_span_id,
            operation_context=operation_context,
        )
        events, _ = self._load_events(lead_id)
        return self._find_operation(
            events,
            session_id=session_id,
            operation_id=operation_id,
            operation_fingerprint=operation_fingerprint,
        )

    @staticmethod
    def _request_fingerprint(
        engine: ConversationEngine,
        session_id: UUID,
        *,
        text: str,
        language: LanguageCode,
        source_span_id: UUID | None,
        operation_context: dict[str, JsonValue] | None,
    ) -> str:
        request: dict[str, JsonValue] = {
            "language": language.value,
            "source_span_id": str(source_span_id) if source_span_id is not None else None,
            "text": text,
        }
        if operation_context is not None:
            request["context"] = operation_context
        return canonical_operation_fingerprint(
            engine,
            session_id,
            request,
        )

    def _synchronize_after_conflict(
        self,
        engine: ConversationEngine,
        preparation: ConversationTurnPreparation,
        events: list[JournaledConversationTurn],
        status: AggregateStatus | None,
    ) -> None:
        try:
            replay = self._replay_loaded(
                preparation.lead_id,
                preparation.session_id,
                events,
                status,
            )
        except LookupError:
            return
        engine.replace_checkpoint(preparation.session_id, replay.checkpoint)

    def replay(self, lead_id: UUID, session_id: UUID) -> ConversationReplay:
        events, status = self._load_events(lead_id)
        return self._replay_loaded(lead_id, session_id, events, status)

    def facts_for_retrieval(
        self,
        lead_id: UUID,
        session_id: UUID,
    ) -> ConversationFactSnapshot:
        events, status = self._load_events(lead_id)
        replay = self._replay_loaded(lead_id, session_id, events, status)
        facts_by_key: dict[str, JournaledConversationFact] = {}
        for item in events:
            if item.event.session_id != session_id:
                continue
            for fact in item.event.result.facts:
                facts_by_key[fact.key] = JournaledConversationFact(
                    fact=fact,
                    aggregate_version=item.aggregate_version,
                    session_id=session_id,
                    language=item.event.result.language,
                    occurred_at=item.occurred_at,
                )
        if {item.fact.fact_id for item in facts_by_key.values()} != {
            fact.fact_id for fact in replay.checkpoint.facts
        }:
            raise JournalCorruptionError("retrieval facts do not match replayed state")
        return ConversationFactSnapshot(
            lead_id=lead_id,
            session_id=session_id,
            aggregate_version=replay.aggregate_version,
            facts=tuple(sorted(facts_by_key.values(), key=lambda item: item.fact.key)),
        )

    def validate_fact_snapshot(self, snapshot: ConversationFactSnapshot) -> None:
        self._validate_active_version(
            snapshot.lead_id,
            snapshot.aggregate_version,
            change_message="conversation facts changed during retrieval",
        )

    def knowledge_source(
        self,
        lead_id: UUID,
        *,
        max_sessions: int = 1_000,
        max_facts: int = 1_000,
        max_revisions: int = 1_000,
    ) -> LeadKnowledgeSourceSnapshot:
        if min(max_sessions, max_facts, max_revisions) < 1:
            raise ValueError("knowledge source capacities must be positive")
        if max_sessions > 1_000 or max_facts > 10_000 or max_revisions > 10_000:
            raise ValueError("knowledge source capacities exceed safe limits")
        events, status = self._load_events(lead_id)
        session_ids = tuple(sorted({item.event.session_id for item in events}, key=str))
        if not session_ids or status is None:
            raise LookupError("Conversation journal not found")
        if len(session_ids) > max_sessions:
            raise JournalHistoryUnavailableError("knowledge source session capacity reached")
        for session_id in session_ids:
            self._replay_loaded(lead_id, session_id, events, status)

        facts: list[JournaledConversationFact] = []
        revisions: list[JournaledConversationRevision] = []
        facts_by_id: dict[UUID, JournaledConversationFact] = {}
        superseded_fact_ids: set[UUID] = set()
        revision_ids: set[UUID] = set()
        for item in events:
            turn_facts = {fact.fact_id: fact for fact in item.event.result.facts}
            if len(facts) + len(item.event.result.facts) > max_facts:
                raise JournalHistoryUnavailableError("knowledge source fact capacity reached")
            if len(revisions) + len(item.event.result.revisions) > max_revisions:
                raise JournalHistoryUnavailableError("knowledge source revision capacity reached")
            for fact in item.event.result.facts:
                if fact.fact_id in facts_by_id:
                    raise JournalCorruptionError("knowledge source has duplicate fact identifiers")
                source = JournaledConversationFact(
                    fact=fact,
                    aggregate_version=item.aggregate_version,
                    session_id=item.event.session_id,
                    language=item.event.result.language,
                    occurred_at=item.occurred_at,
                )
                facts_by_id[fact.fact_id] = source
                facts.append(source)
            for revision in item.event.result.revisions:
                if revision.revision_id in revision_ids:
                    raise JournalCorruptionError(
                        "knowledge source has duplicate revision identifiers"
                    )
                revision_ids.add(revision.revision_id)
                replacement = turn_facts.get(revision.replacement_fact_id)
                previous = (
                    facts_by_id.get(revision.previous_fact_id)
                    if revision.previous_fact_id is not None
                    else None
                )
                if replacement is None or replacement.key != revision.key:
                    raise JournalCorruptionError("knowledge revision replacement is inconsistent")
                if (
                    previous is None
                    or previous.fact.fact_id == revision.replacement_fact_id
                    or previous.fact.key != revision.key
                    or previous.session_id != item.event.session_id
                    or previous.aggregate_version >= item.aggregate_version
                ):
                    raise JournalCorruptionError("knowledge revision source is inconsistent")
                if item.occurred_at < previous.occurred_at:
                    raise JournalCorruptionError(
                        "knowledge revision timestamp precedes fact timestamp"
                    )
                if previous.fact.fact_id in superseded_fact_ids:
                    raise JournalCorruptionError("knowledge fact is superseded more than once")
                superseded_fact_ids.add(previous.fact.fact_id)
                revisions.append(
                    JournaledConversationRevision(
                        revision=revision,
                        aggregate_version=item.aggregate_version,
                        session_id=item.event.session_id,
                        occurred_at=item.occurred_at,
                    )
                )
        return LeadKnowledgeSourceSnapshot(
            lead_id=lead_id,
            aggregate_version=status.current_version,
            session_ids=session_ids,
            facts=tuple(facts),
            revisions=tuple(revisions),
        )

    def validate_knowledge_source(self, snapshot: LeadKnowledgeSourceSnapshot) -> None:
        self._validate_active_version(
            snapshot.lead_id,
            snapshot.aggregate_version,
            change_message="conversation facts changed during projection",
        )

    def validate_knowledge_version(self, lead_id: UUID, aggregate_version: int) -> None:
        self._validate_active_version(
            lead_id,
            aggregate_version,
            change_message="conversation facts changed during knowledge retrieval",
        )

    def _validate_active_version(
        self,
        lead_id: UUID,
        aggregate_version: int,
        *,
        change_message: str,
    ) -> None:
        status = self._repository.status(lead_id)
        if status is None or status.current_version != aggregate_version:
            raise ConcurrencyConflictError(change_message)
        if status.aggregate_type != CONVERSATION_AGGREGATE_TYPE:
            raise JournalCorruptionError("conversation journal aggregate type is invalid")
        if status.privacy_state != "active":
            raise JournalHistoryUnavailableError(f"conversation journal is {status.privacy_state}")

    def read_turns(
        self,
        lead_id: UUID,
        session_id: UUID,
        *,
        limit: int,
    ) -> tuple[JournaledConversationTurn, ...]:
        if limit < 1 or limit > 100:
            raise ValueError("Conversation journal read limit must be between 1 and 100")
        events, status = self._load_events(lead_id)
        self._replay_loaded(lead_id, session_id, events, status)
        session_events = [item for item in events if item.event.session_id == session_id]
        return tuple(session_events[-limit:])

    def restore_session(
        self,
        engine: ConversationEngine,
        lead_id: UUID,
        session_id: UUID,
    ) -> ConversationReplay:
        replay = self.replay(lead_id, session_id)
        engine.restore_checkpoint(session_id, replay.checkpoint)
        return replay

    def synchronize_session(
        self,
        engine: ConversationEngine,
        lead_id: UUID,
        session_id: UUID,
    ) -> ConversationReplay:
        replay = self.replay(lead_id, session_id)
        engine.replace_checkpoint(session_id, replay.checkpoint)
        return replay

    def _load_events(
        self,
        lead_id: UUID,
    ) -> tuple[list[JournaledConversationTurn], AggregateStatus | None]:
        for _ in range(self._load_attempts):
            status_before = self._repository.status(lead_id)
            raw_events = self._repository.read(lead_id, limit=self._max_events + 1)
            status_after = self._repository.status(lead_id)
            if status_before != status_after:
                continue
            if status_after is not None:
                if status_after.aggregate_type != CONVERSATION_AGGREGATE_TYPE:
                    raise JournalCorruptionError("conversation journal aggregate type is invalid")
                if status_after.privacy_state != "active":
                    raise JournalHistoryUnavailableError(
                        f"conversation journal is {status_after.privacy_state}"
                    )
                if status_after.current_version != len(raw_events):
                    raise JournalHistoryUnavailableError(
                        "conversation journal history is incomplete or unavailable"
                    )
            elif raw_events:
                continue
            if len(raw_events) > self._max_events:
                raise JournalHistoryUnavailableError(
                    "conversation journal exceeds configured event capacity"
                )
            for expected_version, event in enumerate(raw_events, start=1):
                if event.aggregate_version != expected_version:
                    raise JournalCorruptionError(
                        "conversation journal aggregate versions are not contiguous"
                    )

            parsed: list[JournaledConversationTurn] = []
            for event in raw_events:
                if event.event_type == TURN_ACCEPTED_EVENT_TYPE:
                    parsed.append(self._parse_event(event, lead_id))
                elif event.event_type.startswith("conversation."):
                    raise JournalCorruptionError(
                        f"unsupported conversation event: {event.event_type}"
                    )
            operation_ids = [item.event.operation_id for item in parsed]
            if len(operation_ids) != len(set(operation_ids)):
                raise JournalCorruptionError("conversation journal has duplicate operation IDs")
            return parsed, status_after
        raise ConcurrencyConflictError("Conversation journal changed while loading")

    def _replay_loaded(
        self,
        lead_id: UUID,
        session_id: UUID,
        events: list[JournaledConversationTurn],
        status: AggregateStatus | None,
    ) -> ConversationReplay:
        session_events = [item for item in events if item.event.session_id == session_id]
        if not session_events or status is None:
            raise LookupError("Conversation journal not found")
        first = session_events[0].event
        facts_by_key: dict[str, RequirementFact] = {}
        evidence: deque[IntentEvidence] = deque(maxlen=first.max_evidence)
        classifications: deque[Classification] = deque(maxlen=first.max_classifications)
        recent_turn_digests: deque[str] = deque(maxlen=min(first.max_turns, 20))
        previous: ConversationTurnEvent | None = None

        for expected_turn, item in enumerate(session_events, start=1):
            event = item.event
            self._validate_replay_event(
                event,
                lead_id=lead_id,
                expected_turn=expected_turn,
                first=first,
                previous=previous,
            )
            for fact in event.result.facts:
                facts_by_key[fact.key] = fact
            evidence.extend(event.result.evidence)
            classifications.append(event.result.classification)
            recent_turn_digests.append(event.turn_digest)
            previous = event

        assert previous is not None
        checkpoint = ConversationStateCheckpoint(
            checkpoint_schema_version="1",
            lead_id=lead_id,
            max_turns=first.max_turns,
            max_facts=first.max_facts,
            max_evidence=first.max_evidence,
            max_classifications=first.max_classifications,
            max_goal_changes=first.max_goal_changes,
            digest_key_id=first.digest_key_id,
            phase=previous.result.phase,
            turn_count=previous.result.turn_count,
            abuse_redirected=previous.abuse_redirected,
            stopped=previous.stopped,
            recent_turn_digests=tuple(recent_turn_digests),
            facts=tuple(facts_by_key.values()),
            evidence=tuple(evidence),
            classifications=tuple(classifications),
            goal_change_count=previous.goal_change_count,
        )
        return ConversationReplay(
            lead_id=lead_id,
            session_id=session_id,
            aggregate_version=status.current_version,
            checkpoint=checkpoint,
            last_result=previous.result,
        )

    @staticmethod
    def _validate_replay_event(
        event: ConversationTurnEvent,
        *,
        lead_id: UUID,
        expected_turn: int,
        first: ConversationTurnEvent,
        previous: ConversationTurnEvent | None,
    ) -> None:
        configuration = (
            event.max_turns,
            event.max_facts,
            event.max_evidence,
            event.max_classifications,
            event.max_goal_changes,
            event.digest_key_id,
        )
        first_configuration = (
            first.max_turns,
            first.max_facts,
            first.max_evidence,
            first.max_classifications,
            first.max_goal_changes,
            first.digest_key_id,
        )
        if configuration != first_configuration:
            raise JournalCorruptionError("conversation journal configuration changes")
        if event.result.turn_count != expected_turn:
            raise JournalCorruptionError("conversation journal turn sequence is inconsistent")
        nested_lead_ids = {
            event.result.classification.lead_id,
            *(fact.lead_id for fact in event.result.facts),
            *(revision.lead_id for revision in event.result.revisions),
            *(item.lead_id for item in event.result.evidence),
        }
        if nested_lead_ids - {lead_id}:
            raise JournalCorruptionError("conversation journal changes lead identity")
        if previous is not None:
            if previous.stopped:
                raise JournalCorruptionError("conversation journal continues after closure")
            if event.goal_change_count < previous.goal_change_count:
                raise JournalCorruptionError("conversation journal goal-change count decreases")
            if previous.abuse_redirected and not event.abuse_redirected:
                raise JournalCorruptionError("conversation journal abuse state regresses")

    @staticmethod
    def _event_from_state(
        *,
        session_id: UUID,
        operation_id: UUID,
        operation_fingerprint: str,
        checkpoint: ConversationStateCheckpoint,
        result: ConversationResult,
    ) -> ConversationTurnEvent:
        if not checkpoint.recent_turn_digests:
            raise ValueError("processed turn is missing its repetition digest")
        return ConversationTurnEvent(
            event_schema_version="1",
            session_id=session_id,
            operation_id=operation_id,
            operation_fingerprint=operation_fingerprint,
            turn_digest=checkpoint.recent_turn_digests[-1],
            max_turns=checkpoint.max_turns,
            max_facts=checkpoint.max_facts,
            max_evidence=checkpoint.max_evidence,
            max_classifications=checkpoint.max_classifications,
            max_goal_changes=checkpoint.max_goal_changes,
            digest_key_id=checkpoint.digest_key_id,
            abuse_redirected=checkpoint.abuse_redirected,
            stopped=checkpoint.stopped,
            goal_change_count=checkpoint.goal_change_count,
            result=result,
        )

    @staticmethod
    def _parse_event(event: AuditEvent, lead_id: UUID) -> JournaledConversationTurn:
        if event.aggregate_type != CONVERSATION_AGGREGATE_TYPE:
            raise JournalCorruptionError("conversation journal aggregate type is invalid")
        _validate_event_size(event.payload)
        try:
            payload = ConversationTurnEvent.model_validate(event.payload)
        except ValidationError as error:
            raise JournalCorruptionError("conversation journal payload is invalid") from error
        if payload.result.classification.lead_id != lead_id:
            raise JournalCorruptionError("conversation journal changes lead identity")
        return JournaledConversationTurn(
            aggregate_version=event.aggregate_version,
            occurred_at=event.occurred_at,
            event=payload,
        )

    @staticmethod
    def _find_operation(
        events: list[JournaledConversationTurn],
        *,
        session_id: UUID,
        operation_id: UUID,
        operation_fingerprint: str,
    ) -> JournaledConversationTurn | None:
        for persisted in events:
            if persisted.event.operation_id != operation_id:
                continue
            if (
                persisted.event.session_id != session_id
                or persisted.event.operation_fingerprint != operation_fingerprint
            ):
                raise JournalOperationConflictError(
                    "operation ID was already used for different turn input"
                )
            return persisted
        return None

    @staticmethod
    def _serialize_event(event: ConversationTurnEvent) -> dict[str, JsonValue]:
        payload = event.model_dump(mode="json")
        _validate_event_size(payload)
        return payload


def _canonical_json(payload: dict[str, JsonValue], *, size_limit: int) -> bytes:
    try:
        encoded = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("operation payload must contain finite JSON values") from error
    if len(encoded) > size_limit:
        raise ValueError("operation payload exceeds size limit")
    return encoded


def _validate_event_size(payload: dict[str, JsonValue]) -> None:
    try:
        encoded = _canonical_json(payload, size_limit=MAX_EVENT_PAYLOAD_BYTES)
    except ValueError as error:
        if "size" in str(error):
            raise JournalHistoryUnavailableError(
                "conversation journal event exceeds payload size limit"
            ) from error
        raise JournalCorruptionError("conversation journal payload is not finite JSON") from error
    if len(encoded) > MAX_EVENT_PAYLOAD_BYTES:
        raise JournalHistoryUnavailableError(
            "conversation journal event exceeds payload size limit"
        )
