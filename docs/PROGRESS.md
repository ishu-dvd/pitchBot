# Progress

## PR 1: Foundation audit

- **Branch:** `chore/foundation-audit`
- **Status:** Merged as PR 1.
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
- **Status:** Merged as PR 2.
- **Base:** Merged PR 1 commit `ee9b2c1`
- **Scope:** Target architecture, deployment profiles, trust boundaries, threat model, compliance/privacy gates, operational cleanup/rollback, source register, and architecture decisions.
- **Implementation impact:** Documentation only; no runtime capability or external side effect is added.
- **Rollback:** Revert the documentation PR. No application state or external provider is changed.

## PR 3: Domain and storage

- **Branch:** `feat/domain-storage`
- **Status:** Merged as PR 3.
- **Base:** Merged PR 2 commit `0da0349`.
- **Scope:** Immutable domain contracts, Alembic-managed SQLite schema, append-only events and suppression, durable aggregate versions, optimistic concurrency, redacted export, privacy operations, retention, and operator CLI.
- **Safety decisions:** Contact policy defaults deny outreach; suppression survives hard deletion; anonymized/deleted aggregates retain closed tombstones and reject future writes; privacy operations are idempotent; retention is timezone-explicit, dry-run by default, and protects opt-out/revocation events.
- **Deferred:** API integration, encryption/key management, PostgreSQL runtime testing, providers, scheduler, and conversation workflows.
- **Rollback:** Revert the PR before applying its migration. After applying it, run the reviewed Alembic downgrade only when no retained data is required; never delete a live database merely to roll back code.

## PR 4: Provider contracts and deterministic mocks

- **Branch:** `feat/provider-mocks`
- **Status:** Merged as PR 4.
- **Base:** Merged PR 3 commit `2b502eb`.
- **Scope:** Streaming speech/model and action contracts, UTC/fake clocks, disabled external adapters, bounded deterministic mocks, strict idempotency, scripted failures, bounded retry/timeouts, and circuit breaking.
- **Safety decisions:** No SDK or socket client is included; external adapters fail closed; mock histories minimize sensitive content and enforce capacity; conflicting idempotency-key reuse fails; one half-open circuit probe is allowed.
- **Deferred:** Concrete providers, dependency injection/API wiring, persistent scheduler, browser audio transport, and provider benchmarks.
- **Rollback:** Revert the PR. It changes no schema, runtime data, provider credential, or external action.

## PR 5: Browser simulator

- **Branch:** `feat/browser-simulator`
- **Status:** Merged as PR 5.
- **Base:** Merged PR 4 commit `8034f74`.
- **Scope:** Same-origin static simulator and FastAPI routes, disclosure-first synthetic sessions, language selection, text turns, explicit previews, bounded history, deterministic replay/failure/latency, interruption, microphone transport, backpressure, reconnect, and cleanup.
- **Safety decisions:** No CORS middleware; no real action; synthetic data only; audio bytes discarded after metadata capture; session-scoped history; bounded sessions/events/audio/queues; restrictive browser headers.
- **Deferred:** Conversation state machine, extraction/classification, speech providers, durable simulator persistence, authenticated public deployment, and lossless/WebRTC transport.
- **Rollback:** Revert the PR. The simulator uses process-local memory and creates no database or external provider state.

## PR 6: Speech and local runtime benchmark harness

- **Branch:** `bench/speech-runtime`
- **Status:** Merged as PR 6.
- **Base:** Merged PR 5 commit `6e64ddf`.
- **Scope:** Candidate/license registry, planned synthetic speech corpus, manifest/hash/provenance validation, VAD/STT/TTS/model metrics, timing/environment helpers, CLI, CI manifest gates, and model-selection ADR.
- **Expanded coverage:** English/Hindi/Hinglish; noise, crosstalk, silence, interruption, repetition, accents, names/numbers; apparel, toys, books, food, import/export, and plastics vocabulary; audio intelligibility/consistency methodology.
- **Safety decisions:** No model/voice selection, model download, private/copyrighted audio, or placeholder benchmark result; code and model/voice licenses are separate gates; voice similarity cannot target an identifiable person.
- **Current environment:** Windows 11, Python 3.12.10, 8 logical CPUs, no accelerator declared.
- **Deferred:** Actual model measurements require reviewed audio, exact model revisions/licenses, and labeled target hardware.
- **Rollback:** Revert the PR. It introduces no runtime model, external provider, database migration, or measured baseline.

## PR 7: Deterministic conversation intelligence

- **Branch:** `feat/conversation-intelligence`
- **Status:** Merged as PR 7.
- **Base:** Merged PR 6 commit `8cbe85f`.
- **Scope:** Bounded session state, multilingual control-signal handling, deterministic business-fact/revision extraction, explicit commercial intent evidence, Hot/Warm/Cold/Review classification, safe response policy, simulator integration, and synthetic persona/adversarial corpus.
- **Safety decisions:** Opt-out has stop precedence; abuse receives at most one neutral redirection; internal-information and prompt-injection requests are refused before extraction; stopped sessions reject turns and previews; language, accent, frustration, persona, and protected/sensitive traits are never intent evidence.
- **Deferred:** Model-backed extraction, durable conversation persistence, action authorization/execution, follow-up scheduling, artifacts, provider integration, and live channels.
- **Rollback:** Revert the PR. Conversation state is process-local and creates no database migration, network request, provider action, or durable record.

## PR 8: Guarded follow-ups, callbacks, and sample decks

- **Branch:** `feat/followups-artifacts`
- **Status:** Merged as PR 8.
- **Base:** Merged PR 7 commit `28401a4`.
- **Scope:** Deny-by-default action policy, minimized follow-up summaries, bounded callback schedule/cancel/reschedule and fake-time dispatch, structured six-industry sample decks, mock adapter orchestration, and guarded simulator previews.
- **Safety decisions:** Buyer/model content cannot authorize actions; unknown or opted-out policy blocks; dispatch rechecks policy; callback resource and idempotency keys remain separate; only allowlisted facts/features enter follow-ups/decks; simulator defaults remain blocked.
- **Deferred:** Durable scheduler/storage integration, binary PPTX/PDF rendering, model-generated decks, provider SDKs, real contact references, live calls/messages, and public deployment.
- **Rollback:** Revert the PR. All callback/deck/action state is in-memory, uses deterministic mocks, and creates no migration, network request, live action, or retained file.

## PR 9: Adversarial action-safety hardening

- **Branch:** `fix/pr8-adversarial-hardening`
- **Status:** Merged as PR 9.
- **Base:** Merged PR 8 commit `fb4a231`.
- **Scope:** Retry-safe turn operations, atomic callback/deck admission, explicit ambiguous-cancellation state, cancel/dispatch serialization, session-owned action cleanup, and expanded internal-extraction/prompt-injection variants.
- **Safety decisions:** HTTP turns require client operation IDs; known action failures roll back local turn state; callback cancellation becomes non-dispatchable before provider I/O; closing sessions fail queued work closed; process-local mock adapter history is reclaimed with session resources.
- **Deferred:** Durable operation journals and distributed locks belong with durable scheduler/storage integration; no live adapter is enabled.
- **Rollback:** Revert PR 9. It adds no migration, network integration, live action, or retained artifact.

## PR 10: Callback preview retry reconciliation

- **Branch:** `fix/callback-preview-retry`
- **Status:** Merged as PR 10.
- **Base:** Merged PR 9 commit `46cf58d`.
- **Scope:** Preserve pending callback schedule claims across task cancellation, reconcile the original idempotent provider request after its due time, and propagate callback-time blocks into the preview authorization result.
- **Safety decisions:** Pending schedules consume capacity and block conflicting callback identifiers; exact retries do not create duplicate provider actions; blocked callback records cannot be labeled approved or consume approved-preview quota.
- **Deferred:** Durable conversation history, latency/evaluation observability, hybrid retrieval, knowledge graphs, speech providers, binary decks, and live channels remain separate reviewed milestones.
- **Rollback:** Revert PR 10. It changes only process-local callback retry behavior and adds no migration, network integration, live action, or retained artifact.

## PR 11: Evaluation and latency contracts

- **Branch:** `feat/evaluation-contracts`
- **Status:** Merged as PR 11.
- **Base:** Merged PR 10 commit `1e2dfe9`.
- **Scope:** Add strict privacy-minimized evaluation snapshots, a generated JSON Schema, dependency-free static reporting, and architecture contracts for real-time speech, durable memory, hybrid retrieval, knowledge graphs, and local observability.
- **Safety decisions:** Evaluation artifacts exclude raw conversation/audio content; threshold success is not deployment authority; retrieval has a hard deadline and cannot bypass policy; append-only events remain authoritative over all derived indexes.
- **Deferred:** Durable simulator journal wiring, BM25 implementation, temporal graph storage, vector/model selection, streaming speech providers, optional MLflow/Phoenix services, binary decks, and live channels remain separate reviewed milestones.
- **Rollback:** Revert PR 11. It adds no migration, model, network service, external telemetry export, live action, or retained buyer content.

## PR 12: Durable conversation journal

- **Branch:** `feat/durable-conversation-journal`
- **Status:** Merged as PR 12.
- **Base:** Merged PR 11 commit `85259a3`.
- **Scope:** Reuse each lead's existing aggregate/event stream for idempotent accepted-turn transitions, rollback-safe processing, optimistic concurrency, ambiguous-commit recovery, and deterministic restart replay.
- **Safety decisions:** No duplicate tables or migration; request fingerprints are journal-computed; raw buyer text and cumulative state are excluded; repetition digests require a managed key; partial/anonymized/malformed history fails closed; replay cannot rerun actions or silently fork live state.
- **Deferred:** Simulator/API wiring, minimized transcript/source-span persistence, BM25, temporal graph storage, vector/model selection, streaming speech, binary decks, and live channels remain separate reviewed milestones.
- **Rollback:** Revert PR 12. Existing schema remains unchanged.

## PR 13: Durable simulator history

- **Branch:** `feat/simulator-durable-history`
- **Status:** Merged as PR 13.
- **Base:** Merged PR 12 commit `75eca2d`.
- **Scope:** Wire simulator turns to the existing conversation journal with prepare/commit semantics, managed-key configuration, restart resume, and bounded minimized durable-history API.
- **Safety decisions:** Disabled by default; action failure precedes journal commit; persistence failure rolls back local state; stale sessions are invalidated; privacy closure and wrong keys fail closed; recovered action-preview responses are not reconstructed.
- **Deferred:** Durable action/scheduler state, authenticated public deployment, BM25, temporal graph storage, vector/model selection, streaming speech, binary decks, and live channels.
- **Rollback:** Disable durable history or revert PR 13. No schema migration is introduced; existing journal events remain governed by the lead privacy lifecycle.

## PR 14: Deterministic BM25 retrieval baseline

- **Branch:** `feat/bm25-retrieval-baseline`
- **Status:** Merged as PR 14.
- **Base:** Merged PR 13 commit `f491ab6`.
- **Scope:** Add dependency-free Unicode BM25 over replay-validated current structured facts, provenance-bound results, privacy/version revalidation, strict bounds, cooperative deadlines, and portable multilingual retrieval evaluation.
- **Safety decisions:** No direct storage reads, runtime cache, raw buyer text, partial timeout results, cross-lead index, action authority, vector dependency, or latency claim; evaluation artifacts exclude queries and retrieved content.
- **Deferred:** Simulator/speech-path retrieval, async hard timeout, persistent indexes, graph storage, hybrid/vector search, reranking, query expansion, and live channels.
- **Rollback:** Revert PR 14. No schema migration, persistent index, runtime API, model, network service, or external side effect is introduced.

## PR 15: Temporal lead knowledge view

- **Branch:** `feat/temporal-knowledge-graph`
- **Status:** Merged as PR 15.
- **Base:** Merged PR 14 commit `1c59cf7`.
- **Scope:** Rebuild bounded immutable lead/session/fact graphs from fully replay-validated journal history with explicit supersession, validity intervals, provenance, and conservative cross-session conflicts.
- **Safety decisions:** No direct storage reader, cache, silent latest-value merge, inferred confirmation, action authority, graph database, model, or network service; malformed history and post-build privacy/version changes fail closed.
- **Deferred:** Persistent graph projections, organization/product/competitor entities, automatic conflict resolution, BM25/graph fusion, HTTP/RAG wiring, embeddings, and live channels.
- **Rollback:** Revert PR 15. No schema migration, persistent graph, API, provider, or external side effect is introduced.

## PR 16: Graph-aware lead retrieval

- **Branch:** `feat/graph-aware-retrieval`
- **Status:** Merged as PR 16.
- **Base:** Merged PR 15 commit `556f9a8`.
- **Scope:** Add explicit session/lead BM25 scope and no-cache lead retrieval over current and conflicting temporal claims while excluding superseded claims and retaining status/provenance.
- **Safety decisions:** Mixed leads fail closed; session scope remains the default; graph construction counts toward the cooperative deadline; timeout returns no partial results; privacy/version is rechecked after scoring and timeout; retrieval remains non-authoritative.
- **Deferred:** Simulator/speech response wiring, async hard timeout, persistent indexes, structural graph queries, automatic conflict resolution, hybrid/vector search, reranking, and live channels.
- **Rollback:** Revert PR 16. No schema migration, persistent index, API, model, provider, or external side effect is introduced.

## PR 17: Graph retrieval evaluation

- **Branch:** `feat/graph-retrieval-evaluation`
- **Status:** Merged as PR 17.
- **Base:** Merged PR 16 commit `640d5ae`.
- **Scope:** Add a reviewed multilingual temporal retrieval suite and deterministic production-path runner with recall, rank, timeout, informational latency, and superseded-claim exposure metrics.
- **Safety decisions:** All superseded claims in a case require explicit exclusion gold; exposure threshold is zero; active statuses must match cross-session values; artifacts omit queries, claims, gold identifiers, and retrieved values; fixtures use no storage, model, or network service.
- **Deferred:** Simulator/speech response wiring, async hard timeout, persistent indexes, structural graph queries, automatic conflict resolution, hybrid/vector retrieval, reranking, and live channels.
- **Rollback:** Revert PR 17. No schema migration, runtime API, model, provider, retained buyer data, or external side effect is introduced.

## PR 18: Callback cancellation reconciliation

- **Branch:** `fix/callback-cancellation-reconciliation`
- **Status:** Merged as PR 18.
- **Base:** Merged PR 17 commit `816aa3d`.
- **Scope:** Preserve permanent scheduler cancellation rejection as an explicit non-dispatchable reconciliation state, reject failed idempotency-key reuse, and support new-key/manual or cleanup recovery.
- **Safety decisions:** Unresolved jobs continue consuming callback capacity; due dispatch excludes them; cleanup keys bind schedule incarnation and attempt, ambiguous outcomes retain the same key, permanent rejection advances it, failed keys remain tombstoned, and local state is removed only after provider acknowledgement.
- **Deferred:** Durable scheduler state, provider-specific reconciliation/webhooks, graph benchmark journal fidelity, simulator admission locking, prompt-injection paraphrase hardening, retrieval wall-clock orchestration, and live channels.
- **Rollback:** Revert PR 18. This changes only bounded process-local callback/mock behavior and adds no schema migration, network provider, live action, or durable schedule.

## PR 19: Retrieval budgets, admission locking, projection fidelity, and paraphrase-resistant safety

- **Branch:** `perf/retrieval-wallclock-deadline`
- **Status:** Implementation complete; awaiting review.
- **Base:** Merged PR 18 commit `3ba645d`.
- **Scope:** Clear the queued reliability and safety gaps in one reviewable change.
  1. Introduce a single `RetrievalDeadline` per search, shared across temporal graph projection, document materialization, tokenization/indexing, scoring, and ranking, reporting version-preserving timeouts when any step exhausts it.
  2. Serialize simulator session admission behind a registry lock plus an explicit reservation set, so concurrent create/resume calls on FastAPI's worker threadpool cannot exceed capacity, double-admit a resumed session, or orphan restored conversation state.
  3. Give customer confirmation real provenance: `TemporalFactClaim` records `confirmed_by_revision_id` and `confirmed_at`, confirmation survives later supersession, and the builder depends on a narrow `LeadKnowledgeSourceReader` protocol instead of the concrete journal.
  4. Replay graph retrieval evaluation cases as journal fact/revision snapshots through the production `TemporalKnowledgeGraphBuilder` and gate on `graph_retrieval.projection_fidelity` instead of hand-assigning claim status in the fixture.
  5. Supplement literal safety phrase lists with clause-bounded, paraphrase-resistant intent templates so reworded opt-out, internal-instruction extraction, and prompt injection are caught without new false positives on ordinary buyer turns, with new adversarial corpus cases and explicit benign counter-examples.
- **Safety decisions:** Timeouts still return no partial results and no indexed count; graph-projection expiry carries the source aggregate version so privacy state and version are rechecked exactly once; BM25 corpus invariants are evaluated before the deadline so corruption is never downgraded to a retryable timeout. Registry critical sections hold only in-memory dictionary operations and never span `await` or blocking journal I/O; failed resume releases its reservation in the same critical section that publishes the session. Confirmation is retained on superseded claims because it is a historical fact, and the model rejects confirmation without complete provenance or dated before the claim's validity. Safety templates never span a clause boundary, suppress only reported first-person speech rather than any first-person token, require both a contact noun and a recurrence marker before a bare negator counts as an opt-out, refuse a negator preceded by an invitation marker ("why not call me again?"), and stand down entirely when another clause asks to be contacted later, because opt-out is terminal and a contradictory turn must stay recoverable. Literal opt-out phrases match whole tokens and consult the space-stripped form only for visibly separator-obfuscated turns. Templates are evaluated over both the format-character-dropped and format-character-separated tokenizations, and short-circuit on absent groups so adversarial long turns stay sub-millisecond. The simulator holds its admission reservation across action cleanup as well as conversation teardown, and republishes the session if cleanup fails so closing stays retryable.
- **Deferred:** Asynchronous hard timeouts, preempting the synchronous journal load, persistent indexes, simulator/speech response wiring, durable simulator session registry, learned or model-based safety classification, and live channels. `graph_retrieval.projection_fidelity` compares claims, fact payloads, and relations, but it is a regression gate on `TemporalKnowledgeGraphBuilder`, not an independent gate on the corpus: the suite validator derives each claim's expected status from the same supersession fields the builder reads. Turning it into a corpus gate requires `status` to become a separately reviewed label the validator does not synthesise, which is deferred to a later reviewed corpus revision.
- **Rollback:** Revert PR 19. It adds no schema migration, persistent index, API surface, model, provider, retained buyer data, or external side effect.
