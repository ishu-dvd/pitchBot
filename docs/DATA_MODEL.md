# Domain and Storage Model

## Current implementation

PR 3 introduces typed domain contracts and a local SQLAlchemy/Alembic persistence boundary. PR 8 uses follow-up, schedule, proposal, execution, and artifact concepts in bounded process-local mock workflows. PR 12 adds a durable conversation journal on the existing event tables, and PR 13 connects it to default-off simulator HTTP flows.

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

## Durable conversation journal

All sessions for a lead share that lead's `lead` aggregate. Every accepted turn appends one `conversation.turn-accepted.v1` event containing:

- A stable operation UUID and journal-computed, session-bound HMAC-SHA-256 request fingerprint.
- The deterministic conversation result.
- Schema-versioned session capacities, the goal-change safety threshold, and post-turn scalar safety state.
- Only the structured facts, evidence, and classification produced by that turn plus a session-bound HMAC-SHA-256 turn digest; raw buyer text and cumulative prior facts are not duplicated.

Idempotent lookup precedes processing, so an exact retry returns the persisted event even after an ambiguous acknowledgement; reuse with different typed input fails. Processing is rolled back in memory if persistence fails. New operations require the next lead aggregate version and a live state matching durable history.

Simulator integration uses a prepare/commit boundary: it validates the operation and aggregate version before conversation/action processing, then persists only after any mock action succeeds. Action failures and persistence failures roll back local conversation and timeline state. An accepted action turn can reconcile an acknowledgement loss while its process-local idempotent action result remains available; after restart, action-preview response reconstruction fails closed.

Journal reads are bounded and verify aggregate type, version, and privacy state before and after loading. Purged, anonymized, hard-deleted, oversized, malformed, unsupported, or internally inconsistent histories cannot be replayed. Replay rebuilds state from per-turn transitions without executing conversation rules, model calls, or actions. Restoration refuses to overwrite a live session; explicit synchronization replaces stale state only from validated durable history. No migration or duplicate transcript/checkpoint table is introduced.

Lead-level export, anonymization, and hard deletion cover every associated session. Time-based deletion of any source event makes the journal unavailable rather than retaining or silently reconstructing expired data from cumulative copies.

Simulator lead aggregates use a deterministic UUID derived from validated synthetic `lead_ref`; session UUIDs remain unguessable capabilities. Durable API replay validates the entire stream before returning at most 100 typed results and excludes raw buyer text, internal lead/source identifiers, request fingerprints, and repetition digests.

## Knowledge views

The implemented BM25 baseline projects current structured facts for one session from a fully validated journal replay. Each result carries the fact ID, source spans, source aggregate version, session, language, and occurrence time. The aggregate's active privacy state and unchanged version are checked again after scoring.

The implemented temporal lead view adds lead/session/fact relations, explicit supersession edges, validity versions/times, and current/superseded/conflicting status. It does not infer that the latest cross-session value is correct: different current values remain conflicting until a later reviewed confirmation mechanism exists.

The graph-aware BM25 view indexes only current and conflicting claims from one lead while retaining each result's temporal status and source provenance. Superseded claims remain available in the temporal graph for audit but are excluded from retrieval. No result merges equal observations or resolves a conflict.

The future expanded knowledge graph remains a derived, rebuildable view of:

- Entities scoped by lead, organization, product, industry, or competitor.
- Temporal facts with observed, confirmed, superseded, disputed, or expired status.
- Source event, source span, extraction version, confidence, and confirmation provenance.
- Relationships carrying the same tenant, consent, retention, and deletion scope as their sources.

Retrieval indexes store identifiers and minimized searchable representations, not independent truth. Results must resolve back to retained source events before citation. Anonymization, hard deletion, or expiry invalidates derived lexical, vector, graph, cache, and evaluation references; suppression records remain protected as already defined.
