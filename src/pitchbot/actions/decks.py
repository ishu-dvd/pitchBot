from __future__ import annotations

import asyncio

from pitchbot.actions.models import DeckIndustry, DeckPreview, DeckRequest, DeckSlide
from pitchbot.adapters import ArtifactAdapter, Clock, EphemeralOperationStore, SystemClock
from pitchbot.domain import features as catalog_features

_INDUSTRY_CONTENT: dict[DeckIndustry, tuple[str, tuple[str, ...]]] = {
    DeckIndustry.APPAREL: (
        "Apparel commerce",
        ("Size and color variants", "Seasonal collections", "Mobile-first catalog"),
    ),
    DeckIndustry.TOYS: (
        "Toy store commerce",
        ("Age and category discovery", "Safety information", "Gift-ready collections"),
    ),
    DeckIndustry.BOOKS: (
        "Book commerce",
        ("Author and genre discovery", "Searchable catalog", "New-release collections"),
    ),
    DeckIndustry.FOOD: (
        "Food commerce",
        ("Menu and availability", "Delivery-ready ordering", "Dietary information"),
    ),
    DeckIndustry.IMPORT_EXPORT: (
        "Import-export showcase",
        ("Product specifications", "Markets and certifications", "Inquiry workflow"),
    ),
    DeckIndustry.PLASTICS: (
        "Plastics manufacturing",
        ("Material and grade catalog", "Technical specifications", "Bulk inquiry workflow"),
    ),
}
_ALLOWED_FEATURES = catalog_features()


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

        subtitle, industry_bullets = _INDUSTRY_CONTENT[request.industry]
        features = tuple(
            feature for feature in request.requested_features if feature in _ALLOWED_FEATURES
        )
        if not features:
            features = ("catalog", "multilingual")
        preview = DeckPreview(
            deck_id=request.deck_id,
            industry=request.industry,
            language=request.language,
            title=f"Sample Business: {subtitle}",
            slides=(
                DeckSlide(
                    title="Business opportunity",
                    bullets=industry_bullets,
                ),
                DeckSlide(
                    title="Suggested website scope",
                    bullets=tuple(_feature_label(item) for item in features),
                ),
                DeckSlide(
                    title="Safe next step",
                    bullets=(
                        "Confirm requirements and content ownership",
                        "Review a synthetic prototype",
                        "Approve scope before implementation",
                    ),
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


def _feature_label(feature: str) -> str:
    return {
        "catalog": "Structured product catalog",
        "online-payments": "Reviewed online payment flow",
        "inventory": "Inventory visibility",
        "whatsapp": "Policy-approved WhatsApp inquiry path",
        "multilingual": "English and Hindi content support",
    }[feature]
