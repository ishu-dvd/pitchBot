from __future__ import annotations

import logging
from datetime import UTC, datetime
from time import perf_counter
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)

from pitchbot.adapters import AudioChunk
from pitchbot.config import settings
from pitchbot.conversation import ConversationEngine, ConversationJournal, ConversationJournalError
from pitchbot.conversation.providers import build_language_model
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
    SessionCapacityError,
    SessionNotFoundError,
    SimulatorService,
    TurnOperationCapacityError,
)
from pitchbot.simulator.speech_output import LockedSocket, ReplyAudioSender
from pitchbot.speech import BargeIn, SpeechTurnPipeline, UtteranceResult
from pitchbot.speech.providers import build_speech_providers
from pitchbot.storage import (
    SqlAlchemyEventRepository,
    create_database_engine,
    create_session_factory,
)

router = APIRouter(prefix="/api/simulator", tags=["simulator"])

logger = logging.getLogger(__name__)

PLAYBACK_FINISHED = "playback-finished"


def _build_service() -> SimulatorService:
    if not settings.enable_durable_history:
        return SimulatorService(
            speech_detector=speech_providers.detector,
            speech_transcriber=speech_providers.transcriber,
            speech_synthesizer=speech_providers.synthesizer,
            language_model=language_model,
        )
    engine = ConversationEngine(
        max_turns=settings.max_turns,
        turn_digest_key=bytes.fromhex(settings.durable_history_digest_key),
    )
    database_engine = create_database_engine(settings.database_url)
    repository = SqlAlchemyEventRepository(create_session_factory(database_engine))
    return SimulatorService(
        conversation_engine=engine,
        conversation_journal=ConversationJournal(repository),
        recall_top_k=settings.lead_recall_top_k,
        recall_deadline_ms=settings.lead_recall_deadline_ms,
        recall_failure_budget=settings.lead_recall_failure_budget,
        speech_detector=speech_providers.detector,
        speech_transcriber=speech_providers.transcriber,
        speech_synthesizer=speech_providers.synthesizer,
        language_model=language_model,
    )


# Built at import, before the service, so a misconfigured provider fails once with a clear
# error rather than at the first spoken utterance - and fails identically whether or not
# durable history is enabled. Weights are NOT loaded here; that is the lifespan's job.
speech_providers = build_speech_providers(settings)
language_model, language_model_id = build_language_model(settings)
logger.info(
    "Speech providers: detector=%s transcriber=%s synthesizer=%s",
    speech_providers.detector_id,
    speech_providers.transcriber_id,
    speech_providers.synthesizer_id,
)
logger.info("Language model: %s", language_model_id)

simulator_service = _build_service()


def is_allowed_websocket_origin(origin: str | None, host: str | None) -> bool:
    if origin is None or host is None:
        return False
    return origin in {f"http://{host}", f"https://{host}"}


@router.post("/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
def create_session(request: CreateSessionRequest) -> SessionResponse:
    try:
        return simulator_service.create_session(request)
    except SessionCapacityError as error:
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
    except SessionCapacityError as error:
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
        pipeline = simulator_service.create_speech_pipeline(session_id)
    except SessionNotFoundError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    media_type = websocket.query_params.get("media_type", "application/octet-stream")
    if len(media_type) > 100:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    # Every write goes through the lock, because the reply-audio task writes to this same
    # socket concurrently with the loop below and interleaved writes corrupt the stream.
    socket = LockedSocket(websocket.send_json, websocket.send_bytes)
    sender = ReplyAudioSender(socket, simulator_service.speech_synthesizer)
    sequence = 0
    try:
        await socket.send_json(
            {
                "type": "ready",
                "audio_retained": False,
                "speech_input_available": simulator_service.speech_input_available,
                "speech_output_available": sender.enabled,
                "end_silence_ms": pipeline.turn_taking.config.end_silence_ms,
            }
        )
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            text = message.get("text")
            if text is not None:
                # The only accepted control frame is a fixed literal, so no untrusted
                # payload is ever parsed on the audio socket.
                if text != PLAYBACK_FINISHED:
                    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                    return
                pipeline.agent_stopped_speaking()
                continue
            audio = message.get("bytes")
            if audio is None:
                continue
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

            frame = await pipeline.push(
                AudioChunk(
                    data=audio,
                    captured_at=datetime.now(UTC),
                    sequence=sequence,
                )
            )
            sequence += 1
            await socket.send_json(
                {
                    "type": "ack",
                    "acknowledged_sequence": event.sequence,
                    "byte_count": len(audio),
                    "audio_retained": False,
                    "state": pipeline.turn_taking.state.value,
                }
            )
            if frame.barge_in is not None:
                # Stop the voice before announcing the interruption: the sooner the task
                # is cancelled, the less audio the buyer has to talk over.
                await sender.abort()
                await socket.send_json(_barge_in_message(frame.barge_in))
            if frame.utterance is not None:
                if not await _handle_utterance(
                    websocket, socket, sender, session_id, pipeline, frame.utterance
                ):
                    return
    except WebSocketDisconnect:
        return
    finally:
        # A reply nobody can hear must not keep synthesising, and its task must not
        # outlive the connection it was speaking to.
        await sender.abort()


def _barge_in_message(barge_in: BargeIn) -> dict[str, object]:
    return {
        "type": "barge-in",
        "at_sequence": barge_in.at_sequence,
        "speech_ms": barge_in.speech_ms,
    }


def _utterance_message(result: UtteranceResult) -> dict[str, object]:
    """Report an utterance using counts and durations only, never the audio."""

    segment = result.segment
    return {
        "type": "utterance",
        "outcome": result.outcome.value,
        "reason": segment.reason.value,
        "frame_count": segment.frame_count,
        "speech_ms": segment.speech_ms,
        "silence_ms": segment.silence_ms,
        "dropped_frames": result.dropped_frames,
        "transcribe_ms": round(result.transcribe_ms, 1),
    }


async def _handle_utterance(
    websocket: WebSocket,
    socket: LockedSocket,
    sender: ReplyAudioSender,
    session_id: UUID,
    pipeline: SpeechTurnPipeline,
    result: UtteranceResult,
) -> bool:
    """Report one utterance. Returns ``False`` when the socket has been closed."""

    message = _utterance_message(result)
    if not result.is_turn or result.text is None:
        await socket.send_json(message)
        return True

    started = perf_counter()
    try:
        turn = await simulator_service.process_turn(
            session_id,
            TurnRequest(
                text=result.text,
                language=result.language or simulator_service.get_session(session_id).language,
                operation_id=uuid4(),
            ),
        )
    except SessionNotFoundError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return False
    except InjectedSimulatorError as error:
        # The utterance itself was understood, so its outcome stays honest; only the
        # engine call failed, exactly as it would have for a typed turn.
        message["error"] = str(error)
        await socket.send_json(message)
        return True
    except TurnOperationCapacityError:
        # Distinguishable from a transient engine fault: this session can accept no
        # further turns at all, spoken or typed, and reconnecting will not help.
        message["error"] = "turn-capacity-reached"
        await socket.send_json(message)
        return True
    except (ConversationJournalError, RuntimeError, ValueError) as error:
        # A spoken turn must fail as visibly and as harmlessly as a typed one.
        message["error"] = type(error).__name__
        await socket.send_json(message)
        return True
    engine_ms = (perf_counter() - started) * 1000

    language = result.language or simulator_service.get_session(session_id).language
    message["transcript"] = result.text
    message["reply"] = turn.reply
    message["disposition"] = turn.disposition.value
    message["safety_signals"] = [signal.value for signal in turn.safety_signals]
    message["engine_ms"] = round(engine_ms, 1)
    message["turn_latency_ms"] = round(
        pipeline.turn_taking.config.end_silence_ms + result.transcribe_ms + engine_ms,
        1,
    )
    # Whether the browser should speak this reply itself. Announced with the reply rather
    # than inferred from whether audio arrives, so the client never has to guess between
    # "server audio is coming" and "server audio is late".
    message["reply_audio"] = sender.enabled
    # The agent now holds the floor, so further buyer speech is an interruption. The
    # browser releases it again with a playback-finished control frame, and the machine
    # reclaims it after `agent_floor_ms` if that frame never arrives. Taken before the
    # reply is announced, so speech arriving during synthesis is an interruption too.
    pipeline.agent_started_speaking()
    await socket.send_json(message)
    # Scheduled, not awaited: synthesis of a long reply was measured at 1,052 ms, and the
    # caller is the only thing classifying buyer audio.
    await sender.start(turn.reply, language)
    return True
