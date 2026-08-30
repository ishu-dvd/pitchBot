# Domain and Storage Model

## Current implementation

PR 3 introduces typed domain contracts and a local SQLAlchemy/Alembic persistence boundary. The repositories are not yet connected to HTTP endpoints or conversation logic.

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
