"""What the two lanes share, arranged so that overwriting is impossible rather than prevented.

The obvious way to let a fast conversational model and a slow deliberating one cooperate is
to give them a common scratchpad and a lock. That design has to *prevent* two problems, and
preventing is weaker than not having them:

* **Overwriting** - two writers racing on one field.
* **Misconception** - a conclusion drawn from a picture that has since changed, applied as
  if it were still current.

This module removes both by construction.

**One writer per field, forever.** The fast lane writes :attr:`Briefing.observations` and
never touches the deliberation. The slow lane writes :attr:`Briefing.deliberation` and never
touches the observations. There is deliberately no method that writes both, so a future
caller cannot accidentally acquire the other lane's field - the mistake is not available.
Neither lane needs a lock against the other, because they are never writing the same thing.

**Conclusions carry the version they were drawn from.** A deliberation records the
observation count it saw. If the buyer has said anything since, the deliberation is *stale*
and :meth:`Briefing.current_deliberation` returns nothing - the reply falls back to what the
rules know rather than confidently repeating a plan built for a different business. The
stale text is still readable through :attr:`Briefing.deliberation` for display and
debugging; it is only barred from being used as current.

**Late answers cannot clobber newer ones.** The slow lane is measured at 5.6 tokens/second,
so a deliberation takes tens of seconds and two can easily be in flight after a cancel.
:meth:`conclude` refuses anything derived from an older version than what is already stored,
so an overtaken result is dropped rather than resurrecting a stale picture.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

MAX_OBSERVATIONS: Final[int] = 200
"""Bound on retained observations.

A conversation cannot make the briefing grow without limit, for the same reason the
callback store is bounded: unbounded per-session memory is a denial of service with extra
steps. Oldest observations are dropped first; the version counter keeps rising, so dropping
history can only ever make a deliberation look stale, never falsely current.
"""

MAX_VALUE_CHARS: Final[int] = 300
"""Bound on one observed value, so a pasted essay cannot become the deliberation prompt."""


class Topic(StrEnum):
    """What an observation is about.

    Deliberately the same vocabulary the planner's ``Slot`` uses, mirrored here rather than
    imported so the deliberation package does not depend on the planner. The test suite
    asserts they stay in step; a silent divergence would let the slow lane reason about a
    topic the agent can never ask about.
    """

    BUSINESS_TYPE = "business_type"
    REQUESTED_FEATURES = "requested_features"
    BUDGET_STATED = "budget_stated"
    TIMELINE = "timeline"


@dataclass(frozen=True, slots=True)
class Observation:
    """One thing the fast lane established about the buyer.

    Frozen because an observation is a record of what was said, not a mutable opinion. A
    later correction is a *new* observation with a higher version, never an edit.
    """

    topic: Topic
    value: str
    version: int

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("an observation with no value is not an observation")
        if len(self.value) > MAX_VALUE_CHARS:
            raise ValueError(f"observation value exceeds {MAX_VALUE_CHARS} characters")
        if self.version < 1:
            raise ValueError("observation version starts at 1")


@dataclass(frozen=True, slots=True)
class SitePlan:
    """What the slow lane concluded: the shape of the site it would propose.

    Every field is prose the model produced under a JSON grammar, so the *shape* is
    guaranteed and the *content* is not. Nothing here is ever spoken to the buyer as fact
    without being marked as a proposal, and none of it can fill a qualification slot -
    a plan is what we would build, not something the buyer told us.
    """

    competitors: tuple[str, ...]
    differentiator: str
    pages: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.pages:
            raise ValueError("a site plan with no pages is not a plan")


@dataclass(frozen=True, slots=True)
class Deliberation:
    """A conclusion, stamped with the picture it was drawn from."""

    plan: SitePlan
    derived_from_version: int
    model_id: str

    def __post_init__(self) -> None:
        if self.derived_from_version < 0:
            raise ValueError("derived_from_version cannot be negative")


class BriefingOwnershipError(RuntimeError):
    """Raised when a lane tries to write a field it does not own."""


@dataclass
class Briefing:
    """The only thing the two lanes share.

    Not thread-safe by design, and not needing to be: the scheduler guarantees the lanes
    never run at the same time, so adding a lock here would advertise a concurrency that
    the measurements say must not exist. See :mod:`pitchbot.deliberation.lanes` - running
    both lanes together cost the turn path 3.37x.
    """

    _observations: list[Observation] = field(default_factory=list)
    _version: int = 0
    _deliberation: Deliberation | None = None

    # -- fast lane: the only writer of observations ------------------------------------

    def observe(self, topic: Topic, value: str) -> Observation:
        """Record something the buyer established. Fast lane only.

        Returns the stored observation so a caller can assert on its version rather than
        reaching into the briefing to find out what happened.
        """

        self._version += 1
        observation = Observation(
            topic=topic,
            value=value.strip()[:MAX_VALUE_CHARS],
            version=self._version,
        )
        self._observations.append(observation)
        if len(self._observations) > MAX_OBSERVATIONS:
            del self._observations[: len(self._observations) - MAX_OBSERVATIONS]
        return observation

    # -- slow lane: the only writer of the deliberation ---------------------------------

    def conclude(self, deliberation: Deliberation) -> bool:
        """Store a conclusion unless it has already been overtaken. Slow lane only.

        Returns whether it was stored. A deliberation derived from an older picture than
        the one already stored is dropped: two can be in flight after a cancel, and the
        later-finishing one is not necessarily the newer one.
        """

        if deliberation.derived_from_version > self._version:
            raise BriefingOwnershipError(
                f"deliberation claims to be derived from version "
                f"{deliberation.derived_from_version}, which is ahead of the briefing's "
                f"{self._version}. A conclusion cannot precede the facts it is drawn from."
            )
        existing = self._deliberation
        if (
            existing is not None
            and deliberation.derived_from_version < existing.derived_from_version
        ):
            return False
        self._deliberation = deliberation
        return True

    # -- readable by both ---------------------------------------------------------------

    @property
    def version(self) -> int:
        """How many things have been observed. Rises forever; never reused."""

        return self._version

    @property
    def observations(self) -> tuple[Observation, ...]:
        return tuple(self._observations)

    @property
    def deliberation(self) -> Deliberation | None:
        """The last conclusion, current or not. Use :meth:`current_deliberation` to act."""

        return self._deliberation

    @property
    def is_stale(self) -> bool:
        """Whether the stored conclusion predates something the buyer has since said."""

        if self._deliberation is None:
            return False
        return self._deliberation.derived_from_version < self._version

    def current_deliberation(self) -> Deliberation | None:
        """The conclusion, only if it still describes the buyer we are talking to."""

        if self._deliberation is None or self.is_stale:
            return None
        return self._deliberation

    def latest_by_topic(self) -> Mapping[Topic, Observation]:
        """The most recent observation per topic - what the slow lane reasons about."""

        latest: dict[Topic, Observation] = {}
        for observation in self._observations:
            latest[observation.topic] = observation
        return latest

    def known_topics(self) -> frozenset[Topic]:
        return frozenset(self.latest_by_topic())

    def __iter__(self) -> Iterator[Observation]:
        return iter(self._observations)


def describe(observations: Sequence[Observation]) -> str:
    """The observations as one paragraph, for a deliberation prompt.

    Ordered by topic rather than by arrival so the same buyer always produces the same
    prompt, which is what makes a deliberation reproducible enough to test.
    """

    latest: dict[Topic, str] = {}
    for observation in observations:
        latest[observation.topic] = observation.value
    return " ".join(
        f"{topic.value.replace('_', ' ')}: {latest[topic]}." for topic in Topic if topic in latest
    )


__all__ = [
    "MAX_OBSERVATIONS",
    "MAX_VALUE_CHARS",
    "Briefing",
    "BriefingOwnershipError",
    "Deliberation",
    "Observation",
    "SitePlan",
    "Topic",
    "describe",
]
