import pytest
from pydantic import ValidationError

from pitchbot.config import Settings


def test_external_side_effects_default_to_disabled() -> None:
    settings = Settings.model_validate({})

    assert settings.enable_telephony is False
    assert settings.enable_whatsapp is False
    assert settings.enable_external_network is False
    assert settings.enable_real_time_audio is False
    assert settings.enable_hosted_demo is False
    assert settings.allowlist_enabled is True
    assert settings.require_ai_disclosure is True
    assert settings.require_dnd_check is True
    assert settings.require_calling_hours is True
    assert settings.enable_durable_history is False


def test_durable_history_requires_a_managed_32_byte_hex_key() -> None:
    with pytest.raises(ValidationError, match="64 hexadecimal characters"):
        Settings.model_validate({"enable_durable_history": True})
    with pytest.raises(ValidationError, match="64 hexadecimal characters"):
        Settings.model_validate(
            {
                "enable_durable_history": True,
                "durable_history_digest_key": "z" * 64,
            }
        )

    settings = Settings.model_validate(
        {
            "enable_durable_history": True,
            "durable_history_digest_key": "ab" * 32,
        }
    )
    assert settings.enable_durable_history is True
