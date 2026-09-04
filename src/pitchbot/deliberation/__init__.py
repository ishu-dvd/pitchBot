"""Two lanes on one CPU: answer the buyer now, think about the deal when nobody is waiting.

The product wants a model that converses without latency *and* a model that can work out
what the buyer's site should actually be. Those are different jobs with irreconcilable
budgets - the turn path has hundreds of milliseconds and a site plan takes ten seconds -
so they are different models. What is *not* obvious, and what this package exists to
encode, is that they must never run at the same time.

Measured on 2026-09-04, 16 logical CPUs, both models int4 on CPU:

* the turn path alone is p50 453 ms
* the turn path with a background model generating is p50 1,504 ms - **3.37x**
* capping the background model to 4 threads makes it 3.59x, and to 2 threads 4.87x
* stopping the background model takes **0.1 ms**, and the next turn is at 0.98x baseline

So the answer is not clever thread budgeting - that measured *worse* - it is strict
exclusion with cheap preemption.

The lanes also do not talk to each other, and that too is measured. A single agent-to-agent
round trip, with each hop a real generation, cost **12,976 ms**; streaming the slow lane's
answer made its first field readable at 2,852 ms and the last at 6,736 ms, so a consumer
acting early acts on a plan with competitors and no pages. Writing and reading a shared
field costs **0.162 microseconds**. The lanes share state, and each owns the fields it
writes, so neither overwriting nor acting on a superseded picture is possible - see
:mod:`pitchbot.deliberation.briefing`.
"""

from __future__ import annotations

from pitchbot.deliberation.artifacts import (
    ArtifactPhrases,
    Slide,
    artifact_languages,
    deck_slides,
    phrases_for,
    site_content,
)
from pitchbot.deliberation.briefing import (
    MAX_OBSERVATIONS,
    MAX_VALUE_CHARS,
    Briefing,
    BriefingOwnershipError,
    Deliberation,
    Observation,
    SitePlan,
    Topic,
    describe,
)
from pitchbot.deliberation.deliberator import (
    MAX_PLAN_SECONDS,
    MIN_TOPICS,
    SCHEMA_NAME,
    BackgroundDeliberation,
    Deliberator,
    PreemptibleModel,
)
from pitchbot.deliberation.lanes import LaneScheduler, LaneStats, YieldBudget

__all__ = [
    "MAX_OBSERVATIONS",
    "MAX_PLAN_SECONDS",
    "MAX_VALUE_CHARS",
    "MIN_TOPICS",
    "SCHEMA_NAME",
    "ArtifactPhrases",
    "BackgroundDeliberation",
    "Briefing",
    "BriefingOwnershipError",
    "Deliberation",
    "Deliberator",
    "LaneScheduler",
    "LaneStats",
    "Observation",
    "PreemptibleModel",
    "SitePlan",
    "Slide",
    "Topic",
    "YieldBudget",
    "artifact_languages",
    "deck_slides",
    "describe",
    "phrases_for",
    "site_content",
]
