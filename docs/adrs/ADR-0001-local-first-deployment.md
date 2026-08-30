# ADR-0001: Local-First Deployment

- **Status:** Accepted
- **Date:** 2026-08-30

## Context

Persistent low-latency speech/model inference, background scheduling, PSTN, official WhatsApp, and GPU capacity cannot all be guaranteed on a permanent free hosted tier.

## Decision

Use `local-full` as the authoritative development/evaluation profile. Treat `hosted-demo` as optional, synthetic-data-only, quota constrained, and non-production. Keep official live adapters in a `live-disabled` profile requiring a separate activation decision.

## Consequences

- Development can remain zero-cost on existing hardware.
- Public demos may sleep or cold-start and carry no SLA.
- Real telephony/messaging remains deferred and may require approved credits or spend.
- GitHub Actions is CI, not application hosting; GitHub Pages can host only static assets.
