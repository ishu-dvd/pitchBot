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

## PR 5: Browser simulator

- **Branch:** `feat/browser-simulator`
- **Status:** Implementation, mandatory self-review, and local validation complete; commits, push, CI, and PR pending.
- **Base:** Merged PR 4 commit `8034f74`.
- **Scope:** Same-origin static simulator and FastAPI routes, disclosure-first synthetic sessions, language selection, text turns, explicit previews, bounded history, deterministic replay/failure/latency, interruption, microphone transport, backpressure, reconnect, and cleanup.
- **Safety decisions:** No CORS middleware; no real action; synthetic data only; audio bytes discarded after metadata capture; session-scoped history; bounded sessions/events/audio/queues; restrictive browser headers.
- **Deferred:** Conversation state machine, extraction/classification, speech providers, durable simulator persistence, authenticated public deployment, and lossless/WebRTC transport.
- **Rollback:** Revert the PR. The simulator uses process-local memory and creates no database or external provider state.

## PR 6: Speech and local runtime benchmark harness

- **Branch:** `bench/speech-runtime`
- **Status:** Implementation, mandatory self-review, and final local validation complete; commits, push, CI, and PR pending.
- **Base:** Merged PR 5 commit `6e64ddf`.
- **Scope:** Candidate/license registry, planned synthetic speech corpus, manifest/hash/provenance validation, VAD/STT/TTS/model metrics, timing/environment helpers, CLI, CI manifest gates, and model-selection ADR.
- **Expanded coverage:** English/Hindi/Hinglish; noise, crosstalk, silence, interruption, repetition, accents, names/numbers; apparel, toys, books, food, import/export, and plastics vocabulary; audio intelligibility/consistency methodology.
- **Safety decisions:** No model/voice selection, model download, private/copyrighted audio, or placeholder benchmark result; code and model/voice licenses are separate gates; voice similarity cannot target an identifiable person.
- **Current environment:** Windows 11, Python 3.12.10, 8 logical CPUs, no accelerator declared.
- **Deferred:** Actual model measurements require reviewed audio, exact model revisions/licenses, and labeled target hardware.
- **Rollback:** Revert the PR. It introduces no runtime model, external provider, database migration, or measured baseline.
