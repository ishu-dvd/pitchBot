"""Turn a deliberation into something a buyer can look at.

The slow lane produces a :class:`~pitchbot.deliberation.briefing.SitePlan`, which is a
structure, not an artifact. This module renders it two ways - as website content and as deck
slides - and both renderings obey the same three rules.

**Only what was concluded, plus fixed scaffolding.** No sentence here invents a competitor,
a page, or a claim. The model's words are placed into headings and bullets; the connecting
text is written here and is identical for every buyer. That means a reviewer can read this
file and know exactly which parts of an artifact a model could have influenced.

**Nothing that reads as a commitment.** A plan is a proposal drawn from four facts in a
half-finished conversation. Prices, dates, and guarantees are absent by construction rather
than by prompt instruction, because a prompt instruction is a request and this is a
guarantee.

**Marked as a proposal, in the buyer's language.** An artifact that does not say it is a
draft will be read as a quote.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from pitchbot.deliberation.briefing import SitePlan
from pitchbot.domain import LanguageCode


@dataclass(frozen=True, slots=True)
class Slide:
    """One slide of the mock deck. Title and bullets, nothing else.

    Deliberately not the existing ``DeckSlide``: that model belongs to the deck *service*,
    which stores, indexes and idempotency-keys previews. This is the content, and keeping
    it separate means a plan can be rendered without touching that machinery.
    """

    title: str
    bullets: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ArtifactPhrases:
    """The fixed scaffolding, per language."""

    draft_notice: str
    competitors_title: str
    differentiator_title: str
    pages_title: str
    content_heading: str
    next_step: str


_PHRASES: Final[dict[LanguageCode, ArtifactPhrases]] = {
    LanguageCode.ENGLISH: ArtifactPhrases(
        draft_notice="Draft outline from our conversation so far. Not a quote.",
        competitors_title="Who you are up against",
        differentiator_title="What would make your site different",
        pages_title="Pages your site needs",
        content_heading="Website outline",
        next_step="Tell me what to change and I will redraw it.",
    ),
    LanguageCode.HINDI: ArtifactPhrases(
        draft_notice="अब तक की बातचीत से बनाया गया मसौदा। यह कोई कोटेशन नहीं है।",
        competitors_title="आपका मुकाबला किससे है",
        differentiator_title="आपकी साइट अलग कैसे होगी",
        pages_title="आपकी साइट के लिए ज़रूरी पेज",
        content_heading="वेबसाइट की रूपरेखा",
        next_step="बताइए क्या बदलना है, मैं दोबारा बना दूँगा।",
    ),
    LanguageCode.TELUGU: ArtifactPhrases(
        draft_notice="ఇప్పటివరకు జరిగిన సంభాషణ ఆధారంగా రూపొందించిన ముసాయిదా. ఇది కోట్ కాదు.",
        competitors_title="మీ పోటీ ఎవరితో",
        differentiator_title="మీ సైట్ ఎలా భిన్నంగా ఉంటుంది",
        pages_title="మీ సైట్‌కు కావలసిన పేజీలు",
        content_heading="వెబ్‌సైట్ రూపరేఖ",
        next_step="ఏం మార్చాలో చెప్పండి, నేను మళ్లీ తయారు చేస్తాను.",
    ),
    LanguageCode.MIXED: ArtifactPhrases(
        draft_notice="Abhi tak ki baat se banaya hua draft outline. Yeh quote nahi hai.",
        competitors_title="Aapka muqabla kisse hai",
        differentiator_title="Aapki site alag kaise hogi",
        pages_title="Aapki site ke liye zaroori pages",
        content_heading="Website outline",
        next_step="Bataiye kya change karna hai, main dobara bana dunga.",
    ),
}
"""Scaffolding in every language the planner replies in.

The plan's own content stays in whatever language the model wrote it, which is English -
that is honest rather than ideal, and it is why the draft notice is translated even when
the bullets are not. Translating model output would need a translation model this product
does not have; pretending the bullets are localised would be worse than admitting they are
not.
"""


def phrases_for(language: LanguageCode) -> ArtifactPhrases:
    return _PHRASES.get(language, _PHRASES[LanguageCode.ENGLISH])


def site_content(plan: SitePlan, language: LanguageCode = LanguageCode.ENGLISH) -> str:
    """The plan as a markdown outline the buyer could be shown or sent."""

    phrases = phrases_for(language)
    lines = [
        f"# {phrases.content_heading}",
        "",
        f"_{phrases.draft_notice}_",
        "",
        f"## {phrases.differentiator_title}",
        "",
        plan.differentiator,
        "",
        f"## {phrases.pages_title}",
        "",
    ]
    lines.extend(f"- {page}" for page in plan.pages)
    lines.extend(["", f"## {phrases.competitors_title}", ""])
    lines.extend(f"- {competitor}" for competitor in plan.competitors)
    lines.extend(["", phrases.next_step, ""])
    return "\n".join(lines)


def deck_slides(plan: SitePlan, language: LanguageCode = LanguageCode.ENGLISH) -> tuple[Slide, ...]:
    """The plan as a three-slide mock: positioning, structure, landscape.

    Three slides because the plan has exactly three things in it. A deck with a slide per
    page would be a table of contents, and a deck with an invented agenda would be inventing.
    """

    phrases = phrases_for(language)
    return (
        Slide(
            title=phrases.differentiator_title,
            bullets=(plan.differentiator, phrases.draft_notice),
        ),
        Slide(title=phrases.pages_title, bullets=plan.pages),
        Slide(title=phrases.competitors_title, bullets=plan.competitors),
    )


def artifact_languages() -> frozenset[LanguageCode]:
    """Languages with their own scaffolding, for the coverage test."""

    return frozenset(_PHRASES)


__all__ = [
    "ArtifactPhrases",
    "Slide",
    "artifact_languages",
    "deck_slides",
    "phrases_for",
    "site_content",
]
