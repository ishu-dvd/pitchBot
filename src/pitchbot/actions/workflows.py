from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from pitchbot.actions.callbacks import CallbackService
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
from pitchbot.adapters import Clock, EphemeralOperationStore, WhatsAppAdapter
from pitchbot.domain import ActionType, LanguageCode


class ActionWorkflowService:
    def __init__(
        self,
        *,
        policy: ActionPolicy,
        callbacks: CallbackService,
        decks: DeckService,
        whatsapp: WhatsAppAdapter,
        clock: Clock,
    ) -> None:
        self._policy = policy
        self._callbacks = callbacks
        self._decks = decks
        self._whatsapp = whatsapp
        self._clock = clock

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
        context: ActionAuthorizationContext,
        operation_id: UUID,
        requested_at: datetime,
    ) -> ActionPreviewResult:
        decision = self._policy.authorize(ActionType.CALLBACK_SCHEDULE, context)
        if decision.status is AuthorizationStatus.BLOCKED:
            return ActionPreviewResult(
                decision=decision, label="Callback preview blocked by policy."
            )
        request = CallbackRequest(
            lead_id=lead_id,
            callback_id=f"sim-{session_id.hex}-{operation_id.hex}",
            run_at=requested_at + timedelta(minutes=delay_minutes),
            timezone="UTC",
            agenda=CallbackAgenda.WEBSITE_DISCOVERY,
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
        features: tuple[str, ...],
        context: ActionAuthorizationContext,
        operation_id: UUID,
    ) -> ActionPreviewResult:
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
                requested_features=features,
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
        parts = ["Synthetic PitchBot follow-up"]
        if follow_up.business_type:
            parts.append(f"Business: {follow_up.business_type}")
        if follow_up.requested_features:
            parts.append(f"Features: {', '.join(follow_up.requested_features)}")
        if follow_up.timeline_summary:
            parts.append(f"Timeline: {follow_up.timeline_summary}")
        if follow_up.next_steps:
            parts.append(f"Next: {', '.join(follow_up.next_steps)}")
        return " | ".join(parts)
