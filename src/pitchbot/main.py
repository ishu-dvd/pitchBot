from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from pitchbot.config import settings
from pitchbot.observability import configure_logging, registry
from pitchbot.simulator.router import credentials, require_credential, speech_providers
from pitchbot.simulator.router import router as simulator_router
from pitchbot.speech.providers import preload_speech_providers


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; connect-src 'self'; "
            "script-src 'self'; style-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Load speech model weights before serving, never during a call.

    Measured: a lazily-loaded transcriber made the first spoken turn report 5,384 ms for
    3.4 s of speech, roughly three seconds of which was model construction. That work
    holds the GIL, so it does not merely delay one caller - it stalls the event loop and
    the audio socket that barge-in depends on. A Piper voice has the same shape: 2,561 ms
    to load, against roughly 110 ms to synthesise a sentence once resident. This is a
    no-op in the default configuration, where neither is enabled.
    """

    await preload_speech_providers(speech_providers)
    yield


app = FastAPI(title="PitchBot API", version="0.1.0", lifespan=lifespan)
configure_logging(level=settings.log_level, json_format=settings.log_json)
app.add_middleware(SecurityHeadersMiddleware)
app.include_router(simulator_router)


@app.get(
    "/metrics",
    include_in_schema=False,
    response_class=PlainTextResponse,
    # Authenticated on exactly the same terms as everything else. An open /metrics would
    # undo the boundary: it reports which languages are being spoken, how many turns ran and
    # how the service is performing, which is precisely the reconnaissance an unauthenticated
    # caller wants. It also sits outside the simulator router, so it does not inherit that
    # router's dependency and has to say so itself.
    dependencies=[Depends(require_credential)],
)
def metrics() -> str:
    return registry.render()


web_directory = Path(__file__).resolve().parents[2] / "apps" / "web"
app.mount("/simulator", StaticFiles(directory=web_directory, html=True), name="simulator-web")


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/simulator/")


@app.get("/health")
def health() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "env": settings.app_env,
        # Whether the API is actually closed. A server reporting "ok" while every endpoint
        # is open is the exact condition this field exists to make visible.
        "authentication_enforced": credentials.enforcing,
        "telephony_enabled": settings.enable_telephony,
        "whatsapp_enabled": settings.enable_whatsapp,
        "external_network_enabled": settings.enable_external_network,
        # Which speech providers are actually running, so "why is nothing being
        # transcribed" is answerable without reading logs or configuration.
        "speech_detector": speech_providers.detector_id,
        "speech_transcriber": speech_providers.transcriber_id,
        "speech_synthesizer": speech_providers.synthesizer_id,
        "speech_transcription_enabled": speech_providers.can_transcribe,
        "speech_synthesis_enabled": speech_providers.can_synthesize,
    }
