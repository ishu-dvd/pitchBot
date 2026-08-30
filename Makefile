.PHONY: install format lint type test audit check run

install:
    python -m pip install -r requirements-dev.lock
    python -m pip install -e . --no-deps

format:
    ruff format .

lint:
    ruff check .
    ruff format --check .

type:
    mypy src tests

test:
    python -m pytest

audit:
    python -m pip_audit

check: lint type test audit

run:
    python -m uvicorn pitchbot.main:app --reload --app-dir src
