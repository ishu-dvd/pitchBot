"""Structured logging, correlation, and metrics for PitchBot."""

from __future__ import annotations

from pitchbot.observability.context import (
    CorrelationContext,
    correlated,
    current_context,
    new_turn_id,
)
from pitchbot.observability.logging import (
    REDACTED,
    SENSITIVE_FIELD_MARKERS,
    JsonLogFormatter,
    configure_logging,
    is_sensitive,
    redact,
)
from pitchbot.observability.metrics import (
    DEFAULT_DURATION_BUCKETS_MS,
    MAX_SERIES,
    Counter,
    Histogram,
    MetricsRegistry,
)
from pitchbot.observability.turn_metrics import (
    TURN_STAGE_MS,
    TURNS_TOTAL,
    UTTERANCES_TOTAL,
    TurnStage,
    describe_metrics,
    record_stage,
    record_turn,
    record_utterance,
    registry,
)

__all__ = [
    "DEFAULT_DURATION_BUCKETS_MS",
    "MAX_SERIES",
    "REDACTED",
    "SENSITIVE_FIELD_MARKERS",
    "TURNS_TOTAL",
    "TURN_STAGE_MS",
    "UTTERANCES_TOTAL",
    "Counter",
    "CorrelationContext",
    "Histogram",
    "JsonLogFormatter",
    "MetricsRegistry",
    "TurnStage",
    "configure_logging",
    "correlated",
    "current_context",
    "describe_metrics",
    "is_sensitive",
    "new_turn_id",
    "record_stage",
    "record_turn",
    "record_utterance",
    "redact",
    "registry",
]
