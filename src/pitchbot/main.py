from fastapi import FastAPI

from pitchbot.config import settings

app = FastAPI(title="PitchBot API", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "env": settings.app_env,
        "telephony_enabled": settings.enable_telephony,
        "whatsapp_enabled": settings.enable_whatsapp,
        "external_network_enabled": settings.enable_external_network,
    }
