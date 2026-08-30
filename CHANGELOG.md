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

### Security

- Added CI secret scanning.
- Excluded environment files, secrets, runtime data, logs, and artifacts from version control.
