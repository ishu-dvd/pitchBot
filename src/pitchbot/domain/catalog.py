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

import re
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
    SOCIAL_PROOF = "social_proof"
    """The buyer is asking who else this has been done for.

    A credibility question, not a comparison: ``COMPARING`` answers *"we are getting other
    quotes"*, which is about price. Measured before this existed, three phrasings of this
    question matched no stance at all and received whatever the planner was going to say.
    """

    NEXT_STEPS = "next_steps"
    """The buyer is asking how this proceeds.

    A buying signal that is not yet a commitment, so it is answered rather than treated as
    ``READY``. It matters most late in a call, where the agent had backed off to *"take
    your time"* and a buyer asking how to start was told not to hurry.
    """


BUSINESS_TYPES: Final[Mapping[str, tuple[str, ...]]] = {
    # Indic entries are stems, not surface forms. `_VOCABULARY_SUFFIXES` already allows the
    # number and case endings, so "कपड़" matches कपड़े *and* कपड़ों, and "దుస్తుల" matches
    # దుస్తుల and దుస్తులు. Listing the nominative alone missed six of eleven natural
    # phrasings, including "कपड़ों की दुकान" - the very words this product's own deck uses
    # for a clothes shop - and every Telugu genitive, which is how a Telugu speaker names a
    # shop at all: "దుస్తుల దుకాణం".
    "apparel": (
        "apparel",
        "clothing",
        "clothes",
        "garment",
        "कपड़",
        "kapde",
        "kapdon",
        "దుస్తుల",
        "బట్టల",
    ),
    "toys": ("toy", "toys", "खिलौन", "khilone", "khilonon", "బొమ్మల"),
    "books": ("book", "books", "किताब", "kitab", "పుస్తకాల"),
    "food": ("food", "restaurant", "bakery", "खाना", "restaurant", "ఆహార", "బేకరీ", "రెస్టారెంట్"),
    "import-export": ("import export", "import-export", "निर्यात", "आयात", "ఎగుమతి", "దిగుమతి"),
    "plastics": ("plastic", "plastics", "प्लास्टिक", "ప్లాస్టిక్"),
}
"""Verticals the product claims to serve, and the words that identify each one."""

FEATURES: Final[Mapping[str, tuple[str, ...]]] = {
    "catalog": (
        "catalog",
        "catalogue",
        "product list",
        "product listing",
        "product page",
        "list our products",
        "list all our products",
        "कैटलॉग",
        "उत्पाद सूची",
        "प्रोडक्ट लिस्ट",
        "కేటలాగ్",
        "ఉత్పత్తుల జాబితా",
        "ప్రొడక్ట్ లిస్ట్",
    ),
    "online-payments": (
        "payment",
        "checkout",
        "pay online",
        "payment gateway",
        "upi",
        "pay by card",
        "card payment",
        "credit card",
        "debit card",
        "net banking",
        "netbanking",
        "razorpay",
        "paytm",
        "भुगतान",
        "पेमेंट",
        "ऑनलाइन पेमेंट",
        "చెల్లింపు",
        "ఆన్‌లైన్ చెల్లింపు",
    ),
    "inventory": (
        "inventory",
        "stock management",
        "stock level",
        "track stock",
        "track our stock",
        "stock track",
        # Not reachable by inflecting "stock track": `_VOCABULARY_SUFFIXES` drops the
        # derivational endings on purpose, so that `booking` cannot read as the *books*
        # business. That guard is right and stays - measured over six gerund phrasings this
        # was the only miss, because "product listing", "payment processing" and "WhatsApp
        # ordering" are all already listed in the form people say them.
        "stock tracking",
        "manage stock",
        "in stock",
        "out of stock",
        "how many units",
        "units left",
        "इन्वेंटरी",
        "स्टॉक ट्रैक",
        "स्टॉक मैनेज",
        "ఇన్వెంటరీ",
        "స్టాక్ ట్రాక్",
        "స్టాక్ నిర్వహణ",
    ),
    "whatsapp": ("whatsapp", "व्हाट्सऐप", "వాట్సాప్"),
    "multilingual": (
        "multilingual",
        "bilingual",
        "hindi and english",
        "telugu and english",
        "hindi aur english",
        "telugu aur english",
        "two languages",
        "multiple languages",
        "more than one language",
        "regional language",
        "हिंदी और अंग्रेजी",
        "दो भाषा",
        "बहुभाषी",
        "బహుభాషా",
        "తెలుగు మరియు ఇంగ్లీష్",
        "రెండు భాషల",
    ),
}
"""Capabilities a buyer can ask for, and the words that identify each one.

Every entry beyond the first of each row was added after measuring, because the original
lists were written the way a specification is written rather than the way a shopkeeper
talks. Driven over four languages, ``inventory`` was **undetectable in all four** - the only
non-obvious phrase was ``stock management``, which nobody says; they say *"track our stock
levels"*, *"स्टॉक ट्रैक"*, *"what is in stock"*. And of eleven ordinary English ways to ask
for something in this list, nine registered nothing at all: *"we want to accept UPI"*,
*"can buyers pay by card"*, *"I need a product page"*, *"the site in two languages"*.

The consequence was never a missing word. It was three artefacts degrading at once: the
deck's *"What you told us"* slide fell back to a default scope, ``agenda_for`` dropped to
``WEBSITE_DISCOVERY`` - the agenda for a buyer who has said nothing - and the WhatsApp
follow-up omitted the line entirely. A buyer who had just listed four requirements was
followed up as though they had listed none.

Phrases are multi-word wherever the single word is ambiguous. Bare ``stock`` is the clearest
case: it matches *"our stock is running low"*, which is a buyer describing their business,
not ordering stock tracking. Matching is whole-term with number and case inflection, so a
listed phrase does not need its plural.
"""


BUDGET_CUES: Final[tuple[str, ...]] = ("budget", "बजट", "బడ్జెట్")
"""The word "budget", in each language PitchBot sells in.

One tuple because there were three copies and they disagreed. `conversation.rules` heard a
budget stated in Telugu, `actions.decks` knew how to display it, and `actions.policy` - the
minimiser that decides what may leave a conversation - omitted `బడ్జెట్` and silently
discarded it. Measured end to end: a Telugu buyer said *"మా బడ్జెట్ 150000"* and the deck
handed back to them read *"బడ్జెట్: ఇంకా చర్చించలేదు"* - budget not yet discussed.

Nothing detected the disagreement, because each copy was internally consistent and every
test that touched a budget was written in English. Keeping the vocabulary in one place is
the only fix that cannot silently regress: a language added here is added everywhere.
"""

BUDGET_INTENT_CUES: Final[tuple[str, ...]] = (
    "willing to spend",
    "ready to spend",
    "happy to spend",
    "could spend",
    "can spend",
    "spend up to",
    "kharch kar sakte",
)
"""Saying what you are prepared to pay, without using the word "budget".

Every entry carries a modal, and that is the whole design. Bare *"we spend five lakh on
ads"* is what a company already pays someone else; *"we can spend five lakh"* is what they
are prepared to pay us. Admitting bare ``spend`` would turn the first into a budget, and a
wrong figure here is quoted back to the buyer and shapes a proposal.
"""

CURRENCY_MARKERS: Final[tuple[str, ...]] = ("₹", "rs.", "rs", "inr")
"""Ways a rupee amount is marked, which stand in for the word "budget" when present."""

INDIC_SCRIPT_RANGES: Final[str] = "\u0900-\u097f\u0c00-\u0c7f\u200c\u200d"
"""Devanagari and Telugu in full, plus the joiners, as a regex character-class body.

Needed because Python's ``\\w`` is defined by :meth:`str.isalnum`, which is false for
combining marks - and in these scripts the vowels *are* combining marks. A class of
``[\\w\\s]`` therefore matches ``बजट द`` and stops dead at the ``ो`` of ``दो``. The bug was
invisible for as long as every budget in every test was written in English, and invisible
again in Hindi whenever a digit happened to come before the first vowel sign.

Whole blocks rather than hand-picked mark ranges: the point of the class is to exclude
markup, links and addresses, and no part of either script is a way to write those.
"""


def budget_alternation() -> str:
    """The cue half of a budget pattern, as one regex alternation.

    Built here rather than in each consumer so the extractor, the minimiser and the deck
    cannot drift apart again. Longest first, because alternation is ordered.
    """

    cues = sorted((*BUDGET_CUES, *BUDGET_INTENT_CUES, *CURRENCY_MARKERS), key=len, reverse=True)
    return "|".join(re.escape(cue) for cue in cues)


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
    Intent.SOCIAL_PROOF: (
        "who else",
        "anyone else",
        "any references",
        "references",
        "worked with",
        "built something like this",
        "done this before",
        "case study",
        "case studies",
        "portfolio",
        "examples of your work",
        "similar work",
        "किसके लिए",
        "पहले किसके",
        "और किसके",
        "उदाहरण दिखा",
        "पहले काम किया",
        "ఎవరికి చేశారు",
        "ఇంతకు ముందు చేశారా",
        "ఉదాహరణలు చూపించ",
        "kiske liye banaya",
        "aur kiske liye",
        "pehle kaam kiya",
        "kaam kar chuke",
    ),
    Intent.NEXT_STEPS: (
        "what happens next",
        "what next",
        "how do we get started",
        "how do we start",
        "how does this work",
        "what is the process",
        "whats the process",
        "next step",
        "next steps",
        "आगे क्या",
        "प्रक्रिया क्या",
        "कैसे शुरू",
        "तरीका क्या",
        "తర్వాత ఏమిటి",
        "ఎలా మొదలు",
        "ప్రక్రియ ఏమిటి",
        "aage kya",
        "kaise shuru karein",
        "process kya hai",
        "aage ka tarika",
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
    Intent.SOCIAL_PROOF,
    Intent.NEXT_STEPS,
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
    "BUDGET_CUES",
    "BUDGET_INTENT_CUES",
    "BUSINESS_TYPES",
    "CURRENCY_MARKERS",
    "FEATURES",
    "INDIC_SCRIPT_RANGES",
    "INTENT_PHRASES",
    "INTENT_PRIORITY",
    "Intent",
    "budget_alternation",
    "business_types",
    "features",
]
