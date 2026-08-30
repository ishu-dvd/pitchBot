# Test Reports

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
