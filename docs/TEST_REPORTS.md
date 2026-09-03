# Test Reports

> **Maintenance note (PR 31):** These per-PR test reports are maintained through **PR 12** (the
> most recent entries below cover PR 11's evaluation contracts and PR 12's durable conversation
> journal). From **PR 13 onward**, per-PR validation is recorded instead in the `Scope` / `Safety
> decisions` / `Test decisions` narrative of [PROGRESS.md](PROGRESS.md) and enforced by the CI
> gates in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml): ruff lint/format, mypy,
> pytest, an Alembic migration check, the benchmark manifest and fail-closed
> evaluation/retrieval/VAD gates, `pip-audit`, and a Gitleaks secret scan. This document is
> deliberately **not** backfilled for PRs 13-29 — reconstructed historical test counts cannot be
> verified against each PR's tree at its merge time, and stating an unverifiable number would
> violate the project's no-overstatement standard. For reference, the current tree at commit
> `8f72520` passes ruff and mypy cleanly and 485 pytest tests.

## Foundation audit

- **Date:** 2026-08-30
- **Environment:** Windows, Python 3.12.10
- **Branch:** `chore/foundation-audit`

### Initial audit

- `python -m pytest`: could not run before dependency installation (`No module named pytest`).
- Development dependencies were then installed in an isolated `.venv`.
- The original TestClient-based test passed with a deprecation warning; it was replaced with an async ASGI transport test.
- `pip-audit`: no known vulnerabilities found; the local editable `pitchbot` package is skipped because it is not a PyPI dependency.

### Final validation

- `ruff check .`: passed.
- `ruff format --check .`: passed (15 files formatted).
- `mypy src tests`: passed (5 source files checked).
- `pytest`: passed (2 tests in 0.93 seconds, no warnings).
- `pip-audit --skip-editable`: passed; no known vulnerabilities found.
- `pre-commit run --all-files`: passed (Ruff lint and format hooks).
- Staged common-secret and Indian phone-number pattern scan: passed.
- `git diff --cached --check`: passed.
- Post-commit `pytest`: passed (2 tests in 0.87 seconds).
- Post-commit Ruff lint/format, mypy, and pre-commit hooks: passed.

### PR CI correction

- The initial application test job passed.
- The initial Gitleaks job failed before scanning because its current PR integration requires the automatic GitHub Actions token explicitly.
- Added least-privilege read permissions and passed `${{ secrets.GITHUB_TOKEN }}` only to the Gitleaks action.

## Architecture and compliance documentation

- **Date:** 2026-08-30
- **Environment:** Windows, Python 3.12.10
- **Branch:** `docs/architecture-compliance`

### Documentation validation

- Local Markdown links: passed.
- Markdown code-fence and trailing-whitespace checks: passed for 18 files.
- Official external source reachability: 16 returned HTTP 200; the MeitY portal returned access-restricted HTTP 403 and is documented as such.
- Documentation review: corrected current-vs-target enforcement wording and characterized government portal links as non-legal citations.

### Regression validation

- `pytest`: passed (2 tests in 0.99 seconds).
- Ruff lint and format checks: passed.
- `mypy src tests`: passed (5 source files checked).
- `pre-commit run --all-files`: passed.
- `git diff --check`: passed.

## Domain and storage

- **Date:** 2026-08-30
- **Environment:** Windows, Python 3.12.10, SQLite
- **Branch:** `feat/domain-storage`

### Validation

- Ruff lint and format: passed.
- `mypy src tests migrations`: passed (21 source files checked).
- `pytest`: passed (19 tests in 2.09 seconds, no warnings).
- Fresh `alembic upgrade head`: passed.
- `alembic check`: passed; no schema drift detected.
- `pip-audit --skip-editable`: passed; no known vulnerabilities found.

### Failures found and corrected

- Test migrations initially followed a lingering `PITCHBOT_DATABASE_URL`; fixtures now scope the environment to each temporary database.
- Hard deletion initially removed suppression and aggregate heads; it now preserves suppression, closes a durable aggregate tombstone, records the privacy operation, and rejects future writes.
- Retention now normalizes UTC cutoffs, preserves aggregate version heads, and cannot reset version continuity.
- Alembic path configuration was updated to remove its legacy separator warning.
- Self-review added idempotent privacy operations, non-empty suppression validation, timezone-required retention CLI input, and tests that closed aggregates cannot be recreated.
- CI self-review removed duplicate feature-branch push runs; feature branches run through pull requests, while `main` pushes and manual dispatch remain available.

## Provider contracts and deterministic mocks

- **Date:** 2026-08-31
- **Environment:** Windows, Python 3.12.10
- **Branch:** `feat/provider-mocks`

### Validation

- `pytest`: passed (39 tests in 1.29 seconds, no warnings).
- Ruff lint and format: passed.
- `mypy src tests migrations`: passed (32 source files checked).

### Self-review corrections

- Corrected async streaming protocol shapes to return asynchronous iterators rather than coroutine-wrapped iterators.
- Added strict idempotency fingerprints; identical retries are stable and different input with the same key is rejected.
- Separated scheduler/artifact/object resource identifiers from operation idempotency keys.
- Added one-probe half-open circuit behavior.
- Bounded mock histories and minimized retained contact, message, prompt, URL, and audio content.
- Kept disabled external adapters unconditionally fail-closed rather than accepting an enabled policy without an implementation.
- Added boundary validation for timezone-aware audio, sequence/sample ranges, transcript confidence, media types, and action-result identifiers.
- Final adversarial review capped the initial retry delay and ensured canceled/unexpected half-open probes release their slot and reopen safely.

## Browser simulator

- **Date:** 2026-08-31
- **Environment:** Windows, Python 3.12.10
- **Branch:** `feat/browser-simulator`

### Final validation

- `pytest`: passed (54 tests in 3.07 seconds, no warnings).
- Ruff lint and format: passed.
- `mypy src tests migrations`: passed (40 source files checked).
- Browser JavaScript module syntax: passed with Node 24.12.0.

### Mandatory pre-commit self-review

- Removed cross-session history keyed by reusable lead references; history is now bounded within the session capability.
- Added generation-based microphone startup cancellation and reconnect-timer cleanup so closing a session cannot resurrect audio.
- Tightened Content Security Policy connections to same-origin only.
- Added server-side WebSocket Origin/Host validation to prevent cross-site socket use.
- Added explicit session closure/capacity recovery and clean WebSocket handling when sessions close.

## Speech and local runtime benchmark harness

- **Date:** 2026-08-31
- **Environment:** Windows 11, Python 3.12.10, Intel64 CPU, 8 logical CPUs, no accelerator declared
- **Branch:** `bench/speech-runtime`

### Final validation

- `pytest`: passed (73 tests in 4.55 seconds, no warnings).
- Ruff lint and format: passed.
- `mypy src tests migrations`: passed (51 source files checked).
- Candidate registry: validated 8 entries.
- Planned corpus: validated 12 entries; canonical SHA-256 `5b7927b81b856d60aedc11d1d95960e39e8f21c568b6b54455a72cc8e8ea1526`.
- Benchmark CLI environment capture: passed.

No VAD, STT, TTS, STS, or model result is claimed by this report.

## Evaluation and latency contracts

- **Date:** 2026-09-01
- **Environment:** Windows 11, Python 3.12.10, SQLite
- **Branch:** `feat/evaluation-contracts`

### Final validation

- `pytest`: passed (132 tests).
- Ruff lint and format: passed (91 files checked).
- `mypy src tests`: passed (64 source files checked).
- `pre-commit run --all-files`: passed.
- Candidate registry and synthetic speech corpus validation: passed.
- Generated evaluation JSON Schema drift test: passed.
- Browser JavaScript syntax: passed.
- Fresh Alembic upgrade and schema-drift check: passed.
- Local Markdown links and code fences: passed.
- `pip-audit`: no known vulnerabilities; the local project is not published on PyPI and was skipped.

### Adversarial self-review corrections

- Made `evaluation_schema_version` mandatory.
- Added state-dependent JSON Schema conditions while retaining CLI validation as the authority for semantic and cross-item invariants.
- Replaced unrestricted evaluation hardware notes with bounded identifiers and numeric capacity fields.
- Prevented report generation from overwriting its source artifact through direct paths, symlinks, or hard-link aliases.

No speech, retrieval, model, or production-latency result is claimed. Passing artifact thresholds remain evidence for review, not automatic promotion.

## Durable conversation journal

- **Date:** 2026-09-01
- **Environment:** Windows 11, Python 3.12.10, SQLite
- **Branch:** `feat/durable-conversation-journal`

### Final validation

- `pytest`: passed (146 tests).
- Ruff lint and format: passed (93 files checked).
- `mypy src tests`: passed (66 source files checked).
- `pre-commit run --all-files`: passed.
- Candidate registry, synthetic speech corpus, and browser JavaScript syntax: passed.
- Fresh Alembic upgrade and schema-drift check: passed; no migration was added.
- `pip-audit`: no known vulnerabilities; the local project is not published on PyPI and was skipped.

### Failures found and corrected

- Full replay initially restored the engine's default goal-change threshold; that safety threshold now belongs to checkpointed session state.
- Replay initially parsed an already-validated journal event twice; it now returns the validated latest checkpoint directly.
- Write-time checks reject stale lead versions, oversized payloads, unpersisted live turns, and restoration over a live session.

### Adversarial self-review corrections

- Replaced cumulative checkpoints with per-turn transitions so expiring an older event cannot leave its facts copied in newer events.
- Moved all sessions under the lead aggregate so existing lead-level privacy operations cover every journal.
- Added aggregate privacy-state checks around event loading to detect concurrent anonymization/deletion.
- Bound fingerprints to typed input inside rollback-safe processing; persistence failures restore the prior live state.
- Replaced unkeyed turn hashes with session-bound HMAC-SHA-256 and fail-closed digest-key identity checks on replay.
- Added durable/live state comparison and explicit synchronization so competing writers cannot create two first turns.
- Serialized append against privacy closure with an active-state/version compare-and-swap, and made anonymization/deletion close the aggregate before mutating events.
- Removed an invalid goal-change-count/turn-count relationship because one turn can validly revise multiple facts.
- Made operation fingerprints session-bound HMAC-SHA-256 values under the same managed digest key.
- Capped journal capacity at 9,999 so the bounded 10,000-row overflow probe remains valid.
- Counted every shared lead-stream event toward journal capacity so unrelated lead events cannot make a newly appended turn immediately unreplayable.
- Required exact 1-based aggregate-version continuity, not only matching event count and aggregate head.

No simulator/API path writes durable events yet, and no raw buyer transcript is claimed as retained.

### Mandatory pre-commit self-review

- Rejected NaN/Infinity in intervals, durations, timers, and measured metrics.
- Penalized extra structured-output fields rather than ignoring hallucinated keys.
- Required generated/available audio files, in-manifest paths, bounded size, SHA-256, and provenance; planned items cannot carry unverified audio evidence.
- Bounded manifest, audio, and transcript inputs and rejected non-standard JSON constants.
- Required non-empty finite metrics plus exact non-placeholder candidate revision and verified model/voice license for measured results.
- Prevented overlapping process-global `tracemalloc` measurements.

## Deterministic conversation intelligence

- **Date:** 2026-08-31
- **Environment:** Windows 11, Python 3.12.10
- **Branch:** `feat/conversation-intelligence`

### Final validation

- `pytest`: passed (91 tests, no warnings).
- Ruff lint and format: passed.
- `mypy src tests migrations`: passed (57 source files checked).
- Pre-commit hooks: passed.
- Dependency audit: passed with no known vulnerabilities; the editable local project was skipped.

### Safety coverage

- Explicit opt-out takes precedence over abuse and prompt-injection signals, closes the conversation, and suppresses action previews.
- Abuse receives one neutral redirection before a second abusive turn closes the conversation.
- Internal-information and instruction-bypass requests are refused before fact or intent extraction.
- Repeated turns do not duplicate facts or evidence; changed requirements create explicit revisions and excessive changes request review.
- Classification uses only explicit commercial evidence and excludes language, accent, frustration, persona labels, and protected or sensitive traits.
- Synthetic English, Hindi, and Hinglish cases cover varied interaction styles and adversarial behaviors without inferring personas for real buyers.

### Mandatory pre-commit self-review

- Deduplicated evidence dimensions so paraphrased requests cannot inflate intent scores.
- Returned only facts and revisions accepted within configured state bounds.
- Suppressed previews for redirects, review-required turns, and stopped conversations.
- Rejected text, injected failures, interruption, and audio after conversation stop without retaining rejected input.
- Hardened opt-out, internal-information, and prompt-injection matching against common separator and zero-width obfuscation.
- Ordered simulator and conversation session creation to avoid partial session state.

## Guarded follow-ups, callbacks, and sample decks

- **Date:** 2026-08-31
- **Environment:** Windows 11, Python 3.12.10
- **Branch:** `feat/followups-artifacts`

### Final validation

- `pytest`: passed (104 tests, no warnings).
- Ruff lint and format: passed.
- `mypy src tests migrations`: passed (64 source files checked).
- Browser JavaScript syntax: passed.
- Pre-commit hooks: passed.
- Dependency audit: passed with no known vulnerabilities; the editable local project was skipped.

### Safety coverage

- Default and unknown policy state blocks every preview with explicit reasons.
- Explicit synthetic disclosure, consent, contact eligibility, conversation disposition, classification state, and quota are required.
- Follow-up summaries ignore raw transcript and contact fields.
- Fake-time callbacks validate future bounds, separate resource/operation keys, support cancel/reschedule, and recheck opt-out before mock dispatch.
- Six-industry deck previews use fixed templates and allowlisted feature labels; no binary file or arbitrary buyer content is rendered.
- Simulator previews invoke deterministic in-memory mocks only and never report a live action as executed.

### Mandatory pre-commit self-review

- Blocked Cold classifications in addition to review-needed classifications.
- Counted only approved mock actions against the per-session quota.
- Preserved immutable per-operation callback results for schedule/cancel idempotency replay.
- Prevented blocked callback attempts from consuming active capacity and rejected cancellation outside scheduled state.
- Removed lead identity from scheduler payloads and replaced arbitrary callback agenda/next-step content with fixed values.
- Removed arbitrary deck business names and allowlisted every follow-up/deck field that reaches mock adapters or previews.

## Adversarial action-safety hardening

- **Date:** 2026-08-31
- **Environment:** Windows 11, Python 3.12.10
- **Branch:** `fix/pr8-adversarial-hardening`

### Final validation

- `pytest`: passed (120 tests, no warnings).
- Ruff lint and format: passed.
- `mypy src tests`: passed (62 source files checked).
- Browser JavaScript syntax: passed.
- Pre-commit hooks: passed.
- Dependency audit: passed with no known vulnerabilities; the editable local project was skipped.

### Mandatory adversarial self-review corrections

- Replaced counter-based preview identity with client operation IDs and whole-turn replay.
- Rolled back conversation, event, language, sequence, and quota state after known action failures or task cancellation while retaining retry-stable callback timing.
- Bounded retained successful and failed turn operations and failed closed when unique operation capacity is exhausted.
- Serialized callback/deck admission and callback cancellation/dispatch; ambiguous cancellation now remains non-dispatchable until reconciled.
- Marked sessions closing before awaited cleanup so queued turns, interruption, and audio work fail closed while failed cleanup remains retryable.
- Marked cleanup callbacks non-dispatchable before provider cancellation so ambiguous cleanup failures remain fail closed.
- Reclaimed session-owned callback, deck, WhatsApp, scheduler, telephony, artifact, and idempotency state from process-local mocks.
- Expanded English/Hinglish unsafe-instruction variants and added a benign configuration control to limit false positives.

## Callback preview retry reconciliation

- **Date:** 2026-08-31
- **Environment:** Windows 11, Python 3.12.10
- **Branch:** `fix/callback-preview-retry`

### Final validation

- `pytest`: passed (124 tests, no warnings).
- Ruff lint and format: passed.
- `mypy src tests`: passed.
- Browser JavaScript syntax: passed.
- Pre-commit hooks: passed.
- Dependency audit: passed with no known vulnerabilities; the editable local project was skipped.

### Safety coverage

- A schedule accepted before task cancellation remains a pending local operation.
- Retrying after the original due time reconciles the exact request and does not duplicate the scheduler action.
- Pending schedules count against capacity and reject conflicting callback identifiers.
- Inactive callback records cannot bypass a full active-callback capacity.
- Definitive scheduler failures release pending capacity; ambiguous cancellation remains retryable.
- Invalid callback times produce blocked preview decisions and cannot consume approved quota.
