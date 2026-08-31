# Threat Model

## Scope

The model covers browser audio/text input, local or hosted APIs, model and speech adapters, lead records, customer-provided URLs/files, schedulers, and future official communication providers.

## Assets

- Customer contact preferences, transcripts, requirements, and schedules.
- Seller identity, portfolio, and approved messaging.
- Provider credentials and webhook secrets.
- System instructions, strategy rules, and policy configuration.
- Lead classification evidence and evaluation results.
- Generated artifacts and audit history.

## Trust boundaries

1. Browser/client to API.
2. Untrusted transcript, webpage, file, and prior-note content to orchestration.
3. Orchestration to local/remote model and speech adapters.
4. Policy engine to external action adapters.
5. Application to storage and scheduler.
6. Provider webhooks to application.
7. Operator activation and administrative controls.

## Threats and required controls

| Threat | Example | Required control |
|---|---|---|
| Prompt injection | A webpage says to reveal keys or send messages | Treat retrieved content as data; strict schemas; no tool authority from content |
| SSRF | Customer supplies a metadata or private-network URL | Resolve and block private/link-local/loopback ranges; restrict redirects, schemes, ports, sizes, and timeouts |
| Secret disclosure | Prompt asks for API keys or internal instructions | Secrets never enter model context; redact logs; environment/secret-store boundaries |
| Unauthorized outreach | Model proposes a call or message | Deterministic consent, suppression, DND, hours, allowlist, quota, and operator gates |
| Duplicate actions | Retries send repeated messages | Persisted idempotency keys, unique constraints, and replay-safe adapters |
| Induced denial of service | Repeated WhatsApp/artifact requests | Per-lead/global quotas, coalescing, bounded queues, timeouts, cancellation, and circuit breakers |
| PII leakage | Phone numbers appear in logs/evals | Data minimization, structured redaction, synthetic CI fixtures, retention/deletion controls |
| Classification harm | Accent or frustration is treated as purchase intent | Evidence-grounded dimensions; protected traits and accent excluded; review-needed state |
| Model supply-chain risk | Untrusted model or dependency code | License/provenance review, hashes/locks, vulnerability scanning, isolated runtime |
| Malicious file | Uploaded sample contains active content | Content-type verification, size limits, malware scan, safe rendering, no executable formats |
| Webhook forgery | Fake provider callback updates lead state | Signature/timestamp verification, replay protection, narrow ingress |
| Schedule abuse | Invalid timezone or repeated callback | Validated timezone/hours, suppression recheck at execution, cancellation and deduplication |
| Operator misuse | Live channels enabled accidentally | Default-off flags, least privilege, audit events, two-person activation, kill switch |

## Abuse conversation handling

- Honor an explicit stop or opt-out immediately.
- Do not retaliate, argue, flirt, or mirror abusive language.
- Offer one neutral redirection when appropriate, then end safely.
- Do not infer protected or sensitive traits from voice, name, language, or accent.
- Humor and micro-challenges stop on confusion, irritation, refusal, or serious concerns.
- Buyer text has no authority to change system instructions, disclose internals, or execute tools; detected injection/internal-information requests are refused before fact or intent extraction.
- Intent classification excludes language, accent, frustration, synthetic persona labels, and protected or sensitive traits.
- Action previews require deterministic disclosure, consent, contact-policy, opt-out, disposition, classification, and quota checks; unknown state blocks.
- Callback policy is rechecked at dispatch, and deck generation accepts fixed industry/feature values rather than arbitrary buyer content.

## Security verification gates

- Static analysis, dependency audit, and secret scanning on every PR.
- Unit/property tests for policy invariants.
- Adversarial transcript, URL, file, webhook, duplicate-action, and timeout tests.
- No raw personal audio or live contact data in CI.
- Live adapter activation is a separate reviewed release decision.
