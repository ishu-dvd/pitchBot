"""Repair transcripts that arrive in the wrong writing system.

Whisper transcribes Telugu speech into **Devanagari**. Measured 2026-09-04 on
faster-whisper ``small`` and ``medium``, four sentences synthesised by Piper's Telugu
voices: 100% of output letters were Devanagari and 0% Telugu, on every clip, at every
size, while the same model *auto-detected* the language as ``te`` with 0.76-0.98
confidence. The model hears Telugu correctly and writes it in Hindi's alphabet.

That is a writing failure, not a hearing one, and the distinction is what makes it fixable.
Read aloud, ``मा बजजे त्रेंड लक्षला रूपायलू`` is the Telugu sentence
``మా బడ్జెట్ రెండు లక్షల రూపాయలు`` - "our budget is two lakh rupees". Every sound is
there; only the letters are from the wrong script.

**The obvious fix, an ``initial_prompt`` script anchor, is a trap.** Anchoring forced the
output to 100% Telugu letters and simultaneously destroyed the words: character error rate
went from 100% to *115.8%* with a short anchor and 90.5% with a domain anchor. It converts
a recoverable failure into an unrecoverable one, and it looks like an improvement on the
one metric anybody would check. It is not used here.

Transliteration is the fix that works: 100% CER falls to **41.0%**. That is not good enough
to show a buyer their own words back, and this module makes no such claim. It is good
enough for the only thing the conversation does with a transcript - match keywords to fill
slots - and it is the difference between Telugu working and Telugu not working at all.

**Why the mapping is derived rather than typed.** Devanagari (U+0900) and Telugu (U+0C00)
are parallel Brahmic blocks, and a constant ``+0x300`` shift is very nearly right - which
is exactly what makes it dangerous. Auditing every assigned codepoint found the shift
lands wrong on **53 of 153**: Hindi's Perso-Arabic nukta letters (क़ ख़ ग़ ज़ फ़) become
Telugu *fraction digits* and ``SIGN TUUMU``, and eighteen more land on unassigned
codepoints. A first probe missed this entirely because the sentences happened to use only
the safe core. So the table is built by matching Unicode *character names* - ``DEVANAGARI
LETTER KA`` maps to ``TELUGU LETTER KA`` and to nothing else - with a small explicit table
for characters whose correct target has a different name. Anything with no defensible
target is left untouched rather than guessed at.
"""

from __future__ import annotations

import unicodedata
from enum import StrEnum
from typing import Final

DEVANAGARI_BLOCK: Final[range] = range(0x0900, 0x0980)
TELUGU_BLOCK: Final[range] = range(0x0C00, 0x0C80)


class Script(StrEnum):
    """A writing system, named by its ISO 15924 code."""

    LATIN = "Latn"
    DEVANAGARI = "Deva"
    TELUGU = "Telu"
    OTHER = "Zyyy"


_RANGES: Final[tuple[tuple[Script, range], ...]] = (
    (Script.LATIN, range(0x0041, 0x0250)),
    (Script.DEVANAGARI, DEVANAGARI_BLOCK),
    (Script.TELUGU, TELUGU_BLOCK),
)


def script_of(char: str) -> Script:
    """Which writing system a single character belongs to."""

    code = ord(char)
    for script, block in _RANGES:
        if code in block:
            return script
    return Script.OTHER


def script_profile(text: str) -> dict[Script, float]:
    """The share of *letters* written in each script.

    Letters only. Counting digits and punctuation would make every sentence look partly
    Latin and hide the signal this exists to detect.
    """

    letters = [c for c in text if unicodedata.category(c).startswith("L")]
    if not letters:
        return {}
    counts: dict[Script, int] = {}
    for char in letters:
        script = script_of(char)
        counts[script] = counts.get(script, 0) + 1
    return {script: count / len(letters) for script, count in counts.items()}


def dominant_script(text: str) -> Script | None:
    """The script most of the letters are in, or ``None`` for text with no letters."""

    profile = script_profile(text)
    if not profile:
        return None
    return max(profile, key=lambda script: profile[script])


# Devanagari characters whose correct Telugu counterpart does *not* share its Unicode name,
# so the name-matching rule below cannot find it. Every entry is a deliberate decision.
_EXPLICIT: Final[dict[str, str]] = {
    # Vowel length. Devanagari is Indo-Aryan and does not contrast short and long e/o, so
    # its plain ``E`` and ``O`` *are* the long vowels; Telugu is Dravidian and does
    # contrast them, so it spends the unqualified name on the **short** one. Matching by
    # name therefore shortens every e and o - ``బడ్జెట్`` for ``బడ్జేట్`` - which is a real
    # word change, not a diacritic quibble. These eight rows restore the phonetic pairing.
    "\u090e": "\u0c0e",  # SHORT E -> E   (both short)
    "\u090f": "\u0c0f",  # E       -> EE  (both long)
    "\u0912": "\u0c12",  # SHORT O -> O   (both short)
    "\u0913": "\u0c13",  # O       -> OO  (both long)
    "\u0946": "\u0c46",  # VOWEL SIGN SHORT E -> VOWEL SIGN E
    "\u0947": "\u0c47",  # VOWEL SIGN E       -> VOWEL SIGN EE
    "\u094a": "\u0c4a",  # VOWEL SIGN SHORT O -> VOWEL SIGN O
    "\u094b": "\u0c4b",  # VOWEL SIGN O       -> VOWEL SIGN OO
    # Hindi's nukta consonants spell Perso-Arabic loans and have no Telugu letter. Telugu
    # writes these sounds with the plain consonant, which is also what a Telugu speaker
    # says. Shifting them by +0x300 instead lands on TELUGU LETTER TSA, DZA, RRRA and three
    # unassigned codepoints - silent corruption that reads as gibberish.
    "\u0958": "\u0c15",  # QA    -> KA
    "\u0959": "\u0c16",  # KHHA  -> KHA
    "\u095a": "\u0c17",  # GHHA  -> GA
    "\u095b": "\u0c1c",  # ZA    -> JA
    "\u095c": "\u0c21",  # DDDHA -> DDA
    "\u095d": "\u0c22",  # RHA   -> DDHA
    "\u095e": "\u0c2b",  # FA    -> PHA
    "\u095f": "\u0c2f",  # YYA   -> YA
    "\u0929": "\u0c28",  # NNNA  -> NA
    "\u0931": "\u0c30",  # RRA   -> RA
    "\u0934": "\u0c33",  # LLLA  -> LLA
    # The nukta mark itself has no meaning once its consonant has been folded.
    "\u093c": "",
    # Devanagari's danda is a sentence terminator. Telugu uses the Latin full stop; the
    # +0x300 target is unassigned.
    "\u0964": ".",
    "\u0965": ".",
    # Independent short A exists in Devanagari for Dravidian transcription but Telugu has
    # no separate letter; its +0x300 target is a combining anusvara, which would attach to
    # the previous letter and change the word.
    "\u0904": "\u0c05",  # SHORT A -> A
}


def _build_table() -> dict[str, str]:
    """Map each Devanagari character to the Telugu character of the same name.

    The rule is one sentence a reader can check - *same Unicode name, different script* -
    which a hand-typed table of a hundred rows would not be. Characters with no
    same-named Telugu counterpart are deliberately absent, so
    :func:`devanagari_to_telugu` leaves them alone instead of inventing a target.
    """

    telugu_by_name: dict[str, str] = {}
    for code in TELUGU_BLOCK:
        char = chr(code)
        try:
            name = unicodedata.name(char)
        except ValueError:
            continue
        telugu_by_name[name.removeprefix("TELUGU ")] = char

    table = dict(_EXPLICIT)
    for code in DEVANAGARI_BLOCK:
        char = chr(code)
        if char in table:
            continue
        try:
            name = unicodedata.name(char)
        except ValueError:
            continue
        counterpart = telugu_by_name.get(name.removeprefix("DEVANAGARI "))
        if counterpart is not None:
            table[char] = counterpart
    return table


DEVANAGARI_TO_TELUGU: Final[dict[str, str]] = _build_table()
"""Character-level mapping, derived once at import from the Unicode database."""


def devanagari_to_telugu(text: str) -> str:
    """Rewrite Devanagari letters as their Telugu counterparts.

    Characters outside Devanagari - Latin, digits, spaces, and Telugu already present -
    pass through unchanged, so this is safe to apply to mixed text.
    """

    return "".join(DEVANAGARI_TO_TELUGU.get(char, char) for char in text)


DEFAULT_REPAIR_THRESHOLD: Final[float] = 0.5
"""How much Devanagari a supposedly-Telugu transcript needs before it is rewritten.

Set at a majority rather than at any-occurrence because a Telugu sentence may legitimately
quote a Hindi word, and rewriting that quote would be a worse outcome than leaving it. In
the measured failure the share is 100%, so the threshold is nowhere near the decision.
"""


def repair_telugu_transcript(
    text: str,
    *,
    threshold: float = DEFAULT_REPAIR_THRESHOLD,
) -> tuple[str, bool]:
    """Rewrite a Telugu transcript that came back in Devanagari.

    Returns the text and whether it was changed, because a caller that silently repaired a
    transcript should be able to say so: the repaired string is a transliteration, not what
    the model produced, and anything that stores or displays it needs to know.

    Only ever applied to a transcript already known to be Telugu. Deciding *from the text*
    that Devanagari means "mis-scripted Telugu" would misfire on every genuine Hindi turn,
    which is the language the project already supports.
    """

    profile = script_profile(text)
    if profile.get(Script.DEVANAGARI, 0.0) < threshold:
        return text, False
    return devanagari_to_telugu(text), True


__all__ = [
    "DEFAULT_REPAIR_THRESHOLD",
    "DEVANAGARI_TO_TELUGU",
    "Script",
    "devanagari_to_telugu",
    "dominant_script",
    "repair_telugu_transcript",
    "script_of",
    "script_profile",
]
