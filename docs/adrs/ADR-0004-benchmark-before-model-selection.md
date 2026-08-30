# ADR-0004: Benchmark Before Model Selection

- **Status:** Accepted
- **Date:** 2026-08-31

## Context

English/Hindi/Hinglish quality, streaming latency, resource use, and licenses vary by model, quantization, voice, hardware, and corpus. Marketing claims or fabricated placeholder numbers cannot select PitchBot's runtime.

## Decision

Do not select a default VAD, STT, TTS, STS, or local language model until a reproducible measured result passes:

- Exact repository/model/voice version and license capture.
- Licensed, consented, or synthetic corpus validation with hashes.
- Labeled hardware and configuration.
- Language/noise/industry slices and latency/resource measurements.
- Human review for TTS intelligibility/naturalness.
- Reviewed limitations and baseline deltas.

Planned corpus entries and deterministic harness tests are not model measurements.

## Consequences

- PR 6 can safely establish the harness without downloading large models or publishing misleading results.
- Model selection remains open until representative audio and target hardware are available.
- Later provider adapters remain replaceable behind existing contracts.
- Hardware-specific results are separate from deterministic CI gates.
