from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from pitchbot.conversation.models import SafetySignal
from pitchbot.conversation.state import ConversationState
from pitchbot.domain import IntentEvidence, LanguageCode, RequirementFact, RequirementRevision

_RULE_VERSION = "conversation-rules-v1"

_OPT_OUT_PHRASES = (
    "do not call",
    "don't call",
    "dont call",
    "stop calling",
    "do not contact",
    "don't contact",
    "dont contact",
    "do not phone",
    "don't phone",
    "dont phone",
    "remove my number",
    "never call",
    "call mat",
    "phone mat",
    "dobara call mat",
    "बंद करो",
    "कॉल मत",
    "फोन मत",
    "दोबारा कॉल मत",
)
_ABUSE_TERMS = (
    "idiot",
    "stupid",
    "moron",
    "shut up",
    "बेवकूफ",
    "चुप रह",
    "bakwas",
    "bewakoof",
)
_INTERNAL_INFO_PHRASES = (
    "api key",
    "password",
    "secret key",
    "system prompt",
    "internal instruction",
    "developer message",
    "hidden prompt",
    "training data",
    "पासवर्ड",
    "गुप्त निर्देश",
)
_PROMPT_INJECTION_PHRASES = (
    "ignore previous instructions",
    "ignore all instructions",
    "forget your instructions",
    "jailbreak",
    "act as unrestricted",
    "bypass your rules",
    "निर्देश भूल",
    "नियम भूल",
)

_BUSINESS_TYPES: dict[str, tuple[str, ...]] = {
    "apparel": ("apparel", "clothing", "clothes", "garment", "कपड़े", "kapde"),
    "toys": ("toy", "toys", "खिलौने", "khilone"),
    "books": ("book", "books", "किताब", "kitab"),
    "food": ("food", "restaurant", "bakery", "खाना", "restaurant"),
    "import-export": ("import export", "import-export", "निर्यात", "आयात"),
    "plastics": ("plastic", "plastics", "प्लास्टिक"),
}
_FEATURES: dict[str, tuple[str, ...]] = {
    "catalog": ("catalog", "catalogue", "कैटलॉग"),
    "online-payments": ("payment", "checkout", "pay online", "भुगतान"),
    "inventory": ("inventory", "stock management", "इन्वेंटरी"),
    "whatsapp": ("whatsapp", "व्हाट्सऐप"),
    "multilingual": ("multilingual", "bilingual", "hindi and english", "हिंदी और अंग्रेजी"),
}
_POSITIVE_EVIDENCE: tuple[tuple[str, float, tuple[str, ...]], ...] = (
    ("budget", 0.25, ("budget", "₹", "rs ", "rupees", "बजट")),
    ("timeline", 0.25, ("this week", "this month", "days", "weeks", "जल्दी", "इस महीने")),
    (
        "decision",
        0.30,
        ("ready to start", "let's start", "lets start", "send proposal", "शुरू करें", "proposal bhejo"),
    ),
    ("next-step", 0.20, ("demo", "meeting", "callback", "call back", "sample", "डेमो", "नमूना")),
)
_NEGATIVE_EVIDENCE: tuple[tuple[str, float, tuple[str, ...]], ...] = (
    ("rejection", -0.70, ("not interested", "no interest", "interested nahi", "रुचि नहीं")),
    ("no-need", -0.50, ("do not need", "don't need", "no website", "नहीं चाहिए")),
)
_BUDGET_PATTERN = re.compile(
    r"(?:budget(?:\s+is)?|बजट|₹|rs\.?|inr)\s*[:=-]?\s*(₹|rs\.?|inr)?\s*([0-9][0-9,]*(?:\s*(?:k|lakh|लाख))?)",
    re.IGNORECASE,
)
_TIMELINE_PATTERN = re.compile(
    r"\b(?:in|within)\s+(\d{1,3}\s+(?:day|days|week|weeks|month|months))\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    facts: tuple[RequirementFact, ...]
    revisions: tuple[RequirementRevision, ...]
    evidence: tuple[IntentEvidence, ...]


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = "".join(
        character
        if character.isalnum()
        or character.isspace()
        or character in "₹'-"
        or unicodedata.category(character).startswith("M")
        else " "
        for character in normalized
    )
    return " ".join(normalized.split())


def detect_safety_signals(text: str) -> tuple[SafetySignal, ...]:
    normalized = normalize_text(text)
    compact = normalized.replace(" ", "")
    signals: list[SafetySignal] = []
    if _contains_any_form(normalized, compact, _OPT_OUT_PHRASES):
        signals.append(SafetySignal.OPT_OUT)
    if _contains_any_form(normalized, compact, _ABUSE_TERMS):
        signals.append(SafetySignal.ABUSE)
    if _contains_any_form(normalized, compact, _INTERNAL_INFO_PHRASES):
        signals.append(SafetySignal.INTERNAL_INFO)
    if _contains_any_form(normalized, compact, _PROMPT_INJECTION_PHRASES):
        signals.append(SafetySignal.PROMPT_INJECTION)
    return tuple(signals)


def is_repeated_turn(state: ConversationState, normalized_text: str) -> bool:
    return normalized_text in state.recent_normalized_turns


def extract_business_signals(
    *,
    state: ConversationState,
    text: str,
    language: LanguageCode,
    source_span_id: UUID,
) -> ExtractionResult:
    del language  # Language and accent are never classification evidence.
    normalized = normalize_text(text)
    candidates: dict[str, str] = {}

    business_type = _match_named_value(normalized, _BUSINESS_TYPES)
    if business_type is not None:
        candidates["business_type"] = business_type

    features = tuple(
        name for name, phrases in _FEATURES.items() if _contains_any(normalized, phrases)
    )
    if features:
        candidates["requested_features"] = ",".join(features)

    budget_match = _BUDGET_PATTERN.search(normalized)
    if budget_match:
        candidates["budget_stated"] = budget_match.group(0)[:100]

    timeline_match = _TIMELINE_PATTERN.search(normalized)
    if timeline_match:
        candidates["timeline"] = timeline_match.group(1)
    elif _contains_any(normalized, ("this week", "this month", "इस महीने", "जल्दी")):
        candidates["timeline"] = "near-term"

    facts: list[RequirementFact] = []
    revisions: list[RequirementRevision] = []
    for key, value in candidates.items():
        existing = state.facts_by_key.get(key)
        if existing is not None and existing.value == value:
            continue
        fact = RequirementFact(
            lead_id=state.lead_id,
            key=key,
            value=value,
            source_span_ids=(source_span_id,),
            confidence=0.85,
        )
        facts.append(fact)
        if existing is not None:
            revisions.append(
                RequirementRevision(
                    lead_id=state.lead_id,
                    key=key,
                    previous_fact_id=existing.fact_id,
                    replacement_fact_id=fact.fact_id,
                    reason="Buyer supplied a different explicit value.",
                )
            )

    evidence = _extract_evidence(state.lead_id, normalized, source_span_id)
    return ExtractionResult(tuple(facts), tuple(revisions), evidence)


def rule_version() -> str:
    return _RULE_VERSION


def _extract_evidence(
    lead_id: UUID, normalized: str, source_span_id: UUID
) -> tuple[IntentEvidence, ...]:
    evidence: list[IntentEvidence] = []
    for dimension, weight, phrases in (*_POSITIVE_EVIDENCE, *_NEGATIVE_EVIDENCE):
        if _contains_any(normalized, phrases):
            evidence.append(
                IntentEvidence(
                    lead_id=lead_id,
                    dimension=dimension,
                    weight=weight,
                    reason=f"Buyer explicitly expressed {dimension} information.",
                    source_span_ids=(source_span_id,),
                )
            )
    return tuple(evidence)


def _contains_any(text: str, phrases: Iterable[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def _contains_any_form(text: str, compact: str, phrases: Iterable[str]) -> bool:
    return any(phrase in text or phrase.replace(" ", "") in compact for phrase in phrases)


def _match_named_value(text: str, choices: dict[str, tuple[str, ...]]) -> str | None:
    for value, phrases in choices.items():
        if _contains_any(text, phrases):
            return value
    return None
