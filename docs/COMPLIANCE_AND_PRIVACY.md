# Compliance and Privacy Gates

This is an engineering checklist, not legal advice. Applicable law, telecom rules, provider terms, and organizational policy must be reviewed by qualified counsel before live outreach.

## AI identity and purpose

- PitchBot identifies itself as an AI sales assistant at the beginning of a live interaction.
- It states the business purpose without pretending to be a person.
- If disclosure cannot be delivered or understood, the live interaction must not proceed.

## Contact authorization

Before every live call or message, policy code must verify:

- Documented legal basis and applicable consent requirements.
- Current suppression/opt-out state.
- India DND/telemarketing eligibility through an approved process.
- Timezone and currently approved contact window.
- Channel-specific consent and provider policy.
- Participant allowlisting during pilots.
- Per-lead and global usage caps.

Unknown or unavailable checks fail closed.

The action policy already enforces the AI-disclosure, contact-allowlist, DND, and calling-hours checks **unconditionally** today (`src/pitchbot/actions/policy.py`): each failed check blocks the action, and no configuration setting can disable any of them. There is deliberately no `require_ai_disclosure`, `require_dnd_check`, `require_calling_hours`, or `allowlist_enabled` toggle — such a switch could only make a mandatory safety gate optional.

## Recording and transcription

- Inform participants and obtain consent where required before recording or retaining transcripts.
- Support a no-recording path where the approved operating model permits it.
- Separate transient speech processing from retained lead facts.
- Never retain raw audio by default.

## Opt-out and suppression

- Treat clear stop, do-not-call, and do-not-message requests as immediate opt-outs.
- Persist suppression across sessions and recheck it at action execution time.
- Do not use persuasion to override an opt-out.
- Provide an auditable correction path for erroneous contact data.

## Data minimization

Collect only information needed for the agreed sales workflow, such as business needs, product type, requested features, budget range, timeline, decision process, blockers, and next steps. Do not solicit or infer caste, religion, health, political views, family status, precise location, or unrelated financial details.

## Retention and data rights

The storage milestone must provide configurable retention plus export, correction, anonymization, and deletion workflows. Audit records should retain policy outcomes without retaining unnecessary sensitive content. Backups must follow the same expiration policy.

## WhatsApp and telephony

- Use only official provider interfaces and approved sender/caller identities.
- WhatsApp messaging access must not be interpreted as permission or capability for automated WhatsApp calls.
- Verify current geography, eligibility, templates, consent, pricing, and calling support before implementation.
- Do not automate personal WhatsApp sessions, browser cookies, QR sessions, or reverse-engineered calling protocols.

## Live activation checklist

All items must pass:

1. Official provider and policy path verified against current documentation.
2. Legal/compliance review completed.
3. AI disclosure and recording language approved.
4. Consent, DND, calling-hours, suppression, and allowlist checks tested.
5. Credentials stored outside source control with least privilege.
6. Webhooks verify signatures and prevent replay.
7. Costs and action counts have hard caps.
8. Monitoring, incident response, and kill switch tested.
9. Data export/deletion and credential revocation tested.
10. Operator explicitly approves the bounded pilot.

## Incident response

- Disable live channel flags and revoke affected credentials.
- Stop queued jobs and preserve minimal audit evidence.
- Identify affected contacts/data and required notifications.
- Correct suppression and retention state.
- Document cause, impact, remediation, and safe restart decision.
