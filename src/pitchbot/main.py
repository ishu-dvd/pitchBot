from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from pitchbot.config import settings
from pitchbot.simulator.router import router as simulator_router
from pitchbot.simulator.router import speech_providers
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
app.add_middleware(SecurityHeadersMiddleware)
app.include_router(simulator_router)

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
