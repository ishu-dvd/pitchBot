# Domain and Storage Model

## Current implementation

PR 3 introduces typed domain contracts and a local SQLAlchemy/Alembic persistence boundary. PR 8 uses follow-up, schedule, proposal, execution, and artifact concepts in bounded process-local mock workflows; repositories are still not connected to HTTP action flows.

## Domain contracts

Immutable Pydantic contracts cover:

- Leads and deny-by-default contact policy.
- Call sessions, turns, and transcript spans.
- Requirement facts and confirmed revisions.
- Intent evidence and versioned Hot/Warm/Cold/review-needed classifications.
- Follow-ups, schedules, action proposals, and executions.
- Consent, opt-out, artifacts, citations, strategies, experiments, and audit events.
- Privacy-operation audit records.

All domain timestamps are timezone-aware. Unknown fields are rejected.

Current follow-up summaries accept only allowlisted business type, feature, budget, timeline, and next-step fields. Callback resource IDs are distinct from schedule/cancel/dispatch idempotency keys. Structured deck previews contain fixed industry templates and allowlisted features; binary PPTX files are not generated.

## Database tables

### `aggregate_records`

Maintains aggregate type, current version, and privacy lifecycle independently from retained event rows. This prevents retention cleanup from resetting version numbers. Anonymized and hard-deleted aggregates retain a minimal tombstone and reject future event writes.

### `event_records`

Stores ordered domain events with a unique `(aggregate_id, aggregate_version)` constraint. Normal business operations append events and never update them.

A privacy-approved anonymization is the only payload mutation: it replaces event payload content with an anonymized marker while preserving event ID, type, order, version, and timestamp. The exception is recorded in `privacy_operation_records`.

### `suppression_records`

Stores append-only global or channel suppression decisions. Automatic retention never deletes suppression records. Hard deletion of a lead journey preserves suppression so prior opt-outs cannot silently fail open.

### `privacy_operation_records`

Stores append-only audit metadata for anonymization and hard deletion without retaining the deleted payload.

### `alembic_version`

Tracks the applied schema revision. Alembic is the only schema authority; application code does not call `create_all`.

## Concurrency

Repositories accept an optional expected aggregate version. A stale version raises `ConcurrencyConflictError`. Database uniqueness constraints also reject two writes that attempt the same aggregate version.

## Retention

- Retention is dry-run by default.
- Opt-out and consent-revocation events are protected from automatic event cleanup.
- Suppression history and aggregate version heads are not automatically purged.
- Hard deletion is explicit and separately audited; it preserves suppression and a minimal closed aggregate tombstone.

## Privacy commands

After applying migrations, the operator can run:

```powershell
python -m pitchbot.storage.cli export <aggregate-uuid>
python -m pitchbot.storage.cli anonymize <aggregate-uuid> --confirm <aggregate-uuid>
python -m pitchbot.storage.cli delete <aggregate-uuid> --confirm <aggregate-uuid>
python -m pitchbot.storage.cli purge --cutoff 2026-01-01T00:00:00+00:00
python -m pitchbot.storage.cli purge --cutoff 2026-01-01T00:00:00+00:00 --execute
```

Export recursively redacts known contact/name/address keys. Destructive per-aggregate commands require the UUID to be repeated exactly. Purge reports eligible rows unless `--execute` is supplied.

Retention cutoffs must include an explicit UTC offset or timezone. Repeated anonymize or hard-delete requests are idempotent and do not create duplicate privacy-operation records.

## Portability

The repository uses generic SQLAlchemy JSON, Boolean, DateTime, string, integer, index, and uniqueness constructs. SQLite is the tested local backend. PostgreSQL execution requires a future driver/profile test, but domain and repository contracts do not expose SQLite-specific APIs.

## Planned durable conversation and knowledge views

Durable conversation history will reuse `aggregate_records` and `event_records`; it will not add a second authoritative transcript table. Each accepted turn will have a stable operation identifier, ordered source spans, minimized text or redacted references, the resulting state transition, and policy/action decisions. Replay must restore the same bounded conversation state after restart without re-executing side effects.

The planned runtime knowledge graph is a derived, rebuildable view of:

- Entities scoped by lead, organization, product, industry, or competitor.
- Temporal facts with observed, confirmed, superseded, disputed, or expired status.
- Source event, source span, extraction version, confidence, and confirmation provenance.
- Relationships carrying the same tenant, consent, retention, and deletion scope as their sources.

Retrieval indexes store identifiers and minimized searchable representations, not independent truth. Results must resolve back to retained source events before citation. Anonymization, hard deletion, or expiry invalidates derived lexical, vector, graph, cache, and evaluation references; suppression records remain protected as already defined.
