from __future__ import annotations

import re
from collections.abc import Mapping
from uuid import UUID

from pitchbot.actions.models import (
    ActionAuthorizationContext,
    ActionDecision,
    AuthorizationStatus,
    BlockReason,
    FollowUpSummary,
)
from pitchbot.adapters.clock import Clock, SystemClock
from pitchbot.domain import ActionType, JsonValue, LanguageCode, LeadTemperature

_NEXT_STEPS = {
    "Review the synthetic preview",
    "Confirm requirements",
    "Review proposal",
}
_BUSINESS_TYPES = {"apparel", "toys", "books", "food", "import-export", "plastics"}
_FEATURES = {"catalog", "online-payments", "inventory", "whatsapp", "multilingual"}
_BUDGET = re.compile(r"(?:budget|बजट|₹|rs\.?|inr)[\w\s₹,.-]{1,90}", re.IGNORECASE)
_TIMELINE = re.compile(
    r"(?:near-term|\d{1,3}\s+(?:day|days|week|weeks|month|months))",
    re.IGNORECASE,
)


class ActionPolicy:
    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()

    def authorize(
        self,
        action_type: ActionType,
        context: ActionAuthorizationContext,
    ) -> ActionDecision:
        reasons: list[BlockReason] = []
        policy = context.contact_policy

        if not context.disclosure_delivered:
            reasons.append(BlockReason.DISCLOSURE_MISSING)
        if not context.consent_granted:
            reasons.append(BlockReason.CONSENT_MISSING)
        if policy.opted_out:
            reasons.append(BlockReason.OPTED_OUT)
        if not policy.outreach_allowed:
            reasons.append(BlockReason.OUTREACH_NOT_ALLOWED)
        if not policy.allowlisted:
            reasons.append(BlockReason.NOT_ALLOWLISTED)
        if not policy.dnd_check_passed:
            reasons.append(BlockReason.DND_NOT_PASSED)
        if not policy.calling_hours_check_passed:
            reasons.append(BlockReason.CALLING_HOURS_NOT_PASSED)
        if context.conversation_disposition != "continue":
            reasons.append(BlockReason.CONVERSATION_NOT_ELIGIBLE)
        if context.temperature is LeadTemperature.REVIEW_NEEDED:
            reasons.append(BlockReason.CLASSIFICATION_REVIEW)
        elif context.temperature is LeadTemperature.COLD:
            reasons.append(BlockReason.CLASSIFICATION_INELIGIBLE)
        if context.used_actions >= context.max_actions:
            reasons.append(BlockReason.QUOTA_EXCEEDED)

        return ActionDecision(
            status=(AuthorizationStatus.BLOCKED if reasons else AuthorizationStatus.APPROVED),
            action_type=action_type,
            reasons=tuple(reasons),
            decided_at=self._clock.now(),
        )


def build_follow_up(
    *,
    lead_id: UUID,
    language: LanguageCode,
    facts: Mapping[str, JsonValue],
    next_steps: tuple[str, ...] = (),
) -> FollowUpSummary:
    business_type = _allowlisted_text(facts.get("business_type"), _BUSINESS_TYPES)
    budget = _pattern_text(facts.get("budget_stated"), _BUDGET)
    timeline = _pattern_text(facts.get("timeline"), _TIMELINE)
    features_value = facts.get("requested_features")
    features = _features(features_value)
    minimized_steps = tuple(step.strip() for step in next_steps if step.strip() in _NEXT_STEPS)[:5]
    return FollowUpSummary(
        lead_id=lead_id,
        language=language,
        business_type=business_type,
        requested_features=features,
        budget_summary=budget,
        timeline_summary=timeline,
        next_steps=minimized_steps,
    )


def _allowlisted_text(value: JsonValue | None, allowed: set[str]) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped if stripped in allowed else None


def _pattern_text(value: JsonValue | None, pattern: re.Pattern[str]) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped if pattern.fullmatch(stripped) else None


def _features(value: JsonValue | None) -> tuple[str, ...]:
    if not isinstance(value, str):
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip() in _FEATURES)[:10]
