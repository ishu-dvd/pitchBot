"""When a model's reading of a turn may be believed, and when it may not.

The shipped path believed a model unconditionally, and measurement on 2026-09-04 showed
what that costs.

**A model may not choose the sales move.** Asked for stance, Qwen2.5-0.5B answered
``stalling`` to all eight test turns and Phi-3.5-mini got 2/8. ``STALLING`` is in
``ANSWERABLE_OBJECTIONS``, so every single reply became "answer the stall" - including the
turn where the buyer said *"Yes, let us go ahead with the proposal."* Answering a stall at
the moment of agreement is the most expensive mistake in a sales conversation, and it was
live whenever a model was configured. Stance is now read only by the rules, which are
deterministic and testable.

**A model may not fill a slot it cannot read.** Measured per language on the extraction
task, one field, few-shot:

=================  =====  =====  =====  ==========
Model              en     hi     te     hinglish
=================  =====  =====  =====  ==========
Qwen2.5-0.5B       3/6    3/6    2/6    3/6
Qwen3-0.6B         5/6    5/6    1/6    3/6
Phi-3.5-mini       5/6    5/6    2/6    5/6
=================  =====  =====  =====  ==========

Telugu is at or below the 1-in-5 that guessing among five values would give, in every
commercially-licensed model that runs on this hardware. The two failures differ in a way
that matters: Qwen3 answers ``none`` for Telugu - it declines - while Phi answers
``business_type`` for four of six, which *fills a qualification slot the buyer never gave*
and stops the agent ever asking. A confident wrong answer is worse than no answer, so
Telugu is not asked at all and falls through to the rules.

**A model's claim is corroborated before it is believed.** Even in English, Phi claimed
``business_type`` for *"I am just looking around for now."* A slot is accepted only when the
turn contains a marker for that topic, so the model can still rescue
*"our budget is around two lakh rupees"* - which the digit-only budget pattern misses - and
cannot invent a business out of a hedge.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Final

from pitchbot.conversation.planning import Slot
from pitchbot.domain import LanguageCode

TRUSTED_LANGUAGES: Final[frozenset[LanguageCode]] = frozenset(
    {LanguageCode.ENGLISH, LanguageCode.HINDI, LanguageCode.MIXED}
)
"""Languages where a model was measured to read a turn better than nothing.

Telugu is absent by measurement, not by omission. When a Telugu-capable model small enough
for this hardware exists, adding it here is a one-line change plus a benchmark run - which
is the point of keeping the gate as data.

``UNKNOWN`` is absent too: if the language could not be determined, the evidence for
trusting the model on it does not exist either.
"""

_MARKERS: Final[Mapping[Slot, tuple[str, ...]]] = {
    Slot.BUSINESS_TYPE: (
        # English
        "sell",
        "sells",
        "selling",
        "business",
        "shop",
        "store",
        "manufacture",
        "manufacturer",
        "manufacturing",
        "wholesale",
        "wholesaler",
        "retail",
        "brand",
        "company",
        "firm",
        "factory",
        "supplier",
        "trade",
        "trading",
        "boutique",
        "bakery",
        "restaurant",
        "salon",
        "we are a",
        "we run",
        # Hindi
        "बेचते",
        "बेचना",
        "व्यवसाय",
        "दुकान",
        "कारखाना",
        "बनाते",
        "कंपनी",
        "थोक",
        # Romanised
        "bechte",
        "bechna",
        "dukaan",
        "dukan",
        "vyapar",
        "vyavasay",
        "banate",
        "karobar",
    ),
    Slot.REQUESTED_FEATURES: (
        # English
        "feature",
        "features",
        "need",
        "needs",
        "want",
        "wants",
        "require",
        "requires",
        "payment",
        "payments",
        "cart",
        "checkout",
        "catalog",
        "catalogue",
        "search",
        "login",
        "dashboard",
        "reorder",
        "reordering",
        "pricing tier",
        "pricing tiers",
        "integration",
        "mobile",
        "app",
        "form",
        "forms",
        "tracking",
        "must support",
        # Hindi
        "चाहिए",
        "जरूरत",
        "ज़रूरत",
        "सुविधा",
        "फीचर",
        "भुगतान",
        "कार्ट",
        # Romanised
        "chahiye",
        "chahiy",
        "zarurat",
        "jarurat",
        "suvidha",
        "feature chahiye",
    ),
    Slot.BUDGET: (
        # English
        "budget",
        "cost",
        "costs",
        "price",
        "pricing",
        "spend",
        "spending",
        "afford",
        "rupee",
        "rupees",
        "lakh",
        "lakhs",
        "crore",
        "crores",
        "thousand",
        "rs",
        "inr",
        "quote",
        "quotation",
        "invest",
        "investment",
        # Hindi
        "बजट",
        "रुपये",
        "रुपए",
        "लाख",
        "करोड़",
        "हजार",
        "हज़ार",
        "कीमत",
        "दाम",
        "खर्च",
        # Romanised
        "budget",
        "rupaye",
        "rupaya",
        "lakh",
        "crore",
        "hazaar",
        "hajar",
        "keemat",
        "daam",
        "kharch",
    ),
    Slot.TIMELINE: (
        # English
        "launch",
        "live",
        "deadline",
        "timeline",
        "by ",
        "before",
        "after",
        "week",
        "weeks",
        "month",
        "months",
        "quarter",
        "year",
        "soon",
        "urgent",
        "diwali",
        "christmas",
        "season",
        "date",
        "deliver",
        "delivery",
        # Hindi
        "तक",
        "पहले",
        "बाद",
        "महीने",
        "महीना",
        "हफ्ते",
        "सप्ताह",
        "जल्दी",
        "दिवाली",
        "समय",
        # Romanised
        "pehle",
        "baad",
        "mahine",
        "mahina",
        "hafte",
        "jaldi",
        "diwali",
        "kab tak",
    ),
}
"""Topic markers, per slot, for the languages a model is trusted in.

There is deliberately no Telugu here. Telugu never reaches corroboration because
:data:`TRUSTED_LANGUAGES` excludes it, and shipping a Telugu lexicon that no native speaker
has reviewed - to gate a model that scores 1/6 in Telugu anyway - would be inventing
confidence rather than measuring it.

Markers are topic words, never sentiment words. ``"expensive"`` is not a budget marker even
though it is about money: Phi labelled *"that sounds expensive compared to other quotes"* as
``budget_stated`` when the buyer stated no budget, and a sentiment marker would have waved
that through.
"""

_ASCII_WORD = re.compile(r"[a-z]+")


def is_trusted(language: LanguageCode) -> bool:
    """Whether a model has been measured to help on this language."""

    return language in TRUSTED_LANGUAGES


def corroborates(text: str, slot: Slot) -> bool:
    """Whether ``text`` contains a marker for ``slot``.

    Latin markers match whole tokens and Indic markers match as substrings, which is not an
    inconsistency but the same rule the language detector settled on for the same reason.
    Latin sales vocabulary is full of short words, so ``"sell"`` matching inside
    ``"seller"`` is fine but ``"banate"`` matching inside ``"banatee"`` is not - token
    boundaries are the only safe rule. Hindi and Telugu inflect by *appending*, so a citation
    form is a prefix of what a buyer actually types: requiring a whole-token match against
    ``बनाते`` would miss ``बनाया``, and it is the mistake that made every Telugu request for
    English go unnoticed until an example script was run.

    Multi-word Latin markers are matched as substrings, because tokenising them away would
    lose the very adjacency that makes them specific.
    """

    lowered = text.casefold()
    tokens = set(_ASCII_WORD.findall(lowered))
    for marker in _MARKERS.get(slot, ()):
        if not marker.isascii():
            if marker in lowered:
                return True
        elif " " in marker:
            if marker in lowered:
                return True
        elif marker in tokens:
            return True
    return False


def accept_slots(
    text: str,
    language: LanguageCode,
    claimed: Iterable[Slot],
) -> frozenset[Slot]:
    """The subset of the model's claims that may be believed for this turn."""

    if not is_trusted(language):
        return frozenset()
    return frozenset(slot for slot in claimed if corroborates(text, slot))


def markers_for(slot: Slot) -> tuple[str, ...]:
    """The markers for one slot, so the coverage test can assert every slot has some."""

    return _MARKERS.get(slot, ())


__all__ = [
    "TRUSTED_LANGUAGES",
    "accept_slots",
    "corroborates",
    "is_trusted",
    "markers_for",
]
