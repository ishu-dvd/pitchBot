from __future__ import annotations

import secrets
from copy import deepcopy
from hashlib import sha256
from hmac import new as new_hmac
from uuid import UUID, uuid4

from pitchbot.conversation.language import decide_language, detect_language
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
    detect_intent,
    detect_safety_signals,
    extract_business_signals,
    is_repeated_turn,
    normalize_text,
    normalized_turn_digest,
    rule_version,
)
from pitchbot.conversation.state import ConversationState
from pitchbot.deliberation.briefing import Briefing, SitePlan, Topic
from pitchbot.domain import (
    Classification,
    Intent,
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
        detect_language_switch: bool = True,
    ) -> None:
        if max_goal_changes < 1:
            raise ValueError("Maximum goal changes must be positive")
        self._capacities = (max_turns, max_facts, max_evidence, max_classifications)
        if min(self._capacities) < 1:
            raise ValueError("Conversation capacities must be positive")
        if turn_digest_key is not None and len(turn_digest_key) < 32:
            raise ValueError("Turn digest key must contain at least 32 bytes")
        self._max_goal_changes = max_goal_changes
        self._detect_language_switch = detect_language_switch
        """Whether a buyer may change the conversation's language by speaking it.

        On by default because it is the behaviour a person expects and the one a call
        needs. Off is for a caller that owns the language itself - a scripted evaluation,
        or an operator console that sets it explicitly - where a detected switch would be
        an unasked-for change to a variable someone else is managing.
        """
        self._turn_digest_key = turn_digest_key or secrets.token_bytes(32)
        self._digest_key_id = sha256(self._turn_digest_key).hexdigest()
        self._states: dict[UUID, ConversationState] = {}
        self._briefings: dict[UUID, Briefing] = {}

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
        transcribed_as: LanguageCode | None = None,
    ) -> ConversationResult:
        """Advance one turn.

        ``transcribed_as`` is the language a transcriber reported for this turn when it
        arrived as speech. It is a *fallback* signal for deciding the conversation's
        language, used only when the text itself says nothing - a turn too short to carry
        script evidence, or one romanised past recognition. It is ranked last on purpose:
        a transcriber that has been given a language to expect reports that language back,
        so on the exact turn a buyer switches it is the least reliable evidence available.

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

        # Resolved before the safety branches, not after, because those branches reply
        # too. A buyer who opts out in Hindi on an English-declared session must be told
        # in Hindi that they will not be contacted again - answering the one turn that
        # ends the relationship in a language they did not use is the worst possible
        # place to get this wrong.
        language, switched = self._resolve_language(
            state, text=text, declared=language, transcribed_as=transcribed_as
        )

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
                language_switched=switched,
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
                language_switched=switched,
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
                language_switched=switched,
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
            self._record_observations(session_id, accepted_facts)
            plan = plan_reply(
                self._understanding_for(state, accepted_facts, understanding, text=text),
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
            reply = render_reply(
                plan,
                language,
                repeated=repeated,
                switched=switched,
                closing_count=state.closing_count,
            )
            if plan.is_closing and plan.intent is not Intent.READY:
                # Counted here for the same reason the asks are: only the engine knows a
                # reply was actually sent, and a plan computed and discarded must not make
                # the agent believe it has already closed.
                state.closing_count += 1

        classification = self._classify(state)
        state.phase = self._phase_for(classification)
        return self._result(
            state,
            reply=reply,
            language=language,
            language_switched=switched,
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
            checkpoint_schema_version="2",
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
            language=state.language,
            declared_language=state.declared_language,
            pending_language=state.pending_language,
            pending_language_count=state.pending_language_count,
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
            language=checkpoint.language,
            declared_language=checkpoint.declared_language,
            pending_language=checkpoint.pending_language,
            pending_language_count=checkpoint.pending_language_count,
        )
        state.recent_turn_digests.extend(checkpoint.recent_turn_digests)
        state.evidence.extend(checkpoint.evidence)
        state.classifications.extend(checkpoint.classifications)
        return state

    def close_session(self, session_id: UUID) -> None:
        self._states.pop(session_id, None)
        # The briefing is per-session state exactly as the conversation state is, so it has
        # to be dropped here too. Leaving it behind would retain every closed conversation's
        # observations for the lifetime of the process - a slow leak that no test of a
        # single session could ever show.
        self._briefings.pop(session_id, None)

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
        language_switched: bool,
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
            language_switched=language_switched,
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

    def _resolve_language(
        self,
        state: ConversationState,
        *,
        text: str,
        declared: LanguageCode,
        transcribed_as: LanguageCode | None = None,
    ) -> tuple[LanguageCode, bool]:
        """Decide which language to answer this turn in, and whether that is a change.

        The caller's ``declared`` language seeds the session and is honoured again
        whenever the caller *changes* it, so an operator who reassigns a live call is
        obeyed at once. Between re-declarations the engine's own decision wins, which is
        what makes this work for a caller that never reads :attr:`~ConversationResult.
        language` back - the HTTP API today. A caller that does read it back sees the two
        agree, so nothing about the existing contract changes until a buyer switches.
        """

        if state.declared_language is LanguageCode.UNKNOWN:
            # Seeding the session, not re-declaring it. This deliberately falls through to
            # detection instead of returning: the caller's opening language is a default,
            # and consuming a whole turn to record it would mean a buyer who asks for Hindi
            # as the very first thing they say is ignored, and one who simply speaks Hindi
            # from the start needs three turns to be heard rather than two.
            state.declared_language = declared
            state.language = declared
        elif declared != state.declared_language:
            # A re-declaration is an instruction, not evidence, so it clears any partial
            # switch: a buyer halfway to Hindi whose operator moves the call to Telugu
            # must not then arrive in Hindi one turn later on the strength of a vote cast
            # before the reassignment.
            switched = declared != state.language
            state.declared_language = declared
            state.language = declared
            state.pending_language = None
            state.pending_language_count = 0
            return declared, switched

        current = state.language
        if not self._detect_language_switch:
            state.language = current
            return current, False
        decision = decide_language(
            current=current,
            reading=detect_language(text, transcribed_as=transcribed_as),
            pending=state.pending_language,
            pending_count=state.pending_language_count,
        )
        state.language = decision.language
        state.pending_language = decision.pending
        state.pending_language_count = decision.pending_count
        return decision.language, decision.switched

    @staticmethod
    def _understanding_for(
        state: ConversationState,
        accepted_facts: list[RequirementFact],
        supplied: TurnUnderstanding | None,
        *,
        text: str,
    ) -> TurnUnderstanding:
        """Merge everything known about the slots, whatever found it.

        The rules and a model are additive, never exclusive: a model that reads a budget the
        regex missed must not also erase a business type the regex caught, and a slot either
        source has ever filled stays filled.

        The buyer's **stance** is merged the same way, and that is a fix rather than a
        refinement. It used to be taken from ``supplied`` alone, so a deployment without the
        optional language model - the default, and the one every test runs in - could never
        observe that a buyer had objected or agreed to buy. Reading it from the rules as
        well means objection handling works with no extra installed at all; a model, when
        present, still wins, because it can read a stance out of a sentence that matches no
        phrase.
        """

        base = understanding_from_facts(
            state.facts_by_key,
            (fact.key for fact in accepted_facts),
            business_type=ConversationEngine._business_type(state),
        )
        remembered = understanding_from_facts(state.understood_slot_keys)
        detected = detect_intent(text)
        if supplied is None:
            return TurnUnderstanding(
                known_slots=base.known_slots | remembered.known_slots,
                filled_now=base.filled_now,
                intent=detected,
                business_type=base.business_type,
            )
        return TurnUnderstanding(
            known_slots=base.known_slots | remembered.known_slots | supplied.known_slots,
            filled_now=base.filled_now | supplied.filled_now,
            # The rules own stance, always. This used to be `supplied.intent or detected`,
            # which let a model that answered `stalling` to every turn - measured, 8/8 on
            # Qwen2.5-0.5B - turn every reply into "answer the stall", including the turn a
            # buyer agreed to buy. A model reads *topics* well and stance badly, so it is
            # asked only for topics and is not consulted here even if a future one offers.
            intent=detected,
            business_type=base.business_type,
        )

    def _record_observations(self, session_id: UUID, accepted: list[RequirementFact]) -> None:
        """Copy this turn's facts into the briefing the slow lane reads.

        One-way on purpose. The engine writes observations and never reads a deliberation
        back into a fact: a plan is what *we* would propose, and letting it flow into the
        buyer's stated requirements would make the agent believe its own suggestions. See
        :mod:`pitchbot.deliberation.briefing` for why ownership is arranged this way.
        """

        if not accepted:
            return
        briefing = self._briefings.setdefault(session_id, Briefing())
        for fact in accepted:
            topic = _TOPICS.get(fact.key)
            if topic is None or not isinstance(fact.value, str) or not fact.value.strip():
                continue
            briefing.observe(topic, fact.value)

    def briefing(self, session_id: UUID) -> Briefing:
        """The shared state for this session, created on first use."""

        return self._briefings.setdefault(session_id, Briefing())

    def site_plan(self, session_id: UUID) -> SitePlan | None:
        """The current plan for this session, or ``None`` if there is not one to trust.

        ``None`` covers three different situations on purpose - never deliberated, still
        deliberating, and deliberated against facts the buyer has since added to. A caller
        that needs to tell them apart should read the briefing; a caller that just wants
        something safe to show should use this.
        """

        briefing = self._briefings.get(session_id)
        if briefing is None:
            return None
        current = briefing.current_deliberation()
        return None if current is None else current.plan

    @staticmethod
    def _business_type(state: ConversationState) -> str | None:
        """The recorded vertical, as a catalogue key, or nothing.

        Read from the accumulated facts rather than from this turn so that a buyer who
        stated their business three turns ago can still be spoken to about it.
        """

        fact = state.facts_by_key.get("business_type")
        if fact is None or not isinstance(fact.value, str):
            return None
        return fact.value

    @staticmethod
    def _reply(language: LanguageCode, key: str) -> str:
        replies = _REPLIES.get(language, _REPLIES[LanguageCode.ENGLISH])
        return replies[key]

    def _get_state(self, session_id: UUID) -> ConversationState:
        try:
            return self._states[session_id]
        except KeyError as error:
            raise LookupError("Conversation session not found") from error


_TOPICS: dict[str, Topic] = {topic.value: topic for topic in Topic}
"""Fact keys the slow lane can reason about, by the key the extractor emits.

A mapping rather than ``Topic(fact.key)`` so that a fact key with no matching topic - and
there are several, evidence dimensions among them - is skipped rather than raising in the
turn path.
"""


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
    LanguageCode.TELUGU: {
        "opt_out": ("అర్థమైంది. నేను సంభాషణ ముగిస్తున్నాను మరియు మిమ్మల్ని మళ్ళీ సంప్రదించవద్దని నమోదు చేస్తాను."),
        "abuse_redirect": (
            "సంభాషణ గౌరవంగా ఉండాలని కోరుకుంటున్నాను. మీ వ్యాపార అవసరం గురించి మాట్లాడవచ్చు, లేదా నేను సంభాషణ ముగిస్తాను."
        ),
        "abuse_stop": "నేను ఇప్పుడు సంభాషణ ముగిస్తున్నాను. ధన్యవాదాలు.",
        "unsafe_request": (
            "అంతర్గత సూచనలు, పాస్‌వర్డ్‌లు లేదా భద్రతా నియమాలను దాటవేయడంలో నేను సహాయం "
            "చేయలేను. వెబ్‌సైట్ అవసరాల విషయంలో సహాయం చేయగలను."
        ),
        "clarify_goals": ("చాలా మార్పులు వినిపించాయి. దయచేసి వెబ్‌సైట్ యొక్క అత్యంత ముఖ్యమైన ఒక లక్ష్యాన్ని స్పష్టం చేయండి."),
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
