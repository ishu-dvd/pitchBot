"""Build the optional local language model from configuration, deny-by-default.

Same two rules as :mod:`pitchbot.speech.providers`, for the same reasons: nothing is
enabled by default, and a provider that is configured but cannot be built stops startup
rather than degrading into silence. The third rule is specific to this one - the licence of
the *weights* is checked here, at build time, so a non-commercial model refuses to start
instead of refusing the first buyer turn.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from pitchbot.adapters.contracts import ModelAdapter
from pitchbot.adapters.errors import PermanentAdapterError
from pitchbot.adapters.onnx_genai_model import (
    INSTALL_HINT,
    ONNX_GENAI_AVAILABLE,
    OnnxGenAiModelAdapter,
)
from pitchbot.config import Settings


class LlmProvider(StrEnum):
    NONE = "none"
    ONNX_GENAI = "onnx-genai"


NO_MODEL_ID: Final[str] = "none"


def build_language_model(settings: Settings) -> tuple[ModelAdapter | None, str]:
    """The configured model, ``None`` when none is configured, or a startup error.

    ``None`` is the default and is not a degraded state: the reply planner produces a
    relevant, slot-driven reply from the rules alone, so a deployment without a model is
    fully functional and simply reads code-mixed input less well.
    """

    provider = LlmProvider(settings.llm_provider)
    if provider is LlmProvider.NONE:
        return None, NO_MODEL_ID
    if not ONNX_GENAI_AVAILABLE:
        raise PermanentAdapterError(
            f"llm_provider={provider.value!r} is configured but the optional dependency is "
            f"not installed. Install it with: {INSTALL_HINT}. Refusing to fall back to no "
            "model, because every turn would then be understood by the rules alone without "
            "anyone being told."
        )
    adapter = OnnxGenAiModelAdapter(
        settings.llm_model_dir,
        settings.llm_model_id,
        allow_non_commercial=settings.llm_allow_non_commercial,
    )
    return adapter, f"{provider.value}:{settings.llm_model_id}"


__all__ = ["NO_MODEL_ID", "LlmProvider", "build_language_model"]
