"""What PitchBot is able to sell, and how a buyer can push back, in one place.

Before this module the same sales vocabulary existed in **three** independent copies:
:mod:`pitchbot.conversation.rules` matched buyer text against it to extract facts,
:mod:`pitchbot.actions.policy` re-declared it as an allowlist for outbound actions, and
:mod:`pitchbot.actions.decks` declared the feature half again. Nothing linked them. Adding a
vertical to the extractor therefore produced facts that the policy layer silently discarded
and the deck builder silently dropped, and every test still passed, because each copy was
individually consistent.

Holding it once turns "support a new vertical" into one edit in one file, which is the
stated goal that new languages and verticals are a **data** change rather than a model
change. It also makes the pitch table checkable: the planner can assert at import time that
it has something to say about every business this system claims to serve, instead of
discovering the gap when a buyer in that vertical is met.

**These keys are a closed vocabulary, and that is a safety property.** Extraction maps
buyer text onto one of these tokens or onto nothing at all; the buyer's own words never
become a key. That is what makes it safe for the planner to compose a reply from a table
indexed by them - the agent is choosing between sentences it was given, not repeating
something a stranger typed. ``budget_stated`` and ``timeline`` are deliberately *not* here,
because those extractors do keep buyer text, and so their values must never be rendered.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Final


class Intent(StrEnum):
    """How the buyer is engaging, which decides what the agent should do next.

    This is the difference between a qualifying questionnaire and a sales conversation. A
    buyer who says a price is too high and is answered with the next form field has been
    told, accurately, that nothing they say changes what happens next.
    """

    EXPLORING = "exploring"
    COMPARING = "comparing"
    READY = "ready_to_buy"
    STALLING = "stalling"
    OBJECTING = "objecting"


BUSINESS_TYPES: Final[Mapping[str, tuple[str, ...]]] = {
    "apparel": (
        "apparel",
        "clothing",
        "clothes",
        "garment",
        "कपड़े",
        "kapde",
        "దుస్తులు",
        "బట్టలు",
    ),
    "toys": ("toy", "toys", "खिलौने", "khilone", "బొమ్మలు"),
    "books": ("book", "books", "किताब", "kitab", "పుస్తకాలు"),
    "food": ("food", "restaurant", "bakery", "खाना", "restaurant", "ఆహారం", "బేకరీ", "రెస్టారెంట్"),
    "import-export": ("import export", "import-export", "निर्यात", "आयात", "ఎగుమతి", "దిగుమతి"),
    "plastics": ("plastic", "plastics", "प्लास्टिक", "ప్లాస్టిక్"),
}
"""Verticals the product claims to serve, and the words that identify each one."""

FEATURES: Final[Mapping[str, tuple[str, ...]]] = {
    "catalog": ("catalog", "catalogue", "कैटलॉग", "కేటలాగ్"),
    "online-payments": ("payment", "checkout", "pay online", "भुगतान", "చెల్లింపు"),
    "inventory": ("inventory", "stock management", "इन्वेंटरी", "ఇన్వెంటరీ"),
    "whatsapp": ("whatsapp", "व्हाट्सऐप", "వాట్సాప్"),
    "multilingual": (
        "multilingual",
        "bilingual",
        "hindi and english",
        "हिंदी और अंग्रेजी",
        "బహుభాషా",
    ),
}
"""Capabilities a buyer can ask for, and the words that identify each one."""


INTENT_PHRASES: Final[Mapping[Intent, tuple[str, ...]]] = {
    Intent.READY: (
        "let's start",
        "lets start",
        "let's go ahead",
        "go ahead",
        "ready to start",
        "send the proposal",
        "send proposal",
        "sign me up",
        "book the demo",
        "शुरू करें",
        "शुरू करते हैं",
        "आगे बढ़ें",
        "प्रस्ताव भेजें",
        "ప్రారంభిద్దాం",
        "మొదలుపెడదాం",
        "ప్రతిపాదన పంపండి",
        "shuru karte hain",
        "shuru karein",
        "aage badhte hain",
        "proposal bhej",
        "ready hain",
        "ముందుకు వెళ్దాం",
    ),
    Intent.OBJECTING: (
        "too expensive",
        "very expensive",
        "too costly",
        "too much",
        "out of budget",
        "over budget",
        "cannot afford",
        "can't afford",
        "expensive",
        "costly",
        "बहुत महंगा",
        "महंगा",
        "बजट से बाहर",
        "बहुत ज़्यादा",
        "చాలా ఖరీదు",
        "ఖరీదు",
        "బడ్జెట్ దాటి",
        "bahut mehanga",
        "bahut mahanga",
        "mehanga",
        "mahanga",
        "budget se bahar",
        "bahut zyada",
        "చాలా ఎక్కువ",
    ),
    Intent.COMPARING: (
        "another vendor",
        "other vendors",
        "another company",
        "other quotes",
        "someone else",
        "already have a",
        "comparing",
        "दूसरी कंपनी",
        "और भी देख रहे",
        "पहले से है",
        "వేరే కంపెనీ",
        "ఇంకొకరు",
        "ఇప్పటికే ఉంది",
        "doosri company",
        "dusri company",
        "aur bhi dekh rahe",
        "pehle se hai",
        "quote le rahe",
    ),
    Intent.STALLING: (
        "think about it",
        "get back to you",
        "call me later",
        "next month",
        "not right now",
        "not now",
        "later",
        "बाद में",
        "सोचकर बताता",
        "सोचकर बताऊंगा",
        "अभी नहीं",
        "अगले महीने",
        "తరువాత",
        "ఆలోచిస్తాను",
        "ఇప్పుడు కాదు",
        "వచ్చే నెల",
        "baad mein",
        "sochkar batata",
        "sochta hoon",
        "sochte hain",
        "abhi nahi",
        "agle mahine",
    ),
}
"""Phrases that reveal a stance, checked in :data:`INTENT_PRIORITY` order.

``EXPLORING`` has no phrases on purpose. It is the absence of a signal, not a signal, and
giving it trigger words would mean competing with the four stances that actually change the
agent's behaviour.

**Known limitation: there is no negation handling.** "it is not expensive for us" matches
``OBJECTING``. Adding a negation window is cheap to write and hard to get right across three
languages and two scripts, and the failure it prevents is answering a price concern that was
not raised - mildly wrong, not harmful. The priority order below removes the case that costs
a sale, which is the one worth spending correctness on.
"""

INTENT_PRIORITY: Final[tuple[Intent, ...]] = (
    Intent.READY,
    Intent.OBJECTING,
    Intent.COMPARING,
    Intent.STALLING,
)
"""Which stance wins when a turn carries more than one.

A buyer says several things in one breath, and "it is expensive but let us start" carries
both a concern and a commitment. Reading it as an objection and asking another qualifying
question is the expensive mistake, because the buyer had already decided and was made to
wait. So a stated commitment outranks everything, and the reply planner answers the concern
*as well* rather than instead - the stance chooses emphasis, not whether the rest is heard.
"""


def business_types() -> frozenset[str]:
    """Every vertical key, for allowlisting and for completeness checks."""

    return frozenset(BUSINESS_TYPES)


def features() -> frozenset[str]:
    """Every feature key, for allowlisting and for completeness checks."""

    return frozenset(FEATURES)


__all__ = [
    "BUSINESS_TYPES",
    "FEATURES",
    "INTENT_PHRASES",
    "INTENT_PRIORITY",
    "Intent",
    "business_types",
    "features",
]
