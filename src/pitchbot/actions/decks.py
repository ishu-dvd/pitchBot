from __future__ import annotations

import asyncio
from typing import Final

from pitchbot.actions.deck_content import DeckPhrases, phrases_for
from pitchbot.actions.models import DeckPreview, DeckRequest, DeckSlide
from pitchbot.adapters import ArtifactAdapter, Clock, EphemeralOperationStore, SystemClock
from pitchbot.domain import BUDGET_CUES
from pitchbot.domain import features as catalog_features

_ALLOWED_FEATURES = catalog_features()

# Leading cue words the budget extractor keeps because it matches from the cue onwards, so
# a captured "budget is 150000" would otherwise reach a slide reading "Budget: budget is
# 150000". Stripped for display only; the stored fact is untouched. Built from the shared
# catalogue so a language added there is stripped here too, longest form first.
_BUDGET_CUES: Final[tuple[str, ...]] = (
    *(f"{cue} is" for cue in BUDGET_CUES),
    *BUDGET_CUES,
)


class DeckService:
    def __init__(
        self,
        *,
        artifact_adapter: ArtifactAdapter,
        clock: Clock | None = None,
        max_decks: int = 100,
    ) -> None:
        if max_decks < 1:
            raise ValueError("Deck capacity must be positive")
        self._artifact_adapter = artifact_adapter
        self._clock = clock or SystemClock()
        self._max_decks = max_decks
        self._previews: dict[str, DeckPreview] = {}
        self._idempotency: dict[str, tuple[str, DeckPreview]] = {}
        self._lock = asyncio.Lock()

    async def create(self, request: DeckRequest) -> DeckPreview:
        async with self._lock:
            return await self._create(request)

    async def _create(self, request: DeckRequest) -> DeckPreview:
        fingerprint = request.model_dump_json()
        previous = self._idempotency.get(request.idempotency_key)
        if previous is not None:
            if previous[0] != fingerprint:
                raise ValueError("Idempotency key reused with different deck input")
            return previous[1]
        if request.deck_id in self._previews:
            raise ValueError("Deck identifier already exists")
        if len(self._previews) >= self._max_decks:
            raise RuntimeError("Deck capacity reached")

        phrases = phrases_for(request.language)
        industry = request.industry.value
        features = tuple(
            feature for feature in request.requested_features if feature in _ALLOWED_FEATURES
        )
        if not features:
            features = ("catalog", "multilingual")
        budget = _stated(request.budget_summary) or phrases.unstated
        timeline = _localised_timeline(request.timeline_summary, phrases) or phrases.unstated
        preview = DeckPreview(
            deck_id=request.deck_id,
            industry=request.industry,
            language=request.language,
            title=phrases.title_template.format(business=phrases.industry_name[industry]),
            slides=(
                # First, and deliberately: a buyer opens a deck to find out whether they
                # were listened to. Everything after this slide is the same for every
                # buyer in the vertical, so leading with the generic material is what made
                # the old deck feel like a template - which it was.
                DeckSlide(
                    title=phrases.heard_title,
                    bullets=(
                        f"{phrases.business_label}: {phrases.industry_name[industry]}",
                        f"{phrases.features_label}: "
                        + ", ".join(phrases.feature_label[item] for item in features),
                        f"{phrases.budget_label}: {budget}",
                        f"{phrases.timeline_label}: {timeline}",
                    ),
                ),
                DeckSlide(
                    title=phrases.opportunity_title,
                    bullets=phrases.industry_bullets[industry],
                ),
                DeckSlide(
                    title=phrases.scope_title,
                    bullets=tuple(phrases.feature_label[item] for item in features),
                ),
                DeckSlide(
                    title=phrases.next_step_title,
                    bullets=phrases.next_steps,
                ),
            ),
            generated_at=self._clock.now(),
        )
        await self._artifact_adapter.create(
            request.deck_id,
            {
                "industry": request.industry.value,
                "language": request.language.value,
                "slide_count": len(preview.slides),
                "format": preview.format,
            },
            request.idempotency_key,
        )
        self._previews[request.deck_id] = preview
        self._idempotency[request.idempotency_key] = (fingerprint, preview)
        return preview

    def get(self, deck_id: str) -> DeckPreview:
        try:
            return self._previews[deck_id]
        except KeyError as error:
            raise LookupError("Deck preview not found") from error

    async def remove_by_prefix(self, deck_id_prefix: str, operation_key_prefix: str) -> None:
        async with self._lock:
            deck_ids = tuple(
                deck_id for deck_id in self._previews if deck_id.startswith(deck_id_prefix)
            )
            for deck_id in deck_ids:
                operation_keys = tuple(
                    key
                    for key, (_, preview) in self._idempotency.items()
                    if preview.deck_id == deck_id
                )
                for key in operation_keys:
                    self._idempotency.pop(key, None)
                self._previews.pop(deck_id, None)
            if isinstance(self._artifact_adapter, EphemeralOperationStore):
                self._artifact_adapter.clear_operations(operation_key_prefix)


def _localised_timeline(summary: str | None, phrases: DeckPhrases) -> str | None:
    """A deadline the buyer can read, from a deadline the allowlist can check.

    ``conversation.rules`` normalises every language onto English units so the outbound
    allowlist stays a short closed list. Rendering that canonical form verbatim handed a
    Telugu buyer a slide reading "3 months", so the unit is translated back on the way out
    while the digits, which every reader here uses, stay as they are.

    Anything that is not a recognised canonical form is returned unchanged rather than
    dropped: an unexpected value is a bug worth seeing, not worth hiding from the buyer.
    """

    text = _stated(summary)
    if text is None:
        return None
    lowered = text.lower().strip()
    if lowered in phrases.timeline_units:
        return phrases.timeline_units[lowered]
    count, _, unit = lowered.partition(" ")
    if count.isdigit() and unit in phrases.timeline_units:
        return f"{count} {phrases.timeline_units[unit]}"
    return text


def _stated(summary: str | None) -> str | None:
    """Present a captured commercial fact without its extraction artefacts.

    The budget extractor matches from the cue word onwards, so the stored fact reads
    "budget is 150000". A slide already labelled "Budget" must not repeat the word, and a
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
