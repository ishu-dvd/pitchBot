from __future__ import annotations

import secrets
from copy import deepcopy
from hashlib import sha256
from hmac import new as new_hmac
from uuid import UUID, uuid4

from pitchbot.conversation.models import (
    ConversationDisposition,
    ConversationPhase,
    ConversationResult,
    ConversationSnapshot,
    ConversationStateCheckpoint,
    SafetySignal,
)
from pitchbot.conversation.planning import (
    TurnUnderstanding,
    plan_reply,
    render_reply,
    understanding_from_facts,
)
from pitchbot.conversation.rules import (
    ExtractionResult,
    detect_safety_signals,
    extract_business_signals,
    is_repeated_turn,
    normalize_text,
    normalized_turn_digest,
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
        turn_digest_key: bytes | None = None,
    ) -> None:
        if max_goal_changes < 1:
            raise ValueError("Maximum goal changes must be positive")
        self._capacities = (max_turns, max_facts, max_evidence, max_classifications)
        if min(self._capacities) < 1:
            raise ValueError("Conversation capacities must be positive")
        if turn_digest_key is not None and len(turn_digest_key) < 32:
            raise ValueError("Turn digest key must contain at least 32 bytes")
        self._max_goal_changes = max_goal_changes
        self._turn_digest_key = turn_digest_key or secrets.token_bytes(32)
        self._digest_key_id = sha256(self._turn_digest_key).hexdigest()
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
            max_goal_changes=self._max_goal_changes,
            digest_key_id=self._digest_key_id,
        )

    def operation_fingerprint(self, session_id: UUID, canonical_request: bytes) -> str:
        if not canonical_request:
            raise ValueError("Canonical operation request must not be empty")
        return new_hmac(
            self._turn_digest_key,
            b"pitchbot.operation.v1\0" + session_id.bytes + canonical_request,
            sha256,
        ).hexdigest()

    def process_turn(
        self,
        session_id: UUID,
        *,
        text: str,
        language: LanguageCode,
        source_span_id: UUID | None = None,
        understanding: TurnUnderstanding | None = None,
    ) -> ConversationResult:
        """Advance one turn.

        ``understanding`` is an optional richer reading of this turn, produced elsewhere -
        today by an opt-in local model. It only ever influences which slot is acknowledged
        and asked for. It is accepted *after* safety detection has already run below, so it
        cannot affect an opt-out, an abuse redirect, or a refused extraction attempt: those
        paths return before it is consulted.
        """

        state = self._get_state(session_id)
        state.ensure_turn_capacity()
        if state.stopped:
            raise RuntimeError("Conversation is closed")

        normalized = normalize_text(text)
        repeated = is_repeated_turn(
            state,
            normalized,
            digest_key=self._turn_digest_key,
            session_id=session_id,
        )
        state.turn_count += 1
        state.recent_turn_digests.append(
            normalized_turn_digest(
                normalized,
                digest_key=self._turn_digest_key,
                session_id=session_id,
            )
        )
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

        if state.goal_change_count >= state.max_goal_changes:
            signals.append(SafetySignal.EXCESSIVE_GOAL_CHANGES)
            disposition = ConversationDisposition.REVIEW
            reply = self._reply(language, "clarify_goals")
        else:
            disposition = ConversationDisposition.CONTINUE
            # The reply is planned from the slots this conversation has actually filled,
            # rather than being one fixed sentence. `understanding` lets a caller supply a
            # richer reading of the turn; with none, the engine's own extracted facts are
            # used, which is the default and needs no model.
            if understanding is not None:
                state.understood_slot_keys.update(slot.value for slot in understanding.filled_now)
            plan = plan_reply(
                self._understanding_for(state, accepted_facts, understanding),
                repeated=repeated,
                asked_counts=state.asked_slot_counts,
            )
            if plan.ask is not None:
                # Counted here, not in the planner, because only the engine knows a reply
                # was actually sent. A plan that is computed and discarded must not make
                # the agent believe it has already asked.
                state.asked_slot_counts[plan.ask.value] = (
                    state.asked_slot_counts.get(plan.ask.value, 0) + 1
                )
            reply = render_reply(plan, language, repeated=repeated)

        classification = self._classify(state)
        state.phase = self._phase_for(classification)
        return self._result(
            state,
            reply=reply,
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

    def export_checkpoint(self, session_id: UUID) -> ConversationStateCheckpoint:
        state = self._get_state(session_id)
        return ConversationStateCheckpoint(
            checkpoint_schema_version="1",
            lead_id=state.lead_id,
            max_turns=state.max_turns,
            max_facts=state.max_facts,
            max_evidence=state.max_evidence,
            max_classifications=state.max_classifications,
            max_goal_changes=state.max_goal_changes,
            digest_key_id=state.digest_key_id,
            phase=state.phase,
            turn_count=state.turn_count,
            abuse_redirected=state.abuse_redirected,
            stopped=state.stopped,
            recent_turn_digests=tuple(state.recent_turn_digests),
            facts=tuple(state.facts_by_key.values()),
            evidence=tuple(state.evidence),
            classifications=tuple(state.classifications),
            goal_change_count=state.goal_change_count,
        )

    def restore_checkpoint(
        self,
        session_id: UUID,
        checkpoint: ConversationStateCheckpoint,
    ) -> None:
        if session_id in self._states:
            raise ValueError("Conversation session already exists")
        self._states[session_id] = self._state_from_checkpoint(checkpoint)

    def replace_checkpoint(
        self,
        session_id: UUID,
        checkpoint: ConversationStateCheckpoint,
    ) -> None:
        if session_id not in self._states:
            raise LookupError("Conversation session not found")
        self._states[session_id] = self._state_from_checkpoint(checkpoint)

    def _state_from_checkpoint(
        self,
        checkpoint: ConversationStateCheckpoint,
    ) -> ConversationState:
        if checkpoint.digest_key_id != self._digest_key_id:
            raise ValueError("Conversation checkpoint uses a different digest key")
        state = ConversationState(
            lead_id=checkpoint.lead_id,
            max_turns=checkpoint.max_turns,
            max_facts=checkpoint.max_facts,
            max_evidence=checkpoint.max_evidence,
            max_classifications=checkpoint.max_classifications,
            max_goal_changes=checkpoint.max_goal_changes,
            digest_key_id=checkpoint.digest_key_id,
            phase=checkpoint.phase,
            turn_count=checkpoint.turn_count,
            abuse_redirected=checkpoint.abuse_redirected,
            stopped=checkpoint.stopped,
            facts_by_key={fact.key: fact for fact in checkpoint.facts},
            goal_change_count=checkpoint.goal_change_count,
        )
        state.recent_turn_digests.extend(checkpoint.recent_turn_digests)
        state.evidence.extend(checkpoint.evidence)
        state.classifications.extend(checkpoint.classifications)
        return state

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
    def _understanding_for(
        state: ConversationState,
        accepted_facts: list[RequirementFact],
        supplied: TurnUnderstanding | None,
    ) -> TurnUnderstanding:
        """Merge everything known about the slots, whatever found it.

        The rules and a model are additive, never exclusive: a model that reads a budget the
        regex missed must not also erase a business type the regex caught, and a slot either
        source has ever filled stays filled.
        """

        base = understanding_from_facts(
            state.facts_by_key,
            (fact.key for fact in accepted_facts),
        )
        remembered = understanding_from_facts(state.understood_slot_keys)
        if supplied is None:
            return TurnUnderstanding(
                known_slots=base.known_slots | remembered.known_slots,
                filled_now=base.filled_now,
            )
        return TurnUnderstanding(
            known_slots=base.known_slots | remembered.known_slots | supplied.known_slots,
            filled_now=base.filled_now | supplied.filled_now,
            intent=supplied.intent,
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
    },
}
_REPLIES[LanguageCode.UNKNOWN] = _REPLIES[LanguageCode.ENGLISH]
