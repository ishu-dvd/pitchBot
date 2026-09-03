# Speech and Local Runtime Benchmarks

## Current status

PR 6 implements benchmark schemas, corpus/candidate validation, multilingual transcript metrics, VAD overlap metrics, structured-output accuracy, timing/resource helpers, and planned synthetic coverage. It does **not** select a production model and contains no measured speech/model result.

PR 22 adds the first measurable speech dimension: a deterministic synthetic **voice-activity (VAD) structural** benchmark. Voice-activity detection only needs speech-vs-silence structure with ground-truth intervals, which can be generated without any model. **STT and TTS remain blocked** — intelligible speech cannot be synthesised without a TTS model, and word/character error rate and TTS naturalness require reviewed real consented or licensed audio, so no STT/TTS corpus item is available and no STT/TTS result is produced (see [ADR-0004](adrs/ADR-0004-benchmark-before-model-selection.md)).

No audio file or model weight is committed. Planned STT/TTS corpus entries are test requirements, not evidence; synthetic VAD audio is regenerated from a seed and hash-verified rather than committed as a binary.

## Candidate review

Repository metadata was checked on 2026-08-31 using the GitHub API:

| Candidate | Kind | Repository license reported | Gate |
|---|---|---:|---|
| faster-whisper | STT | MIT | Model license + measured benchmark required |
| whisper.cpp | STT | MIT | Model license + measured benchmark required |
| Silero VAD | VAD | MIT | Model artifact license + benchmark required |
| py-webrtcvad | VAD | NOASSERTION | Manual license review required |
| llama.cpp | Model runtime | MIT | Model license + structured-output benchmark required |
| Ollama | Model runtime | MIT | Model license; convenience adapter only |
| Piper (`piper1-gpl`) | TTS | GPL-3.0 | Distribution review + each voice license required |
| AI4Bharat Indic-TTS | TTS | MIT | Model/voice license + activity/quality review required |

The assumed `AI4Bharat/IndicConformer` repository was unavailable and is not registered as a candidate. A specific maintained repository/model card must be identified before evaluation.

Repository licenses do not automatically cover downloaded model weights or voices.

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
