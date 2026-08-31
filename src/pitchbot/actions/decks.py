from __future__ import annotations

from pitchbot.actions.models import DeckIndustry, DeckPreview, DeckRequest, DeckSlide
from pitchbot.adapters import ArtifactAdapter, Clock, SystemClock

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
_ALLOWED_FEATURES = {
    "catalog",
    "online-payments",
    "inventory",
    "whatsapp",
    "multilingual",
}


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

    async def create(self, request: DeckRequest) -> DeckPreview:
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


def _feature_label(feature: str) -> str:
    return {
        "catalog": "Structured product catalog",
        "online-payments": "Reviewed online payment flow",
        "inventory": "Inventory visibility",
        "whatsapp": "Policy-approved WhatsApp inquiry path",
        "multilingual": "English and Hindi content support",
    }[feature]
