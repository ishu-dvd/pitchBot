"""Decide what to actually say next, instead of saying the same sentence every turn.

Before this module every ordinary turn returned one fixed string - *"Thanks. What matters
most next: features, budget, timeline, or the decision process?"* - no matter what the
buyer had just said, and no matter how many times they had already answered it. The
conversation could hear, transcribe and speak, and still had nothing to say.

The fix is deliberately **not** a language model. What makes that reply bad is not that it
was written by hand; it is that it ignored state the engine had already extracted. A
planner that reads the slots and asks for a missing one is better on every turn, costs
nothing, needs no dependency, and works identically offline. A model can improve the
*understanding* that feeds this planner (see :mod:`pitchbot.adapters.onnx_genai_model`),
but it is not what makes the reply relevant.

Three rules govern the plan, and each exists because the canned reply broke it:

**Never ask for something already known.** Slots come from the engine's own facts, so a
buyer who has stated a budget is never asked for it again.

**Acknowledge what was just heard.** A reply that ignores the previous sentence reads as
not listening, which for a sales assistant is the whole failure.

**Never invent.** The planner selects *which* slot to mention; it never renders a value
back. Nothing here can quote a price, promise a date, or state a commitment, because the
rendered text is composed of fixed per-language phrases and the buyer's own words are never
echoed into it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from pitchbot.domain import LanguageCode


class Slot(StrEnum):
    """A thing worth knowing about a lead, named by the fact key that fills it.

    These are the keys ``extract_business_signals`` already produces. Naming the slots
    after them rather than inventing a parallel vocabulary means a new extractor - rules
    or model - fills a slot simply by emitting the key, with no mapping table to keep in
    step.
    """

    BUSINESS_TYPE = "business_type"
    REQUESTED_FEATURES = "requested_features"
    BUDGET = "budget_stated"
    TIMELINE = "timeline"


ASK_ORDER: Final[tuple[Slot, ...]] = (
    Slot.BUSINESS_TYPE,
    Slot.REQUESTED_FEATURES,
    Slot.BUDGET,
    Slot.TIMELINE,
)
"""Cheapest and least intrusive question first.

A buyer will say what their business is before they will say what they will pay, so asking
for budget on turn one costs the conversation. The order is a sales judgement rather than a
technical one, which is exactly why it is one visible tuple instead of being spread across
branches.
"""


class Intent(StrEnum):
    """How the buyer is engaging, which changes how a question should be asked."""

    EXPLORING = "exploring"
    COMPARING = "comparing"
    READY = "ready_to_buy"
    STALLING = "stalling"
    OBJECTING = "objecting"


@dataclass(frozen=True, slots=True)
class TurnUnderstanding:
    """What was understood about one buyer turn, from any source.

    The rules produce this from the facts they extracted; a model produces it from a
    constrained-JSON answer. The planner cannot tell the difference, which is the point:
    swapping the source must not change how a reply is composed.
    """

    known_slots: frozenset[Slot] = frozenset()
    filled_now: frozenset[Slot] = frozenset()
    intent: Intent | None = None

    def __post_init__(self) -> None:
        if not self.filled_now <= self.known_slots:
            raise ValueError("a slot filled this turn must also be known")

    @property
    def missing(self) -> tuple[Slot, ...]:
        return tuple(slot for slot in ASK_ORDER if slot not in self.known_slots)


@dataclass(frozen=True, slots=True)
class ReplyPlan:
    """Which slot to reflect back, and which to ask for. Never any buyer text."""

    acknowledge: Slot | None
    ask: Slot | None
    intent: Intent | None = None

    @property
    def is_closing(self) -> bool:
        """Nothing left to ask, so the conversation should move to a next step."""

        return self.ask is None


def understanding_from_facts(
    known_keys: Iterable[str],
    filled_now_keys: Iterable[str] = (),
) -> TurnUnderstanding:
    """Build understanding from the engine's own fact keys.

    Unknown keys are ignored rather than rejected: the extractors legitimately produce
    facts that are not slots, and a new one must not be able to break the reply path.
    """

    known = frozenset(_slots(known_keys))
    filled = frozenset(_slots(filled_now_keys)) & known
    return TurnUnderstanding(known_slots=known, filled_now=filled)


def _slots(keys: Iterable[str]) -> Iterable[Slot]:
    for key in keys:
        try:
            yield Slot(key)
        except ValueError:
            continue


MAX_ASKS_PER_SLOT: Final[int] = 2
"""How often one slot may be asked for before the planner moves on.

Measured against the shipped extractors, this is not a nicety. The budget regex only
matches digits, so a buyer answering *"our budget is around two lakh rupees"* fills no
slot - and without a limit the agent asks for the budget on every remaining turn, which is
the exact failure the planner exists to remove. Giving up after two attempts also matches
how a person behaves: asking a third time reads as not listening, and a buyer who declines
twice has answered.
"""


def plan_reply(
    understanding: TurnUnderstanding,
    *,
    repeated: bool = False,
    asked_counts: Mapping[str, int] | None = None,
    max_asks: int = MAX_ASKS_PER_SLOT,
) -> ReplyPlan:
    """Pick what to acknowledge and what to ask next.

    On a repeated turn nothing is acknowledged. Reflecting a slot back to a buyer who just
    said the same thing again reads as a loop, and repetition is the one case where the
    old fixed reply was accidentally acceptable.

    A slot already asked ``max_asks`` times is skipped even though it is still unknown,
    because it is either unanswerable by this buyer or unextractable from their answer, and
    both look identical from here.
    """

    counts = asked_counts or {}
    acknowledge = None
    if not repeated and understanding.filled_now:
        # Reflect the most advanced thing just learned: a buyer who gave a budget and a
        # business type in one sentence is further along than the earlier slot suggests.
        acknowledge = max(understanding.filled_now, key=ASK_ORDER.index)
    ask = next(
        (slot for slot in understanding.missing if counts.get(slot.value, 0) < max_asks),
        None,
    )
    return ReplyPlan(acknowledge=acknowledge, ask=ask, intent=understanding.intent)


_ACKNOWLEDGE: Final[Mapping[LanguageCode, Mapping[Slot, str]]] = {
    LanguageCode.ENGLISH: {
        Slot.BUSINESS_TYPE: "Thanks, that helps me picture the business.",
        Slot.REQUESTED_FEATURES: "Noted on what the site needs to do.",
        Slot.BUDGET: "Thanks for being straight about the budget.",
        Slot.TIMELINE: "Understood on the timing.",
    },
    LanguageCode.HINDI: {
        Slot.BUSINESS_TYPE: "धन्यवाद, इससे आपका व्यवसाय समझने में मदद मिली।",
        Slot.REQUESTED_FEATURES: "वेबसाइट में क्या चाहिए, वह नोट कर लिया।",
        Slot.BUDGET: "बजट साफ़ बताने के लिए धन्यवाद।",
        Slot.TIMELINE: "समय-सीमा समझ गया।",
    },
}

_ASK: Final[Mapping[LanguageCode, Mapping[Slot, str]]] = {
    LanguageCode.ENGLISH: {
        Slot.BUSINESS_TYPE: "What does your business sell?",
        Slot.REQUESTED_FEATURES: "What should the website let your customers do?",
        Slot.BUDGET: "What budget range are you working with?",
        Slot.TIMELINE: "When would you like this live?",
    },
    LanguageCode.HINDI: {
        Slot.BUSINESS_TYPE: "आपका व्यवसाय क्या बेचता है?",
        Slot.REQUESTED_FEATURES: "वेबसाइट पर आपके ग्राहक क्या कर पाएँ?",
        Slot.BUDGET: "आपका अनुमानित बजट कितना है?",
        Slot.TIMELINE: "यह वेबसाइट कब तक चालू करनी है?",
    },
}

_CLOSING: Final[Mapping[LanguageCode, str]] = {
    LanguageCode.ENGLISH: (
        "That covers what I need. Would a short demo or a written proposal help more?"
    ),
    LanguageCode.HINDI: "मुझे ज़रूरी जानकारी मिल गई। क्या एक छोटा डेमो ठीक रहेगा या लिखित प्रस्ताव?",
}

_REPEATED: Final[Mapping[LanguageCode, str]] = {
    LanguageCode.ENGLISH: "I have that noted.",
    LanguageCode.HINDI: "यह मैंने दर्ज कर लिया है।",
}


def _table(language: LanguageCode) -> LanguageCode:
    """Hindi and mixed share the Hindi phrasing; everything else falls back to English.

    ``MIXED`` is code-switched Hindi-English, and a buyer writing Hinglish reads Hindi
    fluently, so answering in Hindi is right. ``UNKNOWN`` gets English because guessing
    Hindi for an unidentified language would be a worse failure than being formal.
    """

    return (
        LanguageCode.HINDI
        if language in (LanguageCode.HINDI, LanguageCode.MIXED)
        else (LanguageCode.ENGLISH)
    )


def render_reply(plan: ReplyPlan, language: LanguageCode, *, repeated: bool = False) -> str:
    """Compose the reply from fixed phrases only.

    Nothing the buyer wrote reaches this string. That is a safety property, not a stylistic
    one: it is what makes it impossible for this path to quote a fabricated price back, and
    it is why a prompt-injected turn cannot be reflected into the agent's own words.
    """

    table = _table(language)
    parts: list[str] = []
    if repeated:
        parts.append(_REPEATED[table])
    elif plan.acknowledge is not None:
        parts.append(_ACKNOWLEDGE[table][plan.acknowledge])
    parts.append(_CLOSING[table] if plan.ask is None else _ASK[table][plan.ask])
    return " ".join(parts)


__all__ = [
    "ASK_ORDER",
    "Intent",
    "ReplyPlan",
    "Slot",
    "TurnUnderstanding",
    "plan_reply",
    "render_reply",
    "understanding_from_facts",
]
