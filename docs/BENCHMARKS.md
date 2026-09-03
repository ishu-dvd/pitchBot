# Speech and Local Runtime Benchmarks

## Current status

PR 6 implements benchmark schemas, corpus/candidate validation, multilingual transcript metrics, VAD overlap metrics, structured-output accuracy, timing/resource helpers, and planned synthetic coverage. It does **not** select a production model and contains no measured speech/model result.

PR 22 adds the first measurable speech dimension: a deterministic synthetic **voice-activity (VAD) structural** benchmark. Voice-activity detection only needs speech-vs-silence structure with ground-truth intervals, which can be generated without any model. **STT and TTS remain blocked** — intelligible speech cannot be synthesised without a TTS model, and word/character error rate and TTS naturalness require reviewed real consented or licensed audio, so no STT/TTS corpus item is available and no STT/TTS result is produced (see [ADR-0004](adrs/ADR-0004-benchmark-before-model-selection.md)).

PR 30 runs the first **real** speech provider — `py-webrtcvad` — through that benchmark, and reports a negative result: the synthetic corpus is **not adequate to select a VAD provider**, and **no provider is selected**. The measurement, the reasoning, and what the corpus would need are in [Measured result](#measured-result-py-webrtcvad-on-the-synthetic-vad-corpus) below. `min_f1` on this suite remains a harness regression gate and must not be read as a provider ranking.

PR 33 adds the first provider that **produces speech**: an opt-in Piper text-to-speech adapter. It satisfies the gate this document set for Piper — *"Distribution review + each voice license required"* — and the review returned a **blocking finding**: **no published Piper Hindi voice is cleared for commercial use**, and the widely used `en_US-amy-low` is not either. See [Piper distribution and voice license review](#piper-distribution-and-voice-license-review-2026-09-03). **No TTS provider is selected**, no TTS quality is claimed, and no voice is bundled; the adapter refuses a non-commercial voice unless a caller explicitly opts in for local evaluation.

No audio file or model weight is committed. Planned STT/TTS corpus entries are test requirements, not evidence; synthetic VAD audio is regenerated from a seed and hash-verified rather than committed as a binary.

## Candidate review

Repository metadata was checked on 2026-08-31 using the GitHub API:

| Candidate | Kind | Repository license reported | Gate |
|---|---|---:|---|
| faster-whisper | STT | MIT | Model license + measured benchmark required |
| whisper.cpp | STT | MIT | Model license + measured benchmark required |
| Silero VAD | VAD | MIT | Model artifact license + benchmark required |
| py-webrtcvad | VAD | NOASSERTION | **License reviewed 2026-09-03 (below). Measured 2026-09-03; not selected.** |
| llama.cpp | Model runtime | MIT | Model license + structured-output benchmark required |
| Ollama | Model runtime | MIT | Model license; convenience adapter only |
| Piper (`piper1-gpl`) | TTS | GPL-3.0 | **Reviewed 2026-09-03 (below). Adapter landed opt-in; no provider or voice selected.** |
| AI4Bharat Indic-TTS | TTS | MIT | Model/voice license + activity/quality review required |

The assumed `AI4Bharat/IndicConformer` repository was unavailable and is not registered as a candidate. A specific maintained repository/model card must be identified before evaluation.

Repository licenses do not automatically cover downloaded model weights or voices.

### py-webrtcvad license review (2026-09-03)

`docs/BENCHMARKS.md` recorded GitHub's automatic detection as `NOASSERTION` and required a
manual review. That review was performed against the `LICENSE` file inside the installed
wheel rather than the repository landing page, which is where the ambiguity comes from: the
file carries **two** licenses, and GitHub's classifier declines to name one.

- Python binding — **MIT**, Copyright (c) 2016 John Wiseman.
- Vendored WebRTC C code under `cbits/webrtc/` — **BSD-3-Clause**, Copyright (c) 2011,
  The WebRTC project authors.

Both are permissive and compatible. The distribution's own metadata declares `License: MIT`
and the OSI MIT classifier. Recorded as `MIT AND BSD-3-Clause`. There are **no model
weights**: the GMM parameters are compiled into the C extension, so no separate model or
voice license exists and nothing is downloaded at any point.

### Piper distribution and voice license review (2026-09-03)

This table required *"Distribution review + each voice license required"* before Piper could
be used. Both halves were performed; the second returned a blocking finding.

#### Distribution: the runtime is copyleft

`piper-tts` 1.7.0 ships the verbatim **GNU GPL v3** text as its `COPYING` file and bundles
`espeak-ng` phoneme data, which is itself GPL-3.0-or-later. Recorded as
**`GPL-3.0-or-later`**. This is materially different from the permissive `webrtc-vad`
extra reviewed above, and it is why the adapter is shaped the way it is:

- Piper is an **optional extra** (`pip install "pitchbot[piper-tts]"`), never a runtime
  dependency. `pitchbot` imports and the whole suite passes with it absent.
- It is imported at runtime through `importlib`, never vendored and never redistributed.
- Installing it is a deliberate operator action, and the operator then owns the
  obligations of whatever they distribute.

Shipping a combined artifact that *includes* Piper is a distribution question this
repository has **not** answered. Nothing here authorises that.

Its runtime dependency closure is small — `onnxruntime` (MIT) and `pathvalidate` (MIT) —
and CPU-only; the heavier `torch` set is confined to Piper's `train` extra.

#### Voices: a voice's license is not the runtime's license

Each voice is a separate `.onnx` + `.onnx.json` pair with its own license, taken from the
dataset it was trained on. Every voice below was reviewed from its upstream `MODEL_CARD`
on 2026-09-03. **PitchBot is a sales assistant, so "non-commercial" is disqualifying for
its stated purpose rather than a footnote.**

| Voice | Dataset license | Commercial use |
|---|---|---|
| `en_US-joe-medium` | CC0-1.0 | **yes** |
| `en_US-libritts_r-medium` | CC BY 4.0 | **yes** (attribution) |
| `en_GB-alba-medium` | CC BY 4.0 | **yes** (attribution) |
| `en_US-amy-low` | CC BY-NC-SA 4.0 (finetune of RyanSpeech) | no |
| `en_US-ryan-low` | CC BY-NC-SA 4.0 | no |
| `en_US-hfc_female-medium` | CC BY-NC-SA 4.0 | no |
| `en_US-lessac-medium` | Blizzard 2013 restricted | no |
| `hi_IN-pratham-medium` | CC BY-NC-SA 4.0 | no |
| `hi_IN-priyamvada-medium` | CC BY-NC-SA 4.0 | no |
| `hi_IN-rohan-medium` | IIT-M IndicTTS custom — **document did not respond** | no (unresolved) |

**Blocking finding: Piper published exactly three `hi_IN` voices at review time and none of
them is cleared for commercial use.** Two are explicitly non-commercial; the third points
at a license PDF that did not respond when fetched. A bilingual *commercial* deployment
therefore cannot be served by Piper's published Hindi voices today. This is a finding about
the available voices, not a gap in the adapter.

An unresolvable license is recorded as **not** permitting commercial use. That is the only
reading that is safe to encode: the alternative is clearing a voice through a gate on the
strength of a document nobody has read.

Note also that `en_US-amy-low` — the default in much Piper example code — is a finetune of
RyanSpeech and inherits **CC BY-NC-SA 4.0**. A voice's license follows its training data
through finetuning, so the base model's license has to be chased, not just the voice's own
card.

#### How the finding is enforced

The review is encoded as data in `KNOWN_VOICE_LICENSES`
(`pitchbot.adapters.piper_tts`) rather than left in prose, and
`PiperVoiceRegistry` is **deny-by-default**: a voice whose license does not permit
commercial use, or whose license could not be established, is refused unless the caller
passes `allow_non_commercial=True`. That flag exists because local evaluation is a
legitimate non-commercial use, and it is deliberately noisy to write down. A test pins the
Hindi finding, so adding a commercially-usable Hindi voice is a deliberate act that has to
update it.

Adding a language is a **data** change — one catalog row plus a voice file — and never a
code change.

#### Measured behaviour of the adapter (not a quality claim)

These are engineering properties of the integration, measured on
`Windows-11`, Python 3.12, CPU only, no accelerator, voice `en_US-joe-medium`. **They say
nothing about speech quality, naturalness, or intelligibility**, which require the blinded
human rubrics and consented audio described under [TTS and audio similarity](#tts-and-audio-similarity). No TTS provider is selected.

| Property | Measured | Why it matters |
|---|---|---|
| Synthesis real-time factor | ~0.04–0.09 | Far faster than real time on 8 CPUs |
| Event-loop stall during synthesis | **~20 ms** | Chunks advance on a worker thread, so the audio socket keeps serving |
| Event-loop stall on first (lazy) load | **~2,100 ms** | Loading holds the GIL despite the thread; `preload()` moves it to startup |
| Streaming granularity | one chunk per sentence | First audio leaves before the last sentence is synthesised |
| Determinism, default | **not reproducible** | VITS samples its duration predictor |
| Determinism, `DETERMINISTIC_SYNTHESIS` | byte-identical across runs | Required for any corpus item carrying an `audio_sha256` |
| Concurrent synthesis, one voice | byte-identical to serial | Measured safe, so the adapter does not serialize |
| Network access at load or synthesis | **none** | Verified with the process's sockets disabled |

Two of these corrected a wrong assumption rather than confirming a right one: an earlier
draft serialized synthesis behind a per-voice lock that measurement showed was unnecessary,
and the ~2.1 s stall was attributed to synthesis until load and synthesis were measured
separately.

**Consequence for the VAD corpus.** [What the corpus would need to select a VAD](#what-the-corpus-would-need-to-select-a-vad) asks for spectrally speech-like audio, which the
byte-size synthetic generator cannot produce. Piper output *is* speech-like and can now be
made byte-reproducible, so it can generate that corpus. That work is not in this PR.

### VAD candidate comparison, measured 2026-09-03

Package sizes are from the PyPI JSON API for the `cp312`/`win_amd64` artifact, or the
sdist where no wheel is published.

| Candidate | Distribution | Artifact size | Runtime dependencies | Weights | Accelerator |
|---|---|---:|---|---|---|
| py-webrtcvad | `webrtcvad-wheels==2.0.14` | **19.4 KiB** wheel | **none** | none (compiled in) | not required |
| Silero VAD | `silero-vad==6.2.1` | 27.6 MiB sdist | `torch>=1.12`, `torchaudio>=0.12` | bundled | not required |
| — its `torch` floor | `torch==2.14.0` | 118.4 MiB wheel | (transitive set) | — | — |

Silero is MIT and is genuinely small *as a model*, but it cannot be installed without
PyTorch, so adopting it costs at least ~118 MiB of wheel plus `torchaudio` on a target box
with 8 logical CPUs and no accelerator — roughly four orders of magnitude more than the
WebRTC extension, for a capability the WebRTC extension already provides at
1,700x real time. **`py-webrtcvad` was therefore the candidate taken to measurement**, and
Silero remains unmeasured. That ordering is a cost judgement, not a quality claim: the
finding below is that this corpus cannot rank VAD *quality* at all, so it could not have
separated them even if both had been run.

### Measured result: py-webrtcvad on the synthetic VAD corpus

- **Measurement source:** measured (not planned, not placeholder).
- **Package:** `webrtcvad-wheels==2.0.14` (prebuilt-wheel distribution of `py-webrtcvad`).
- **Algorithm:** `webrtc-gmm-vad` — the WebRTC project's Gaussian-mixture VAD over six
  sub-bands. **No model weights**; nothing is downloaded at import, construction, or
  detection time.
- **License:** `MIT AND BSD-3-Clause` (reviewed above).
- **Hardware:** `Windows-11-10.0.26200-SP0`, `AMD64 Family 25 Model 1 Stepping 1
  AuthenticAMD`, 16 logical CPUs, **no accelerator**, Python 3.12.10. This box has more
  logical CPUs than the 8-CPU target on record; the detector is single-threaded and
  1,700x faster than real time here, so the cost conclusion is not sensitive to that.
- **Corpus:** `evals/corpora/vad-cases.json`, suite `pitchbot-vad-structural` v1, 8 cases,
  every case regenerated from its seed and hash-verified before scoring.
- **Frame handling:** clip PCM at 16 kHz / 20 ms / 640 bytes per frame, fed unmodified.
  No resampling and no padding; see "Frame compatibility" below.

Mean F1 by aggressiveness mode, and whether the suite's `min_f1 = 0.85` gate passed:

| Detector | Mode | Mean F1 | Min F1 | Cases failed | Gate |
|---|---:|---:|---:|---:|---|
| `MockVoiceActivityDetector` (byte-size placeholder) | — | 1.0000 | 1.0000 | 0 | pass |
| py-webrtcvad | 0 | 0.8736 | 0.7937 | 4 | **fail** |
| py-webrtcvad | 1 | 0.8758 | 0.7937 | 3 | **fail** |
| py-webrtcvad | 2 | 0.8949 | 0.8276 | 2 | **fail** |
| py-webrtcvad | 3 | 0.9036 | 0.8276 | 2 | **fail** |

Per case, mode 3 (its best):

| Case | Language | Condition | Precision | Recall | F1 |
|---|---|---|---:|---:|---:|
| `en-apparel-clear` | en | clear | 0.8750 | 1.0000 | 0.9333 |
| `hi-toys-leading-trailing-silence` | hi | leading-silence | 0.8000 | 1.0000 | 0.8889 |
| `mixed-books-inter-word-pause` | mixed | inter-word-pause | 0.7059 | 1.0000 | **0.8276** |
| `en-food-background-noise` | en | background-noise | 0.7143 | 1.0000 | **0.8333** |
| `hi-plastics-crosstalk` | hi | crosstalk | 0.8571 | 1.0000 | 0.9231 |
| `mixed-import-export-barge-in` | mixed | barge-in | 1.0000 | 1.0000 | 1.0000 |
| `en-toys-noise-burst` | en | noise-burst | 0.8750 | 1.0000 | 0.9333 |
| `hi-apparel-long-silence` | hi | long-silence | 0.8000 | 1.0000 | 0.8889 |

Per slice, mode 3:

| Slice | F1 | | Slice | F1 |
|---|---:|---|---|---:|
| `lang.en` | 0.9000 | | `vert.apparel` | 0.9111 |
| `lang.hi` | 0.9003 | | `vert.books` | 0.8276 |
| `lang.mixed` | 0.9138 | | `vert.food` | 0.8333 |
| `cond.clear` | 0.9333 | | `vert.import-export` | 1.0000 |
| `cond.leading-silence` | 0.8889 | | `vert.plastics` | 0.9231 |
| `cond.long-silence` | 0.8889 | | `vert.toys` | 0.9111 |
| `cond.inter-word-pause` | 0.8276 | | | |
| `cond.background-noise` | 0.8333 | | | |
| `cond.crosstalk` | 0.9231 | | | |
| `cond.barge-in` | 1.0000 | | | |
| `cond.noise-burst` | 0.9333 | | | |

Cost, on the labeled hardware above:

- **Real-time factor 0.000655** mean / **0.000704** p95 for a single pass of the corpus
  (~1,500x faster than real time), from the emitted artifact now that the runner's clock can
  resolve it. Amortised over 20 repetitions of the whole corpus the figure is **0.000576**
  (163.2 s of audio in 0.094 s); the single-pass number is slightly higher because it
  includes constructing a fresh detector per case, which is required — WebRTC adapts an
  internal noise model, so a detector must not be reused across clips.
- For reference the byte-length placeholder measures 0.000324 mean on the same run, so a
  real acoustic detector costs roughly **twice a byte-length comparison** here. Both are
  three orders of magnitude inside real time.
- **Peak Python allocation 60.2 KiB** for the whole run (placeholder: 27.4 KiB). The
  detector holds no tensors and no weights; the C extension's own state is a fixed-size
  struct per instance. Python allocation tracking does not measure native RSS, but with no
  weights and a fixed-size struct there is no significant native allocation to measure.
- Single-threaded, CPU-only, no accelerator, no network.

### What this number does and does not mean

**It does not mean py-webrtcvad is a worse voice-activity detector than the placeholder.**
It means this corpus cannot compare them, and the direction of the result shows why.

- **Recall is 1.0000 on every case in every mode.** The detector never missed speech. The
  entire deficit is precision.
- **Every false positive is WebRTC's speech-tail hangover.** Inspecting per-frame decisions,
  the false positives are contiguous runs of 4-6 frames (80-120 ms) immediately *after* a
  speech segment ends, plus a ~4-frame warm-up while the adaptive noise model settles at
  the start of a non-silent clip. Holding the decision briefly past the end of speech is
  deliberate behaviour that stops a real VAD from clipping the low-energy tail of a word.
  This corpus's "silence" is digital zero, so that behaviour can only ever cost precision
  here and can never earn anything back.
- **A twelve-line RMS energy threshold scores a perfect 1.0000 mean F1 and 1.0000 min F1
  on this corpus** — better than every mode of the real detector, with no model, no
  dependency, and no license. The corpus's speech regions are uniform noise at amplitude
  8,000 and its non-speech regions are amplitude 0-300, so speech-vs-non-speech here *is*
  an amplitude decision, and amplitude thresholding is the optimal strategy.
- **The corpus contains no non-speech signal at speech energy**, which is the only kind of
  case that could distinguish an acoustic model from an energy threshold. PR 24 documented
  deliberately avoiding it, on the grounds that a byte-size placeholder could not reject a
  loud broadband burst. Measured here, neither can WebRTC's VAD: amplitude-8,000 white
  noise and a 440 Hz pure tone are both classified as speech in **100%** of frames in
  **every** aggressiveness mode. Real VADs reject *low-energy stationary* noise; they are
  not built to reject loud broadband signals, so this vocabulary cannot exercise the
  discriminative behaviour that would rank one VAD above another.

So the number above measures the corpus, not the model. Ranking candidates on it would
select the trivial energy threshold and reject every real acoustic VAD — which is exactly
the self-fulfilling evaluation ADR-0004 exists to prevent.

**Verdict: no VAD provider is selected by this measurement.** ADR-0004's gate is not
satisfied, because "a reproducible measured result passes" requires the result to be
*about the candidate*, and this one is not. `py-webrtcvad` remains a candidate in good
standing — permissively licensed, weight-free, 19.4 KiB, and 1,700x real time on a
no-accelerator box — and the adapter and measurement path are now in place, so it can be
re-measured without further code the moment an adequate corpus exists.

### What the corpus would need to select a VAD

1. **Spectrally speech-like "speech".** Formant structure, harmonicity, and an
   amplitude envelope rather than uniform noise, so a detector's sub-band model is
   exercised instead of its energy path.
2. **Non-speech at speech energy.** Loud stationary broadband noise, hum, music, and
   impulsive noise labeled non-speech. Without this, precision measures hangover length
   and nothing else.
3. **A realistic noise floor.** Non-speech that is not digital zero, so a warm-up or
   hangover frame lands on plausible audio rather than on mathematically perfect silence.
4. **Boundary tolerance in the metric, or a declared hangover budget.** A detector that
   holds 100 ms past end-of-speech is behaving correctly for endpointing; the suite should
   score that against the endpointing budget it already declares (700 ms end-of-speech
   silence) rather than as a precision error at 20 ms granularity.
5. **Reviewed real consented or licensed audio** for anything that claims to rank quality.
   Items 1-4 make a synthetic corpus fairer; only real audio makes it representative.

Until at least items 1-3 exist, `min_f1` on this suite is a harness regression gate and
must not be read as a provider ranking.

### Frame compatibility

WebRTC's VAD accepts only mono 16-bit little-endian PCM at 8/16/32/48 kHz in exactly
10, 20, or 30 ms frames. The corpus declares `sample_rate_hz: 16000` and `frame_ms: 20`,
which is 320 samples / 640 bytes — **directly compatible**, so nothing was resampled,
padded, or truncated, and the audio scored is byte-for-byte the audio the committed
`audio_sha256` covers.

The generator's `frames`, however, are **not** PCM. They are truncated byte strings
(48-600 bytes) whose *length* stands in for a variable-bitrate codec's output, which is
what the byte-size mock classifies on. Feeding them to an acoustic detector is invalid
input rather than a lower score, so `run-speech` selects the frame representation from the
detector profile (`VadFrameSource`): the mock receives the length proxy, a real detector
receives `SyntheticClip.pcm_frames`. The adapter refuses a frame of any other length rather
than guessing.

### Clock resolution

`run-speech` previously timed cases with `time.monotonic_ns`, whose resolution on Windows
is **15.625 ms**. A per-case VAD pass costs well under a millisecond, so every
`speech.real_time_factor` quantised to 0 — which silently emptied `--max-rtf`, the only
gate that can reject a candidate too heavy for the target box. The runner now uses
`time.perf_counter_ns` (monotonic; ~200 ns observed here). `run-retrieval` and
`run-graph-retrieval` still use `monotonic_ns` for their latency metrics; their budgets are
tens to hundreds of milliseconds so the effect is smaller, but it is the same defect and is
recorded as deferred.

## Planned audio coverage

`evals/corpora/speech-cases.json` defines synthetic text requirements for:

- English, Hindi, and Hinglish/code switching.
- Apparel, toys, books, food, import/export, and plastics vocabulary.
- Budgets, dates, units, product certificates, features, decisions, samples, and callbacks.
- Office/street noise, crosstalk, silence, interruptions, repetition, accents, and numbers.

When an audio item becomes `available`, validation requires:

- A path inside the manifest directory.
- SHA-256 of the exact file.
- Synthetic generation, license, or consent provenance.
- Reference transcript and language/tags.

Private call recordings and copyrighted recordings without an evaluation license are prohibited.

## Metrics

### VAD

- Speech-duration precision, recall, and F1.
- Start/end latency and false starts/ends (added with measured adapter runs).
- Correct silence detection.
- CPU/GPU and real-time factor on labeled hardware.

### STT

- Unicode-aware WER and CER.
- Separate English, Hindi, Hinglish, noise, overlap, names, numbers, and industry slices.
- Language confusion and partial/final latency.
- Real-time factor, native RSS, and accelerator memory.

WER/CER normalization is versioned and cannot be changed alongside a baseline without a reviewed delta.

### TTS and audio similarity

Waveform similarity between different valid voices is not a quality metric, and PitchBot must not optimize toward cloning or impersonating a real person. Evaluate:

- First-audio and completion latency.
- Real-time factor and duration regression.
- Intelligibility through consented human transcription and optional round-trip ASR, with ASR bias disclosed.
- Naturalness, pronunciation, energy, interruption recovery, and English/Hindi/Hinglish quality through blinded human rubrics.
- Same-model/config regression using approved acoustic/embedding metrics only after license, bias, and threshold review.
- Speaker similarity only to verify that a selected synthetic voice remains internally consistent—not similarity to any identifiable person.

### Local model runtime

- Structured field accuracy and schema-valid response rate.
- Fact precision/recall against source spans in later eval PRs.
- First-token/completion latency, tokens/second, timeout rate, native RSS, and accelerator memory.
- English/Hindi/Hinglish consistency and prompt-injection robustness in later PRs.

## Measurement protocol

Every committed result must include:

- `measurement_source: measured` (placeholder is invalid).
- Candidate repository revision and exact model/voice identifier.
- Model/voice license.
- Canonical corpus manifest SHA-256.
- Hardware/OS/Python and accelerator details.
- Warmup/repetition count and full configuration.
- Metrics and limitations.

Measured metrics must be finite and non-empty. Placeholder/unknown revisions or model/voice licenses are invalid. Benchmark manifest parsing rejects non-standard JSON constants and applies bounded input sizes.

`measure_async` reports median, nearest-rank p95, min/max, and peak Python allocations. Python allocation tracking does not measure native model RSS or GPU memory; adapters must report those separately.

Hardware-specific jobs must not gate ordinary shared CI because runner variance can create misleading regressions.

## Evaluation snapshots and local reports

The versioned schema at `evals/schemas/evaluation-run-v1.json` supports free, local, trackable evaluation snapshots. It exposes state-dependent threshold, case-status, and run-status constraints for non-Python consumers. The `pitchbot-bench validate-evaluation` command remains authoritative for cross-item uniqueness, timestamp ordering, finite-number, and gate-consistency checks, and **returns a non-zero exit code when the gate fails**, so it can fail a build. Each snapshot records:

- Exact code revision plus reviewed suite, corpus, and configuration hashes.
- Bounded hardware identifiers and numeric capacity labels plus timezone-aware run boundaries; free-form hardware notes are excluded.
- Finite run/case metrics with explicit units, direction, and thresholds.
- English/Hindi/Hinglish, industry, and buyer-persona slices.
- Bounded machine-readable failure codes without raw transcript, prompt, audio, contact, or retrieved content.

Running snapshots may be rewritten atomically by the future harness. Completed or failed snapshots require completion metadata; completed snapshots require unique case results. `artifact-gates=pass` means only that every included threshold and case passed. It is not model, provider, deployment, or live-action approval.

### Suite-aware artifact gates

`EvaluationRun` is a transport contract: it can faithfully represent a run missing every metric its suite exists to measure, and it must be able to, because a failed run is still a run. Deciding whether such an artifact *passes* therefore needs the suite's own declaration of what a complete result contains, which is what `pitchbot.benchmarks.gates.EvaluationGateSpec` carries. Each reviewed suite declares:

- The run-level and per-case metric names a complete artifact contains. Missing names fail; presence is checked before any threshold is consulted, so a metric-stripped artifact cannot pass on an unrelated metric that happens to be above its bar.
- Which run-level aggregates are folds of which per-case metric (mean, min, max, nearest-rank p95) and which are per-case failure-code rates. Each is recomputed from the cases and compared within float tolerance, so a run-level number that was not produced by the cases it summarizes is rejected even when it clears its own threshold — and even when it carries no threshold at all.
- Optionally, the corpus and the exact case-id set. The runners supply these from the manifest they just loaded, via `EvaluationGateSpec.for_suite`; artifact-only callers, which never see a manifest, fall back to the reviewed spec registered for the artifact's `suite_id`.

A `suite_id` with no registered spec **fails closed**: there is nothing to check it against, so it cannot report a pass. Adding a suite therefore means adding its spec to `SUITE_GATE_SPECS`; a test runs every shipped suite and asserts the emitted artifact satisfies its own registered spec, so a renamed or dropped metric breaks the build instead of silently ceasing to be gated.

`validate-evaluation`, `run-retrieval`, `run-graph-retrieval`, and `run-speech` all return a non-zero exit code when the gate fails, and print the machine-readable reasons (`missing-run-metric:…`, `aggregate-inconsistent:…`, `case-not-passed:…`, `gate-below-threshold:…`, `unknown-suite:…`). `render-evaluation` labels its "Artifact gates" card from the same gate and lists the same reasons; it still exits `0`, because rendering a report of a failing run is not itself a failure.

The static report generator escapes all labels, contains no JavaScript or remote resources, and refuses to overwrite an existing report unless `--force` is supplied. Reports belong in ignored `benchmark-results/` unless an artifact is deliberately reviewed for commit.

### Initial design targets

Targets must be encoded in a reviewed suite manifest and measured on labeled target hardware before use:

| Dimension | Initial design target | Required slices |
|---|---:|---|
| Retrieval | 50 ms target, 200 ms hard deadline | lexical, hybrid, cold/warm cache |
| Interruption | speech playback stops within 200 ms p95 | English, Hindi, Hinglish, noise |
| Endpointing | 700 ms end-of-speech silence, 20 s maximum utterance | English, Hindi, Hinglish, noise, crosstalk |
| Barge-in detection | 300 ms of contiguous buyer speech | noise, crosstalk, overlap |
| Endpoint to reply | 1000 ms target for endpoint + transcription + engine | language, hardware, warm/cold |
| First response audio | no claim until provider benchmark | language, hardware, warm/cold |
| Professional speech | at least 4/5 blinded rubric | clarity, brevity, tone, pronunciation |
| Safety handling | 100% expected disposition on critical cases | abuse, opt-out, injection, extraction |
| Grounding | 100% citations for externally sourced claims | competitor, product, deck content |

The endpointing, barge-in, and endpoint-to-reply rows are the configured budgets of the
implemented turn-taking machine. The simulator reports `transcribe_ms`, `engine_ms`, and a
derived `turn_latency_ms` per utterance, but with no speech provider selected these are
instrumentation of the implemented path, not measured results, and must not be published
as benchmark claims.

Latency never overrides opt-out, policy, citation, or durable-action requirements. A retrieval timeout degrades to current conversation state rather than delaying speech.

## Synthetic VAD structural benchmark

The synthetic VAD benchmark is the only speech measurement the harness can honestly produce today, because it needs speech-vs-silence *structure* rather than intelligible speech.

### Generator

`pitchbot.benchmarks.audio` generates, from a seed, a real 16-bit PCM WAV (stdlib `wave`) plus a stream of byte frames carrying exact ground-truth speech/silence intervals. It is dependency-free and **bit-for-bit reproducible** across runs and platforms: samples come from Python's platform-independent Mersenne-Twister PRNG and are packed little-endian, so the SHA-256 of the WAV is a real, verifiable hash. Segment kinds cover the structural conditions this document already calls for — clear speech, leading/trailing silence, inter-word pauses, background noise, crosstalk (overlapping speakers, still speech for VAD), barge-in onset timing, and short noise bursts that must **not** be classified as speech.

Each emitted frame's byte length rises with its energy, mirroring a variable-bitrate codec where a silent 20 ms frame encodes far smaller than a spoken one. This is exactly the premise the shipped `MockVoiceActivityDetector` byte-size heuristic models (see [Provider contracts](PROVIDER_CONTRACTS.md)), so voiced frames encode above and non-voiced frames — including bursts — below the detector's 512-byte threshold. A loud sustained broadband burst is beyond what a byte-size placeholder can reject and would need a real acoustic model, which ADR-0004 has not authorised; synthetic bursts are therefore short low-energy transients.

### Corpus

`evals/corpora/vad-cases.json` is a VAD-scoped suite, separate from the STT/TTS-oriented `evals/corpora/speech-cases.json` (whose 12 items stay `planned`). Each case commits a seed, an ordered segment list, and the `audio_sha256` of the WAV that seed produces. Audio is **not** committed as a binary: `validate-speech-suite` and `run-speech` regenerate every case and verify the committed hash, keeping the repository binary-free while making the hash real and enforced. The suite carries `en` / `hi` / `mixed` language slices, the six existing verticals (apparel, toys, books, food, import-export, plastics), and eight structural conditions. Adding a new language slice or vertical is a pure data edit — append a case with its `seed`, segments, and generated hash; no code changes.

### Runner and gates

`pitchbot-bench run-speech` runs each case through the existing `VoiceActivityDetector` contract, computes overlap precision/recall/F1 with `vad_precision_recall_f1`, and emits an `EvaluationRun` conforming to `evals/schemas/evaluation-run-v1.json`. It reports per-slice F1 (`speech.vad_f1.lang.*`, `speech.vad_f1.cond.*`, `speech.vad_f1.vert.*`) so a regression in Hindi alone is visible and gates independently, plus overall mean/min F1. `real_time_factor` and peak Python allocation are informational by default and become gating under `--max-rtf`, so a candidate too heavy for a no-accelerator 8-core box is rejected on labeled hardware while shared CI stays free of wall-clock flakiness. The artifact records the canonical corpus hash, the generator version and detector configuration (hashed into `configuration_sha256`), and labeled hardware; its `suite_id`/`corpus_id` and metric names mark it unambiguously as a **synthetic-VAD structural** result. It is **not** a model selection and **not** an STT or TTS measurement — no `wer`, `cer`, or naturalness metric can appear.

`run-speech` gates **fail closed and set the process exit code**. It applies the reviewed VAD gate narrowed to the loaded suite (`speech_gates_pass`), which fails unless the run corresponds to that suite and corpus, carries exactly its case set, and carries every required metric (per-case precision/recall/F1 and real-time factor, per-slice/mean/min F1, real-time factor, peak allocation) before all cases and every gating threshold are checked, and which additionally requires the mean/min/p95 and per-slice aggregates to agree with the per-case F1 values they summarize. A failing gate prints `artifact-gates=fail` **and returns a non-zero exit code**, so CI can actually fail on a regression. Since PR 27 this is the same shared machinery `validate-evaluation`, `run-retrieval`, and `run-graph-retrieval` use; PR 24's deliberately forked copy is gone.


## Commands

```powershell
pitchbot-bench validate-candidates benchmarks/candidates.json
pitchbot-bench validate-corpus evals/corpora/speech-cases.json
pitchbot-bench validate-speech-suite evals/corpora/vad-cases.json
pitchbot-bench run-speech evals/corpora/vad-cases.json benchmark-results/vad.json --run-id vad-local-1 --git-revision <commit>
# Optional provider (pip install "pitchbot[webrtc-vad]"). Exits non-zero: see the measured
# result above - the corpus rejects the real detector, and that is the finding.
pitchbot-bench run-speech evals/corpora/vad-cases.json benchmark-results/vad-webrtc.json --run-id vad-webrtc-1 --git-revision <commit> --detector webrtc --webrtc-mode 3
pitchbot-bench validate-retrieval-suite evals/corpora/retrieval-cases.json
pitchbot-bench run-retrieval evals/corpora/retrieval-cases.json benchmark-results/bm25.json --run-id bm25-local-1 --git-revision <commit>
pitchbot-bench validate-graph-retrieval-suite evals/corpora/graph-retrieval-cases.json
pitchbot-bench run-graph-retrieval evals/corpora/graph-retrieval-cases.json benchmark-results/graph-bm25.json --run-id graph-bm25-local-1 --git-revision <commit>
pitchbot-bench score-transcript --reference "नमस्ते" --hypothesis "नमस्ते"
pitchbot-bench evaluation-schema --output evals/schemas/evaluation-run-v1.json
pitchbot-bench validate-evaluation benchmark-results/run.json
pitchbot-bench render-evaluation benchmark-results/run.json benchmark-results/report.html
pitchbot-bench environment
```

Measured results belong in an explicitly reviewed artifact/report path, not `benchmark-results/`, which is ignored by default.

The session retrieval runner emits recall@k, reciprocal rank, timeout rate, and informational latency. The graph retrieval runner additionally gates superseded-claim exposure at zero over reviewed cross-session conflict, explicit supersession, and equal-observation cases. The synthetic VAD runner regenerates each seed-defined case, verifies its committed audio hash, and gates per-language/condition/vertical F1 against the suite's `min_f1`, with real-time factor gating available on labeled hardware via `--max-rtf`. All three reuse the same evaluation schema and the same suite-aware fail-closed gate, and all three return a non-zero exit code on a gate failure. Their artifacts contain allowlisted case, language, industry, persona, and tag labels plus metrics; synthetic queries, claims/documents, opaque gold identifiers, retrieved values, and audio seeds/segments remain in the reviewed suites and are not copied into run history.
