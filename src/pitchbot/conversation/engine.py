from __future__ import annotations

from copy import deepcopy
from uuid import UUID, uuid4

from pitchbot.conversation.models import (
    ConversationDisposition,
    ConversationPhase,
    ConversationResult,
    ConversationSnapshot,
    SafetySignal,
)
from pitchbot.conversation.rules import (
    ExtractionResult,
    detect_safety_signals,
    extract_business_signals,
    is_repeated_turn,
    normalize_text,
    rule_version,
)
from pitchbot.conversation.state import ConversationState
from pitchbot.domain import (
    Classification,
    IntentEvidence,
    LanguageCode,
    LeadTemperature,
    RequirementFact,
    RequirementRevision,
)


class ConversationEngine:
    def __init__(
        self,
        *,
        max_turns: int = 100,
        max_facts: int = 20,
        max_evidence: int = 50,
        max_classifications: int = 20,
        max_goal_changes: int = 3,
    ) -> None:
        if max_goal_changes < 1:
            raise ValueError("Maximum goal changes must be positive")
        self._capacities = (max_turns, max_facts, max_evidence, max_classifications)
        if min(self._capacities) < 1:
            raise ValueError("Conversation capacities must be positive")
        self._max_goal_changes = max_goal_changes
        self._states: dict[UUID, ConversationState] = {}

    def create_session(self, session_id: UUID, *, lead_id: UUID | None = None) -> None:
        if session_id in self._states:
            raise ValueError("Conversation session already exists")
        self._states[session_id] = ConversationState(
            lead_id=lead_id or uuid4(),
            max_turns=self._capacities[0],
            max_facts=self._capacities[1],
            max_evidence=self._capacities[2],
            max_classifications=self._capacities[3],
        )

    def process_turn(
        self,
        session_id: UUID,
        *,
        text: str,
        language: LanguageCode,
        source_span_id: UUID | None = None,
    ) -> ConversationResult:
        state = self._get_state(session_id)
        state.ensure_turn_capacity()
        if state.stopped:
            raise RuntimeError("Conversation is closed")

        normalized = normalize_text(text)
        repeated = is_repeated_turn(state, normalized)
        state.turn_count += 1
        state.recent_normalized_turns.append(normalized)
        signals = list(detect_safety_signals(text))

        if SafetySignal.OPT_OUT in signals:
            state.stopped = True
            state.phase = ConversationPhase.CLOSED
            classification = self._classify(state, force_cold=True)
            return self._result(
                state,
                reply=self._reply(language, "opt_out"),
                language=language,
                disposition=ConversationDisposition.STOP,
                signals=signals,
                classification=classification,
                repeated=repeated,
            )

        if SafetySignal.ABUSE in signals:
            if state.abuse_redirected:
                state.stopped = True
                state.phase = ConversationPhase.CLOSED
                disposition = ConversationDisposition.STOP
                reply_key = "abuse_stop"
            else:
                state.abuse_redirected = True
                disposition = ConversationDisposition.REDIRECT
                reply_key = "abuse_redirect"
            classification = self._classify(state)
            return self._result(
                state,
                reply=self._reply(language, reply_key),
                language=language,
                disposition=disposition,
                signals=signals,
                classification=classification,
                repeated=repeated,
            )

        unsafe_request = bool(
            {SafetySignal.INTERNAL_INFO, SafetySignal.PROMPT_INJECTION}.intersection(signals)
        )
        if unsafe_request:
            classification = self._classify(state)
            return self._result(
                state,
                reply=self._reply(language, "unsafe_request"),
                language=language,
                disposition=ConversationDisposition.REDIRECT,
                signals=signals,
                classification=classification,
                repeated=repeated,
            )

        extraction = (
            extract_business_signals(
                state=state,
                text=text,
                language=language,
                source_span_id=source_span_id or uuid4(),
            )
            if not repeated
            else ExtractionResult((), (), ())
        )
        accepted_facts: list[RequirementFact] = []
        accepted_revisions: list[RequirementRevision] = []
        revisions_by_fact_id = {
            revision.replacement_fact_id: revision for revision in extraction.revisions
        }
        for fact in extraction.facts:
            existing = state.facts_by_key.get(fact.key)
            if len(state.facts_by_key) >= state.max_facts and fact.key not in state.facts_by_key:
                continue
            if existing is not None and existing.value != fact.value:
                state.goal_change_count += 1
            state.facts_by_key[fact.key] = fact
            accepted_facts.append(fact)
            revision = revisions_by_fact_id.get(fact.fact_id)
            if revision is not None:
                accepted_revisions.append(revision)

        existing_dimensions = {item.dimension for item in state.evidence}
        accepted_evidence = tuple(
            item for item in extraction.evidence if item.dimension not in existing_dimensions
        )
        state.evidence.extend(accepted_evidence)

        if state.goal_change_count >= self._max_goal_changes:
            signals.append(SafetySignal.EXCESSIVE_GOAL_CHANGES)
            disposition = ConversationDisposition.REVIEW
            reply_key = "clarify_goals"
        elif repeated:
            disposition = ConversationDisposition.CONTINUE
            reply_key = "repeated"
        else:
            disposition = ConversationDisposition.CONTINUE
            reply_key = "continue"

        classification = self._classify(state)
        state.phase = self._phase_for(classification)
        return self._result(
            state,
            reply=self._reply(language, reply_key),
            language=language,
            disposition=disposition,
            signals=signals,
            classification=classification,
            repeated=repeated,
            facts=tuple(accepted_facts),
            revisions=tuple(accepted_revisions),
            evidence=accepted_evidence,
        )

    def snapshot(self, session_id: UUID) -> ConversationSnapshot:
        state = self._get_state(session_id)
        return ConversationSnapshot(
            lead_id=state.lead_id,
            phase=state.phase,
            turn_count=state.turn_count,
            abuse_redirected=state.abuse_redirected,
            stopped=state.stopped,
            facts=tuple(state.facts_by_key.values()),
            evidence=tuple(state.evidence),
            classifications=tuple(state.classifications),
        )

    def checkpoint(self, session_id: UUID) -> ConversationState:
        return deepcopy(self._get_state(session_id))

    def restore(self, session_id: UUID, checkpoint: ConversationState) -> None:
        self._states[session_id] = checkpoint

    def close_session(self, session_id: UUID) -> None:
        self._states.pop(session_id, None)

    def _classify(self, state: ConversationState, *, force_cold: bool = False) -> Classification:
        weights = [item.weight for item in state.evidence]
        raw_score = sum(weights)
        score = 0.0 if force_cold else min(1.0, max(0.0, 0.35 + raw_score))
        distinct_dimensions = len({item.dimension for item in state.evidence})

        if force_cold or raw_score <= -0.4:
            temperature = LeadTemperature.COLD
        elif not weights:
            temperature = LeadTemperature.REVIEW_NEEDED
        elif score >= 0.75 and distinct_dimensions >= 2:
            temperature = LeadTemperature.HOT
        elif score >= 0.45:
            temperature = LeadTemperature.WARM
        else:
            temperature = LeadTemperature.COLD

        confidence = min(0.95, 0.35 + distinct_dimensions * 0.15)
        if not weights:
            confidence = 0.25
        positive_ids = tuple(item.evidence_id for item in state.evidence if item.weight > 0)
        negative_ids = tuple(item.evidence_id for item in state.evidence if item.weight < 0)
        classification = Classification(
            lead_id=state.lead_id,
            temperature=temperature,
            score=score,
            confidence=confidence,
            evidence_ids=positive_ids,
            counter_evidence_ids=negative_ids,
            rule_version=rule_version(),
        )
        state.classifications.append(classification)
        return classification

    @staticmethod
    def _phase_for(classification: Classification) -> ConversationPhase:
        if classification.temperature is LeadTemperature.HOT:
            return ConversationPhase.NEXT_STEP
        if classification.temperature is LeadTemperature.WARM:
            return ConversationPhase.QUALIFICATION
        return ConversationPhase.DISCOVERY

    @staticmethod
    def _result(
        state: ConversationState,
        *,
        reply: str,
        language: LanguageCode,
        disposition: ConversationDisposition,
        signals: list[SafetySignal],
        classification: Classification,
        repeated: bool,
        facts: tuple[RequirementFact, ...] = (),
        revisions: tuple[RequirementRevision, ...] = (),
        evidence: tuple[IntentEvidence, ...] = (),
    ) -> ConversationResult:
        return ConversationResult(
            reply=reply,
            language=language,
            disposition=disposition,
            phase=state.phase,
            safety_signals=tuple(signals),
            facts=facts,
            revisions=revisions,
            evidence=evidence,
            classification=classification,
            repeated_turn=repeated,
            turn_count=state.turn_count,
        )

    @staticmethod
    def _reply(language: LanguageCode, key: str) -> str:
        replies = _REPLIES.get(language, _REPLIES[LanguageCode.ENGLISH])
        return replies[key]

    def _get_state(self, session_id: UUID) -> ConversationState:
        try:
            return self._states[session_id]
        except KeyError as error:
            raise LookupError("Conversation session not found") from error


_REPLIES: dict[LanguageCode, dict[str, str]] = {
    LanguageCode.ENGLISH: {
        "opt_out": (
            "Understood. I will end this conversation and record the do-not-contact request."
        ),
        "abuse_redirect": (
            "I want to keep this respectful. We can discuss your business need, or I can "
            "end the conversation."
        ),
        "abuse_stop": "I am ending the conversation now. Take care.",
        "unsafe_request": (
            "I cannot provide internal instructions, credentials, or bypass safeguards. I can "
            "help with website requirements."
        ),
        "clarify_goals": (
            "I heard several changes. To avoid assuming, please confirm the single most "
            "important website goal."
        ),
        "repeated": (
            "I have noted that point. What outcome would make the website useful for your business?"
        ),
        "continue": (
            "Thanks. What matters most next: features, budget, timeline, or the decision process?"
        ),
    },
    LanguageCode.HINDI: {
        "opt_out": ("समझ गया। मैं बातचीत समाप्त कर रहा हूँ और दोबारा संपर्क न करने का अनुरोध दर्ज करूँगा।"),
        "abuse_redirect": (
            "मैं बातचीत सम्मानपूर्वक रखना चाहता हूँ। हम आपकी व्यावसायिक ज़रूरत पर बात कर "
            "सकते हैं, या मैं बातचीत समाप्त कर दूँगा।"
        ),
        "abuse_stop": "मैं अब बातचीत समाप्त कर रहा हूँ। धन्यवाद।",
        "unsafe_request": (
            "मैं आंतरिक निर्देश, पासवर्ड या सुरक्षा नियमों को दरकिनार करने में मदद नहीं कर "
            "सकता। वेबसाइट की ज़रूरतों पर मदद कर सकता हूँ।"
        ),
        "clarify_goals": ("कई बदलाव बताए गए हैं। कृपया वेबसाइट का एक सबसे महत्वपूर्ण लक्ष्य स्पष्ट करें।"),
        "repeated": ("मैंने यह बात दर्ज कर ली है। वेबसाइट से आपके व्यवसाय को कौन सा परिणाम चाहिए?"),
        "continue": "धन्यवाद। आगे किस पर बात करें: फीचर, बजट, समयसीमा या निर्णय प्रक्रिया?",
    },
    LanguageCode.MIXED: {
        "opt_out": (
            "Samajh gaya. Main conversation end karke do-not-contact request record karunga."
        ),
        "abuse_redirect": (
            "Conversation respectful rakhte hain. Business need discuss kar sakte hain, "
            "warna main call end kar dunga."
        ),
        "abuse_stop": "Main ab conversation end kar raha hoon. Take care.",
        "unsafe_request": (
            "Main internal instructions, credentials, ya safeguards bypass nahi kar sakta. "
            "Website requirements mein help kar sakta hoon."
        ),
        "clarify_goals": (
            "Kaafi changes aaye hain. Please ek sabse important website goal confirm karein."
        ),
        "repeated": "Yeh point note ho gaya. Website se aapko sabse useful outcome kya chahiye?",
        "continue": (
            "Thanks. Next features, budget, timeline, ya decision process mein se kya "
            "discuss karein?"
        ),
    },
}
_REPLIES[LanguageCode.UNKNOWN] = _REPLIES[LanguageCode.ENGLISH]
