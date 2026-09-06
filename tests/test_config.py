import re
from pathlib import Path

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


def test_every_setting_is_documented_in_env_example() -> None:
    """A setting nobody can discover is barely more use than one that does not exist.

    `.env.example` is where an operator finds out what they are allowed to change, so it
    drifting from `Settings` is a silent loss of a feature - which is what happened to the
    turn-taking thresholds: they were reachable in code and absent from the file, and
    nothing noticed. Asserted in both directions, because a setting listed here and removed
    from `Settings` is a line in someone's `.env` that quietly does nothing.
    """

    text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    documented = {match.group(1).lower() for match in re.finditer(r"^PITCHBOT_(\w+)=", text, re.M)}

    assert documented - set(Settings.model_fields) == set(), "documented but not a setting"
    assert set(Settings.model_fields) - documented == set(), "a setting nobody can find"
