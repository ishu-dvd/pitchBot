# Changelog

All notable changes to PitchBot are documented here.

## Unreleased

### Added

- Python 3.12 project and pinned development dependency lock.
- FastAPI health endpoint.
- Default-off configuration for all external side effects.
- Ruff, mypy, pytest, pre-commit, dependency audit, and CI configuration.
- Contribution, security, branching, progress, and test-report documentation.
- Target component, sequence, deployment, and data-flow architecture.
- Compliance/privacy gates, threat model, operational rollback guidance, source register, and architecture decision records.
- Immutable domain contracts and Alembic-managed append-only journey storage.
- Durable aggregate versions, suppression history, privacy-operation audit, redacted export, confirmed anonymization/deletion, and retention controls.
- Provider-neutral speech, model, channel, scheduler, research, artifact, object-storage, and clock contracts.
- Deterministic bounded mocks with strict idempotency, fault injection, network denial, retries, timeouts, and circuit breaking.
- Same-origin browser simulator with AI disclosure, text turns, explicit action previews, deterministic replay/failures/latency, bounded history, interruption, and session cleanup.
- Bounded metadata-only `MediaRecorder`/WebSocket transport with Opus preference, backpressure, chunk limits, and capped reconnects.
- Versioned VAD/STT/TTS/model candidate and synthetic corpus registries with license/provenance gates.
- Unicode-aware WER/CER, VAD overlap, real-time factor, structured-output, duration-regression, timing, environment, and manifest validation utilities.
- Deterministic multilingual conversation safety, bounded fact/revision extraction, evidence-grounded intent classification, and synthetic adversarial/persona cases.
- Deny-by-default mock action authorization, minimized follow-ups, fake-time callback lifecycle, and six-industry structured sample-deck previews.
- Strict evaluation-run contracts, generated JSON Schema, and dependency-free local HTML reports.
- Restart-safe append-only conversation journaling with incremental transitions, typed-input retry reconciliation, rollback-safe persistence, optimistic concurrency, and fail-closed replay.
- Shared wall-clock retrieval budgets enforced cooperatively across graph projection, indexing, scoring, and ranking, with version-preserving timeouts and no partial results.
- Customer-confirmation provenance on temporal fact claims, retained across supersession and derived only from confirmed journal revisions.
- Graph retrieval evaluation now projects reviewed corpora through the production temporal builder and gates on projection fidelity.
- Paraphrase-resistant safety detection for opt-out, internal-instruction extraction, and prompt injection using bounded-window intent templates.
- Non-authoritative lead recall on simulator turns: budgeted graph-aware retrieval of the lead's own prior claims, run after the durable commit, skipped on safety signals, non-continuing dispositions, and durable replay, run off the event loop with a per-session failure budget, and rendered read-only in the browser demo.
- Streaming speech turn-taking: a `VoiceActivityDetector` contract and deterministic mock, an endpointing/barge-in state machine, and a transcription pipeline wired into the simulator audio WebSocket so a spoken utterance becomes an ordinary turn. No speech-to-text provider is selected, so utterances report `transcriber-unavailable` by default; audio is buffered only for the utterance in flight, byte-capped, and never persisted.
- Synthetic voice-activity structural benchmark: a deterministic, dependency-free audio generator that emits a reproducible WAV plus ground-truth-labeled byte frames from a seed, a hash-verified VAD corpus regenerated at run time rather than committed as binary, and a `run-speech` command that scores per-language/condition/vertical F1 through the existing detector contract and gates the corpus in CI. Its gate fails closed — it validates that the required VAD metrics are present and returns a non-zero exit code on failure, rather than inheriting the shared runners' fail-open, always-return-zero behaviour. Voice activity is the only speech dimension measurable without a model; STT and TTS remain blocked pending reviewed real audio.

### Fixed

- Multilingual safety parity in the conversation matcher: do-not-message opt-outs ("stop messaging me", `mujhe WhatsApp mat bhejna`, `मुझे संदेश मत भेजो`) are now detected as immediate opt-outs in English, Hindi, and Hinglish; requests for our own operating rules or policies are detected while scoped product questions ("your policies on returns") stay clean; a bare Devanagari `बंद करो` no longer terminally opts a buyer out of a demo they only asked to close; and Latin homoglyphs, marks stacked on a Latin base, and safety words split across spaces no longer bypass detection.

### Security

- Added CI secret scanning.
- Excluded environment files, secrets, runtime data, logs, and artifacts from version control.
- Hardened mock action retries, callback cancellation races, concurrent capacity admission, session cleanup, and paraphrased internal-instruction extraction attempts.
- Reconciled canceled callback scheduling attempts without false approval or duplicate provider actions.
- Kept the speech and durable turn paths live under fault: a voice-activity failure now reaches the turn-taking machine as silence instead of pinning an open utterance in `LISTENING`, audio abandoned when the agent yields the floor is released instead of being prepended to the next utterance, the durable journal replay and commit run off the event loop that serves the audio socket, and a session whose cleanup fails during invalidation stays addressable so a delete can reclaim its callback and deck capacity.
