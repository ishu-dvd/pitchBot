from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4, uuid5

from pitchbot.actions import (
    ActionAuthorizationContext,
    ActionPolicy,
    ActionPreviewResult,
    ActionWorkflowService,
    CallbackService,
    DeckService,
    build_follow_up,
)
from pitchbot.adapters import AdapterError, Clock, SystemClock
from pitchbot.adapters.mocks import (
    MockArtifactAdapter,
    MockSchedulerAdapter,
    MockTelephonyAdapter,
    MockWhatsAppAdapter,
)
from pitchbot.conversation import (
    ConversationDisposition,
    ConversationEngine,
    ConversationJournal,
    ConversationResult,
    ConversationState,
    JournalCorruptionError,
    JournalHistoryUnavailableError,
    JournalOperationConflictError,
)
from pitchbot.domain import ContactPolicy, LanguageCode
from pitchbot.simulator.models import (
    AudioMetadata,
    CreateSessionRequest,
    DurableConversationResult,
    DurableHistoryResponse,
    DurableHistoryTurn,
    LeadHistoryResponse,
    PreviewAction,
    ResumeSessionRequest,
    SessionResponse,
    SimulatorEvent,
    SimulatorEventType,
    TurnRequest,
    TurnResponse,
)
from pitchbot.simulator.scenarios import SCENARIOS
from pitchbot.storage import AggregateClosedError, ConcurrencyConflictError

logger = logging.getLogger(__name__)

DISCLOSURES = {
    LanguageCode.ENGLISH: "Hello, I am PitchBot, an AI sales assistant. This is a simulation.",
    LanguageCode.HINDI: "नमस्ते, मैं पिचबॉट हूँ, एक एआई सेल्स असिस्टेंट। यह एक सिमुलेशन है।",
    LanguageCode.MIXED: "Namaste, main PitchBot hoon, ek AI sales assistant. Yeh simulation hai.",
    LanguageCode.UNKNOWN: "Hello, I am PitchBot, an AI sales assistant. Please choose a language.",
}


class SessionNotFoundError(LookupError):
    pass


class InjectedSimulatorError(RuntimeError):
    pass


class TurnConflictError(RuntimeError):
    pass


class DurableHistoryDisabledError(RuntimeError):
    pass


class DurableActionReplayUnavailableError(RuntimeError):
    pass


class SessionAdmissionConflictError(RuntimeError):
    pass


class SessionCapacityError(RuntimeError):
    pass


_LEAD_ID_NAMESPACE = UUID("a327c17a-6d9f-4c26-b255-5366e5bb8d1d")


@dataclass(slots=True)
class _TurnOperation:
    fingerprint: str
    started_at: datetime
    response: TurnResponse | None = None
    injected_failure: str | None = None
    action_completed: bool = False


@dataclass(slots=True)
class _Session:
    session_id: UUID
    lead_id: UUID
    lead_ref: str
    language: LanguageCode
    events: deque[SimulatorEvent]
    preview_consent_granted: bool
    contact_policy: ContactPolicy
    next_sequence: int = 1
    audio_chunks_received: int = 0
    approved_preview_count: int = 0
    closing: bool = False
    recovered: bool = False
    turn_operations: dict[UUID, _TurnOperation] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SimulatorService:
    def __init__(
        self,
        *,
        clock: Clock | None = None,
        max_sessions: int = 100,
        max_events_per_session: int = 200,
        max_history_events_per_lead: int = 500,
        max_audio_chunks_per_session: int = 2_000,
        max_turn_operations_per_session: int = 100,
        conversation_engine: ConversationEngine | None = None,
        conversation_journal: ConversationJournal | None = None,
        action_workflows: ActionWorkflowService | None = None,
    ) -> None:
        if (
            min(
                max_sessions,
                max_events_per_session,
                max_history_events_per_lead,
                max_audio_chunks_per_session,
                max_turn_operations_per_session,
            )
            < 1
        ):
            raise ValueError("Simulator capacities must be positive")
        self._clock = clock or SystemClock()
        self._max_sessions = max_sessions
        self._max_events_per_session = max_events_per_session
        self._max_audio_chunks_per_session = max_audio_chunks_per_session
        self._max_turn_operations_per_session = max_turn_operations_per_session
        self._conversation = conversation_engine or ConversationEngine()
        self._journal = conversation_journal
        if action_workflows is None:
            action_policy = ActionPolicy(clock=self._clock)
            action_workflows = ActionWorkflowService(
                policy=action_policy,
                callbacks=CallbackService(
                    scheduler=MockSchedulerAdapter(),
                    telephony=MockTelephonyAdapter(),
                    policy=action_policy,
                    clock=self._clock,
                ),
                decks=DeckService(
                    artifact_adapter=MockArtifactAdapter(),
                    clock=self._clock,
                ),
                whatsapp=MockWhatsAppAdapter(),
                clock=self._clock,
            )
        self._actions = action_workflows
        self._sessions: dict[UUID, _Session] = {}
        self._registry_lock = threading.Lock()
        self._admitting: set[UUID] = set()

    def create_session(self, request: CreateSessionRequest) -> SessionResponse:
        session_id = uuid4()
        lead_id = (
            uuid5(_LEAD_ID_NAMESPACE, request.lead_ref) if self._journal is not None else uuid4()
        )
        session = _Session(
            session_id=session_id,
            lead_id=lead_id,
            lead_ref=request.lead_ref,
            language=request.language,
            events=deque(maxlen=self._max_events_per_session),
            preview_consent_granted=request.preview_consent_granted,
            contact_policy=request.contact_policy,
        )
        self._append_event(
            session,
            SimulatorEventType.DISCLOSURE,
            text=DISCLOSURES[request.language],
            language=request.language,
        )
        with self._registry_lock:
            self._reserve_capacity()
            self._conversation.create_session(session_id, lead_id=lead_id)
            self._sessions[session_id] = session
        return self._session_response(session)

    def _reserve_capacity(self) -> None:
        if len(self._sessions) + len(self._admitting) >= self._max_sessions:
            raise SessionCapacityError("Simulator session capacity reached")

    def _begin_teardown(self, session_id: UUID, session: _Session) -> bool:
        """Remove the session and hold its identifier so no resume can interleave."""

        with self._registry_lock:
            if self._sessions.get(session_id) is not session:
                return False
            del self._sessions[session_id]
            self._admitting.add(session_id)
        return True

    def _release_admission(self, session_id: UUID) -> None:
        with self._registry_lock:
            self._admitting.discard(session_id)

    def _abort_teardown(self, session_id: UUID, session: _Session) -> None:
        """Republish a session whose teardown failed, so closing stays retryable."""

        with self._registry_lock:
            self._sessions[session_id] = session
            self._admitting.discard(session_id)

    async def process_turn(self, session_id: UUID, request: TurnRequest) -> TurnResponse:
        session = self._get_session(session_id)
        async with session.lock:
            self._ensure_session_active(session_id, session)
            if session.recovered and request.preview_action is not PreviewAction.NONE:
                raise DurableActionReplayUnavailableError(
                    "Action previews are unavailable for recovered sessions"
                )
            fingerprint = request.model_dump_json(
                exclude={"inject_failure", "operation_id", "simulated_latency_ms"}
            )
            operation = session.turn_operations.get(request.operation_id)
            if operation is not None:
                if operation.fingerprint != fingerprint:
                    raise TurnConflictError("Turn operation identifier reused with different input")
                if operation.response is not None:
                    return operation.response
                if operation.injected_failure is not None:
                    raise InjectedSimulatorError(operation.injected_failure)
            else:
                if len(session.turn_operations) >= self._max_turn_operations_per_session:
                    raise RuntimeError("Simulator turn operation capacity reached")
                operation = _TurnOperation(fingerprint=fingerprint, started_at=self._clock.now())
                session.turn_operations[request.operation_id] = operation
            if self._journal is None:
                self._ensure_conversation_open(session_id)
            if request.inject_failure:
                if self._journal is not None:
                    self._ensure_conversation_open(session_id)
                message = "Deterministic simulator failure injected"
                self._append_event(
                    session,
                    SimulatorEventType.FAILURE,
                    text=f"{message}.",
                )
                operation.injected_failure = message
                raise InjectedSimulatorError(message)
            conversation_checkpoint = self._conversation.checkpoint(session_id)
            event_checkpoint = deque(session.events, maxlen=session.events.maxlen)
            language_checkpoint = session.language
            next_sequence_checkpoint = session.next_sequence
            approved_preview_checkpoint = session.approved_preview_count
            if request.simulated_latency_ms:
                await asyncio.sleep(request.simulated_latency_ms / 1_000)
            preparation = None
            replaying_durable_turn = False
            if self._journal is not None:
                try:
                    preparation = self._journal.prepare_turn(
                        self._conversation,
                        session_id,
                        operation_id=request.operation_id,
                        text=request.text,
                        language=request.language,
                        operation_context=request.model_dump(
                            mode="json",
                            exclude={
                                "inject_failure",
                                "language",
                                "operation_id",
                                "simulated_latency_ms",
                                "text",
                            },
                        ),
                    )
                except ConcurrencyConflictError:
                    await self._discard_session(session)
                    raise
                except JournalOperationConflictError:
                    session.turn_operations.pop(request.operation_id, None)
                    raise
                except (
                    AggregateClosedError,
                    JournalCorruptionError,
                    JournalHistoryUnavailableError,
                ):
                    await self._discard_session(session)
                    raise
                if preparation.existing is not None:
                    if (
                        request.preview_action is not PreviewAction.NONE
                        and not operation.action_completed
                    ):
                        raise DurableActionReplayUnavailableError(
                            "Action preview response is unavailable after session recovery"
                        )
                    outcome = preparation.existing.event.result
                    if request.preview_action is PreviewAction.NONE:
                        session.language = outcome.language
                        response = self._turn_response(session, outcome, preview=None)
                        operation.response = response
                        return response
                    replaying_durable_turn = True
            conversation_checkpoint = self._conversation.checkpoint(session_id)
            if not replaying_durable_turn:
                self._ensure_conversation_open(session_id)
                outcome = self._conversation.process_turn(
                    session.session_id,
                    text=request.text,
                    language=request.language,
                )
            session.language = request.language
            self._append_event(
                session,
                SimulatorEventType.BUYER_TURN,
                text=request.text,
                language=request.language,
            )
            reply = outcome.reply
            self._append_event(
                session,
                SimulatorEventType.ASSISTANT_TURN,
                text=reply,
                language=request.language,
            )
            self._append_event(
                session,
                SimulatorEventType.CONVERSATION_OUTCOME,
                language=request.language,
                metadata={
                    "disposition": outcome.disposition.value,
                    "phase": outcome.phase.value,
                    "temperature": outcome.classification.temperature.value,
                    "safety_signal_count": len(outcome.safety_signals),
                    "repeated_turn": outcome.repeated_turn,
                },
            )
            preview: ActionPreviewResult | None = None
            if outcome.disposition is not ConversationDisposition.CONTINUE:
                preview = None
            elif request.preview_action is not PreviewAction.NONE:
                snapshot = self._conversation.snapshot(session.session_id)
                context = ActionAuthorizationContext(
                    disclosure_delivered=True,
                    consent_granted=session.preview_consent_granted,
                    contact_policy=session.contact_policy,
                    temperature=outcome.classification.temperature,
                    conversation_disposition=outcome.disposition.value,
                    used_actions=session.approved_preview_count,
                )
                facts = {fact.key: fact.value for fact in snapshot.facts}
                try:
                    if request.preview_action is PreviewAction.WHATSAPP:
                        preview = await self._actions.preview_whatsapp(
                            session_id=session.session_id,
                            follow_up=build_follow_up(
                                lead_id=snapshot.lead_id,
                                language=request.language,
                                facts=facts,
                                next_steps=("Review the synthetic preview",),
                            ),
                            context=context,
                            operation_id=request.operation_id,
                        )
                    elif request.preview_action is PreviewAction.CALLBACK:
                        preview = await self._actions.preview_callback(
                            session_id=session.session_id,
                            lead_id=snapshot.lead_id,
                            delay_minutes=request.callback_delay_minutes,
                            context=context,
                            operation_id=request.operation_id,
                            requested_at=operation.started_at,
                        )
                    else:
                        features = tuple(
                            item.strip()
                            for item in str(facts.get("requested_features", "")).split(",")
                            if item.strip()
                        )
                        preview = await self._actions.preview_deck(
                            session_id=session.session_id,
                            lead_id=snapshot.lead_id,
                            industry=request.deck_industry,
                            language=request.language,
                            features=features,
                            context=context,
                            operation_id=request.operation_id,
                        )
                    operation.action_completed = True
                except asyncio.CancelledError:
                    self._conversation.restore(session_id, conversation_checkpoint)
                    session.events = event_checkpoint
                    session.language = language_checkpoint
                    session.next_sequence = next_sequence_checkpoint
                    session.approved_preview_count = approved_preview_checkpoint
                    raise
                except (AdapterError, RuntimeError, ValueError):
                    self._conversation.restore(session_id, conversation_checkpoint)
                    session.events = event_checkpoint
                    session.language = language_checkpoint
                    session.next_sequence = next_sequence_checkpoint
                    session.approved_preview_count = approved_preview_checkpoint
                    raise
                if preview.decision.status.value == "approved":
                    session.approved_preview_count += 1
                self._append_event(
                    session,
                    SimulatorEventType.ACTION_PREVIEW,
                    text=preview.label,
                    language=request.language,
                    metadata={
                        "action": request.preview_action.value,
                        "authorization": preview.decision.status.value,
                        "block_reason_count": len(preview.decision.reasons),
                        "executed": preview.executed,
                    },
                )
            if self._journal is not None and not replaying_durable_turn:
                if preparation is None:
                    raise RuntimeError("Durable journal preparation is missing")
                try:
                    self._journal.commit_turn(
                        self._conversation,
                        preparation,
                        outcome,
                    )
                except ConcurrencyConflictError:
                    await self._discard_session(session)
                    raise
                except (
                    AggregateClosedError,
                    JournalCorruptionError,
                    JournalHistoryUnavailableError,
                ):
                    await self._discard_session(session)
                    raise
                except asyncio.CancelledError:
                    self._restore_turn(
                        session,
                        conversation_checkpoint,
                        event_checkpoint,
                        language_checkpoint,
                        next_sequence_checkpoint,
                        approved_preview_checkpoint,
                    )
                    raise
                except Exception:
                    self._restore_turn(
                        session,
                        conversation_checkpoint,
                        event_checkpoint,
                        language_checkpoint,
                        next_sequence_checkpoint,
                        approved_preview_checkpoint,
                    )
                    raise
            response = self._turn_response(session, outcome, preview=preview)
            operation.response = response
            return response

    async def interrupt(self, session_id: UUID) -> SessionResponse:
        session = self._get_session(session_id)
        async with session.lock:
            self._ensure_session_active(session_id, session)
            self._ensure_conversation_open(session_id)
            self._append_event(
                session,
                SimulatorEventType.INTERRUPTION,
                text="Playback interrupted by simulator control.",
                language=session.language,
            )
            return self._session_response(session)

    async def record_audio_metadata(
        self,
        session_id: UUID,
        metadata: AudioMetadata,
    ) -> SimulatorEvent:
        session = self._get_session(session_id)
        async with session.lock:
            self._ensure_session_active(session_id, session)
            self._ensure_conversation_open(session_id)
            if session.audio_chunks_received >= self._max_audio_chunks_per_session:
                raise RuntimeError("Simulator audio metadata capacity reached")
            session.audio_chunks_received += 1
            return self._append_event(
                session,
                SimulatorEventType.AUDIO_METADATA,
                language=session.language,
                metadata={
                    "byte_count": metadata.byte_count,
                    "media_type": metadata.media_type,
                    "audio_retained": False,
                },
            )

    def get_session(self, session_id: UUID) -> SessionResponse:
        return self._session_response(self._get_session(session_id))

    def get_lead_history(self, session_id: UUID) -> LeadHistoryResponse:
        session = self._get_session(session_id)
        return LeadHistoryResponse(
            lead_ref=session.lead_ref,
            events=list(session.events),
        )

    def resume_session(
        self,
        session_id: UUID,
        request: ResumeSessionRequest,
    ) -> SessionResponse:
        if self._journal is None:
            raise DurableHistoryDisabledError("Durable conversation history is disabled")
        with self._registry_lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                if existing.lead_ref != request.lead_ref:
                    raise SessionNotFoundError(f"Unknown session: {session_id}")
                return self._session_response(existing)
            if session_id in self._admitting:
                raise SessionAdmissionConflictError(
                    f"Simulator session is already being resumed: {session_id}"
                )
            self._reserve_capacity()
            self._admitting.add(session_id)
        try:
            lead_id = uuid5(_LEAD_ID_NAMESPACE, request.lead_ref)
            replay = self._journal.restore_session(
                self._conversation,
                lead_id,
                session_id,
            )
            session = _Session(
                session_id=session_id,
                lead_id=lead_id,
                lead_ref=request.lead_ref,
                language=replay.last_result.language,
                events=deque(maxlen=self._max_events_per_session),
                preview_consent_granted=False,
                contact_policy=ContactPolicy(),
                recovered=True,
            )
        except BaseException:
            self._conversation.close_session(session_id)
            with self._registry_lock:
                self._admitting.discard(session_id)
            raise
        with self._registry_lock:
            self._sessions[session_id] = session
            self._admitting.discard(session_id)
        return self._session_response(session)

    def get_durable_history(
        self,
        session_id: UUID,
        *,
        limit: int,
    ) -> DurableHistoryResponse:
        if self._journal is None:
            raise DurableHistoryDisabledError("Durable conversation history is disabled")
        session = self._get_session(session_id)
        turns = self._journal.read_turns(
            session.lead_id,
            session_id,
            limit=limit,
        )
        return DurableHistoryResponse(
            session_id=session_id,
            turns=[
                DurableHistoryTurn(
                    aggregate_version=turn.aggregate_version,
                    occurred_at=turn.occurred_at,
                    result=DurableConversationResult.model_validate(
                        turn.event.result,
                        from_attributes=True,
                    ),
                )
                for turn in turns
            ],
        )

    async def close_session(self, session_id: UUID) -> None:
        session = self._get_session(session_id, allow_closing=True)
        async with session.lock:
            session.closing = True
            if not self._begin_teardown(session_id, session):
                raise SessionNotFoundError(f"Unknown session: {session_id}")
            # The reservation is held across action cleanup too, so a concurrent resume
            # cannot restore state that this teardown is about to erase by prefix.
            try:
                await self._actions.cleanup_session(session_id)
            except BaseException:
                self._abort_teardown(session_id, session)
                raise
            try:
                self._conversation.close_session(session_id)
            finally:
                self._release_admission(session_id)

    def replay(self, scenario_id: str) -> list[dict[str, str]]:
        try:
            scenario = SCENARIOS[scenario_id]
        except KeyError as error:
            raise SessionNotFoundError(f"Unknown scenario: {scenario_id}") from error
        return [
            {
                "speaker": turn.speaker,
                "text": turn.text,
                "language": turn.language.value,
            }
            for turn in scenario
        ]

    @staticmethod
    def _turn_response(
        session: _Session,
        outcome: ConversationResult,
        *,
        preview: ActionPreviewResult | None,
    ) -> TurnResponse:
        return TurnResponse(
            session_id=session.session_id,
            reply=outcome.reply,
            preview=preview,
            disposition=outcome.disposition,
            phase=outcome.phase,
            temperature=outcome.classification.temperature.value,
            safety_signals=list(outcome.safety_signals),
            repeated_turn=outcome.repeated_turn,
            events=list(session.events),
        )

    def _restore_turn(
        self,
        session: _Session,
        conversation_checkpoint: ConversationState,
        event_checkpoint: deque[SimulatorEvent],
        language_checkpoint: LanguageCode,
        next_sequence_checkpoint: int,
        approved_preview_checkpoint: int,
    ) -> None:
        self._conversation.restore(session.session_id, conversation_checkpoint)
        session.events = event_checkpoint
        session.language = language_checkpoint
        session.next_sequence = next_sequence_checkpoint
        session.approved_preview_count = approved_preview_checkpoint

    async def _discard_session(self, session: _Session) -> None:
        session.closing = True
        session_id = session.session_id
        if not self._begin_teardown(session_id, session):
            return
        try:
            await self._actions.cleanup_session(session_id)
        except (AdapterError, RuntimeError, ValueError):
            logger.exception("Failed to clean up invalidated simulator session")
        finally:
            try:
                self._conversation.close_session(session_id)
            finally:
                self._release_admission(session_id)

    def _get_session(self, session_id: UUID, *, allow_closing: bool = False) -> _Session:
        try:
            session = self._sessions[session_id]
        except KeyError as error:
            raise SessionNotFoundError(f"Unknown session: {session_id}") from error
        if session.closing and not allow_closing:
            raise SessionNotFoundError(f"Unknown session: {session_id}")
        return session

    def _ensure_session_active(self, session_id: UUID, session: _Session) -> None:
        if session.closing or self._sessions.get(session_id) is not session:
            raise SessionNotFoundError(f"Unknown session: {session_id}")

    def _ensure_conversation_open(self, session_id: UUID) -> None:
        if self._conversation.snapshot(session_id).stopped:
            raise RuntimeError("Conversation is closed")

    def _append_event(
        self,
        session: _Session,
        event_type: SimulatorEventType,
        *,
        text: str | None = None,
        language: LanguageCode = LanguageCode.UNKNOWN,
        metadata: dict[str, str | int | bool] | None = None,
    ) -> SimulatorEvent:
        event = SimulatorEvent(
            sequence=session.next_sequence,
            event_type=event_type,
            text=text,
            language=language,
            occurred_at=self._clock.now(),
            metadata=metadata or {},
        )
        session.next_sequence += 1
        session.events.append(event)
        return event

    @staticmethod
    def _session_response(session: _Session) -> SessionResponse:
        return SessionResponse(
            session_id=session.session_id,
            lead_ref=session.lead_ref,
            language=session.language,
            events=list(session.events),
        )
