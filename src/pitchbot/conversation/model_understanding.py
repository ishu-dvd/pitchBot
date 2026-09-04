"""Turn a model's constrained answer into the understanding the planner already consumes.

The planner does not know or care where understanding comes from - that is the whole point
of :class:`~pitchbot.conversation.planning.TurnUnderstanding`. This module is the adapter
between one specific source (a local language model answering a fixed schema) and that
shared shape, and it is the only place a model influences what the agent says.

Two properties are deliberate.

**It is best effort.** A model that is slow, missing, or wrong loses this turn's
improvement and nothing else: the caller falls back to the facts the rules extracted, which
is exactly the behaviour that shipped before a model existed. Nothing here retries, and a
failure is logged once rather than raised into the turn path.

**It cannot widen what the model may decide.** The answer is mapped through the same closed
`Slot` vocabulary the planner uses, and an unrecognised value is dropped rather than
trusted. Combined with constrained decoding this means the model chooses *among* known
slots and can never introduce one - so it cannot cause the agent to ask a question nobody
wrote.

**It asks one question, not two, and never asks for stance.** Measured 2026-09-04: asked for
a topic *and* a stance in one call, Qwen2.5-0.5B returned ``none``/``stalling`` for all
eight test turns - a constant function - and because ``STALLING`` is an answerable
objection, every reply became "answer the stall", including the turn where the buyer said
*"Yes, let us go ahead with the proposal."* Asked only for the topic, the same model
answered usefully. See :mod:`pitchbot.conversation.model_trust` for the per-language
numbers and for the gate that decides when the answer is believed.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from pitchbot.adapters.contracts import ModelAdapter
from pitchbot.adapters.errors import AdapterError
from pitchbot.conversation.model_trust import accept_slots, is_trusted
from pitchbot.conversation.planning import Slot, TurnUnderstanding
from pitchbot.domain import LanguageCode

logger = logging.getLogger(__name__)

SCHEMA_NAME = "turn-topic-v1"

MAX_TURN_CHARS = 600
"""Bound on what is handed to the model.

Prompt length dominates latency here - measured on Phi-3.5-mini, prefill was 4,302 ms of a
6,724 ms turn, and it scales with tokens. A buyer cannot make the agent arbitrarily slow by
pasting an essay.
"""

_INSTRUCTION = (
    "Which topic did the buyer's message give information about? "
    "The message may be in English, Hindi, or romanised Hindi.\n\n"
    'Message: "We run a bakery in Pune." -> {"topic": "business_type"}\n'
    'Message: "We can spend about fifty thousand." -> {"topic": "budget_stated"}\n'
    'Message: "It needs online payments and a cart." -> {"topic": "requested_features"}\n'
    'Message: "We want it live by March." -> {"topic": "timeline"}\n'
    'Message: "Hmm, let me think about it." -> {"topic": "none"}\n\n'
    "Message: "
)
"""Few-shot, because it was measured to be worth its cost.

On the same twelve cases, bare instructions scored 6/12 on both models; with these five
examples Phi-3.5-mini reached 10/12 and Qwen2.5-0.5B 7/12. The examples cost prompt tokens -
Phi's p50 went from 1,138 ms to 3,701 ms - which is affordable only because this call is
best effort and the rules answer without it.
"""


class ModelTurnUnderstanding:
    """Reads one buyer turn with a language model, or gives up quietly."""

    def __init__(self, model: ModelAdapter) -> None:
        self._model = model

    async def understand(
        self,
        text: str,
        language: LanguageCode,
        known_keys: Iterable[str],
    ) -> TurnUnderstanding | None:
        """The model's reading of this turn, or ``None`` to fall back to the rules.

        Returns ``None`` immediately for an untrusted language rather than asking and then
        discarding the answer: the call costs 0.6-4.3 s of a turn the buyer is waiting
        through, and spending that to produce something we have already decided not to
        believe is worse than not spending it.
        """

        if not is_trusted(language):
            return None
        try:
            completion = await self._model.complete_structured(
                _INSTRUCTION + f'"{text[:MAX_TURN_CHARS]}"',
                SCHEMA_NAME,
            )
        except (AdapterError, RuntimeError, ValueError):
            logger.warning("Model turn understanding failed; using extracted facts", exc_info=True)
            return None
        return _to_understanding(completion.value, known_keys, text=text, language=language)


def _to_understanding(
    value: object,
    known_keys: Iterable[str],
    *,
    text: str,
    language: LanguageCode,
) -> TurnUnderstanding | None:
    if not isinstance(value, dict):
        return None
    known = {slot for key in known_keys if (slot := _slot(str(key))) is not None}
    claimed = _slot(str(value.get("topic", "")))
    accepted = accept_slots(text, language, () if claimed is None else (claimed,))
    # A slot the model says was just filled counts as known even if the rules missed it.
    # That is the entire value of this path: the shipped budget pattern only matches digits,
    # so "our budget is around two lakh rupees" fills nothing and the agent asks again.
    # It counts only once the turn is shown to be about that topic at all - Phi claimed
    # `business_type` for "I am just looking around for now", which would have silently
    # retired a qualification question the buyer never answered.
    known |= accepted
    return TurnUnderstanding(
        known_slots=frozenset(known),
        filled_now=accepted,
        # Never a stance. The rules read intent; a model measured at 1/8 and 2/8 does not.
        intent=None,
    )


def _slot(raw: str) -> Slot | None:
    try:
        return Slot(raw)
    except ValueError:
        return None


__all__ = ["MAX_TURN_CHARS", "SCHEMA_NAME", "ModelTurnUnderstanding"]
