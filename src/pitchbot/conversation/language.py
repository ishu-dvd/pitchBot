"""Deciding which language the conversation is actually in, turn by turn.

Until now the language was declared once, by the caller, and never revisited. That is fine
for a CLI where a person passes ``--language`` and means it. It is wrong for a call, which
is where this is going: in an Indian B2B call the buyer switching language mid-conversation
is *normal*, not an edge case, and until this module existed every part of the system
would carry on in the old one - the reply, the voice, and the transcription hint.

The transcription hint is the expensive one. A forced Whisper language does not degrade
gracefully when the buyer switches; it rewrites the speech into the wrong alphabet and
destroys the words (measured, see ``docs/BENCHMARKS.md``). So a switch that is not noticed
does not merely sound rude - it stops the system understanding anything at all.

Three signals, deliberately ordered:

1. **A request.** The buyer names a language and a way of speaking it. This is obeyed at
   once, with no hysteresis, because a person who asks to be spoken to in Hindi and is
   answered twice more in English has been ignored, and knows it.
2. **Script.** Devanagari and Telugu are unambiguous evidence, and are what a transcriber
   or a keyboard actually produces.
3. **Romanised vocabulary.** Latin letters cannot separate English from Hinglish, so a
   closed list of unambiguous romanised tokens does it instead.

Everything except a request goes through hysteresis: :func:`decide_language` needs the same
new language on consecutive turns before it switches. One borrowed word must not flip a
call, and "namaste, we run a shop" is an English sentence.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from pitchbot.conversation.planning import supported_languages
from pitchbot.domain import LanguageCode
from pitchbot.speech.scripts import Script, dominant_script

DEFAULT_SWITCH_AFTER: Final[int] = 2
"""Consecutive turns in a new language before the conversation follows.

One is too eager: a single borrowed word, or one utterance Whisper mislabels, would change
the reply language, the voice and the transcription hint all at once. Three is too slow -
by then the buyer has said two things the system answered in the wrong language, which is
the complaint this exists to remove. Two costs at most one mistimed reply.

A *request* bypasses this entirely; see :func:`detect_language`.
"""

MIN_ROMANISED_MARKERS: Final[int] = 2
"""Distinct romanised tokens needed before Latin text is read as an Indic language.

One is not evidence. "Namaste, we run a retail shop" is an English sentence containing a
greeting, and switching the whole conversation to Hindi on the strength of it would be
worse than not detecting Hinglish at all.
"""


class LanguageEvidence(StrEnum):
    """Why a language was read from a turn. Ordered by how much it should be trusted."""

    REQUESTED = "requested"
    SCRIPT = "script"
    VOCABULARY = "vocabulary"
    TRANSCRIBER = "transcriber"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class LanguageReading:
    """What one turn says about the language it is in."""

    language: LanguageCode | None
    evidence: LanguageEvidence

    @property
    def is_request(self) -> bool:
        return self.evidence is LanguageEvidence.REQUESTED


@dataclass(frozen=True, slots=True)
class LanguageDecision:
    """The language to use now, and the hysteresis state to carry to the next turn."""

    language: LanguageCode
    switched: bool
    pending: LanguageCode | None
    pending_count: int


_LANGUAGE_TOKENS: Final[Mapping[LanguageCode, tuple[str, ...]]] = {
    LanguageCode.ENGLISH: (
        "english",
        "angrezi",
        "angreji",
        "अंग्रेज़ी",
        "अंग्रेजी",
        "इंग्लिश",
        "ఇంగ్లీష",
        "ఆంగ్ల",
    ),
    LanguageCode.HINDI: ("hindi", "हिंदी", "हिन्दी", "హిందీ"),
    LanguageCode.TELUGU: ("telugu", "తెలుగు", "तेलुगु", "तेलगु"),
    LanguageCode.MIXED: ("hinglish", "हिंग्लिश", "हिंदी और अंग्रेजी", "hindi english mix"),
}
"""Ways of naming each language, in every script a buyer might use to name it.

A Hindi speaker asks for English in Devanagari, and a Telugu speaker asks for Telugu in
Telugu. Indexing only by the language's own script would catch the request nobody makes.

The Telugu entries are **stems, not citation forms**, because Telugu agglutinates its case
endings onto the noun. A buyer writes ``ఇంగ్లీషులో`` ("in English"), which does not contain
``ఇంగ్లీష్`` - the citation form ends in a virama that the inflected form replaces with a
vowel sign. Matching the citation form silently missed every Telugu-language request for
English, which is exactly the turn a Telugu speaker uses to ask for English. Found by
running ``examples/switch-request-te.txt``, not by a unit test written from the same
assumption as the code.
"""

_SPEECH_CUES: Final[tuple[str, ...]] = (
    "speak",
    "talk",
    "say it",
    "switch",
    "change to",
    "baat",
    "bol",
    "boliye",
    "bolo",
    "matlad",
    "बात",
    "बोल",
    "मुझसे",
    "మాట్లాడ",
    "మాట",
    "చెప్ప",
)
"""A language name alone is not a request.

"We sell Hindi books" names a language and asks for nothing. Requiring a way of *speaking*
alongside the name is what separates a request from a mention, and it is why this is a
closed list rather than a bare name match.
"""

_ROMANISED: Final[Mapping[LanguageCode, frozenset[str]]] = {
    LanguageCode.MIXED: frozenset(
        {
            "aap",
            "aapka",
            "aapke",
            "aapko",
            "hai",
            "hain",
            "kya",
            "kitna",
            "kitne",
            "chahiye",
            "nahi",
            "nahin",
            "haan",
            "mera",
            "meri",
            "hamara",
            "hamari",
            "hamare",
            "humein",
            "hume",
            "karenge",
            "batata",
            "acha",
            "badhiya",
            "rupaiya",
            "hogi",
            "hoon",
            "yeh",
            "woh",
            "thoda",
            "raha",
            "rahe",
            "rahi",
            "baad",
            "liye",
            "wala",
            "wale",
            "wali",
            "phir",
            "bilkul",
            "shuru",
            "mehanga",
            "mahanga",
            "sasta",
            "kaise",
            "kahan",
            "kaun",
            "kyun",
            "kyunki",
            "kuch",
            "sab",
            "sabhi",
            "aisa",
            "zaroorat",
            "zaroori",
            "milega",
            "dekhiye",
            "dekhta",
            "karo",
            "karna",
            "karte",
            "kijiye",
            "batao",
            "bataiye",
            "theek",
            "accha",
            "achha",
            "paisa",
            "paise",
            "rupaye",
            "dukan",
            "dukaan",
            "vyapar",
            "namaste",
            "bhai",
            "abhi",
            "lekin",
            "aur",
            "bhi",
            "mein",
            "hoga",
            "jaldi",
            "kaam",
            "sahi",
            "matlab",
            "zyada",
            "jyada",
            "chahta",
            "chahte",
            "sakta",
            "sakte",
        }
    ),
    LanguageCode.TELUGU: frozenset(
        {
            "meeru",
            "miru",
            "nenu",
            "manchi",
            "cheppandi",
            "kavali",
            "enti",
            "ela",
            "vyaparam",
            "dukanam",
            "rupayalu",
            "chala",
            "ledu",
            "avunu",
            "dhanyavadalu",
            "unnaru",
            "undi",
            "kosam",
            "ippudu",
            "entha",
        }
    ),
}
"""Romanised tokens that are not also English words.

Deliberately excluded despite being common: ``main`` (English "main"), ``ka``/``ki``/``ke``
/``ko``/``se``/``ho`` (two letters, and initials in a business name would match). A false
positive here changes the language of the whole conversation, so the list is short and
every entry is one that cannot be read as English.

Romanised Telugu has no settled spelling, so that set is smaller and is best-effort. Script
evidence is the reliable path for Telugu; this only helps a buyer typing on a Latin keyboard.
"""

_SCRIPT_LANGUAGES: Final[Mapping[Script, LanguageCode]] = {
    Script.DEVANAGARI: LanguageCode.HINDI,
    Script.TELUGU: LanguageCode.TELUGU,
}

_WORD = re.compile(r"[a-z]+")


def switchable_languages() -> frozenset[LanguageCode]:
    """Languages the conversation may switch *to*.

    Derived from the planner rather than listed here, so a language that cannot yet hold a
    conversation can never be switched into. Adding a phrase set is the only way to become
    switchable, which is the correct coupling: being able to detect a language and being
    able to speak it are not the same capability, and only the second one helps the buyer.
    """

    return supported_languages() | {LanguageCode.MIXED}


def detect_language(
    text: str,
    *,
    transcribed_as: LanguageCode | None = None,
) -> LanguageReading:
    """Read the language of one buyer turn.

    ``transcribed_as`` is the transcriber's own label when the turn arrived as speech. It
    is used only as a fallback, because a forced transcriber reports the language it was
    told to expect rather than the one that was spoken - so on the very turn a switch
    happens, it is the least reliable signal available, not the most.
    """

    lowered = text.lower()
    requested = _requested_language(lowered)
    if requested is not None:
        return LanguageReading(requested, LanguageEvidence.REQUESTED)

    script = dominant_script(text)
    if script is not None and script in _SCRIPT_LANGUAGES:
        return LanguageReading(_SCRIPT_LANGUAGES[script], LanguageEvidence.SCRIPT)

    if script is Script.LATIN:
        romanised = _romanised_language(lowered)
        if romanised is not None:
            return LanguageReading(romanised, LanguageEvidence.VOCABULARY)
        return LanguageReading(LanguageCode.ENGLISH, LanguageEvidence.SCRIPT)

    if transcribed_as is not None and transcribed_as in switchable_languages():
        return LanguageReading(transcribed_as, LanguageEvidence.TRANSCRIBER)
    return LanguageReading(None, LanguageEvidence.NONE)


def _requested_language(lowered: str) -> LanguageCode | None:
    if not any(cue in lowered for cue in _SPEECH_CUES):
        return None
    named = [
        language
        for language, tokens in _LANGUAGE_TOKENS.items()
        if any(token in lowered for token in tokens)
    ]
    # Naming two languages in one breath ("I speak Hindi, can we do English?") is genuinely
    # ambiguous, and guessing which one was the request is how a conversation ends up in
    # the language the buyer was moving away from. Ask nothing, change nothing.
    return named[0] if len(named) == 1 else None


def _romanised_language(lowered: str) -> LanguageCode | None:
    words = set(_WORD.findall(lowered))
    scores = {language: len(words & markers) for language, markers in _ROMANISED.items()}
    best = max(scores, key=lambda language: scores[language])
    if scores[best] < MIN_ROMANISED_MARKERS:
        return None
    # A tie means the tokens do not separate the two, which for romanised Indic text is
    # common enough to be worth refusing rather than resolving by dictionary order.
    if sum(1 for count in scores.values() if count == scores[best]) > 1:
        return None
    return best


def decide_language(
    *,
    current: LanguageCode,
    reading: LanguageReading,
    pending: LanguageCode | None = None,
    pending_count: int = 0,
    switch_after: int = DEFAULT_SWITCH_AFTER,
) -> LanguageDecision:
    """Apply hysteresis to a reading and return the language to answer in.

    Pure, so the conversation state stays a plain dataclass and a checkpoint restores the
    hysteresis exactly. A switch decided from state the engine did not persist would be
    silently forgotten across a restore, and the buyer would be answered in the language
    they had already moved away from.
    """

    if reading.language is None or reading.language not in switchable_languages():
        return LanguageDecision(current, False, pending, pending_count)

    if reading.language == current:
        # Returning to the current language is itself evidence against a pending switch:
        # hysteresis counts *consecutive* turns, and without this reset a conversation that
        # alternates languages would accumulate votes and eventually switch on a tie.
        return LanguageDecision(current, False, None, 0)

    if reading.is_request:
        return LanguageDecision(reading.language, True, None, 0)

    count = pending_count + 1 if pending == reading.language else 1
    if count >= switch_after:
        return LanguageDecision(reading.language, True, None, 0)
    return LanguageDecision(current, False, reading.language, count)


__all__ = [
    "DEFAULT_SWITCH_AFTER",
    "LanguageDecision",
    "LanguageEvidence",
    "LanguageReading",
    "decide_language",
    "detect_language",
    "switchable_languages",
]
