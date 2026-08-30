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
