.PHONY: install format lint type test migrations audit check run

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

migrations:
    python -c "from pathlib import Path; Path('data').mkdir(exist_ok=True)"
    python -m alembic upgrade head

audit:
    python -m pip_audit

check: lint type test migrations audit

run:
    python -m uvicorn pitchbot.main:app --reload --app-dir src
