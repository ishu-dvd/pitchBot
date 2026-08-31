# ADR-0005: Portable Evaluation Before Dashboard

- **Status:** Accepted
- **Date:** 2026-09-01

## Context

PitchBot needs visible, comparable evaluation history without requiring a paid service, network account, or heavyweight local stack. A dashboard alone is not durable evidence: service schemas change, local databases can disappear, and screenshots cannot be validated in CI. Evaluation data can also leak buyer content if unrestricted traces or prompts are retained.

## Decision

Use a versioned, bounded JSON evaluation snapshot as the portable source of run evidence. Validate it with repository code and render a dependency-free static HTML report. Snapshots contain hashes, exact code revision, hardware labels, finite metrics, case dimensions, and machine-readable failure codes; they exclude raw transcripts, prompts, audio, contact details, and retrieved content.

OpenTelemetry is the planned runtime instrumentation boundary. Phoenix or MLflow may later provide optional local views, but neither becomes authoritative or sends data externally by default. A passing artifact is evidence for human review, not automatic deployment or live-action authorization.

## Consequences

- Evaluation evidence remains diffable, CI-validatable, and viewable offline at zero service cost.
- Schema evolution requires a reviewed version change.
- Live dashboards can be replaced without changing the evidence format.
- Rich debugging content must remain local, explicitly enabled, retention-bound, and separate from committed snapshots.
- Reviewed suite manifests and baseline comparison remain required before promotion decisions.
