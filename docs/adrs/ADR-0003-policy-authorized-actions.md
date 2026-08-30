# ADR-0003: Policy-Authorized External Actions

- **Status:** Accepted
- **Date:** 2026-08-30

## Context

A model may suggest useful mid-call actions, but direct model access to telephony, WhatsApp, schedules, URLs, files, or generated artifacts creates unacceptable authorization, duplication, privacy, and cost risks.

## Decision

Models return typed proposals only. Deterministic policy code validates disclosure, consent, contact policy, suppression, DND, calling hours, allowlists, classification evidence, quotas, idempotency, provider state, and operator requirements before an adapter executes.

## Consequences

- External effects can be tested with deterministic mocks.
- Block reasons and approvals are auditable.
- Provider retries must be idempotent.
- Unknown policy state fails closed.
