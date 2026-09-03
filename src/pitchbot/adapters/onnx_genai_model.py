"""The first real language model behind ``ModelAdapter``: local, CPU, constrained to JSON.

``ModelAdapter`` has existed since the adapter contracts were written and has never had an
implementation - only mocks. This is one, and it is deliberately the *only* kind that fits
this product: a small open-weight model, run locally through ONNX Runtime, whose output is
**structurally constrained** to a schema rather than parsed hopefully out of prose.

Why this runtime. ``onnxruntime-genai`` is MIT, publishes a Windows cp312 wheel on PyPI,
and depends on nothing but ``numpy`` and ``onnxruntime`` - which this project already
installs for Piper and faster-whisper. The obvious alternative, ``llama-cpp-python``,
publishes **no** Windows wheel on PyPI at any version, so installing it means a CMake and
MSVC source build; that is not a dependency a local-first product can ask for.

Why constrained decoding rather than "please reply in JSON". Measured 2026-09-03 on
Qwen2.5-0.5B: unguided, the same prompt returned ``"buyer_intent": "budget"`` and
``"next_question": "what would you like us to build?"`` - both outside the enum, both
syntactically valid JSON. Guided, every one of ten turns produced a schema-valid answer.
Constraint masks the logits so a violating token is unreachable; prompting only makes
violation less likely, and a small model takes the offer. No retry loop exists here because
none is needed.

**The generator is built once and rewound, and that is not an optimisation detail.**
Compiling the JSON-schema grammar happens inside ``og.Generator(model, params)`` and was
measured at 1,767-1,934 ms for Qwen and ~500 ms for Phi, independent of schema size
(a one-enum schema cost the same as a three-enum one), and it is never cached: a fresh
generator per turn pays it every turn. ``rewind_to(0)`` costs ~1 ms and leaves guidance
working correctly. Reusing one generator therefore cut Qwen's per-turn cost from 2,350 ms
to 440 ms - a 5.3x difference that decides whether this is usable at all.

One generator is not safe to share, so calls are serialised behind a lock. That is honest
rather than incidental: this adapter is a **single local model**, and pretending otherwise
would trade correct answers for apparent concurrency.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from types import ModuleType
from typing import Any, Final

from pitchbot.adapters.contracts import ModelAdapter, StructuredCompletion
from pitchbot.adapters.errors import PermanentAdapterError

logger = logging.getLogger(__name__)


def _import_genai() -> ModuleType | None:
    """Import the runtime if present, without a static dependency on it.

    ``importlib`` rather than a guarded ``import`` for the same reason as the speech
    adapters: a static import makes ``mypy`` report a different diagnostic depending on
    whether the optional extra happens to be installed, and no suppression is right in
    both states.
    """

    try:
        return importlib.import_module("onnxruntime_genai")
    except ImportError:
        return None


_MODULE: Final[ModuleType | None] = _import_genai()

ONNX_GENAI_AVAILABLE: Final[bool] = _MODULE is not None
INSTALL_HINT: Final[str] = 'pip install "pitchbot[local-llm]"'
PROVIDER_ID: Final[str] = "onnxruntime-genai"
RUNTIME_LICENSE: Final[str] = "MIT"
LICENSE_REVIEW_DATE: Final[str] = "2026-09-03"


@dataclass(frozen=True, slots=True)
class ModelLicense:
    """The licence of a model's *weights*, which is not the runtime's licence.

    ``permits_commercial_use`` is ``False`` both for a licence that forbids it and for one
    that could not be established, because "unknown" and "denied" must behave identically
    at a gate.
    """

    identifier: str
    permits_commercial_use: bool
    reference_url: str
    notes: str = ""


APACHE_2_0: Final[ModelLicense] = ModelLicense(
    identifier="Apache-2.0",
    permits_commercial_use=True,
    reference_url="https://www.apache.org/licenses/LICENSE-2.0",
)
MIT: Final[ModelLicense] = ModelLicense(
    identifier="MIT",
    permits_commercial_use=True,
    reference_url="https://opensource.org/license/mit",
)
QWEN_RESEARCH: Final[ModelLicense] = ModelLicense(
    identifier="qwen-research",
    permits_commercial_use=False,
    reference_url="https://huggingface.co/Qwen/Qwen2.5-3B-Instruct/blob/main/LICENSE",
    notes=(
        "'FOR NON-COMMERCIAL PURPOSES ONLY'. The Qwen2.5 family is licence-split: 0.5B and "
        "1.5B are Apache-2.0 while 3B is not, so the family name is not a licence."
    ),
)
LLAMA_COMMUNITY: Final[ModelLicense] = ModelLicense(
    identifier="llama3.2",
    permits_commercial_use=False,
    reference_url="https://www.llama.com/llama3_2/license/",
    notes=(
        "Not a permissive licence: additional-user threshold, mandatory 'Built with Llama' "
        "attribution, a naming rule on derived models, and an incorporated acceptable-use "
        "policy. Recorded as denied because those obligations are a product decision, not "
        "an engineering one."
    ),
)
GEMMA_TERMS: Final[ModelLicense] = ModelLicense(
    identifier="gemma",
    permits_commercial_use=False,
    reference_url="https://ai.google.dev/gemma/terms",
    notes=(
        "Requires passing Google's use restrictions into your own terms of use as an "
        "enforceable provision - a viral obligation on this product's EULA."
    ),
)

KNOWN_MODEL_LICENSES: Final[Mapping[str, ModelLicense]] = {
    # --- Permissive ------------------------------------------------------------------
    "Qwen/Qwen2.5-0.5B-Instruct": APACHE_2_0,
    "Qwen/Qwen2.5-1.5B-Instruct": APACHE_2_0,
    "Qwen/Qwen3-0.6B": APACHE_2_0,
    "Qwen/Qwen3-1.7B": APACHE_2_0,
    "microsoft/Phi-3.5-mini-instruct": MIT,
    "microsoft/Phi-4-mini-instruct": MIT,
    "HuggingFaceTB/SmolLM2-360M-Instruct": APACHE_2_0,
    "HuggingFaceTB/SmolLM2-1.7B-Instruct": APACHE_2_0,
    "ibm-granite/granite-3.1-2b-instruct": APACHE_2_0,
    # --- NOT permissive ---------------------------------------------------------------
    "Qwen/Qwen2.5-3B-Instruct": QWEN_RESEARCH,
    "meta-llama/Llama-3.2-1B-Instruct": LLAMA_COMMUNITY,
    "meta-llama/Llama-3.2-3B-Instruct": LLAMA_COMMUNITY,
    "google/gemma-2-2b-it": GEMMA_TERMS,
}
"""Weight licences reviewed on 2026-09-03 against each model's upstream licence file.

The important entries are the refusals. ``Qwen2.5-3B`` sits between two Apache-2.0
siblings and is non-commercial, and a quantised re-upload of any of these does **not**
relicense it - so a model must be named by its **upstream** id here, never by whichever
conversion repository the files happened to come from.
"""

SCHEMAS: Final[Mapping[str, str]] = {
    "turn-understanding-v1": json.dumps(
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "buyer_intent": {
                    "type": "string",
                    "enum": ["exploring", "comparing", "ready_to_buy", "stalling", "objecting"],
                },
                "acknowledge": {
                    "type": "string",
                    "enum": [
                        "business_type",
                        "requested_features",
                        "budget_stated",
                        "timeline",
                        "none",
                    ],
                },
            },
            "required": ["buyer_intent", "acknowledge"],
        }
    ),
}
"""Every schema this adapter will answer, by the name ``complete_structured`` takes.

A closed registry rather than a caller-supplied schema: the grammar for each is compiled
once at preload, and a schema that arrives at call time could not be. It also means the
set of things a model is allowed to decide is reviewable in one place.
"""


def require_genai() -> ModuleType:
    if _MODULE is None:
        raise PermanentAdapterError(
            f"onnxruntime-genai is not installed; install the optional extra with: {INSTALL_HINT}"
        )
    return _MODULE


def installed_version() -> str | None:
    try:
        return metadata.version("onnxruntime-genai")
    except metadata.PackageNotFoundError:
        return None


def model_license(model_id: str) -> ModelLicense:
    """The reviewed licence, or a refusal. Never a guess."""

    known = KNOWN_MODEL_LICENSES.get(model_id)
    if known is None:
        raise PermanentAdapterError(
            f"model {model_id!r} has no reviewed licence in KNOWN_MODEL_LICENSES. Add it "
            "with its upstream licence rather than inferring one from a conversion "
            "repository, which does not relicense the weights it converts."
        )
    return known


@dataclass(frozen=True, slots=True)
class OnnxGenAiProvenance:
    """Exactly what produced an answer, as ADR-0004 requires it to be captured."""

    provider_id: str
    package_version: str
    runtime_license: str
    model_id: str
    model_license: str
    model_permits_commercial_use: bool
    model_path: str


class OnnxGenAiModelAdapter(ModelAdapter):
    """A local model that can only answer in one of :data:`SCHEMAS`."""

    def __init__(
        self,
        model_path: Path | str,
        model_id: str,
        *,
        allow_non_commercial: bool = False,
        max_new_tokens: int = 96,
    ) -> None:
        if max_new_tokens < 1:
            raise ValueError("max_new_tokens must be positive")
        licence = model_license(model_id)
        if not licence.permits_commercial_use and not allow_non_commercial:
            raise PermanentAdapterError(
                f"model {model_id!r} is licensed {licence.identifier!r}, which does not "
                "permit commercial use. PitchBot is a sales assistant, so this is "
                "disqualifying for production. Pass allow_non_commercial=True only for "
                f"local evaluation. {licence.notes} License: {licence.reference_url}"
            )
        self._model_path = Path(model_path)
        self._model_id = model_id
        self._license = licence
        self._max_new_tokens = max_new_tokens
        self._model: Any | None = None
        self._tokenizer: Any | None = None
        self._generators: dict[str, Any] = {}
        self._lock = asyncio.Lock()

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def license(self) -> ModelLicense:
        return self._license

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def provenance(self) -> OnnxGenAiProvenance:
        return OnnxGenAiProvenance(
            provider_id=PROVIDER_ID,
            package_version=installed_version() or "not-installed",
            runtime_license=RUNTIME_LICENSE,
            model_id=self._model_id,
            model_license=self._license.identifier,
            model_permits_commercial_use=self._license.permits_commercial_use,
            model_path=str(self._model_path),
        )

    async def preload(self) -> None:
        """Load the model and compile every schema's grammar, once.

        Both costs are large, both hold the GIL, and neither depends on what a buyer says:
        measured, the model load is 1.9 s (Qwen) to 4.9 s (Phi) and each grammar compile a
        further 0.5-1.9 s. Paying them on the first buyer turn would stall the event loop -
        including the audio socket that barge-in depends on - so they are paid at startup,
        exactly as the speech providers do.
        """

        async with self._lock:
            await asyncio.to_thread(self._load)

    def _load(self) -> None:
        if self._model is not None:
            return
        module = require_genai()
        if not self._model_path.is_dir():
            raise PermanentAdapterError(
                f"model directory not found at {self._model_path}; models are "
                "operator-supplied and are never downloaded by PitchBot"
            )
        if not (self._model_path / "genai_config.json").is_file():
            raise PermanentAdapterError(
                f"{self._model_path} has no genai_config.json, so it is not an ONNX Runtime "
                "GenAI model directory. A plain ONNX export of the same weights will not "
                "load here."
            )
        try:
            model = module.Model(str(self._model_path))
            tokenizer = module.Tokenizer(model)
        except Exception as error:  # noqa: BLE001 - surfaced as a permanent adapter error
            raise PermanentAdapterError(
                f"onnxruntime-genai failed to load {self._model_id!r} from "
                f"{self._model_path}: {error}"
            ) from error
        self._model = model
        self._tokenizer = tokenizer
        for name in SCHEMAS:
            self._generators[name] = self._build_generator(module, model, name)

    def _build_generator(self, module: ModuleType, model: Any, schema_name: str) -> Any:
        params = module.GeneratorParams(model)
        params.set_search_options(do_sample=False, max_length=2048)
        # Third argument enables fast-forward tokens: the grammar emits characters it has
        # already forced - braces, quotes, field names - without running the model. Measured
        # on Phi it cut generated tokens from 39 to 25 and ~540 ms per turn, with identical
        # answers. It defaults to False, so leaving it out silently pays for every brace.
        params.set_guidance("json_schema", SCHEMAS[schema_name], True)
        return module.Generator(model, params)

    async def complete_structured(
        self,
        instruction: str,
        schema_name: str,
    ) -> StructuredCompletion:
        """Answer ``instruction`` as JSON that satisfies ``schema_name``.

        Serialised: one generator per schema is reused across calls, so two concurrent
        turns would interleave into one another's token sequence.
        """

        if schema_name not in SCHEMAS:
            raise PermanentAdapterError(
                f"unknown schema {schema_name!r}; this adapter answers only {sorted(SCHEMAS)}"
            )
        async with self._lock:
            if self._model is None:
                await asyncio.to_thread(self._load)
            value = await asyncio.to_thread(self._generate, instruction, schema_name)
        return StructuredCompletion(
            value=value,
            model_version=f"{self._model_id}@{installed_version() or 'unknown'}",
        )

    def _generate(self, instruction: str, schema_name: str) -> dict[str, Any]:
        tokenizer = self._tokenizer
        generator = self._generators.get(schema_name)
        assert tokenizer is not None and generator is not None  # guarded by _load
        messages = json.dumps([{"role": "user", "content": instruction}])
        tokens = tokenizer.encode(
            tokenizer.apply_chat_template(messages=messages, add_generation_prompt=True)
        )
        generator.rewind_to(0)
        generator.append_tokens(tokens)
        steps = 0
        while not generator.is_done() and steps < self._max_new_tokens:
            generator.generate_next_token()
            steps += 1
        # Fast-forwarded tokens are appended to the sequence without ever being returned by
        # `get_next_tokens`, so collecting per-step tokens silently drops most of the JSON.
        produced = list(generator.get_sequence(0))[len(tokens) :]
        text = tokenizer.decode(produced).strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as error:
            # Constrained decoding makes this unreachable for a completed answer; it is
            # reachable only if generation was cut off by max_new_tokens mid-object.
            raise PermanentAdapterError(
                f"model output was not valid JSON after {steps} tokens, which means "
                f"generation was truncated rather than constrained: {text[:200]!r}"
            ) from error
        if not isinstance(parsed, dict):
            raise PermanentAdapterError(f"schema {schema_name!r} must yield an object")
        return parsed


__all__ = [
    "INSTALL_HINT",
    "KNOWN_MODEL_LICENSES",
    "ONNX_GENAI_AVAILABLE",
    "PROVIDER_ID",
    "SCHEMAS",
    "ModelLicense",
    "OnnxGenAiModelAdapter",
    "OnnxGenAiProvenance",
    "model_license",
    "require_genai",
]
