from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from uuid import UUID

from pitchbot.conversation.models import ConversationPhase
from pitchbot.domain import Classification, IntentEvidence, RequirementFact


@dataclass(slots=True)
class ConversationState:
    lead_id: UUID
    max_turns: int
    max_facts: int
    max_evidence: int
    max_classifications: int
    phase: ConversationPhase = ConversationPhase.DISCOVERY
    turn_count: int = 0
    abuse_redirected: bool = False
    stopped: bool = False
    recent_normalized_turns: deque[str] = field(default_factory=deque)
    facts_by_key: dict[str, RequirementFact] = field(default_factory=dict)
    evidence: deque[IntentEvidence] = field(default_factory=deque)
    classifications: deque[Classification] = field(default_factory=deque)
    goal_change_count: int = 0

    def __post_init__(self) -> None:
        capacities = (
            self.max_turns,
            self.max_facts,
            self.max_evidence,
            self.max_classifications,
        )
        if min(capacities) < 1:
            raise ValueError("Conversation capacities must be positive")
        self.recent_normalized_turns = deque(maxlen=min(self.max_turns, 20))
        self.evidence = deque(maxlen=self.max_evidence)
        self.classifications = deque(maxlen=self.max_classifications)

    def ensure_turn_capacity(self) -> None:
        if self.turn_count >= self.max_turns:
            raise RuntimeError("Conversation turn capacity reached")
