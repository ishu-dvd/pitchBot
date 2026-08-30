# Operations, Cleanup, and Rollback

## Current foundation

Run locally after installing the locked development dependencies:

```powershell
Copy-Item .env.example .env
python -m uvicorn pitchbot.main:app --reload
```

Stop with `Ctrl+C`. The foundation creates no database, queue, generated artifact, provider account, or external message.

## Planned profile controls

### `local-full`

- Explicit startup and health/readiness checks.
- Local persistent volumes documented before use.
- Backup/restore and migration checks before upgrades.
- CPU and optional GPU resource profiles.

### `hosted-demo`

- Synthetic data only.
- External actions disabled in code and configuration.
- Strict request, session, and resource quotas.
- No availability or persistence guarantee on free hosting.

### `live-disabled`

- Provider credentials absent by default.
- Activation requires reviewed configuration and operator approval.
- Kill switch disables new actions without deleting audit evidence.

## Cleanup requirements

Future cleanup tooling must support:

- Stop API/workers and cancel queued jobs.
- Delete generated local artifacts and transient audio.
- Export, anonymize, or delete selected lead journeys.
- Expire backups consistently with primary data.
- Revoke provider credentials and webhook registrations.
- Remove allowlisted test contacts without logging their values.

## Rollback requirements

- Application rollback uses a reviewed release tag or reverted PR.
- Schema rollback must be tested separately; never delete customer data merely to downgrade code.
- Disable external action flags before rollback.
- Preserve suppression records and idempotency history across compatible rollbacks.
- Record the version, reason, operator, validation, and unresolved risks.

## Recovery objectives

No production recovery objective is claimed during the zero-cost simulator phase. Establish measured recovery and data-loss objectives before any live pilot.
