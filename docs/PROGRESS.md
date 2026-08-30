# Progress

## PR 1: Foundation audit

- **Branch:** `chore/foundation-audit`
- **Status:** Foundation implementation, commits, and local validation complete; push, CI, and PR pending.
- **Environment:** Windows, Python 3.12.10
- **Commits:** `593fe49`, `0a4931f`, `e1aaa51`
- **Scope:** Python packaging, dependency lock, safe configuration defaults, FastAPI health endpoint, CI, contribution/security guidance, and baseline validation.
- **Deferred:** Architecture/compliance details, domain models, persistence, provider adapters, simulator, speech/runtime benchmarks, conversation logic, actions, evaluations, WebRTC, deployment, and live integrations.
- **Safety:** Telephony, WhatsApp, external networking, real-time audio, and hosted-demo behavior remain disabled by default.
- **Rollback:** Revert the foundation PR. No persistent data migration or external side effect is introduced.

### Audit decisions

- Removed uncommitted implementations belonging to later PR milestones.
- Removed permissive wildcard CORS and simulator endpoints from the foundation API.
- Removed placeholder VAD benchmark numbers so they cannot be mistaken for measurements.
- Removed the destructive storage utility and duplicate unmanaged schema approach pending the reviewed storage milestone.

## PR 2: Architecture and compliance documentation

- **Branch:** `docs/architecture-compliance`
- **Status:** Documentation and local validation complete; commits, push, CI, and PR pending.
- **Base:** Merged PR 1 commit `ee9b2c1`
- **Scope:** Target architecture, deployment profiles, trust boundaries, threat model, compliance/privacy gates, operational cleanup/rollback, source register, and architecture decisions.
- **Implementation impact:** Documentation only; no runtime capability or external side effect is added.
- **Rollback:** Revert the documentation PR. No application state or external provider is changed.

## PR 3: Domain and storage

- **Branch:** `feat/domain-storage`
- **Status:** Implementation and local validation complete; commits, push, CI, and PR pending.
- **Base:** Merged PR 2 commit `0da0349`.
- **Scope:** Immutable domain contracts, Alembic-managed SQLite schema, append-only events and suppression, durable aggregate versions, optimistic concurrency, redacted export, privacy operations, retention, and operator CLI.
- **Safety decisions:** Contact policy defaults deny outreach; suppression survives hard deletion; anonymized/deleted aggregates retain closed tombstones and reject future writes; privacy operations are idempotent; retention is timezone-explicit, dry-run by default, and protects opt-out/revocation events.
- **Deferred:** API integration, encryption/key management, PostgreSQL runtime testing, providers, scheduler, and conversation workflows.
- **Rollback:** Revert the PR before applying its migration. After applying it, run the reviewed Alembic downgrade only when no retained data is required; never delete a live database merely to roll back code.

## PR 4: Provider contracts and deterministic mocks

- **Branch:** `feat/provider-mocks`
- **Status:** Implementation and local validation complete; commits, push, CI, and PR pending.
- **Base:** Merged PR 3 commit `2b502eb`.
- **Scope:** Streaming speech/model and action contracts, UTC/fake clocks, disabled external adapters, bounded deterministic mocks, strict idempotency, scripted failures, bounded retry/timeouts, and circuit breaking.
- **Safety decisions:** No SDK or socket client is included; external adapters fail closed; mock histories minimize sensitive content and enforce capacity; conflicting idempotency-key reuse fails; one half-open circuit probe is allowed.
- **Deferred:** Concrete providers, dependency injection/API wiring, persistent scheduler, browser audio transport, and provider benchmarks.
- **Rollback:** Revert the PR. It changes no schema, runtime data, provider credential, or external action.
