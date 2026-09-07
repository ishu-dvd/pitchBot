"""Turning a minimised summary into text a buyer can read.

Both buyer-facing artefacts - the deck and the WhatsApp follow-up - are handed the same
:class:`~pitchbot.actions.models.FollowUpSummary`, and both have to solve the same two
presentation problems: a captured budget still carries its cue word, and a captured
deadline is in canonical English units. The deck learned to solve them in PR 54 and 55;
the WhatsApp branch did not, so a Telugu buyer who said *"మా బడ్జెట్ రెండు లక్షలు, మూడు
నెలల్లో"* received a message with no budget in it at all and the word ``months`` in it.

That is the same shape of defect this project keeps finding: one branch is fixed and its
sibling, which was handed the identical data, is not. So the formatting lives here once
rather than beside either artefact.

What is deliberately *not* shared is how a missing value is presented. A slide has a fixed
layout and must fill every row, so the deck substitutes :attr:`DeckPhrases.unstated`. A
message is a list of what is known, so it omits the line. Both callers therefore compose
their own lines from these values.
"""

from __future__ import annotations

from typing import Final

from pitchbot.actions.deck_content import DeckPhrases
from pitchbot.domain import BUDGET_CUES

# Leading cue words the budget extractor keeps because it matches from the cue onwards, so
# a captured "budget is 150000" would otherwise be shown as "Budget: budget is 150000".
# Stripped for display only; the stored fact is untouched. Built from the shared catalogue
# so a language added there is stripped here too, longest form first.
_BUDGET_CUES: Final[tuple[str, ...]] = (
    *(f"{cue} is" for cue in BUDGET_CUES),
    *BUDGET_CUES,
)


def stated_budget(summary: str | None) -> str | None:
    """Present a captured budget without its extraction artefacts.

    The extractor matches from the cue word onwards, so the stored fact reads
    "budget is 150000". Text already labelled "Budget" must not repeat the word, and a
    buyer reading their own figure back should see the figure.
    """

    if summary is None:
        return None
    text = summary.strip()
    lowered = text.lower()
    for cue in _BUDGET_CUES:
        if lowered.startswith(cue.lower()):
            text = text[len(cue) :].lstrip(" :=-")
            break
    return text or None


def localised_timeline(summary: str | None, phrases: DeckPhrases) -> str | None:
    """A deadline the buyer can read, from a deadline the allowlist can check.

    ``conversation.rules`` normalises every language onto English units so the outbound
    allowlist stays a short closed list. Rendering that canonical form verbatim handed a
    Telugu buyer "3 months", so the unit is translated back on the way out while the
    digits, which every reader here uses, stay as they are.

    Anything that is not a recognised canonical form is returned unchanged rather than
    dropped: an unexpected value is a bug worth seeing, not worth hiding from the buyer.
    """

    text = _stripped(summary)
    if text is None:
        return None
    lowered = text.lower().strip()
    if lowered in phrases.timeline_units:
        return phrases.timeline_units[lowered]
    count, _, unit = lowered.partition(" ")
    if count.isdigit() and unit in phrases.timeline_units:
        return f"{count} {phrases.timeline_units[unit]}"
    return text


def _stripped(summary: str | None) -> str | None:
    if summary is None:
        return None
    return summary.strip() or None


__all__ = ["localised_timeline", "stated_budget"]
