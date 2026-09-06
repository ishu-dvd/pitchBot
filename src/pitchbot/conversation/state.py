from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from uuid import UUID

from pitchbot.conversation.models import ConversationPhase
from pitchbot.domain import Classification, IntentEvidence, LanguageCode, RequirementFact


@dataclass(slots=True)
class ConversationState:
    lead_id: UUID
    max_turns: int
    max_facts: int
    max_evidence: int
    max_classifications: int
    max_goal_changes: int
    digest_key_id: str
    phase: ConversationPhase = ConversationPhase.DISCOVERY
    turn_count: int = 0
    abuse_redirected: bool = False
    stopped: bool = False
    recent_turn_digests: deque[str] = field(default_factory=deque)
    facts_by_key: dict[str, RequirementFact] = field(default_factory=dict)
    evidence: deque[IntentEvidence] = field(default_factory=deque)
    classifications: deque[Classification] = field(default_factory=deque)
    goal_change_count: int = 0
    asked_slot_counts: dict[str, int] = field(default_factory=dict)
    """How many times the agent has asked for each slot.

    Bounded by construction: there are four slots, so this cannot grow with turn count.
    It exists because a buyer who does not answer a question must not be asked it forever -
    which is the same "says the same thing every turn" failure the planner was added to fix,
    just one level down.
    """

    closing_count: int = 0
    """How many times this conversation has already delivered a closing line.

    Transient, like :attr:`asked_slot_counts`, and deliberately not part of the durable
    checkpoint: it shapes the next sentence, it is not a fact about the buyer. A resumed
    call starting its close afresh is the right behaviour anyway - the buyer has not heard
    those sentences on this leg.
    """

    understood_slot_keys: set[str] = field(default_factory=set)
    """Slots a richer understanding reported as filled, which the extractors missed.

    Without this the improvement lasts exactly one turn. The shipped budget regex needs
    digits, so a model reading *"around two lakh rupees"* fills the budget slot for that
    turn - and then the next turn rebuilds the slot set from the rules' facts alone, the
    budget is missing again, and the agent asks for it a second time. Also bounded by the
    number of slots.
    """

    language: LanguageCode = LanguageCode.UNKNOWN
    """The language the conversation is actually being held in.

    Distinct from :attr:`declared_language` because the two diverge the moment a buyer
    switches. The caller keeps declaring what it believes; this is what was decided.
    """

    declared_language: LanguageCode = LanguageCode.UNKNOWN
    """The language the caller passed on the previous turn.

    Kept so a caller that *deliberately* changes language - an operator switching a live
    call, say - can be told apart from one that simply keeps repeating the language it
    opened with. Without it the engine could not honour a re-declaration without also
    letting a stale declaration undo a switch the buyer asked for.
    """

    pending_language: LanguageCode | None = None
    """A different language heard but not yet acted on."""

    pending_language_count: int = 0
    """Consecutive turns :attr:`pending_language` has been heard.

    Held in state rather than in a detector object so a checkpoint restores it. Hysteresis
    kept outside the state would silently reset on every restore, and a buyer mid-switch
    would have to start convincing the system again.
    """

    def __post_init__(self) -> None:
        capacities = (
            self.max_turns,
            self.max_facts,
            self.max_evidence,
            self.max_classifications,
            self.max_goal_changes,
        )
        if min(capacities) < 1:
            raise ValueError("Conversation capacities must be positive")
        if len(self.digest_key_id) != 64 or any(
            character not in "0123456789abcdef" for character in self.digest_key_id
        ):
            raise ValueError("Conversation digest key ID must be a SHA-256 digest")
        self.recent_turn_digests = deque(maxlen=min(self.max_turns, 20))
        self.evidence = deque(maxlen=self.max_evidence)
        self.classifications = deque(maxlen=self.max_classifications)

    def ensure_turn_capacity(self) -> None:
        if self.turn_count >= self.max_turns:
            raise RuntimeError("Conversation turn capacity reached")
