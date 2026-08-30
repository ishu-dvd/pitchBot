# PitchBot

PitchBot is a zero-cost-first, bilingual sales-assistant project for English and Hindi e-commerce discovery conversations.

This foundation milestone provides only:

- Python packaging and development tooling.
- A FastAPI application with a health endpoint.
- Configuration whose external side effects are disabled by default.
- CI, contribution, security, and branch-gate documentation.

Conversation logic, storage, provider adapters, browser simulation, speech models, evaluations, deployment, telephony, and WhatsApp are intentionally deferred to separately reviewed pull requests.

## Requirements

- Python 3.12

## Local setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.lock
python -m pip install -e . --no-deps
Copy-Item .env.example .env
python -m uvicorn pitchbot.main:app --reload
```

Open `http://127.0.0.1:8000/health` to confirm the API is running.

## Validation

```powershell
ruff check .
ruff format --check .
mypy src tests
python -m pytest
python -m pip_audit
```

## Safety defaults

Telephony, WhatsApp, external network access, real-time audio, and hosted-demo behavior are disabled in `.env.example`. Never commit `.env`, credentials, phone numbers, personal audio, or live transcripts.

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and [docs/BRANCHING_AND_GATES.md](docs/BRANCHING_AND_GATES.md).
