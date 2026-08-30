# ADR-0002: Cascaded Speech Pipeline First

- **Status:** Accepted for benchmarking
- **Date:** 2026-08-30

## Context

PitchBot needs inspectable bilingual transcripts, evidence-grounded classification, guarded tool actions, replay, and evaluation. Direct speech-to-speech can reduce latency but can make these controls harder to observe consistently.

## Decision

Benchmark and implement streaming VAD/STT → conversation and policy engine → streaming TTS first. Keep each provider behind a contract. Consider direct STS only if it preserves equivalent transcripts, policy enforcement, action traces, interruption handling, and evaluations.

## Consequences

- Better auditability and deterministic replay.
- More measurable pipeline stages.
- Potentially higher latency than direct STS, which must be addressed through streaming and bounded queues.
- No speech model is selected until target-hardware benchmarks exist.
