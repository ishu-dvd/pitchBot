from __future__ import annotations

from pydantic import Field, model_validator
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
    enable_durable_history: bool = False
    durable_history_digest_key: str = ""

    allowlist_enabled: bool = True
    allowed_contacts: str = Field(default="")
    require_ai_disclosure: bool = True
    require_dnd_check: bool = True
    require_calling_hours: bool = True

    lead_recall_top_k: int = 3
    lead_recall_deadline_ms: int = 150
    lead_recall_failure_budget: int = 3

    timezone: str = "Asia/Kolkata"
    max_call_minutes: int = 12
    max_turns: int = 80

    @model_validator(mode="after")
    def validate_durable_history(self) -> Settings:
        if not self.enable_durable_history:
            return self
        if len(self.durable_history_digest_key) != 64:
            raise ValueError(
                "durable_history_digest_key must be 64 hexadecimal characters "
                "when durable history is enabled"
            )
        try:
            bytes.fromhex(self.durable_history_digest_key)
        except ValueError as exc:
            raise ValueError(
                "durable_history_digest_key must be 64 hexadecimal characters "
                "when durable history is enabled"
            ) from exc
        return self


settings = Settings()
