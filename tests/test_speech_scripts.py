"""Tests for the Devanagari-to-Telugu transcript repair.

The interesting cases are all about *not* corrupting things, because the failure this
module fixes is itself a silent corruption: Whisper returning confident, fluent, wrongly
spelled text. A repair that introduces its own silent corruption would be worse than the
bug, so most of what is asserted here is what must stay unchanged.
"""

from __future__ import annotations

import unicodedata

import pytest

from pitchbot.speech.scripts import (
    DEVANAGARI_TO_TELUGU,
    Script,
    devanagari_to_telugu,
    dominant_script,
    repair_telugu_transcript,
    script_profile,
)

TELUGU_BLOCK = range(0x0C00, 0x0C80)

# The exact string faster-whisper `small` returned for a Piper-synthesised Telugu sentence
# meaning "our budget is two lakh rupees", and the sentence that was actually spoken.
WHISPER_DEVANAGARI = "मा बजजे त्रेंड लक्षला रूपायलू"
SPOKEN_TELUGU = "మా బడ్జెట్ రెండు లక్షల రూపాయలు."


def test_every_mapping_target_is_a_real_telugu_character() -> None:
    """The naive `+0x300` shift lands on unassigned codepoints and fraction digits.

    Auditing the whole block found the constant offset wrong for 53 of 153 assigned
    Devanagari characters. This asserts the derived table never emits one of those: every
    target is either empty, ASCII punctuation, or a genuinely assigned Telugu character.
    """

    for source, target in DEVANAGARI_TO_TELUGU.items():
        assert unicodedata.name(source, "").startswith("DEVANAGARI")
        for char in target:
            if char in ".":
                continue
            assert ord(char) in TELUGU_BLOCK, f"{source!r} -> {char!r} is not Telugu"
            assert unicodedata.name(char, "").startswith("TELUGU")


def test_hindi_nukta_letters_fold_to_their_base_consonant() -> None:
    """`+0x300` maps these onto TSA, DZA, RRRA and unassigned codepoints.

    Whisper spells Telugu with Hindi's alphabet, so it reaches for these letters. Folding
    them to the plain consonant is what a Telugu speaker actually says.
    """

    assert devanagari_to_telugu("क़ख़ग़ज़ड़ढ़फ़य़") == "కఖగజడఢఫయ"


def test_vowel_length_is_preserved_not_shortened() -> None:
    """Devanagari's plain E and O are *long*; Telugu spends the plain name on the short one.

    Matching by Unicode name alone therefore shortens every e and o, turning `బడ్జేట్`
    into `బడ్జెట్` - a different word, not a diacritic quibble.
    """

    assert devanagari_to_telugu("े") == "ే"  # VOWEL SIGN E -> VOWEL SIGN EE
    assert devanagari_to_telugu("ो") == "ో"  # VOWEL SIGN O -> VOWEL SIGN OO
    assert devanagari_to_telugu("ए") == "ఏ"  # LETTER E -> LETTER EE
    assert devanagari_to_telugu("ओ") == "ఓ"  # LETTER O -> LETTER OO


def test_the_measured_transcript_becomes_readable_telugu() -> None:
    repaired = devanagari_to_telugu(WHISPER_DEVANAGARI)

    assert dominant_script(repaired) is Script.TELUGU
    assert script_profile(repaired)[Script.TELUGU] == 1.0
    # Not equality: transliteration recovers the sounds, not the spelling. Whisper's own
    # Devanagari rendering of Telugu is lossy, and this module never claims otherwise.
    assert "లక్షల" in repaired
    assert "రూపాయల" in repaired


def test_latin_digits_and_punctuation_pass_through() -> None:
    """Mixed text is normal: a buyer says a number, a brand, a URL."""

    assert devanagari_to_telugu("budget 200000 rs") == "budget 200000 rs"
    assert devanagari_to_telugu("मा 50000 rs") == "మా 50000 rs"


def test_already_telugu_text_is_untouched() -> None:
    """Repair must be idempotent: a second pass over a repaired transcript is a no-op."""

    once = devanagari_to_telugu(WHISPER_DEVANAGARI)
    assert devanagari_to_telugu(once) == once
    assert devanagari_to_telugu(SPOKEN_TELUGU) == SPOKEN_TELUGU


def test_repair_reports_whether_it_changed_anything() -> None:
    """A caller storing a transcript needs to know it is a transliteration, not the model's."""

    repaired, changed = repair_telugu_transcript(WHISPER_DEVANAGARI)
    assert changed
    assert repaired != WHISPER_DEVANAGARI

    unchanged, changed = repair_telugu_transcript(SPOKEN_TELUGU)
    assert not changed
    assert unchanged == SPOKEN_TELUGU


def test_repair_leaves_mostly_telugu_text_with_a_hindi_quotation_alone() -> None:
    """A Telugu sentence may legitimately quote Hindi; rewriting the quote is worse.

    The threshold is a majority rather than any-occurrence for exactly this. In the
    measured failure the Devanagari share is 100%, nowhere near the boundary.
    """

    mixed = "మా బడ్జెట్ రెండు లక్షలు, वो बोले"
    repaired, changed = repair_telugu_transcript(mixed)
    assert not changed
    assert repaired == mixed


@pytest.mark.parametrize("text", ["", "   ", "12345", "!!!", "\n"])
def test_text_without_letters_has_no_dominant_script(text: str) -> None:
    assert dominant_script(text) is None
    assert repair_telugu_transcript(text) == (text, False)


def test_danda_becomes_a_full_stop() -> None:
    """Telugu writes sentence ends with a period; the `+0x300` target is unassigned."""

    assert devanagari_to_telugu("मा।") == "మా."
