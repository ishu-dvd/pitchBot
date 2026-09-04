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
from pitchbot.domain.catalog import Intent, business_types


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


class SalesMove(StrEnum):
    """What the agent is *doing* this turn, as opposed to which slot it mentions.

    The planner used to have exactly one move - ask for the next missing slot - and a
    single terminal state when nothing was left to ask. That is a qualifying questionnaire.
    A sales conversation also has to answer the thing the buyer just pushed back on, say
    what is actually on offer once enough is known, and ask for a commitment; and it has to
    choose between those rather than run them in a fixed order.
    """

    ASK = "ask"
    ANSWER_OBJECTION = "answer_objection"
    PITCH = "pitch"
    CLOSE = "close"


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
    business_type: str | None = None
    """Which vertical the buyer is in, as a catalogue key - never their own words.

    Carried so the planner can say something specific about the business instead of a
    generic line. It is safe to render because extraction can only ever set it to one of
    :data:`~pitchbot.domain.catalog.BUSINESS_TYPES`; there is no path by which buyer text
    reaches this field.
    """

    def __post_init__(self) -> None:
        if not self.filled_now <= self.known_slots:
            raise ValueError("a slot filled this turn must also be known")

    @property
    def missing(self) -> tuple[Slot, ...]:
        return tuple(slot for slot in ASK_ORDER if slot not in self.known_slots)


@dataclass(frozen=True, slots=True)
class ReplyPlan:
    """What to say this turn, as a set of moves. Never any buyer text."""

    acknowledge: Slot | None
    ask: Slot | None
    intent: Intent | None = None
    objection: Intent | None = None
    """A stance that needs answering in its own words before anything else is said."""
    pitch: str | None = None
    """A business-type key to make the value statement for, or ``None`` to stay quiet."""
    move: SalesMove = SalesMove.ASK
    """The primary thing this turn does, for logging and for the caller to reason about."""

    @property
    def is_closing(self) -> bool:
        """Nothing left to ask, so the conversation should move to a next step."""

        return self.move is SalesMove.CLOSE


def understanding_from_facts(
    known_keys: Iterable[str],
    filled_now_keys: Iterable[str] = (),
    *,
    business_type: str | None = None,
) -> TurnUnderstanding:
    """Build understanding from the engine's own fact keys.

    Unknown keys are ignored rather than rejected: the extractors legitimately produce
    facts that are not slots, and a new one must not be able to break the reply path.

    ``business_type`` is accepted only if it is a catalogue key. An unrecognised value is
    dropped rather than carried, because the one place this value is used is composing a
    sentence about the buyer's business, and the guarantee that only catalogue keys reach
    that table is what keeps the reply path unable to repeat buyer text.
    """

    known = frozenset(_slots(known_keys))
    filled = frozenset(_slots(filled_now_keys)) & known
    vertical = business_type if business_type in business_types() else None
    return TurnUnderstanding(known_slots=known, filled_now=filled, business_type=vertical)


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


ANSWERABLE_OBJECTIONS: Final[tuple[Intent, ...]] = (
    Intent.OBJECTING,
    Intent.COMPARING,
    Intent.STALLING,
)
"""Stances that deserve a sentence of their own before the conversation moves on.

``READY`` is missing on purpose: agreement is not a concern to be handled, it is a
signal to stop qualifying and close. ``EXPLORING`` is missing because it is the ordinary
case and answering it would mean reacting to every turn.
"""


def plan_reply(
    understanding: TurnUnderstanding,
    *,
    repeated: bool = False,
    asked_counts: Mapping[str, int] | None = None,
    max_asks: int = MAX_ASKS_PER_SLOT,
) -> ReplyPlan:
    """Decide the whole move for this turn: answer, acknowledge, pitch, ask, or close.

    The ordering below is the sales judgement, and each rule exists because breaking it is
    a way to lose a buyer who was still willing:

    **A stated commitment stops the questions.** A buyer who says "let us start" and is
    asked for their timeline has been made to wait by a form. ``READY`` closes immediately
    even when slots remain unknown, because the remaining slots can be settled by a human
    on the call that closing books.

    **Pushback is answered, and the conversation still moves.** An objection sets
    ``objection`` *in addition to* whatever else the turn does, rather than replacing it.
    Answering and then falling silent trades one failure for another: the buyer is heard
    and then abandoned. Answering and continuing is what a person does.

    **The pitch happens once, when the vertical becomes known.** It is tied to
    ``filled_now`` rather than to a stored "have I pitched" flag, which means it needs no
    new conversation state and lands at the only moment it is ever new information. A
    repeated turn has an empty ``filled_now``, so a buyer who says the same thing twice is
    not pitched twice.

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

    intent = understanding.intent
    objection = intent if intent in ANSWERABLE_OBJECTIONS else None
    pitch = (
        understanding.business_type
        if Slot.BUSINESS_TYPE in understanding.filled_now and not repeated
        else None
    )

    if intent is Intent.READY or ask is None:
        move = SalesMove.CLOSE
        ask = None
    elif objection is not None:
        move = SalesMove.ANSWER_OBJECTION
    elif pitch is not None:
        move = SalesMove.PITCH
    else:
        move = SalesMove.ASK

    return ReplyPlan(
        acknowledge=acknowledge,
        ask=ask,
        intent=intent,
        objection=objection,
        pitch=pitch,
        move=move,
    )


@dataclass(frozen=True, slots=True)
class LanguagePhrases:
    """Every fixed phrase one language needs, in one place.

    Held as a single object rather than as four parallel ``Mapping[LanguageCode, ...]``
    tables because parallel tables let a language be *half* added: filling three of them
    and forgetting the fourth type-checks, passes every existing test, and produces a
    ``KeyError`` only for the buyer who reaches the missing branch. Adding Telugu did
    exactly that during development. One block per language makes the omission impossible
    to write, and :func:`supported_languages` makes it impossible to ship unnoticed.
    """

    acknowledge: Mapping[Slot, str]
    ask: Mapping[Slot, str]
    objection: Mapping[Intent, str]
    pitch: Mapping[str, str]
    closing: str
    confirm: str
    repeated: str
    switched: str

    def __post_init__(self) -> None:
        missing = [
            slot for slot in ASK_ORDER if slot not in self.acknowledge or slot not in self.ask
        ]
        if missing:
            raise ValueError(f"language phrases missing slots: {[s.value for s in missing]}")
        unanswered = [intent for intent in ANSWERABLE_OBJECTIONS if intent not in self.objection]
        if unanswered:
            raise ValueError(
                f"language phrases missing objections: {[i.value for i in unanswered]}"
            )
        unpitched = sorted(business_types() - set(self.pitch))
        if unpitched:
            # A vertical the extractor recognises but the planner cannot talk about is a
            # buyer who gets a generic sentence about a business we claimed to understand.
            # Checking here rather than at render time means adding a vertical to the
            # catalogue fails at import, in every language at once.
            raise ValueError(f"language phrases missing pitches: {unpitched}")


_PHRASES: Final[Mapping[LanguageCode, LanguagePhrases]] = {
    LanguageCode.ENGLISH: LanguagePhrases(
        acknowledge={
            Slot.BUSINESS_TYPE: "Thanks, that helps me picture the business.",
            Slot.REQUESTED_FEATURES: "Noted on what the site needs to do.",
            Slot.BUDGET: "Thanks for being straight about the budget.",
            Slot.TIMELINE: "Understood on the timing.",
        },
        ask={
            Slot.BUSINESS_TYPE: "What does your business sell?",
            Slot.REQUESTED_FEATURES: "What should the website let your customers do?",
            Slot.BUDGET: "What budget range are you working with?",
            Slot.TIMELINE: "When would you like this live?",
        },
        closing=("That covers what I need. Would a short demo or a written proposal help more?"),
        confirm=(
            "Good - I will put the proposal together and send it across, "
            "and we can walk through it whenever suits you."
        ),
        repeated="I have that noted.",
        switched="Of course, let us carry on in English.",
        objection={
            Intent.OBJECTING: (
                "That is fair, and price should match the work. "
                "We scope to what you actually need rather than a fixed package."
            ),
            Intent.COMPARING: (
                "Comparing is sensible. "
                "It is worth checking what each quote includes before judging the number."
            ),
            Intent.STALLING: (
                "No pressure at all. "
                "I can leave you the details and pick this up whenever it suits you."
            ),
        },
        pitch={
            "apparel": (
                "For clothing, buyers decide on the photograph, "
                "so size charts and a fast product page do most of the selling."
            ),
            "toys": (
                "Toy buyers filter by age and safety first, "
                "so clear age bands and stock counts prevent most abandoned carts."
            ),
            "books": (
                "For books, search and category browsing carry the store, "
                "because buyers arrive knowing roughly what they want."
            ),
            "food": (
                "For food, ordering hours and delivery areas matter more than anything, "
                "since a wrong answer there loses the order outright."
            ),
            "import-export": (
                "For import-export, buyers want specifications and bulk enquiry, "
                "not a checkout, so the site should qualify rather than sell."
            ),
            "plastics": (
                "For plastics, buyers compare grades and quantities, "
                "so a technical catalogue with an enquiry form does the real work."
            ),
        },
    ),
    LanguageCode.HINDI: LanguagePhrases(
        acknowledge={
            Slot.BUSINESS_TYPE: "धन्यवाद, इससे आपका व्यवसाय समझने में मदद मिली।",
            Slot.REQUESTED_FEATURES: "वेबसाइट में क्या चाहिए, वह नोट कर लिया।",
            Slot.BUDGET: "बजट साफ़ बताने के लिए धन्यवाद।",
            Slot.TIMELINE: "समय-सीमा समझ गया।",
        },
        ask={
            Slot.BUSINESS_TYPE: "आपका व्यवसाय क्या बेचता है?",
            Slot.REQUESTED_FEATURES: "वेबसाइट पर आपके ग्राहक क्या कर पाएँ?",
            Slot.BUDGET: "आपका अनुमानित बजट कितना है?",
            Slot.TIMELINE: "यह वेबसाइट कब तक चालू करनी है?",
        },
        closing="मुझे ज़रूरी जानकारी मिल गई। क्या एक छोटा डेमो ठीक रहेगा या लिखित प्रस्ताव?",
        confirm=("बढ़िया — मैं प्रस्ताव तैयार करके भेज देता हूँ, और जब आपको सुविधा हो तब उस पर बात कर लेंगे।"),
        repeated="यह मैंने दर्ज कर लिया है।",
        switched="बिलकुल, आगे की बात हिंदी में करते हैं।",
        objection={
            Intent.OBJECTING: (
                "बात सही है, कीमत काम के हिसाब से ही होनी चाहिए। "
                "हम तय पैकेज नहीं, आपकी ज़रूरत के हिसाब से दायरा बनाते हैं।"
            ),
            Intent.COMPARING: (
                "तुलना करना ठीक है। बस यह देख लीजिए कि हर कोटेशन में क्या-क्या शामिल है, फिर कीमत देखिए।"
            ),
            Intent.STALLING: (
                "कोई जल्दी नहीं है। मैं जानकारी भेज देता हूँ, जब आपको सही लगे तब आगे बात करते हैं।"
            ),
        },
        pitch={
            "apparel": (
                "कपड़ों में ग्राहक तस्वीर देखकर तय करता है, "
                "इसलिए साइज़ चार्ट और तेज़ प्रोडक्ट पेज ही ज़्यादातर बिक्री कराते हैं।"
            ),
            "toys": (
                "खिलौनों में ग्राहक पहले उम्र और सुरक्षा देखता है, इसलिए साफ़ उम्र-श्रेणी और स्टॉक दिखाना ज़रूरी है।"
            ),
            "books": (
                "किताबों में सर्च और श्रेणी-ब्राउज़िंग ही दुकान चलाती है, "
                "क्योंकि ग्राहक पहले से जानता है कि उसे क्या चाहिए।"
            ),
            "food": (
                "खाने में ऑर्डर का समय और डिलीवरी क्षेत्र सबसे ज़रूरी है, "
                "क्योंकि वहाँ गलत जानकारी से ऑर्डर सीधा हाथ से निकल जाता है।"
            ),
            "import-export": (
                "आयात-निर्यात में ग्राहक स्पेसिफिकेशन और थोक पूछताछ चाहता है, "
                "इसलिए साइट को बेचने से ज़्यादा पूछताछ छाँटनी चाहिए।"
            ),
            "plastics": (
                "प्लास्टिक में ग्राहक ग्रेड और मात्रा की तुलना करता है, "
                "इसलिए तकनीकी कैटलॉग और पूछताछ फ़ॉर्म ही असली काम करते हैं।"
            ),
        },
    ),
    LanguageCode.TELUGU: LanguagePhrases(
        acknowledge={
            Slot.BUSINESS_TYPE: "ధన్యవాదాలు, మీ వ్యాపారం అర్థమైంది.",
            Slot.REQUESTED_FEATURES: "వెబ్‌సైట్‌లో ఏమి కావాలో నమోదు చేసుకున్నాను.",
            Slot.BUDGET: "బడ్జెట్ స్పష్టంగా చెప్పినందుకు ధన్యవాదాలు.",
            Slot.TIMELINE: "సమయం గురించి అర్థమైంది.",
        },
        ask={
            Slot.BUSINESS_TYPE: "మీ వ్యాపారం ఏమి అమ్ముతుంది?",
            Slot.REQUESTED_FEATURES: "వెబ్‌సైట్‌లో మీ కస్టమర్లు ఏమి చేయగలగాలి?",
            Slot.BUDGET: "మీ బడ్జెట్ ఎంత అనుకుంటున్నారు?",
            Slot.TIMELINE: "ఇది ఎప్పటికి సిద్ధంగా ఉండాలి?",
        },
        closing=("నాకు కావలసిన సమాచారం వచ్చింది. ఒక చిన్న డెమో మంచిదా లేక రాతపూర్వక ప్రతిపాదనా?"),
        confirm=("మంచిది — నేను ప్రతిపాదన సిద్ధం చేసి పంపిస్తాను, మీకు వీలైనప్పుడు దాని గురించి మాట్లాడుకుందాం."),
        repeated="అది నేను నమోదు చేసుకున్నాను.",
        switched="తప్పకుండా, ఇక తెలుగులోనే మాట్లాడుకుందాం.",
        objection={
            Intent.OBJECTING: (
                "మీరు చెప్పింది సబబే, ధర పనికి తగినట్టే ఉండాలి. మేము నిర్ణీత ప్యాకేజీ కాదు, మీ అవసరానికి తగినట్టే పరిధి నిర్ణయిస్తాం."
            ),
            Intent.COMPARING: ("పోల్చి చూడటం మంచిదే. ప్రతి కోట్‌లో ఏమి కలిసి ఉందో చూసిన తర్వాతే ధరను బేరీజు వేయండి."),
            Intent.STALLING: ("తొందరేమీ లేదు. వివరాలు పంపిస్తాను, మీకు వీలైనప్పుడు ముందుకు వెళ్దాం."),
        },
        pitch={
            "apparel": (
                "దుస్తుల్లో కొనేవారు ఫోటో చూసే నిర్ణయిస్తారు, "
                "అందుకే సైజు చార్ట్ మరియు వేగవంతమైన ఉత్పత్తి పేజీ ఎక్కువ అమ్మకాలు చేస్తాయి."
            ),
            "toys": (
                "బొమ్మల్లో కొనేవారు ముందుగా వయసు, భద్రత చూస్తారు, అందుకే స్పష్టమైన వయసు విభాగాలు, స్టాక్ వివరాలు అవసరం."
            ),
            "books": (
                "పుస్తకాల్లో సెర్చ్ మరియు విభాగాల బ్రౌజింగ్ దుకాణాన్ని నడిపిస్తాయి, ఎందుకంటే కొనేవారికి ఏమి కావాలో ముందే తెలుసు."
            ),
            "food": (
                "ఆహారంలో ఆర్డర్ సమయం, డెలివరీ ప్రాంతం అన్నిటికన్నా ముఖ్యం, అక్కడ తప్పు సమాచారం ఉంటే ఆర్డర్ నేరుగా పోతుంది."
            ),
            "import-export": (
                "ఎగుమతి-దిగుమతిలో కొనేవారు స్పెసిఫికేషన్లు, బల్క్ విచారణ కోరుకుంటారు, కాబట్టి సైట్ అమ్మడం కంటే విచారణలు వడపోయాలి."
            ),
            "plastics": (
                "ప్లాస్టిక్‌లో కొనేవారు గ్రేడ్లు, పరిమాణాలు పోల్చుతారు, అందుకే సాంకేతిక కేటలాగ్ మరియు విచారణ ఫారం అసలు పని చేస్తాయి."
            ),
        },
    ),
}


def supported_languages() -> frozenset[LanguageCode]:
    """Languages the planner can actually speak, as opposed to ones the enum names.

    ``LanguageCode`` will always list more members than there are phrase sets - ``MIXED``
    and ``UNKNOWN`` are routing states, not languages anyone writes sentences in. Exposing
    the real set lets a caller check before promising a buyer a language, and lets a test
    assert completeness without hard-coding the list it is checking.
    """

    return frozenset(_PHRASES)


def _table(language: LanguageCode) -> LanguageCode:
    """Which phrase set answers this language.

    ``MIXED`` is code-switched Hindi-English, and a buyer writing Hinglish reads Hindi
    fluently, so answering in Hindi is right. ``UNKNOWN`` gets English because guessing an
    Indic language for unidentified text would be a worse failure than being formal. Any
    language with its own phrase set answers in itself.
    """

    if language in _PHRASES:
        return language
    return LanguageCode.HINDI if language is LanguageCode.MIXED else LanguageCode.ENGLISH


def render_reply(
    plan: ReplyPlan,
    language: LanguageCode,
    *,
    repeated: bool = False,
    switched: bool = False,
) -> str:
    """Compose the reply from fixed phrases only.

    Nothing the buyer wrote reaches this string. That is a safety property, not a stylistic
    one: it is what makes it impossible for this path to quote a fabricated price back, and
    it is why a prompt-injected turn cannot be reflected into the agent's own words. The
    pitch is indexed by a catalogue key rather than by extracted text for exactly this
    reason - the key is one of ours, so the sentence is one of ours.

    The order is how a person answers: deal with the objection, confirm what was heard,
    say the relevant thing, then ask or close. A reply that asks first and explains after
    reads as not having listened, which is the failure this whole module exists to remove.
    """

    phrases = _PHRASES[_table(language)]
    parts: list[str] = []
    if switched:
        # First, and in the new language, because it is the answer to the thing the buyer
        # most recently did. A reply that switches language without saying so reads as a
        # glitch; one that says so first reads as having listened - and for a buyer who
        # actually asked, this sentence is the whole answer to their request.
        parts.append(phrases.switched)
    if plan.objection is not None:
        parts.append(phrases.objection[plan.objection])
    if repeated:
        parts.append(phrases.repeated)
    elif plan.acknowledge is not None:
        parts.append(phrases.acknowledge[plan.acknowledge])
    if plan.pitch is not None:
        parts.append(phrases.pitch[plan.pitch])
    if plan.ask is not None:
        parts.append(phrases.ask[plan.ask])
    elif plan.intent is Intent.READY:
        # A buyer who has agreed must not be asked the closing question again. Repeating
        # "would a demo or a proposal help?" at the one moment they said yes is the most
        # expensive place in the conversation to read as not listening, and it is what
        # this path did until the shipped sales script was actually run.
        parts.append(phrases.confirm)
    else:
        parts.append(phrases.closing)
    return " ".join(parts)


__all__ = [
    "ANSWERABLE_OBJECTIONS",
    "ASK_ORDER",
    "Intent",
    "LanguagePhrases",
    "ReplyPlan",
    "SalesMove",
    "Slot",
    "TurnUnderstanding",
    "plan_reply",
    "render_reply",
    "supported_languages",
    "understanding_from_facts",
]
