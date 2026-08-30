# Contributing

## Branching
- `main` is protected.
- Create short-lived feature branches from `main`.
- Keep commits scoped and buildable.

## Local setup
1. Create a virtual environment.
2. Install dependencies: `pip install -e .[dev]`
3. Install hooks: `pre-commit install`
4. Run checks: `ruff check . && mypy src tests && pytest`

## Pull requests
- Require CI green before merge.
- Do not commit secrets, phone numbers, or personal audio.
- Keep all external side effects disabled by default.
- Add/update tests and docs for behavior changes.
