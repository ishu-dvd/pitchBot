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
    assert settings.enable_durable_history is False


def test_removed_safety_relaxation_knobs_are_gone() -> None:
    # PR 30 removed require_ai_disclosure/require_dnd_check/require_calling_hours/
    # allowlist_enabled because the ActionPolicy enforces those gates
    # unconditionally; a config switch could only ever disable a mandatory safety
    # gate. This assertion locks that out so the dead settings cannot silently
    # return as config a future reader might wire to a disable path.
    for removed in (
        "require_ai_disclosure",
        "require_dnd_check",
        "require_calling_hours",
        "allowlist_enabled",
    ):
        assert removed not in Settings.model_fields, f"{removed} must stay removed"
        assert not hasattr(Settings.model_validate({}), removed)


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
