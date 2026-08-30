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
