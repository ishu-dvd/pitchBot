# Speech and Local Runtime Benchmarks

## Current status

PR 6 implements benchmark schemas, corpus/candidate validation, multilingual transcript metrics, VAD overlap metrics, structured-output accuracy, timing/resource helpers, and planned synthetic coverage. It does **not** select a production model and contains no measured speech/model result.

No audio file or model weight is committed. Planned corpus entries are test requirements, not evidence.

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

The versioned schema at `evals/schemas/evaluation-run-v1.json` supports free, local, trackable evaluation snapshots. It exposes state-dependent threshold, case-status, and run-status constraints for non-Python consumers. The `pitchbot-bench validate-evaluation` command remains authoritative for cross-item uniqueness, timestamp ordering, finite-number, and gate-consistency checks. Each snapshot records:

- Exact code revision plus reviewed suite, corpus, and configuration hashes.
- Bounded hardware identifiers and numeric capacity labels plus timezone-aware run boundaries; free-form hardware notes are excluded.
- Finite run/case metrics with explicit units, direction, and thresholds.
- English/Hindi/Hinglish, industry, and buyer-persona slices.
- Bounded machine-readable failure codes without raw transcript, prompt, audio, contact, or retrieved content.

Running snapshots may be rewritten atomically by the future harness. Completed or failed snapshots require completion metadata; completed snapshots require unique case results. `artifact-gates=pass` means only that every included threshold and case passed. It is not model, provider, deployment, or live-action approval.

The static report generator escapes all labels, contains no JavaScript or remote resources, and refuses to overwrite an existing report unless `--force` is supplied. Reports belong in ignored `benchmark-results/` unless an artifact is deliberately reviewed for commit.

### Initial design targets

Targets must be encoded in a reviewed suite manifest and measured on labeled target hardware before use:

| Dimension | Initial design target | Required slices |
|---|---:|---|
| Retrieval | 50 ms target, 200 ms hard deadline | lexical, hybrid, cold/warm cache |
| Interruption | speech playback stops within 200 ms p95 | English, Hindi, Hinglish, noise |
| First response audio | no claim until provider benchmark | language, hardware, warm/cold |
| Professional speech | at least 4/5 blinded rubric | clarity, brevity, tone, pronunciation |
| Safety handling | 100% expected disposition on critical cases | abuse, opt-out, injection, extraction |
| Grounding | 100% citations for externally sourced claims | competitor, product, deck content |

Latency never overrides opt-out, policy, citation, or durable-action requirements. A retrieval timeout degrades to current conversation state rather than delaying speech.

## Commands

```powershell
pitchbot-bench validate-candidates benchmarks/candidates.json
pitchbot-bench validate-corpus evals/corpora/speech-cases.json
pitchbot-bench score-transcript --reference "नमस्ते" --hypothesis "नमस्ते"
pitchbot-bench evaluation-schema --output evals/schemas/evaluation-run-v1.json
pitchbot-bench validate-evaluation benchmark-results/run.json
pitchbot-bench render-evaluation benchmark-results/run.json benchmark-results/report.html
pitchbot-bench environment
```

Measured results belong in an explicitly reviewed artifact/report path, not `benchmark-results/`, which is ignored by default.
