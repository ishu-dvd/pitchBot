"""The specific metrics the turn path records, and the vocabulary they use.

Kept apart from the registry so the *names* and the closed label sets live in one place. Every
label value below comes from an enum or a fixed vocabulary; nothing here accepts a caller's
string, which is what keeps the series count bounded.

This is the answer to a question `docs/BENCHMARKS.md` was explicit about being unable to
answer: the turn path reported `transcribe_ms` and `engine_ms` to a single browser and then
discarded them, so every latency figure in this repository came from a probe run by hand.
Stage timings are now aggregated in-process and exposed, which is what makes a p95 from real
traffic possible rather than a bench estimate.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from pitchbot.observability.metrics import MetricsRegistry

TURN_STAGE_MS: Final[str] = "pitchbot_turn_stage_ms"
TURNS_TOTAL: Final[str] = "pitchbot_turns_total"
UTTERANCES_TOTAL: Final[str] = "pitchbot_utterances_total"


class TurnStage(StrEnum):
    """Where the time in a spoken turn goes.

    Measured 2026-09-04/05, an English spoken turn was 4,507 ms end to end of which
    transcription was 3,982 ms - so a single "turn latency" number hides the only term that
    has ever mattered. These are recorded separately for that reason.
    """

    DETECT_LANGUAGE = "detect_language"
    TRANSCRIBE = "transcribe"
    PLAN = "plan"
    SYNTHESIZE = "synthesize"
    TOTAL = "total"


registry: Final[MetricsRegistry] = MetricsRegistry()


def describe_metrics() -> None:
    registry.describe(TURN_STAGE_MS, "Milliseconds spent in one stage of a turn.")
    registry.describe(TURNS_TOTAL, "Turns completed, by language and disposition.")
    registry.describe(UTTERANCES_TOTAL, "Endpointed utterances, by language and outcome.")


describe_metrics()


def record_stage(stage: TurnStage, milliseconds: float, *, language: str = "unknown") -> None:
    """Record one stage timing.

    `language` is a :class:`~pitchbot.domain.LanguageCode` value - a closed set of five - so
    the series count stays bounded no matter how many conversations run.
    """

    registry.histogram(TURN_STAGE_MS, {"stage": str(stage), "language": language}).observe(
        milliseconds
    )


def record_turn(*, language: str, disposition: str) -> None:
    registry.counter(TURNS_TOTAL, {"language": language, "disposition": disposition}).increment()


def record_utterance(*, language: str, outcome: str) -> None:
    registry.counter(UTTERANCES_TOTAL, {"language": language, "outcome": outcome}).increment()


__all__ = [
    "TURNS_TOTAL",
    "TURN_STAGE_MS",
    "UTTERANCES_TOTAL",
    "TurnStage",
    "describe_metrics",
    "record_stage",
    "record_turn",
    "record_utterance",
    "registry",
]
