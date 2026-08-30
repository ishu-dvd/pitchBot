from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from pitchbot.config import settings
from pitchbot.simulator.router import router as simulator_router


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


app = FastAPI(title="PitchBot API", version="0.1.0")
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
    }
