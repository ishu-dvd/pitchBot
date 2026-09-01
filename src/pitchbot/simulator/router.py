from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)

from pitchbot.config import settings
from pitchbot.conversation import ConversationEngine, ConversationJournal, ConversationJournalError
from pitchbot.simulator.models import (
    AudioMetadata,
    CreateSessionRequest,
    DurableHistoryResponse,
    LeadHistoryResponse,
    ResumeSessionRequest,
    SessionResponse,
    TurnRequest,
    TurnResponse,
)
from pitchbot.simulator.service import (
    DurableHistoryDisabledError,
    InjectedSimulatorError,
    SessionAdmissionConflictError,
    SessionNotFoundError,
    SimulatorService,
)
from pitchbot.storage import (
    SqlAlchemyEventRepository,
    create_database_engine,
    create_session_factory,
)

router = APIRouter(prefix="/api/simulator", tags=["simulator"])


def _build_service() -> SimulatorService:
    if not settings.enable_durable_history:
        return SimulatorService()
    engine = ConversationEngine(
        max_turns=settings.max_turns,
        turn_digest_key=bytes.fromhex(settings.durable_history_digest_key),
    )
    database_engine = create_database_engine(settings.database_url)
    repository = SqlAlchemyEventRepository(create_session_factory(database_engine))
    return SimulatorService(
        conversation_engine=engine,
        conversation_journal=ConversationJournal(repository),
    )


simulator_service = _build_service()


def is_allowed_websocket_origin(origin: str | None, host: str | None) -> bool:
    if origin is None or host is None:
        return False
    return origin in {f"http://{host}", f"https://{host}"}


@router.post("/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
def create_session(request: CreateSessionRequest) -> SessionResponse:
    try:
        return simulator_service.create_session(request)
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(error)
        ) from error


@router.get("/sessions/{session_id}", response_model=SessionResponse)
def get_session(session_id: UUID) -> SessionResponse:
    try:
        return simulator_service.get_session(session_id)
    except SessionNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@router.post("/sessions/{session_id}/resume", response_model=SessionResponse)
def resume_session(session_id: UUID, request: ResumeSessionRequest) -> SessionResponse:
    try:
        return simulator_service.resume_session(session_id, request)
    except LookupError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Durable session not found",
        ) from error
    except DurableHistoryDisabledError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ConversationJournalError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except SessionAdmissionConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(error)
        ) from error


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def close_session(session_id: UUID) -> Response:
    try:
        await simulator_service.close_session(session_id)
    except SessionNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sessions/{session_id}/turns", response_model=TurnResponse)
async def process_turn(session_id: UUID, request: TurnRequest) -> TurnResponse:
    if "operation_id" not in request.model_fields_set:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="operation_id is required for retry-safe turn requests",
        )
    try:
        return await simulator_service.process_turn(session_id, request)
    except SessionNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except InjectedSimulatorError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    except RuntimeError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.post("/sessions/{session_id}/interrupt", response_model=SessionResponse)
async def interrupt(session_id: UUID) -> SessionResponse:
    try:
        return await simulator_service.interrupt(session_id)
    except SessionNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.get("/sessions/{session_id}/history", response_model=LeadHistoryResponse)
def get_lead_history(session_id: UUID) -> LeadHistoryResponse:
    try:
        return simulator_service.get_lead_history(session_id)
    except SessionNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@router.get(
    "/sessions/{session_id}/durable-history",
    response_model=DurableHistoryResponse,
)
def get_durable_history(
    session_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
) -> DurableHistoryResponse:
    try:
        return simulator_service.get_durable_history(session_id, limit=limit)
    except SessionNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except DurableHistoryDisabledError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ConversationJournalError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.get("/replay/{scenario_id}")
def replay(scenario_id: str) -> dict[str, object]:
    try:
        return {"scenario_id": scenario_id, "turns": simulator_service.replay(scenario_id)}
    except SessionNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@router.websocket("/sessions/{session_id}/audio")
async def audio_socket(websocket: WebSocket, session_id: UUID) -> None:
    if not is_allowed_websocket_origin(
        websocket.headers.get("origin"),
        websocket.headers.get("host"),
    ):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    try:
        simulator_service.get_session(session_id)
    except SessionNotFoundError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    media_type = websocket.query_params.get("media_type", "application/octet-stream")
    if len(media_type) > 100:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    try:
        while True:
            audio = await websocket.receive_bytes()
            if len(audio) > 262_144:
                await websocket.close(code=status.WS_1009_MESSAGE_TOO_BIG)
                return
            try:
                event = await simulator_service.record_audio_metadata(
                    session_id,
                    AudioMetadata(
                        byte_count=len(audio),
                        media_type=media_type,
                        captured_at=datetime.now(UTC),
                    ),
                )
            except SessionNotFoundError:
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return
            except RuntimeError:
                await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER)
                return
            await websocket.send_json(
                {
                    "acknowledged_sequence": event.sequence,
                    "byte_count": len(audio),
                    "audio_retained": False,
                }
            )
    except WebSocketDisconnect:
        return
