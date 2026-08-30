# Progress

## PR 1: Foundation audit

- **Branch:** `chore/foundation-audit`
- **Status:** Foundation implementation, commits, and local validation complete; push, CI, and PR pending.
- **Environment:** Windows, Python 3.12.10
- **Commits:** `593fe49`, `0a4931f`, `e1aaa51`
- **Scope:** Python packaging, dependency lock, safe configuration defaults, FastAPI health endpoint, CI, contribution/security guidance, and baseline validation.
- **Deferred:** Architecture/compliance details, domain models, persistence, provider adapters, simulator, speech/runtime benchmarks, conversation logic, actions, evaluations, WebRTC, deployment, and live integrations.
- **Safety:** Telephony, WhatsApp, external networking, real-time audio, and hosted-demo behavior remain disabled by default.
- **Rollback:** Revert the foundation PR. No persistent data migration or external side effect is introduced.

### Audit decisions

- Removed uncommitted implementations belonging to later PR milestones.
- Removed permissive wildcard CORS and simulator endpoints from the foundation API.
- Removed placeholder VAD benchmark numbers so they cannot be mistaken for measurements.
- Removed the destructive storage utility and duplicate unmanaged schema approach pending the reviewed storage milestone.

## PR 2: Architecture and compliance documentation

- **Branch:** `docs/architecture-compliance`
- **Status:** Documentation and local validation complete; commits, push, CI, and PR pending.
- **Base:** Merged PR 1 commit `ee9b2c1`
- **Scope:** Target architecture, deployment profiles, trust boundaries, threat model, compliance/privacy gates, operational cleanup/rollback, source register, and architecture decisions.
- **Implementation impact:** Documentation only; no runtime capability or external side effect is added.
- **Rollback:** Revert the documentation PR. No application state or external provider is changed.
