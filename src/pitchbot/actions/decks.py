from __future__ import annotations

import asyncio
from typing import Final

from pitchbot.actions.deck_content import phrases_for
from pitchbot.actions.models import DeckPreview, DeckRequest, DeckSlide
from pitchbot.actions.summary_text import localised_timeline, stated_budget
from pitchbot.adapters import ArtifactAdapter, Clock, EphemeralOperationStore, SystemClock
from pitchbot.domain import features as catalog_features

_ALLOWED_FEATURES = catalog_features()

_DEFAULT_SCOPE: Final[tuple[str, str]] = ("catalog", "multilingual")
"""What to propose to a buyer who named nothing.

Both are things every business in this catalogue needs, and a proposal slide reading
"we would build nothing" helps no one. This is a proposal, so it appears only under
"what we would build" - never under "what you told us".
"""


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
        stated_features = tuple(
            feature for feature in request.requested_features if feature in _ALLOWED_FEATURES
        )
        # A proposal cannot be empty, so an unstated scope falls back to the two things
        # every one of these businesses needs. That default belongs on the proposal slide
        # and nowhere else: it used to be applied before the "what you told us" slide was
        # built, so a buyer who had asked for nothing was shown a slide with their name on
        # it reading "Asked for: Structured product catalogue, Content in more than one
        # language". Putting a request in the buyer's mouth on the one slide whose whole
        # job is to prove they were listened to is worse than proposing nothing.
        proposed_features = stated_features or _DEFAULT_SCOPE
        asked_for = (
            ", ".join(phrases.feature_label[item] for item in stated_features)
            if stated_features
            else phrases.unstated
        )
        budget = stated_budget(request.budget_summary) or phrases.unstated
        timeline = localised_timeline(request.timeline_summary, phrases) or phrases.unstated
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
                        f"{phrases.features_label}: {asked_for}",
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
                    bullets=tuple(phrases.feature_label[item] for item in proposed_features),
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
