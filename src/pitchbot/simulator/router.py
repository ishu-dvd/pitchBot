from __future__ import annotations

import logging
from datetime import UTC, datetime
from time import perf_counter
from typing import NoReturn
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
    WebSocket,
    WebSocketDisconnect,
    WebSocketException,
    status,
)
from starlette.requests import HTTPConnection

from pitchbot.adapters import AudioChunk
from pitchbot.config import settings
from pitchbot.conversation import ConversationEngine, ConversationJournal, ConversationJournalError
from pitchbot.conversation.providers import build_language_model
from pitchbot.domain import LanguageCode
from pitchbot.observability import (
    TurnStage,
    correlated,
    new_turn_id,
    record_stage,
    record_turn,
    record_utterance,
)
from pitchbot.security import ApiCredential, CredentialStore, RateLimiter, parse_api_keys
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
from pitchbot.simulator.speech_output import LockedSocket, ReplyAudioSender, ThinkingFiller
from pitchbot.speech import BargeIn, SpeechTurnPipeline, UtteranceResult
from pitchbot.speech.providers import build_speech_providers, build_turn_taking
from pitchbot.speech.recovery import recovery_phrase
from pitchbot.storage import (
    SqlAlchemyEventRepository,
    create_database_engine,
    create_session_factory,
)

logger = logging.getLogger(__name__)

PLAYBACK_FINISHED = "playback-finished"

API_KEY_HEADER = "x-api-key"
WEBSOCKET_KEY_PREFIX = "pitchbot.key."
WEBSOCKET_SUBPROTOCOL = "pitchbot.v1"
"""How a browser authenticates a WebSocket, which cannot carry a custom header.

`new WebSocket(url, ["pitchbot.v1", "pitchbot.key.<secret>"])`. The browser sends both in
`Sec-WebSocket-Protocol`; the server reads the key from there and accepts `pitchbot.v1`,
so the secret is never echoed back. A query parameter was rejected for this: it would be
written to every access log and proxy trace the connection passes through.
"""

credentials = CredentialStore(parse_api_keys(settings.api_keys))
rate_limiter = RateLimiter(
    capacity=settings.api_rate_limit_burst,
    refill_per_second=settings.api_rate_limit_per_second,
)


def _offered_subprotocols(connection: HTTPConnection) -> tuple[str, ...]:
    offered = connection.headers.get("sec-websocket-protocol")
    if not offered:
        return ()
    return tuple(item.strip() for item in offered.split(",") if item.strip())


def _accepted_subprotocol(connection: HTTPConnection) -> str | None:
    """Echo back the plain protocol name, never the one carrying the secret."""

    return (
        WEBSOCKET_SUBPROTOCOL
        if WEBSOCKET_SUBPROTOCOL in _offered_subprotocols(connection)
        else None
    )


def _presented_key(connection: HTTPConnection) -> str | None:
    header = connection.headers.get(API_KEY_HEADER)
    if header is not None:
        return header
    for entry in _offered_subprotocols(connection):
        if entry.startswith(WEBSOCKET_KEY_PREFIX):
            return entry[len(WEBSOCKET_KEY_PREFIX) :]
    return None


def _reject(
    connection: HTTPConnection,
    *,
    status_code: int,
    detail: str,
    retry_after: str | None,
) -> NoReturn:
    if connection.scope["type"] == "websocket":
        # 1008 is "policy violation". An HTTPException here would surface as a server
        # error rather than a clean close, and the client would learn nothing.
        raise WebSocketException(code=1008, reason=detail)
    headers = {"Retry-After": retry_after} if retry_after is not None else None
    raise HTTPException(status_code=status_code, detail=detail, headers=headers)


async def require_credential(connection: HTTPConnection) -> ApiCredential | None:
    """Authenticate and rate-limit one request, for HTTP and WebSocket alike.

    Typed against ``HTTPConnection`` - the base of both ``Request`` and ``WebSocket`` - so
    a single dependency covers every route on this router. Registering it per-route would
    have left the next endpoint someone adds unauthenticated by default, which is how the
    API came to have ten open endpoints in the first place.
    """

    if not credentials.enforcing:
        # Only reachable with app_env='local'; Settings refuses to build otherwise.
        return None
    credential = credentials.identify(_presented_key(connection))
    if credential is None:
        _reject(
            connection,
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid X-API-Key is required",
            retry_after=None,
        )
    decision = rate_limiter.check(credential.name)
    if not decision.allowed:
        logger.warning("Rate limit exceeded for credential %s", credential.name)
        _reject(
            connection,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            retry_after=decision.retry_after_header,
        )
    return credential


router = APIRouter(
    prefix="/api/simulator",
    tags=["simulator"],
    # Registered on the router, not per route, so an endpoint added later is authenticated
    # by default. Per-route registration is how this API came to have ten open endpoints.
    dependencies=[Depends(require_credential)],
)


def _build_service() -> SimulatorService:
    if not settings.enable_durable_history:
        return SimulatorService(
            speech_detector=speech_providers.detector,
            speech_transcriber=speech_providers.transcriber,
            speech_synthesizer=speech_providers.synthesizer,
            language_model=language_model,
            speech_early_detection_seconds=settings.speech_stt_early_detection_seconds,
            speech_transcribe_timeout_ms=settings.speech_stt_timeout_ms,
            turn_taking=turn_taking,
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
        speech_early_detection_seconds=settings.speech_stt_early_detection_seconds,
        speech_transcribe_timeout_ms=settings.speech_stt_timeout_ms,
        turn_taking=turn_taking,
    )


# Built at import, before the service, so a misconfigured provider fails once with a clear
# error rather than at the first spoken utterance - and fails identically whether or not
# durable history is enabled. Weights are NOT loaded here; that is the lifespan's job.
speech_providers = build_speech_providers(settings)
turn_taking = build_turn_taking(settings)
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
    with correlated(session_id=session_id, turn_id=new_turn_id()):
        started = perf_counter()
        try:
            response = await simulator_service.process_turn(session_id, request)
        except SessionNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except InjectedSimulatorError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(error),
            ) from error
        except RuntimeError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        record_stage(
            TurnStage.PLAN,
            (perf_counter() - started) * 1000,
            language=request.language.value,
        )
        record_turn(language=request.language.value, disposition=response.disposition.value)
        return response


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
    if not settings.enable_real_time_audio:
        # Read through the module rather than captured at import, so the flag reflects the
        # running configuration and not a startup snapshot.
        #
        # This endpoint accepts a live microphone stream. Until now the flag that claims to
        # gate it was inert - `README.md` and `.env.example` both promised "real-time audio
        # disabled by default" while the socket was mounted and reachable regardless. An
        # operator reading either would have believed audio ingest was off. Refused first,
        # before the session is even looked up, so a disabled deployment tells a caller
        # nothing about which session ids exist.
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    if not is_allowed_websocket_origin(
        websocket.headers.get("origin"),
        websocket.headers.get("host"),
    ):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    try:
        simulator_service.get_session(session_id)
        # Read lazily, not captured: the buyer may switch language mid-conversation, and a
        # filler is spoken *before* this turn's transcript exists, so the best available
        # answer to "what language is this call in" is the one the last turn settled on.
        filler = (
            ThinkingFiller(
                language_of=lambda: simulator_service.get_session(session_id).language,
            )
            if settings.speech_backchannel_enabled
            else None
        )
        pipeline = simulator_service.create_speech_pipeline(
            session_id,
            on_thinking=None if filler is None else filler.start,
        )
    except SessionNotFoundError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept(subprotocol=_accepted_subprotocol(websocket))
    media_type = websocket.query_params.get("media_type", "application/octet-stream")
    if len(media_type) > 100:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    # Every write goes through the lock, because the reply-audio task writes to this same
    # socket concurrently with the loop below and interleaved writes corrupt the stream.
    socket = LockedSocket(websocket.send_json, websocket.send_bytes)
    sender = ReplyAudioSender(
        socket,
        simulator_service.speech_synthesizer,
        on_first_frame=lambda milliseconds, language: record_stage(
            TurnStage.SYNTHESIZE, milliseconds, language=language.value
        ),
    )
    if filler is not None:
        filler.attach(sender)
    sequence = 0
    try:
        await socket.send_json(
            {
                "type": "ready",
                "audio_retained": False,
                "speech_input_available": simulator_service.speech_input_available,
                "speech_output_available": sender.enabled,
                "end_silence_ms": pipeline.turn_taking.config.end_silence_ms,
                "backchannel_available": filler is not None and filler.enabled,
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
                await simulator_service.record_audio_metadata(
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

            acknowledged = sequence
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
                    # The frame's own sequence. It used to be the sequence of the timeline
                    # event each frame appended, which stopped being one-per-frame when a
                    # microphone at 33 frames a second began evicting the conversation from
                    # its own timeline.
                    "acknowledged_sequence": acknowledged,
                    "byte_count": len(audio),
                    "audio_retained": False,
                    "state": pipeline.turn_taking.state.value,
                }
            )
            if frame.barge_in is not None:
                # Stop the voice before announcing the interruption: the sooner the task
                # is cancelled, the less audio the buyer has to talk over.
                if filler is not None:
                    await filler.abort()
                await sender.abort()
                await socket.send_json(_barge_in_message(frame.barge_in))
            if frame.utterance is not None:
                if not await _handle_utterance(
                    websocket, socket, sender, session_id, pipeline, frame.utterance, filler
                ):
                    return
    except WebSocketDisconnect:
        return
    finally:
        # A reply nobody can hear must not keep synthesising, and its task must not
        # outlive the connection it was speaking to.
        if filler is not None:
            await filler.abort()
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
    filler: ThinkingFiller | None = None,
) -> bool:
    """Report one utterance and stop filling the silence it opened.

    The ``finally`` is what guarantees the second half. Most of the paths below never
    reach a reply - noise, a failed engine call, a closed socket - and a filler left
    running past its own turn would speak into the next one.
    """

    try:
        return await _reply_to_utterance(
            websocket, socket, sender, session_id, pipeline, result, filler
        )
    finally:
        if filler is not None:
            await filler.settle()


async def _reply_to_utterance(
    websocket: WebSocket,
    socket: LockedSocket,
    sender: ReplyAudioSender,
    session_id: UUID,
    pipeline: SpeechTurnPipeline,
    result: UtteranceResult,
    filler: ThinkingFiller | None,
) -> bool:
    """Report one utterance. Returns ``False`` when the socket has been closed."""

    message = _utterance_message(result)
    language_label = (result.language or LanguageCode.UNKNOWN).value
    record_utterance(language=language_label, outcome=result.outcome.value)
    record_stage(TurnStage.TRANSCRIBE, result.transcribe_ms, language=language_label)
    if result.detect_language_ms is not None:
        # Only recorded when a hint actually landed. A detection that was abandoned
        # unfinished contributed nothing and would otherwise look like time well spent.
        record_stage(TurnStage.DETECT_LANGUAGE, result.detect_language_ms, language=language_label)
    if not result.is_turn or result.text is None:
        # A dropped turn used to be reported as JSON and nothing else, so a buyer who spoke
        # and got silence could not tell the agent apart from a dropped call. Only genuine
        # system failures are answered - see `speech/recovery.py` for why a cough is not.
        language = simulator_service.get_session(session_id).language
        phrase = recovery_phrase(result.outcome, language)
        if phrase is None or not sender.enabled:
            await socket.send_json(message)
            return True
        message["reply"] = phrase
        message["reply_audio"] = sender.enabled
        message["recovery"] = True
        # The floor is taken exactly as it is for a real reply: the agent is about to
        # speak, so buyer speech over it is an interruption and must stay one.
        pipeline.agent_started_speaking()
        await socket.send_json(message)
        if filler is not None:
            await filler.settle()
        await sender.start(phrase, language)
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
    record_stage(
        TurnStage.TOTAL,
        pipeline.turn_taking.config.end_silence_ms + result.transcribe_ms + engine_ms,
        language=language,
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
    # Let the filler finish its word first. `start` would otherwise abort it mid-syllable
    # and tell the client to discard what it had buffered, which sounds like a fault where
    # a completed "hmm" sounds like a person. Bounded, because this is the receive loop.
    if filler is not None:
        await filler.settle()
    # Scheduled, not awaited: synthesis of a long reply was measured at 1,052 ms, and the
    # caller is the only thing classifying buyer audio.
    await sender.start(turn.reply, language)
    return True
