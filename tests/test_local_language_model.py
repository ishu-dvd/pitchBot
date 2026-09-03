"""Tests for the local language model path: reachable, licence-gated, and never required.

Three properties matter here and none of them is about model quality:

*It is off by default and the conversation still works.* The planner reads the rules'
facts, so a deployment with no model is fully functional.

*A configured model that cannot be built stops startup.* Same rule as the speech providers,
plus one more that is specific to weights: a licence that does not permit commercial use is
refused at construction, not at the first buyer turn.

*A model failure loses the improvement, never the turn.* Understanding is best effort.
"""

from __future__ import annotations

import pytest

from pitchbot.adapters.contracts import StructuredCompletion
from pitchbot.adapters.errors import PermanentAdapterError, TransientAdapterError
from pitchbot.adapters.mocks import MockModelAdapter
from pitchbot.adapters.onnx_genai_model import (
    KNOWN_MODEL_LICENSES,
    SCHEMAS,
    OnnxGenAiModelAdapter,
    model_license,
)
from pitchbot.config import Settings
from pitchbot.conversation.model_understanding import (
    SCHEMA_NAME,
    ModelTurnUnderstanding,
)
from pitchbot.conversation.planning import Intent, Slot
from pitchbot.conversation.providers import NO_MODEL_ID, LlmProvider, build_language_model
from pitchbot.domain import LanguageCode

_PROVIDERS = "pitchbot.conversation.providers"


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


def _model_settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "llm_provider": "onnx-genai",
        "llm_model_dir": "/models/example",
        "llm_model_id": "Qwen/Qwen2.5-0.5B-Instruct",
    }
    return _settings(**{**defaults, **overrides})


# --------------------------------------------------------------------------------------
# Deny by default
# --------------------------------------------------------------------------------------


def test_no_model_is_configured_by_default() -> None:
    model, identifier = build_language_model(_settings())

    assert model is None
    assert identifier == NO_MODEL_ID


def test_provider_enum_matches_the_accepted_configuration_values() -> None:
    assert {item.value for item in LlmProvider} == {"none", "onnx-genai"}


@pytest.mark.parametrize("value", ["llama-cpp", "openai", ""])
def test_an_unknown_provider_name_is_rejected_at_import(value: str) -> None:
    with pytest.raises(ValueError) as error:
        _settings(llm_provider=value)

    assert "llm_provider" in str(error.value)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [({"llm_model_dir": ""}, "llm_model_dir"), ({"llm_model_id": ""}, "llm_model_id")],
)
def test_an_enabled_provider_with_nothing_to_load_is_rejected(
    overrides: dict[str, object],
    expected: str,
) -> None:
    with pytest.raises(ValueError) as error:
        _model_settings(**overrides)

    assert expected in str(error.value)


def test_a_configured_model_without_the_extra_refuses_to_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(f"{_PROVIDERS}.ONNX_GENAI_AVAILABLE", False)

    with pytest.raises(PermanentAdapterError) as error:
        build_language_model(_model_settings())

    assert "pitchbot[local-llm]" in str(error.value)


# --------------------------------------------------------------------------------------
# The licence gate, which is about weights rather than the runtime
# --------------------------------------------------------------------------------------


def test_an_unreviewed_model_is_refused_rather_than_assumed() -> None:
    with pytest.raises(PermanentAdapterError) as error:
        model_license("some-org/some-model")

    assert "reviewed licence" in str(error.value)


def test_the_qwen_family_is_licence_split_and_the_gate_knows_it() -> None:
    """0.5B and 1.5B are Apache-2.0; 3B is non-commercial. A family name is not a licence."""

    assert KNOWN_MODEL_LICENSES["Qwen/Qwen2.5-0.5B-Instruct"].permits_commercial_use is True
    assert KNOWN_MODEL_LICENSES["Qwen/Qwen2.5-1.5B-Instruct"].permits_commercial_use is True
    assert KNOWN_MODEL_LICENSES["Qwen/Qwen2.5-3B-Instruct"].permits_commercial_use is False


def test_a_non_commercial_model_is_refused_at_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not at the first buyer turn: PitchBot is a sales assistant."""

    with pytest.raises(PermanentAdapterError) as error:
        OnnxGenAiModelAdapter("/models/example", "Qwen/Qwen2.5-3B-Instruct")

    assert "does not permit commercial use" in str(error.value)


def test_a_non_commercial_model_is_allowed_only_when_explicitly_enabled() -> None:
    adapter = OnnxGenAiModelAdapter(
        "/models/example",
        "Qwen/Qwen2.5-3B-Instruct",
        allow_non_commercial=True,
    )

    assert adapter.license.permits_commercial_use is False
    assert adapter.provenance().model_permits_commercial_use is False


def test_construction_does_not_load_weights() -> None:
    """The directory need not even exist; loading is what ``preload`` is for."""

    adapter = OnnxGenAiModelAdapter("/models/does-not-exist", "microsoft/Phi-3.5-mini-instruct")

    assert adapter.is_loaded is False
    assert adapter.model_id == "microsoft/Phi-3.5-mini-instruct"


@pytest.mark.asyncio
async def test_an_unknown_schema_is_refused() -> None:
    adapter = OnnxGenAiModelAdapter("/models/example", "microsoft/Phi-3.5-mini-instruct")

    with pytest.raises(PermanentAdapterError) as error:
        await adapter.complete_structured("hello", "not-a-schema")

    assert "answers only" in str(error.value)


def test_the_understanding_schema_is_registered() -> None:
    assert SCHEMA_NAME in SCHEMAS


# --------------------------------------------------------------------------------------
# Understanding is additive and best effort
# --------------------------------------------------------------------------------------


def _completion(**value: str) -> StructuredCompletion:
    return StructuredCompletion(value=value, model_version="test")


@pytest.mark.asyncio
async def test_a_model_reading_fills_a_slot_the_rules_missed() -> None:
    """The shipped budget regex needs digits, so "two lakh rupees" extracts nothing."""

    source = ModelTurnUnderstanding(
        MockModelAdapter([_completion(acknowledge="budget_stated", buyer_intent="exploring")])
    )

    understanding = await source.understand(
        "Our budget is around two lakh rupees.",
        LanguageCode.ENGLISH,
        ["business_type"],
    )

    assert understanding is not None
    assert Slot.BUDGET in understanding.known_slots
    assert understanding.filled_now == frozenset({Slot.BUDGET})
    assert Slot.BUSINESS_TYPE in understanding.known_slots
    assert understanding.intent is Intent.EXPLORING


@pytest.mark.asyncio
async def test_a_model_failure_falls_back_rather_than_failing_the_turn() -> None:
    source = ModelTurnUnderstanding(MockModelAdapter([TransientAdapterError("busy")]))

    assert await source.understand("anything", LanguageCode.ENGLISH, []) is None


@pytest.mark.asyncio
async def test_a_value_outside_the_slot_vocabulary_is_dropped() -> None:
    """Constrained decoding makes this unreachable; the mapping refuses it anyway."""

    source = ModelTurnUnderstanding(
        MockModelAdapter([_completion(acknowledge="pizza", buyer_intent="dancing")])
    )

    understanding = await source.understand("hello", LanguageCode.ENGLISH, [])

    assert understanding is not None
    assert understanding.filled_now == frozenset()
    assert understanding.intent is None
