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

`WebRtcVoiceActivityDetector` (PR 30) is the first real provider behind this contract. The
contract is unchanged: it implements `detect(AudioChunk) -> VoiceActivity` as written.

- **Optional dependency.** `pitchbot` imports, and the whole suite passes, with `webrtcvad`
  absent. `pitchbot.adapters.webrtc_vad` itself also imports cleanly without it and exposes
  `WEBRTC_VAD_AVAILABLE`, so callers probe rather than guarding an `ImportError`; only
  *constructing* the detector requires the package, and that raises `PermanentAdapterError`
  naming the extra. The module is deliberately **not** re-exported from
  `pitchbot.adapters.__init__`, so the core import graph structurally cannot depend on it.
  Install with `pip install "pitchbot[webrtc-vad]"`.
- **Frame constraints are refused, not repaired.** WebRTC accepts only mono 16-bit
  little-endian PCM at 8/16/32/48 kHz in exactly 10/20/30 ms frames. A frame of any other
  rate or length raises `PermanentAdapterError` rather than being resampled, padded, or
  truncated, because each of those silently changes the signal being measured.
- **`confidence` is not a posterior.** `webrtcvad` returns a single boolean per frame and
  its GMM likelihood ratio is unreachable through the public API, so the adapter reports a
  fixed constant for every frame. A number that varied with the decision would fabricate a
  probability the library never produced. `is_speech` is the whole signal; this detector's
  `confidence` must not be thresholded.
- **No weights, no download.** The GMM parameters are compiled into the C extension, so
  nothing is fetched at import, construction, or detection time, and no model or voice
  license exists separately from the package license (`MIT AND BSD-3-Clause`).
- **Stateful by design.** WebRTC adapts an internal noise model across frames, so an
  instance must be fed one clip's frames in order and a fresh instance used per clip. The
  benchmark runner constructs one detector per case.

The adapter is **not** a selected provider. See [Benchmarks](BENCHMARKS.md) for the measured
result and why the current corpus cannot select one.

## Speech synthesis

`PiperTextToSpeechAdapter` (PR 33) is the first provider that produces speech. The contract
is unchanged: it implements
`synthesize(text, language) -> AsyncIterator[SynthesizedAudioChunk]` as written.

- **Optional dependency.** `pitchbot` imports, and the whole suite passes, with `piper`
  absent. `pitchbot.adapters.piper_tts` itself also imports cleanly without it and exposes
  `PIPER_AVAILABLE`, so callers probe rather than guarding an `ImportError`. Like the VAD
  adapter it is deliberately **not** re-exported from `pitchbot.adapters.__init__`, so the
  core import graph structurally cannot depend on it. Install with
  `pip install "pitchbot[piper-tts]"`.
- **The runtime is GPL-3.0-or-later**, unlike every other dependency in this repository.
  PitchBot never vendors or redistributes it and imports it only at runtime; installing the
  extra is a deliberate operator action. See the distribution review in
  [Benchmarks](BENCHMARKS.md).
- **Voices are operator-supplied and never downloaded.** A voice is addressed by an explicit
  filesystem path that must already exist, and the `.onnx.json` sidecar must sit beside it.
  Piper's own downloader is never invoked; load and synthesis were verified with the
  process's sockets disabled.
- **License gate, deny by default.** Each voice carries its own license, taken from its
  training data, and **most published Piper voices are non-commercial**. `PiperVoiceRegistry`
  refuses a voice that does not permit commercial use, or whose license could not be
  established, unless the caller passes `allow_non_commercial=True` for local evaluation.
  `preload()` applies the same gate at startup, so a denied voice fails before first use.
- **No fallback voice, ever.** Piper does not reject a language mismatch — feeding
  Devanagari to an English voice produces confident, fluent, wrong audio. An unmapped
  language therefore raises `PermanentAdapterError` naming the mapped languages rather than
  being served by whichever voice happened to be configured.
- **Exactly one chunk carries `is_final=True`.** Piper yields one chunk per sentence and
  yields *nothing* for empty, whitespace-only, or punctuation-only text. A consumer waiting
  on `is_final` to release a playback buffer would wait forever, so the adapter emits a
  single empty final chunk in that case.
- **The event loop is not blocked during synthesis**, because chunks are advanced one at a
  time inside `asyncio.to_thread`. Audio therefore starts flowing after the first sentence
  rather than after the whole utterance, which is what makes barge-in survivable.
- **Loading a voice *does* stall the loop (~2.1 s) and must be preloaded.** Constructing the
  ONNX session holds the GIL, so the worker thread cannot yield. Call `preload()` during
  startup to convert an unpredictable mid-conversation freeze into a predictable startup
  cost.
- **Output is non-deterministic by default.** VITS samples its duration predictor, so the
  same text twice is not byte-identical. `DETERMINISTIC_SYNTHESIS` makes it reproducible,
  which any corpus item carrying an `audio_sha256` requires.
- **Concurrency is unrestricted and that was measured**, not assumed: concurrent synthesis
  through one loaded voice is byte-identical to serial, so the adapter holds no per-voice
  lock.

The adapter is **not** a selected provider and makes no quality claim. TTS naturalness and
intelligibility need the blinded human rubrics and consented audio described in
[Benchmarks](BENCHMARKS.md).

## Speech recognition

`FasterWhisperSpeechToTextAdapter` (PR 34) is the first provider that turns buyer audio into
text. The contract is unchanged: it implements
`transcribe(AsyncIterator[AudioChunk]) -> AsyncIterator[TranscriptChunk]` as written.

- **Optional dependency.** `pitchbot` imports, and the whole suite passes, with
  `faster_whisper` absent; the module exposes `FASTER_WHISPER_AVAILABLE` so callers probe
  rather than guarding an `ImportError`, and it is not re-exported from
  `pitchbot.adapters.__init__`. Install with `pip install "pitchbot[faster-whisper]"`.
- **Nothing is downloaded unless you say so.** `WhisperModel` fetches weights on first use
  by default; the adapter passes `local_files_only=True` unless `allow_download=True` is
  set explicitly, so a missing model raises an error naming how to pre-fetch it instead of
  starting a large download mid-call or in CI.
- **Licences are permissive and were checked, not assumed.** Package and CTranslate2 are
  MIT, the `Systran/faster-whisper-*` weights are MIT, and the upstream `openai/whisper-*`
  models are Apache-2.0. An unreviewed model identifier is refused at construction.
- **Utterance-batch, not chunk-streamed.** Whisper encodes a padded 30-second window, so
  cost is ~constant per call and chopping audio into small chunks would pay a full window
  pass per chunk. The adapter consumes one endpointed utterance and transcribes it once.
- **Exactly one final chunk carries the complete transcript.** This is load-bearing:
  `SpeechTurnPipeline._best_transcript` keeps the **last final**, so a per-segment final
  would silently discard everything said before the last segment. Non-final partials are
  emitted per decoded segment and are *cumulative*, so any single partial is self-sufficient.
- **Audio is refused rather than repaired.** Whisper expects 16 kHz mono; an utterance at
  any other declared rate raises, because reinterpreting the rate does not fail — it
  transcribes pitch-shifted, time-stretched speech and reports a plausible wrong duration.
  The rate and size checks run **before** the model loads.
- **`confidence` is a real quantity.** It is `exp(avg_logprob)` — the geometric mean token
  probability the decoder produced — aggregated across segments weighted by duration. Unlike
  the VAD adapter's fixed constant, this one carries information and may be thresholded;
  the pipeline's `MIN_TRANSCRIPT_CONFIDENCE` does exactly that.
- **A language is never invented.** Whisper labels anything, including silence (measured:
  two seconds of digital silence reported as `en` at probability 0.362). Below
  `min_language_probability` the adapter reports `UNKNOWN`. It never *infers*
  `LanguageCode.MIXED`, because deriving code-switching from a single-label model would be
  inventing a distinction the model did not draw; a caller may still declare it.
- **Loading stalls the loop and must be preloaded**, for the same GIL reason as Piper. Call
  `preload()` at startup. Decoding itself runs segment-by-segment on a worker thread.

The adapter is **not** a selected provider and makes no word-error-rate claim. See
[Benchmarks](BENCHMARKS.md) for the measurements and why synthesised speech cannot rank
recognition quality.

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
