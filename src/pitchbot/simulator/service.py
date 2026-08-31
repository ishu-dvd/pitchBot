from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from pitchbot.adapters.clock import Clock, SystemClock
from pitchbot.conversation import ConversationDisposition, ConversationEngine
from pitchbot.domain import LanguageCode
from pitchbot.simulator.models import (
    AudioMetadata,
    CreateSessionRequest,
    LeadHistoryResponse,
    PreviewAction,
    SessionResponse,
    SimulatorEvent,
    SimulatorEventType,
    TurnRequest,
    TurnResponse,
)
from pitchbot.simulator.scenarios import SCENARIOS

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


@dataclass(slots=True)
class _Session:
    session_id: UUID
    lead_ref: str
    language: LanguageCode
    events: deque[SimulatorEvent]
    next_sequence: int = 1
    audio_chunks_received: int = 0
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
        conversation_engine: ConversationEngine | None = None,
    ) -> None:
        if (
            min(
                max_sessions,
                max_events_per_session,
                max_history_events_per_lead,
                max_audio_chunks_per_session,
            )
            < 1
        ):
            raise ValueError("Simulator capacities must be positive")
        self._clock = clock or SystemClock()
        self._max_sessions = max_sessions
        self._max_events_per_session = max_events_per_session
        self._max_audio_chunks_per_session = max_audio_chunks_per_session
        self._conversation = conversation_engine or ConversationEngine()
        self._sessions: dict[UUID, _Session] = {}

    def create_session(self, request: CreateSessionRequest) -> SessionResponse:
        if len(self._sessions) >= self._max_sessions:
            raise RuntimeError("Simulator session capacity reached")
        session = _Session(
            session_id=uuid4(),
            lead_ref=request.lead_ref,
            language=request.language,
            events=deque(maxlen=self._max_events_per_session),
        )
        self._conversation.create_session(session.session_id)
        self._sessions[session.session_id] = session
        self._append_event(
            session,
            SimulatorEventType.DISCLOSURE,
            text=DISCLOSURES[request.language],
            language=request.language,
        )
        return self._session_response(session)

    async def process_turn(self, session_id: UUID, request: TurnRequest) -> TurnResponse:
        session = self._get_session(session_id)
        async with session.lock:
            self._ensure_conversation_open(session_id)
            if request.inject_failure:
                self._append_event(
                    session,
                    SimulatorEventType.FAILURE,
                    text="Deterministic simulator failure injected.",
                )
                raise InjectedSimulatorError("Deterministic simulator failure injected")
            if request.simulated_latency_ms:
                await asyncio.sleep(request.simulated_latency_ms / 1_000)
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
            preview = self._preview(request.preview_action)
            if outcome.disposition is not ConversationDisposition.CONTINUE:
                preview = None
            elif preview is not None:
                self._append_event(
                    session,
                    SimulatorEventType.ACTION_PREVIEW,
                    text=preview["label"],
                    language=request.language,
                    metadata={"action": request.preview_action.value, "executed": False},
                )
            return TurnResponse(
                session_id=session.session_id,
                reply=reply,
                preview=preview,
                disposition=outcome.disposition,
                phase=outcome.phase,
                temperature=outcome.classification.temperature.value,
                safety_signals=list(outcome.safety_signals),
                repeated_turn=outcome.repeated_turn,
                events=list(session.events),
            )

    async def interrupt(self, session_id: UUID) -> SessionResponse:
        session = self._get_session(session_id)
        async with session.lock:
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

    async def close_session(self, session_id: UUID) -> None:
        session = self._get_session(session_id)
        async with session.lock:
            self._sessions.pop(session_id, None)
            self._conversation.close_session(session_id)

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

    def _get_session(self, session_id: UUID) -> _Session:
        try:
            return self._sessions[session_id]
        except KeyError as error:
            raise SessionNotFoundError(f"Unknown session: {session_id}") from error

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
    def _preview(action: PreviewAction) -> dict[str, str] | None:
        labels = {
            PreviewAction.WHATSAPP: "Mock WhatsApp preview prepared; nothing was sent.",
            PreviewAction.CALLBACK: "Mock callback preview prepared; nothing was scheduled.",
            PreviewAction.ARTIFACT: "Mock artifact preview prepared; nothing was generated.",
        }
        label = labels.get(action)
        return None if label is None else {"action": action.value, "label": label}

    @staticmethod
    def _session_response(session: _Session) -> SessionResponse:
        return SessionResponse(
            session_id=session.session_id,
            lead_ref=session.lead_ref,
            language=session.language,
            events=list(session.events),
        )
