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
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from pitchbot.adapters.contracts import ModelAdapter
from pitchbot.adapters.errors import AdapterError
from pitchbot.conversation.planning import Intent, Slot, TurnUnderstanding
from pitchbot.domain import LanguageCode

logger = logging.getLogger(__name__)

SCHEMA_NAME = "turn-understanding-v1"

MAX_TURN_CHARS = 600
"""Bound on what is handed to the model.

Prompt length dominates latency here - measured on Phi-3.5-mini, prefill was 4,302 ms of a
6,724 ms turn, and it scales with tokens. A buyer cannot make the agent arbitrarily slow by
pasting an essay.
"""

_INSTRUCTION = (
    "You analyse one buyer message from a B2B sales conversation about building a website. "
    "The buyer writes in English, Hindi, or romanised Hinglish.\n"
    "Fields:\n"
    "- acknowledge: the topic the buyer JUST GAVE INFORMATION ABOUT in this message. "
    "Use 'none' only if the message contains no information about any topic.\n"
    "- buyer_intent: the buyer's stance.\n"
    "Topics: business_type (what they sell), requested_features (what the site must do), "
    "budget_stated (money, price, cost), timeline (dates, deadlines, how soon).\n"
    "Never invent prices, dates, or commitments.\n\n"
    "Buyer message:\n"
)


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
        """The model's reading of this turn, or ``None`` to fall back to the rules."""

        del language  # The model detects the language itself; it is not told.
        try:
            completion = await self._model.complete_structured(
                _INSTRUCTION + text[:MAX_TURN_CHARS],
                SCHEMA_NAME,
            )
        except (AdapterError, RuntimeError, ValueError):
            logger.warning("Model turn understanding failed; using extracted facts", exc_info=True)
            return None
        return _to_understanding(completion.value, known_keys)


def _to_understanding(
    value: object,
    known_keys: Iterable[str],
) -> TurnUnderstanding | None:
    if not isinstance(value, dict):
        return None
    known = {slot for key in known_keys if (slot := _slot(str(key))) is not None}
    filled = _slot(str(value.get("acknowledge", "")))
    if filled is not None:
        # A slot the model says was just filled counts as known even if the rules missed
        # it. That is the entire value of this path: the shipped budget pattern only
        # matches digits, so "our budget is around two lakh rupees" fills nothing and the
        # agent asks again.
        known.add(filled)
    return TurnUnderstanding(
        known_slots=frozenset(known),
        filled_now=frozenset({filled} if filled is not None else ()),
        intent=_intent(str(value.get("buyer_intent", ""))),
    )


def _slot(raw: str) -> Slot | None:
    try:
        return Slot(raw)
    except ValueError:
        return None


def _intent(raw: str) -> Intent | None:
    try:
        return Intent(raw)
    except ValueError:
        return None


__all__ = ["MAX_TURN_CHARS", "SCHEMA_NAME", "ModelTurnUnderstanding"]
