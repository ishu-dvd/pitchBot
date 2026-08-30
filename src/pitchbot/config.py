from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PITCHBOT_", extra="ignore")

    app_env: str = "local"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./data/pitchbot.db"

    enable_telephony: bool = False
    enable_whatsapp: bool = False
    enable_external_network: bool = False
    enable_real_time_audio: bool = False
    enable_hosted_demo: bool = False

    allowlist_enabled: bool = True
    allowed_contacts: str = Field(default="")
    require_ai_disclosure: bool = True
    require_dnd_check: bool = True
    require_calling_hours: bool = True

    timezone: str = "Asia/Kolkata"
    max_call_minutes: int = 12
    max_turns: int = 80


settings = Settings()
