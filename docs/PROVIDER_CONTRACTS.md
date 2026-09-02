# Provider Contracts and Deterministic Mocks

## Current implementation

PR 4 defines provider-neutral contracts and in-memory test adapters. PR 8 invokes only these mocks behind deterministic action policy for synthetic previews and fake-time callback tests. It does not add a provider SDK, socket client, external request, live call, live message, model, speech engine, durable scheduler worker, or hosted service.

## Contracts

The adapter boundary covers:

- Streaming speech-to-text and text-to-speech.
- Structured model completion.
- Telephony and WhatsApp actions.
- Scheduling and cancellation.
- Guarded research results.
- Artifact creation.
- Binary object storage.
- Replaceable UTC clocks.

Resource identity and operation idempotency are separate. For example, a scheduler job key identifies the job while an idempotency key identifies one schedule/cancel attempt. This permits a canceled job to be rescheduled with a new operation key. A permanently rejected cancellation enters `cancellation-required`: it remains non-dispatchable and capacity-counted, the failed operation key cannot be reused, and reconciliation requires a new key.

## Voice activity

`VoiceActivityDetector.detect(AudioChunk) -> VoiceActivity` is synchronous and per frame.
It reports `is_speech`, a bounded `confidence`, and the frame `sequence`. It carries no
audio and no transcript, so a detector cannot become a text channel. Implementations must
be cheap enough to run on every frame on the real-time path; a failure is raised as an
`AdapterError` and treated by callers as silence rather than as a call-ending error.

`MockVoiceActivityDetector` classifies by encoded frame size, optionally following a
scripted decision list. It exists so endpointing and barge-in can be built and tested
before any acoustic model is licensed and benchmarked, and it must never appear in a
benchmark claim.

## Streaming

STT consumes an asynchronous stream of timestamped, sequenced audio chunks and produces an asynchronous transcript stream. TTS produces sequenced audio chunks. Provider implementations must preserve order, cancellation, and bounded buffering; those transport concerns are implemented in later milestones.

## Mocks

Mocks are deterministic and in-memory:

- STT/TTS/model outputs can be scripted.
- Action adapters support scripted transient/permanent failures.
- Identical retries return the original result without a duplicate action.
- Reusing an idempotency key with different input raises an explicit conflict.
- Recorded histories are bounded and fail rather than growing indefinitely.
- Contact references, message text, prompts, and raw audio are not retained in diagnostic histories; only redacted values or size/sequence metadata are recorded.
- Object storage intentionally retains supplied bytes because retrieval is its tested function, but it is bounded by its configured action capacity.

Mock data must remain synthetic.

## Network denial

`NetworkDisabledTelephonyAdapter`, `NetworkDisabledWhatsAppAdapter`, and `NetworkDisabledResearchAdapter` always raise `ExternalNetworkDisabledError`. They contain no network client and cannot be enabled through runtime input.

Future external adapters must:

1. Require an explicitly enabled `NetworkPolicy` and channel-specific feature flag.
2. Validate policy and authorization before creating or invoking a network client.
3. Use official provider APIs only.
4. Apply timeouts, bounded response/body sizes, and redacted telemetry.
5. Pass contract, failure, and zero-network tests.

## Retry and timeout

`execute_with_retry`:

- Retries only `TransientAdapterError` and converted attempt timeouts.
- Never retries `PermanentAdapterError`.
- Uses a bounded attempt count, per-attempt timeout, exponential delay, and maximum delay.
- Rejects configurations whose initial delay exceeds the maximum delay.
- Propagates task cancellation because cancellation is not handled as a provider failure.
- Accepts an injectable sleeper for deterministic tests.

Callers should generate one idempotency key before entering the retry loop and reuse it for every attempt.

## Circuit breaker

The circuit breaker:

- Opens after a configured number of transient failures.
- Rejects calls while open.
- Uses an injected clock for deterministic recovery.
- Allows one half-open probe only.
- Closes after a successful probe or reopens after a failed probe.
- Reopens and releases the probe slot if a probe is canceled or aborts unexpectedly; cancellation still propagates.

The recommended order for a future provider operation is:

1. Validate policy and network/channel enablement.
2. Enter the circuit breaker.
3. Execute a bounded retry operation whose individual attempts have timeouts.
4. Record a redacted result keyed by the original idempotency key.

## Clocks

`SystemClock` returns UTC. `FakeClock` requires timezone-aware input and cannot move backward. PR 8 implements bounded in-memory callback scheduling for deterministic tests: future times are validated, cancel/reschedule uses distinct operation keys, due jobs are ordered deterministically, and policy is rechecked before mock telephony dispatch. Permanent cancellation rejection is retained for explicit reconciliation; cleanup keys bind callback ID, schedule incarnation, and attempt, retain the same key after ambiguous outcomes, advance after permanent rejection, and remove local state only after provider acknowledgement. Schedules disappear on restart and are not production callbacks.
