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
| Journal replay poisoning | Malformed or partial events alter restored policy state | Versioned strict payloads, contiguous versions, bounded reads, identity/sequence checks, fail-closed replay |
| Stale or cross-lead retrieval | Cached or mixed indexes expose invalid facts | No runtime cache; replay-validated single-lead snapshots; explicit session/lead scope; post-score and timeout privacy/version checks; bounded provenance-bearing results |
| Knowledge conflict collapse | A newer session silently overwrites a different claim | Supersede only explicit same-session revisions; preserve differing cross-session claims as conflicts |
| Retrieval evaluation blind spot | Quality scores pass while superseded facts are exposed | Reviewed temporal cases; explicit excluded-claim gold set; zero-tolerance exposure gate; production retriever under test |
| Recovered action ambiguity | A restart retry recreates an unverifiable preview response | Persist conversation only after action success; reconcile only while the idempotent local result exists; otherwise fail closed |
| Induced denial of service | Repeated WhatsApp/artifact requests | Per-lead/global quotas, coalescing, bounded queues, timeouts, cancellation, and circuit breakers |
| PII leakage | Phone numbers appear in logs/evals | Data minimization, structured redaction, synthetic CI fixtures, retention/deletion controls |
| Classification harm | Accent or frustration is treated as purchase intent | Evidence-grounded dimensions; protected traits and accent excluded; review-needed state |
| Model supply-chain risk | Untrusted model or dependency code | License/provenance review, hashes/locks, vulnerability scanning, isolated runtime |
| Malicious file | Uploaded sample contains active content | Content-type verification, size limits, malware scan, safe rendering, no executable formats |
| Webhook forgery | Fake provider callback updates lead state | Signature/timestamp verification, replay protection, narrow ingress |
| Schedule abuse | Invalid timezone or repeated callback | Validated timezone/hours, suppression recheck at execution, cancellation and deduplication |
| Cancellation rejection | Provider rejects cancellation while its job remains live | Explicit cancellation-required state; no dispatch; active-capacity retention; failed-key tombstone; new-key reconciliation before cleanup |
| Operator misuse | Live channels enabled accidentally | Default-off flags, least privilege, audit events, two-person activation, kill switch |

## Abuse conversation handling

- Honor an explicit stop or opt-out immediately.
- Do not retaliate, argue, flirt, or mirror abusive language.
- Offer one neutral redirection when appropriate, then end safely.
- Do not infer protected or sensitive traits from voice, name, language, or accent.
- Humor and micro-challenges stop on confusion, irritation, refusal, or serious concerns.
- Buyer text has no authority to change system instructions, disclose internals, or execute tools; detected injection/internal-information requests are refused before fact or intent extraction.
- Opt-out, internal-instruction extraction, and injection detection combine literal multilingual phrase lists with deterministic bounded-window intent templates, so reordered wording and paraphrase are caught without adding models or dependencies. Templates never span a clause boundary, match both the format-character-dropped and format-character-separated tokenizations, and split on hyphens and underscores, so zero-width and joining obfuscation cannot hide an override.
- Injection templates suppress only reported first-person speech ("just forget everything I said", "forget my earlier budget"), never a bare first-person token, because a single appended "I insist" would otherwise disable them.
- Opt-out is terminal and unrecoverable, so its precision is treated as a safety property: literal phrases match whole tokens (never inside `call matlab`), the space-stripped form is consulted only when the turn is visibly separator-obfuscated, bare negators require both a contact noun and a recurrence marker and are refused after an invitation marker ("why not call me again?"), and any clause asking to be contacted later overrides the opt-out reading so a contradictory turn stays recoverable.
- Intent classification excludes language, accent, frustration, synthetic persona labels, and protected or sensitive traits.
- Action previews require deterministic disclosure, consent, contact-policy, opt-out, disposition, classification, and quota checks; unknown state blocks.
- Callback policy is rechecked at dispatch, and deck generation accepts fixed industry/feature values rather than arbitrary buyer content.
- Durable conversation replay restores validated checkpoints only; it never reruns rules/models/actions, accepts unknown events, or overwrites a live session. Recovery requires both the session UUID capability and matching synthetic lead reference.
- BM25 session scope rejects mixed lead/session documents; lead scope rejects mixed leads, excludes superseded claims, preserves conflicts, returns no partial timeout results, and cannot authorize actions.
- Temporal knowledge builds replay every lead session, reject malformed revision chains, preserve conflict, recheck privacy/version, and remain derived non-authoritative views.

## Security verification gates

- Static analysis, dependency audit, and secret scanning on every PR.
- Unit/property tests for policy invariants.
- Adversarial transcript, URL, file, webhook, duplicate-action, and timeout tests.
- No raw personal audio or live contact data in CI.
- Live adapter activation is a separate reviewed release decision.
