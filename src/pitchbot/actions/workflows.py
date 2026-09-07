from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from pitchbot.actions.callbacks import CallbackService
from pitchbot.actions.deck_content import phrases_for
from pitchbot.actions.decks import DeckService
from pitchbot.actions.models import (
    ActionAuthorizationContext,
    ActionPreviewResult,
    AuthorizationStatus,
    CallbackAgenda,
    CallbackRequest,
    CallbackStatus,
    DeckIndustry,
    DeckRequest,
    FollowUpSummary,
)
from pitchbot.actions.policy import ActionPolicy
from pitchbot.actions.summary_text import localised_timeline, stated_budget
from pitchbot.adapters import Clock, EphemeralOperationStore, WhatsAppAdapter
from pitchbot.domain import DEFAULT_TIMEZONE, ActionType, LanguageCode


def agenda_for(follow_up: FollowUpSummary) -> CallbackAgenda:
    """What the next call is actually about, given how far this one got.

    ``preview_callback`` hardcoded :attr:`CallbackAgenda.WEBSITE_DISCOVERY`, so every
    callback this product has ever arranged claimed to be about discovering what the buyer
    needs - including callbacks with buyers who had already stated their vertical, their
    feature list, their budget and their deadline. The other two members of the enum
    appeared **nowhere** outside their own definition: two thirds of a modelled concept,
    dead.

    The agenda a buyer is told about is the promise the next call has to keep, so it is
    read from the same minimised summary the deck and the follow-up message are built from
    rather than guessed.
    """

    if follow_up.budget_summary or follow_up.timeline_summary:
        # They have named money or a date. The next conversation is about a number.
        return CallbackAgenda.PROPOSAL_REVIEW
    if follow_up.requested_features:
        return CallbackAgenda.REQUIREMENTS_REVIEW
    return CallbackAgenda.WEBSITE_DISCOVERY


class ActionWorkflowService:
    def __init__(
        self,
        *,
        policy: ActionPolicy,
        callbacks: CallbackService,
        decks: DeckService,
        whatsapp: WhatsAppAdapter,
        clock: Clock,
        callback_timezone: str = DEFAULT_TIMEZONE,
    ) -> None:
        self._policy = policy
        self._callbacks = callbacks
        self._decks = decks
        self._whatsapp = whatsapp
        self._clock = clock
        self._callback_timezone = callback_timezone

    async def preview_whatsapp(
        self,
        *,
        session_id: UUID,
        follow_up: FollowUpSummary,
        context: ActionAuthorizationContext,
        operation_id: UUID,
    ) -> ActionPreviewResult:
        decision = self._policy.authorize(ActionType.WHATSAPP_PREVIEW, context)
        if decision.status is AuthorizationStatus.BLOCKED:
            return ActionPreviewResult(
                decision=decision, label="WhatsApp preview blocked by policy."
            )
        message = self._render_follow_up(follow_up)
        result = await self._whatsapp.send_message(
            f"synthetic:{follow_up.lead_id}",
            message,
            f"simulator:{session_id}:whatsapp:{operation_id}",
        )
        return ActionPreviewResult(
            decision=decision,
            label="Mock WhatsApp preview prepared; nothing was sent.",
            provider_reference=result.provider_reference,
        )

    async def preview_callback(
        self,
        *,
        session_id: UUID,
        lead_id: UUID,
        delay_minutes: int,
        follow_up: FollowUpSummary,
        context: ActionAuthorizationContext,
        operation_id: UUID,
        requested_at: datetime,
    ) -> ActionPreviewResult:
        """Arrange the next call, about what this one actually established.

        Takes the same minimised summary the deck and the WhatsApp follow-up are built
        from, for the same reason `preview_deck` does: one place decides what a
        conversation may emit. Before this it took no conversation input at all beyond a
        delay, so it sent the scheduler a fixed agenda and a fixed timezone for every
        buyer.
        """

        decision = self._policy.authorize(ActionType.CALLBACK_SCHEDULE, context)
        if decision.status is AuthorizationStatus.BLOCKED:
            return ActionPreviewResult(
                decision=decision, label="Callback preview blocked by policy."
            )
        request = CallbackRequest(
            lead_id=lead_id,
            callback_id=f"sim-{session_id.hex}-{operation_id.hex}",
            run_at=requested_at + timedelta(minutes=delay_minutes),
            timezone=self._callback_timezone,
            agenda=agenda_for(follow_up),
            idempotency_key=f"simulator:{session_id}:callback:{operation_id}",
        )
        callback = await self._callbacks.schedule(request, context)
        if callback.status is CallbackStatus.BLOCKED:
            return ActionPreviewResult(
                decision=decision.model_copy(
                    update={
                        "status": AuthorizationStatus.BLOCKED,
                        "reasons": callback.block_reasons,
                    }
                ),
                label="Callback preview blocked.",
                callback=callback,
            )
        return ActionPreviewResult(
            decision=decision,
            label="Mock callback scheduled in memory; no real callback was created.",
            provider_reference=callback.provider_reference,
            callback=callback,
        )

    async def preview_deck(
        self,
        *,
        session_id: UUID,
        lead_id: UUID,
        industry: DeckIndustry,
        language: LanguageCode,
        follow_up: FollowUpSummary,
        context: ActionAuthorizationContext,
        operation_id: UUID,
    ) -> ActionPreviewResult:
        """Build the deck from the same minimised summary WhatsApp already receives.

        Taking a `FollowUpSummary` rather than a bare feature tuple means there is exactly
        one place that decides what a conversation may emit - `build_follow_up` - so the
        deck cannot widen it. Before PR 54 this took only the features and the buyer's
        budget and timing never reached a slide.
        """

        decision = self._policy.authorize(ActionType.ARTIFACT_PREVIEW, context)
        if decision.status is AuthorizationStatus.BLOCKED:
            return ActionPreviewResult(
                decision=decision, label="Artifact preview blocked by policy."
            )
        deck = await self._decks.create(
            DeckRequest(
                lead_id=lead_id,
                deck_id=f"sim-{session_id.hex}-{operation_id.hex}",
                industry=industry,
                language=language,
                requested_features=follow_up.requested_features,
                budget_summary=follow_up.budget_summary,
                timeline_summary=follow_up.timeline_summary,
                idempotency_key=f"simulator:{session_id}:deck:{operation_id}",
            )
        )
        return ActionPreviewResult(
            decision=decision,
            label="Structured sample-deck preview generated in memory.",
            deck=deck,
        )

    async def cleanup_session(self, session_id: UUID) -> None:
        resource_prefix = f"sim-{session_id.hex}-"
        operation_prefix = f"simulator:{session_id}:"
        await self._callbacks.remove_by_prefix(resource_prefix, f"{operation_prefix}callback:")
        await self._decks.remove_by_prefix(resource_prefix, f"{operation_prefix}deck:")
        if isinstance(self._whatsapp, EphemeralOperationStore):
            self._whatsapp.clear_operations(f"{operation_prefix}whatsapp:")

    @staticmethod
    def _render_follow_up(follow_up: FollowUpSummary) -> str:
        """The message a buyer receives after the call, in the language they spoke.

        This branch and :meth:`preview_deck` are handed the identical minimised summary,
        and until now only the deck used it properly. The message was assembled from raw
        catalogue keys in hardcoded English, and dropped the budget entirely - so a Telugu
        buyer who said *"మా బడ్జెట్ రెండు లక్షలు, మూడు నెలల్లో"* was sent
        ``Business: apparel | Timeline: 3 months`` with no budget in it at all.

        Rendering from the same phrase table the deck uses means adding a language cannot
        leave one artefact behind. A line is omitted when the fact was never stated: a
        slide has a fixed layout and fills the row with ``unstated``, a message is a list
        of what is known.
        """

        phrases = phrases_for(follow_up.language)
        parts = [phrases.follow_up_intro]
        if follow_up.business_type:
            business = phrases.industry_name.get(follow_up.business_type, follow_up.business_type)
            parts.append(f"{phrases.business_label}: {business}")
        if follow_up.requested_features:
            labels = ", ".join(
                phrases.feature_label.get(item, item) for item in follow_up.requested_features
            )
            parts.append(f"{phrases.features_label}: {labels}")
        budget = stated_budget(follow_up.budget_summary)
        if budget:
            parts.append(f"{phrases.budget_label}: {budget}")
        timeline = localised_timeline(follow_up.timeline_summary, phrases)
        if timeline:
            parts.append(f"{phrases.timeline_label}: {timeline}")
        if follow_up.next_steps:
            # The buyer's own next steps are allowlisted English identifiers, so the
            # localised copy is shown instead - exactly as the deck's closing slide does.
            parts.append(f"{phrases.next_label}: {', '.join(phrases.next_steps)}")
        return " | ".join(parts)
