# Speech and Local Runtime Benchmarks

## Current status

PR 6 implements benchmark schemas, corpus/candidate validation, multilingual transcript metrics, VAD overlap metrics, structured-output accuracy, timing/resource helpers, and planned synthetic coverage. It does **not** select a production model and contains no measured speech/model result.

PR 22 adds the first measurable speech dimension: a deterministic synthetic **voice-activity (VAD) structural** benchmark. Voice-activity detection only needs speech-vs-silence structure with ground-truth intervals, which can be generated without any model. **STT and TTS remain blocked** — intelligible speech cannot be synthesised without a TTS model, and word/character error rate and TTS naturalness require reviewed real consented or licensed audio, so no STT/TTS corpus item is available and no STT/TTS result is produced (see [ADR-0004](adrs/ADR-0004-benchmark-before-model-selection.md)).

PR 30 runs the first **real** speech provider — `py-webrtcvad` — through that benchmark, and reports a negative result: the synthetic corpus is **not adequate to select a VAD provider**, and **no provider is selected**. The measurement, the reasoning, and what the corpus would need are in [Measured result](#measured-result-py-webrtcvad-on-the-synthetic-vad-corpus) below. `min_f1` on this suite remains a harness regression gate and must not be read as a provider ranking.

PR 33 adds the first provider that **produces speech**: an opt-in Piper text-to-speech adapter. It satisfies the gate this document set for Piper — *"Distribution review + each voice license required"* — and the review returned a **blocking finding**: **no published Piper Hindi voice is cleared for commercial use**, and the widely used `en_US-amy-low` is not either. See [Piper distribution and voice license review](#piper-distribution-and-voice-license-review-2026-09-03). **No TTS provider is selected**, no TTS quality is claimed, and no voice is bundled; the adapter refuses a non-commercial voice unless a caller explicitly opts in for local evaluation.

PR 34 adds the first provider that **recognises speech**: an opt-in `faster-whisper` adapter. Its licences are clean (see [faster-whisper license review](#faster-whisper-license-review-2026-09-03)), but measuring it produced a finding that changes how speech recognition must be integrated: **real-time factor is a misleading metric for Whisper**, because cost is essentially constant per padded 30-second window rather than proportional to audio. The number that matters is *latency after end-of-speech*, and it is roughly **2.1 s**. See [Measured result: faster-whisper](#measured-result-faster-whisper-cpu-int8). **No STT provider is selected** and no word-error-rate claim is made: the only speech available is synthesised, and round-trip measurement cannot separate recognition quality from synthesis quality.

PR 36 measures the **second** local Whisper runtime, `whisper.cpp`, against the integrated one and extends coverage from two languages to six across five scripts. Two results matter. `whisper.cpp` runs locally with no accelerator but is **~1.7x slower** than the integrated runtime with no offsetting advantage, so it is **measured and not adopted**. And **character error rate without number normalisation is close to meaningless for this content**: a perfect Arabic transcription scored 52.4% CER purely because the model wrote digits where the reference wrote words. See [Local Whisper runtimes and language coverage](#local-whisper-runtimes-and-language-coverage-measured-2026-09-03).

No audio file or model weight is committed. Planned STT/TTS corpus entries are test requirements, not evidence; synthetic VAD audio is regenerated from a seed and hash-verified rather than committed as a binary.

## Candidate review

Repository metadata was checked on 2026-08-31 using the GitHub API:

| Candidate | Kind | Repository license reported | Gate |
|---|---|---:|---|
| faster-whisper | STT | MIT | **Reviewed 2026-09-03 (below). Adapter landed opt-in; no provider selected.** |
| whisper.cpp | STT | MIT | **Measured 2026-09-03 (below). Runs locally; not adopted — slower than the integrated runtime with no offsetting advantage.** |
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

### faster-whisper license review (2026-09-03)

This table required *"Model license + measured benchmark required"*. Unlike the Piper voice
review, nothing here is restricted — but it was checked rather than assumed, because PR 33
found a non-commercial license hiding behind a finetune.

| Artifact | License | Commercial |
|---|---|---|
| `faster-whisper` (SYSTRAN) | MIT | yes |
| CTranslate2 | MIT | yes |
| `Systran/faster-whisper-*` weights | **MIT** | yes |
| upstream `openai/whisper-*` | **Apache-2.0** | yes |

The weights are chased to their upstream model, which is the lesson PR 33 paid for: a
model's license follows the artifact it was derived from. Recorded in
`KNOWN_MODEL_LICENSES` (`pitchbot.adapters.faster_whisper_stt`), and an unreviewed model
identifier is refused at construction rather than run.

**Weights are never downloaded by PitchBot.** `WhisperModel` fetches from Hugging Face on
first use by default; the adapter inverts that and passes `local_files_only=True` unless a
caller explicitly opts in, so a missing model is an error naming how to pre-fetch it rather
than a silent multi-hundred-megabyte download mid-call or in CI.

### Measured result: faster-whisper (CPU, int8)

- **Measurement source:** measured (not planned, not placeholder).
- **Package:** `faster-whisper==1.2.1`, model `small`, `compute_type=int8`, `device=cpu`,
  `beam_size=1`.
- **Hardware:** Windows 11, Python 3.12, CPU only, **no accelerator**.
- **Audio:** Piper-synthesised speech. **Not human speech**, and therefore a floor rather
  than a quality claim — see the limitation below.

#### The finding: real-time factor is the wrong metric for Whisper

Whisper always encodes a **padded 30-second mel window**, so cost is essentially constant
per call rather than proportional to the audio:

| audio | inference | RTF |
|---:|---:|---:|
| 3.58 s | 2.09 s | 0.584 |
| 7.15 s | 2.15 s | 0.301 |
| 14.30 s | 2.10 s | 0.147 |
| 28.61 s | 2.09 s | 0.073 |
| 42.91 s | **3.93 s** | 0.092 |

**Twelve times the audio for 1.9x the time** — and that 1.9x appears only at 42.91 s, where
the clip crosses into a *second* window and costs almost exactly twice as much. Cost
**quantises per 30-second window**.

RTF therefore looks alarming on a short clip and excellent on a long one while the model
does identical work. An earlier reading of "RTF 1.06" on a 1.7 s Hindi clip was an artifact
of dividing a fixed ~2.1 s cost by a short duration, not a throughput limit.

**The number that matters is latency after end-of-speech: ~2.1 s, roughly constant.** That
is what a caller waits through, and it is long — natural conversational turn gaps are around
200 ms. It is recorded here as this model's floor on this hardware rather than hidden behind
a flattering RTF.

Three design consequences are implemented in the adapter:

1. **Chopping audio into small streaming chunks would be pathological**, because every chunk
   pays a full window pass. The adapter consumes one endpointed utterance and transcribes it
   once; `SpeechTurnPipeline` already buffers exactly that way.
2. **Partials are real, not fabricated.** Whisper emits segments as it decodes and never
   revises them, so each is yielded as a non-final chunk carrying the transcript so far.
3. **Exactly one final chunk carries the complete text**, because
   `SpeechTurnPipeline._best_transcript` keeps the *last* final transcript — a per-segment
   final would silently discard everything before the last segment.

#### Model size is a correctness constraint, not a speed preference

Round-trip measurement across sizes (synthesised speech, CPU/int8):

| model | EN WER | HI WER | Hindi output |
|---|---:|---:|---|
| `tiny` | 23.4% | **105%** | romanised Latin |
| `base` | 9.1% | **100%** | **Urdu / Arabic script** |
| `small` | 0.0-11% | 22-50% | correct Devanagari |

`tiny` and `base` are **disqualified for this product**. They do not merely score badly on
Hindi — they emit the wrong *script* entirely, which no threshold tuning fixes. Speed cannot
be bought by going smaller, so `small` is the default and a test pins that.

#### Limitation that prevents this being a provider selection

**Round-trip WER cannot separate recognition quality from synthesis quality.** The audio was
produced by Piper, whose Hindi voice is itself of unmeasured quality and non-commercially
licensed. A high Hindi WER here may be measuring bad synthesis rather than bad recognition —
the same class of problem as the VAD corpus measuring itself, below. These numbers are a
**floor, not a WER claim about real buyers**.

[TTS and audio similarity](#tts-and-audio-similarity) already requires ASR bias to be
disclosed when round-trip ASR is used; this is that disclosure. **No STT provider is
selected**, and ADR-0004's gate is not satisfied, because that requires reviewed consented
or licensed human audio which does not yet exist in this repository.

### Local Whisper runtimes and language coverage, measured 2026-09-03

Two open questions: is there a *second* local Whisper runtime worth using, and does the
`small` model actually work beyond English and Hindi? Both were measured.

- **Runtimes:** `faster-whisper` (CTranslate2, `int8`) vs `whisper.cpp`
  (ggml via `pywhispercpp==1.5.1`, MIT, prebuilt `cp312`/`win_amd64` wheel, 487 MB
  `ggml-small`, CPU only, no accelerator). Both at model size `small`, so the comparison is
  about the **runtime**, not the model.
- **Languages:** six across five scripts, synthesised with Piper and transcribed back.

#### whisper.cpp runs locally, and is not adopted

| | total inference, 6 clips | relative |
|---|---:|---|
| `faster-whisper` (CTranslate2, int8) | **13.98 s** | 1.00x |
| `whisper.cpp` (ggml, default f16) | 23.27 s | 1.66x slower |

It installs and runs cleanly on the target platform with a lightweight dependency set
(`numpy`, `requests`, `tqdm`, `platformdirs` — no PyTorch), and its transcripts are
essentially identical to the integrated runtime's. It is simply **slower here with no
offsetting advantage**, so `faster-whisper` remains the integrated runtime and
`whisper.cpp` is recorded as a viable fallback rather than a replacement.

One caveat that keeps this honest: the ggml model used was the default **f16** build, while
CTranslate2 ran **int8**. A quantised ggml model would narrow the gap and possibly close
it. This is a measurement of the default configuration of each, not a claim about the
ceiling of either.

#### The `small` model gets the script right in every language tested

Script correctness is the decisive signal, because the earlier size sweep showed that when
a Whisper model is out of its depth it does not merely score badly — `tiny` emitted
romanised Latin for Hindi and `base` emitted Urdu/Arabic script, which no threshold tuning
fixes.

| Language | Script | Script correct | CER | **CER, numbers normalised** |
|---|---|---|---:|---:|
| English | Latin | yes | 29.6% | **0.0%** |
| Spanish | Latin | yes | 26.3% | **0.0%** |
| Arabic | Arabic | yes | 28.6% | 15.0% |
| Russian | Cyrillic | yes | 41.7% | 15.8% |
| Hindi | Devanagari | yes | 57.9% | 27.3% |
| Chinese | Han | yes | 83.3% | 62.5% |

**All six produced the correct script in both runtimes.** So `small` is genuinely
multilingual, and the wrong-script failure mode is a property of `tiny`/`base`, not of
Whisper at this size.

Quality nonetheless varies enormously. European languages are essentially perfect once
number formatting is accounted for; Hindi is mediocre; **Chinese is poor** and would need
its own evaluation before any claim is made about it.

#### CER without number normalisation is close to meaningless here

This is the finding with the widest consequences, and it was nearly missed.

The reference sentences spell numbers as words ("fourteen thousand"); the model writes them
as digits ("14,000"). Both are correct transcriptions of the same speech. Stripping numerals
and number-words before scoring changes the picture completely:

- English and Spanish go from ~29% and ~26% CER to **0.0%** — the recognition was perfect
  and the entire error was formatting.
- Arabic was scored at **52.4%** for `faster-whisper` and **4.8%** for `whisper.cpp`, which
  looked like a large runtime disagreement. It was not: the two runtimes simply made
  *different formatting choices* — one wrote `14.000`, the other wrote
  `أربعة عشر الف` — and both recognised the speech correctly. Normalised, `faster-whisper`
  scores **0.0%** on Arabic.

`docs/BENCHMARKS.md` already requires that "WER/CER normalization is versioned and cannot be
changed alongside a baseline without a reviewed delta". This measurement is the concrete
justification for that rule: an unnormalised score made a **perfect** transcription look
like a 52% failure, and would have ranked two runtimes apart that in fact agreed. Any future
STT suite must define and version number normalisation *before* it reports a single number.

#### What this is not

**No STT provider is selected and no accuracy claim is made.** The audio is Piper-synthesised,
so it cannot separate recognition quality from synthesis quality, and several of the voices
used carry unresolved or unknown licences — they were used for local evaluation that
distributes nothing and commits no audio. Two of the six (`ar_JO-kareem`, and both Russian
and Chinese voices) report `Unknown` or unresolved licences on their model cards, which is a
further data point for the voice-licensing finding in
[Piper distribution and voice license review](#piper-distribution-and-voice-license-review-2026-09-03):
Piper's published catalogue is **largely licence-unclear**, not merely non-commercial in the
Hindi case.

Reproduced by `probe_whisper_runtimes.py` / `probe_whisper_sidebyside.py`, which are
development probes and are deliberately not committed.

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

Items 1-3 were **prototyped and measured on 2026-09-03**, now that PR 33 makes real speech
synthesis available. The result corrects item 2 — see
[Measured: which properties actually separate](#measured-which-properties-actually-separate-a-vad-from-a-threshold) below.

1. **Spectrally speech-like "speech".** Formant structure, harmonicity, and an
   amplitude envelope rather than uniform noise, so a detector's sub-band model is
   exercised instead of its energy path.
2. **Non-speech at speech energy, *abutting* speech.** Loud stationary broadband noise,
   hum, music, and impulsive noise labeled non-speech — but **placed immediately adjacent
   to speech, with no silent gap**. Measurement showed the original form of this
   requirement, which did not specify adjacency, does not work: loud non-speech in its own
   isolated region is rejected no better by an acoustic VAD than by a threshold. What
   discriminates is the **boundary**.
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

### Measured: which properties actually separate a VAD from a threshold

A four-clip corpus was generated from real Piper speech at 16 kHz, with a non-zero noise
floor and non-speech placed at the *same RMS* as the speech, and scored against an
**oracle-tuned** RMS threshold — the best threshold energy detection could achieve on that
exact corpus, chosen with hindsight. That is the strongest possible baseline; beating a
hand-picked threshold is the bar an acoustic model has to clear to be worth its dependency.

| Clip construction | Oracle RMS F1 | py-webrtcvad F1 | Delta |
|---|---:|---:|---:|
| Speech **abutting** loud non-speech, no gap | 0.7634 | 0.8193 | **+0.056** |
| Speech in a realistic room-tone floor | 0.9516 | 0.9697 | +0.018 |
| Speech vs. isolated loud broadband noise | 0.8262 | 0.8229 | −0.003 |
| Speech vs. isolated 440 Hz tone and 50 Hz hum | 0.7135 | 0.7107 | −0.003 |

Aggregated over all four, the best detector mode reached mean F1 0.8243 against the
oracle threshold's 0.8137 — and **lost** on worst-case F1 (0.7033 vs 0.7135). Three of the
four aggressiveness modes lost outright.

Two conclusions, both of which change the corpus design:

- **"Non-speech at speech energy" is not sufficient on its own, and as originally written
  is counter-productive.** Isolated loud noise, tone, and hum are rejected no better by the
  acoustic detector than by a threshold; those two clip types make the corpus *worse* at
  discriminating. This is consistent with what PR 30 already observed — real VADs reject
  low-energy stationary noise, not loud broadband signals — so requiring that rejection
  measures a property the detector does not claim to have.
- **The discriminating property is boundary placement.** When non-speech sits immediately
  against speech at the same level, an energy threshold has *no information at all* to
  locate the transition, while a spectral model does. That is the only construction here
  that separated the two clearly.

**This is still not a corpus that can rank VAD quality**, and none is being added. A
+0.056 margin on one clip construction, against one detector, is evidence about corpus
*design*, not a provider ranking. A corpus that could rank would need the boundary
construction above as its dominant case, and would need to demonstrate a stable ordering
between **at least two** real detectors rather than one detector against a threshold.

Reproduced by `probe_corpus_viability.py` / `probe_corpus_perclip.py`, which are
development probes and are deliberately not committed: they depend on an optional TTS
extra and a non-commercially-licensed voice used for local evaluation only, and no audio
is committed to the repository.

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

## Streaming a synthesised reply (measured 2026-09-03)

These numbers decided the shape of the outbound speech path, so they are recorded here
rather than inside the code that depends on them. Hardware: 8-core CPU, no accelerator.
Voice `en_US-joe-medium` (CC0), `piper-tts` 1.7.0, mono 16-bit PCM at 22,050 Hz.

### Piper streams by sentence, and a sentence is large

| Reply | Chars | Chunks | Bytes | Audio | Time to first chunk | Total synthesis |
| --- | --- | --- | --- | --- | --- | --- |
| short | 27 | 1 | 72,192 | 1.64 s | 593 ms | 593 ms |
| typical | 107 | 2 | 265,216 | 6.01 s | 316 ms | 316 ms |
| long | 392 | 5 | 914,944 | 20.75 s | 527 ms | 1,052 ms |

Individual chunks in the long reply ranged from **80,384 bytes (1.82 s)** to **352,256
bytes (7.99 s)**. Two consequences follow directly:

- A 352 KB write **exceeds the 256 KB bound the inbound side of the same socket enforces**,
  and it cannot be abandoned part-way. Barge-in that can only take effect on a sentence
  boundary is not barge-in, so the stream is re-cut into fixed 32 KB frames (0.74 s of
  audio each) before it reaches the socket.
- Every frame must be a whole number of 16-bit samples. The client rebuilds frames into an
  `Int16Array`, so an odd-length frame byte-shifts every sample after it: the reply becomes
  noise rather than merely clicking.

### Synthesis is far faster than playback

Once the voice is resident, synthesis runs at roughly **19x realtime** (1,052 ms produced
20.75 s of audio; 1,000 ms produced 6.01 s in the two-sentence case). Repeating one
sentence four times shows a warm-up of about 2.4x on the first call after load:

| Call | Elapsed | Rate |
| --- | --- | --- |
| 0 | 255 ms | 8.2x realtime |
| 1 | 127 ms | 16.4x |
| 2 | 106 ms | 19.6x |
| 3 | 114 ms | 18.2x |

Because the whole reply is available long before any of it finishes playing, **pacing the
send to realtime buys nothing** and costs delivery certainty - the audio is better sent
while the network is known to be working. It does *not* follow that synthesis can run
inline: 1,052 ms on the socket's receive loop would blind the interruption detector for
about a second on every turn, which is why it runs as a background task.

### Cancelling between chunks is immediate and safe

Cancelling a synthesis in flight delivered **no further chunks**, and the adapter produced
**byte-identical output on its next use**. An earlier measurement reporting
`cancel-to-stopped=0 ms` was invalid - it cancelled an `asyncio.sleep` parked between
chunks rather than a live synthesis - and was re-run against the real case. One caveat
stands: `asyncio.to_thread` cancellation abandons the awaiting coroutine but cannot stop
the worker thread, so at most one already-started sentence continues on a pooled thread.

### Loading a voice belongs at startup

Loading `en_US-joe-medium` took **2,561 ms** and holds the GIL, against roughly **110 ms**
to synthesise a whole sentence through a voice already resident. Measured end to end
through the audio socket, the difference lands entirely on the first buyer:

| | Time to first audio frame |
| --- | --- |
| Voice preloaded by the application lifespan | **371 ms** |
| Voice loaded lazily on the first reply | **2,671 ms** |

### End-to-end round trip

The outbound path was verified by feeding the PCM that arrived over the WebSocket back
through `faster-whisper` (`small`, CPU, int8) rather than by counting bytes:

- Reply text: *"Thanks. What matters most next: features, budget, timeline, or the decision
  process?"*
- Heard back: *"Thanks. What matters most next? Features, budget, timeline, or the decision
  process."*

Verbatim modulo punctuation. The turn delivered 8 frames, 260,608 bytes and 5,909.5 ms of
audio in **376 ms** of wall clock.


## Local language model, measured 2026-09-03

Hardware: 8 logical CPU cores, no accelerator, Windows, Python 3.12. Runtime
`onnxruntime-genai` 0.15.2 (MIT). **No model is selected**; ADR-0004's gate is not
satisfied, because that requires a reviewed extraction corpus which does not exist here.

### Runtime choice was decided by a wheel, not by benchmarks

`llama-cpp-python` (MIT) is the better inference engine, and PyPI hosts **only source
tarballs for it -- no Windows wheel at any version ever published**. Installing it on a
clean Windows box therefore attempts a CMake/MSVC build. `onnxruntime-genai` is MIT, ships
a `cp312-win_amd64` wheel, and declares exactly `numpy` and `onnxruntime>=1.28` -- no
PyTorch, and `onnxruntime` is already present for Piper and faster-whisper.

### The dominant cost is compiling the grammar, not running the model

| Stage | Guided | Unguided |
| --- | --- | --- |
| `og.Generator(model, params)` | **1,934 ms** | 3 ms |
| `append_tokens` (prefill) | 110 ms | 103 ms |
| decode (~30 tokens) | 293 ms | 573 ms |
| **total per call** | **2,483 ms** | 678 ms |

`set_guidance` itself costs 0 ms -- it stores a string. The grammar is compiled inside the
generator constructor, is **independent of schema size** (a one-enum schema cost 1,801 ms
against 1,915 ms for three), and is **never cached**: repeat calls paid it again.

**`rewind_to(0)` costs ~1 ms and leaves guidance working**, so one generator can serve many
turns. That single change took Qwen2.5-0.5B from 2,350 ms to **440 ms** per turn. The
compile becomes a startup cost, exactly like loading model weights.

`set_guidance(..., enable_ff_tokens=True)` -- which **defaults to False** -- lets the
grammar emit tokens it has already forced without running the model: generated tokens fell
from 39 to 25 and ~540 ms per turn, with identical answers. Note that fast-forwarded tokens
never appear in `get_next_tokens()`, so a caller collecting per-step tokens silently loses
most of the JSON; read `get_sequence(0)` instead.

### Constrained decoding is not "asking for JSON"

Unguided, the same prompt returned `"buyer_intent": "budget"` and
`"next_question": "what would you like us to build?"` -- syntactically valid JSON, both
values outside the enum. Guided, all ten turns were schema-valid. Constraint masks the
logits so a violating token is unreachable; prompting only makes violation less likely, and
a small model takes the offer. This is why no retry loop exists.

### Two models, and a hard trade-off

Scored on `acknowledge` -- which topic the buyer just gave information about -- over ten
turns spanning English, romanised Hinglish, and Devanagari.

| Model | Licence | On disk | Startup | Per turn | Valid JSON | Correct |
| --- | --- | --- | --- | --- | --- | --- |
| Qwen2.5-0.5B-Instruct | Apache-2.0 | 0.88 GB | 3,638 ms | **950 ms** | 10/10 | **1/10** |
| Phi-3.5-mini-instruct | MIT | 2.78 GB | 5,404 ms | **7,263 ms** | 10/10 | **10/10** |

The fast model is unusable and the accurate one is slow. Prefill dominates Phi (4,302 ms of
6,724 ms), but **latency cannot be bought back from the prompt**: shortening the system
instruction cut prefill to 1,699 ms and accuracy from 4/4 to 1/4. In the running
application, with generator reuse and fast-forward, a Phi turn costs **~5.2 s** against
**~1 ms** with no model.

### Weight licences reviewed

| Model | Licence | Commercial use |
| --- | --- | --- |
| `Qwen/Qwen2.5-0.5B-Instruct`, `Qwen/Qwen2.5-1.5B-Instruct` | Apache-2.0 | yes |
| `Qwen/Qwen3-0.6B`, `Qwen/Qwen3-1.7B` | Apache-2.0 | yes |
| `microsoft/Phi-3.5-mini-instruct`, `microsoft/Phi-4-mini-instruct` | MIT | yes |
| `HuggingFaceTB/SmolLM2-*-Instruct` | Apache-2.0 | yes |
| **`Qwen/Qwen2.5-3B-Instruct`** | qwen-research | **no** -- "FOR NON-COMMERCIAL PURPOSES ONLY" |
| `meta-llama/Llama-3.2-1B/3B-Instruct` | llama3.2 | **recorded as denied** |
| `google/gemma-2-2b-it` | gemma | **recorded as denied** |

Two traps are worth stating plainly. **The Qwen2.5 family is licence-split**: 0.5B and 1.5B
are Apache-2.0 while 3B is not, so a family name is not a licence. And **a quantised
re-upload does not relicense what it converts**, so the gate reads the *upstream* model id,
never the conversion repository the files came from. Llama and Gemma are denied not because
their terms are unusable but because accepting them (attribution obligations, an
additional-user threshold, use policies that must be passed into this product's own terms)
is a product decision rather than an engineering one.

### Hindi coverage is the weak point, again

Among licence-clean candidates: SmolLM2 declares English only; Granite and Phi-4-mini
enumerate language lists that **exclude** Hindi; Phi-3.5-mini says only "multilingual"
without enumerating it. The one small model that officially names Hindi is Llama-3.2, which
is licence-disqualified. Qwen3 (Apache-2.0) claims 100+ languages and is the best
licence-clean option on paper.

Empirically, though, Phi-3.5-mini read **Devanagari and romanised Hinglish correctly on all
ten turns** despite not enumerating Hindi. Model-card language lists and measured behaviour
disagree here, and neither should be trusted alone -- the same lesson as PR 36's finding
that character error rate without number normalisation is close to meaningless.

## Telugu, measured 2026-09-04

Telugu was added as the third language. Every claim below is from a probe on this machine
(8 logical CPUs, Windows, Python 3.12, CPU only), and the headline result is the opposite
of what the language order suggests.

### Telugu is the only Indic language Piper can speak commercially

Piper publishes three `te_IN` voices. Two are trained on `ai4bharat/indicvoices_r`,
whose Hugging Face dataset card states `license: cc-by-4.0` -- verified against the
upstream dataset, not only the voice card.

| Voice | Dataset | Licence | Commercial |
| --- | --- | --- | --- |
| `te_IN-padmavathi-medium` | ai4bharat/indicvoices_r | CC-BY-4.0 | **yes**, with attribution |
| `te_IN-venkatesh-medium` | ai4bharat/indicvoices_r | CC-BY-4.0 | **yes**, with attribution |
| `te_IN-maya-medium` | IITM IndicTTS | unresolved | no (recorded conservatively) |

Set against PR 33's Hindi finding -- all three `hi_IN` voices non-commercial or
unresolved -- Telugu is today the **only** Indic language this project can ship with a
voice. That was not predictable from the language's size or priority, and it is the kind of
thing only a licence review surfaces.

Synthesis performance matches the other voices: 16.3-21.7x realtime, 107-154 ms per
sentence, one chunk per sentence, 2.5 s voice load.

### Whisper hears Telugu and writes Hindi

The decisive Telugu finding. faster-whisper transcribes Telugu speech into **Devanagari**:

| Model | Telugu letters | Devanagari letters | CER | ms/clip | Auto-detected language |
| --- | --- | --- | --- | --- | --- |
| `small` | 0.0% | 100.0% | 100.0% | 2,570 | `te` at 0.90-0.95 |
| `medium` | 0.0% | 100.0% | 100.7% | 7,408 | `te` at 0.76-0.98 |

Not a detection failure -- the model names the language correctly and confidently, then
spells it in the wrong alphabet. `medium` costs 2.9x more and fixes nothing.

The sounds are right. `मा बजजे त्रेंड लक्षला रूपायलू` read aloud is
`మా బడ్జెట్ రెండు లక్షల రూపాయలు` -- "our budget is two lakh rupees".

### The obvious fix makes it worse

Whisper conditions on `initial_prompt`, so a Telugu anchor should force the script. It
does, and it destroys the words:

| Anchor | Telugu letters | CER |
| --- | --- | --- |
| none | 0.0% | **100.0%** |
| `తెలుగు.` | 100.0% | 115.8% |
| `ఇది తెలుగు సంభాషణ.` | 100.0% | 95.2% |
| domain sentence | 100.0% | 90.5% |

Anchoring converts a *recoverable* failure into an unrecoverable one, while improving the
one metric anybody would check first. It is not used.

### Transliteration recovers 59% of what was lost

Devanagari (U+0900) and Telugu (U+0C00) are parallel Brahmic blocks, so the repair is a
character mapping with no dependency, no model and no network.

| | CER |
| --- | --- |
| Whisper output as returned | 100.0% |
| After transliteration | **41.0%** |

Enough to match keywords and fill slots; **not** enough to quote a buyer's words back, and
`pitchbot.speech.scripts` makes no such claim.

### A constant offset is wrong, and looks right

The blocks are parallel, so `+0x300` nearly works -- which is the danger. Auditing every
assigned codepoint found the shift wrong for **53 of 153**: Hindi's Perso-Arabic nukta
letters (क़ ख़ ग़ ज़ फ़) land on Telugu *fraction digits* and `SIGN TUUMU`, and eighteen
more land on unassigned codepoints. The first probe missed this because the four test
sentences happened to use only the safe core.

Deriving the table by matching Unicode *character names* fixes those and introduces a
subtler bug: Devanagari is Indo-Aryan and does not contrast short and long e/o, so its
plain `E` **is** the long vowel, while Telugu spends the unqualified name on the short
one. Name-matching therefore shortens every e and o -- `బడ్జెట్` for `బడ్జేట్`, a
different word. The shipped table is name-derived plus an explicit table for both classes.

### Which local model understands three languages

Same task as PR 39 -- classify which slot a buyer turn filled -- extended to Telugu, and to
Telugu **as the pipeline actually delivers it** after transliteration.

| Model | en | hi | te | te (from ASR) | mean latency |
| --- | --- | --- | --- | --- | --- |
| Qwen2.5-0.5B-Instruct | 2/4 | 1/4 | 2/4 | **0/2** | 515 ms |
| Phi-3.5-mini-instruct | **4/4** | 3/4 | 2/4 | **0/2** | 5,531 ms |

Three things follow. Accuracy degrades monotonically `en > hi > te`. So does latency --
Phi costs 4,519 ms in English and 6,715 ms in Telugu for the same task, because Telugu
consumes more tokens per character. And **neither model classified a single transliterated
Telugu turn correctly**, which is the only input the spoken path can produce.

The conclusion is the same one PR 39 reached for a different reason: the model is not what
makes Telugu work. The deterministic planner and the rule extractors are, and they run in
under 15 ms.

## Microphone capture, measured 2026-09-04

Target hardware: Windows 11, Python 3.12, 8 logical CPUs, no accelerator, RDP session with
remote-audio redirection. `sounddevice` 0.5.6, PortAudio V19.7.0-devel.

### PortAudio opens 16 kHz mono directly, so no resampling code is needed

`sd.check_input_settings(samplerate=R, channels=1, dtype="int16")` was accepted for
**16 000, 44 100 and 48 000 Hz**, and a real 0.5 s capture at 16 kHz returned 8 000 int16
samples. Every input device enumerated reported a `default_samplerate` of 44 100, so the
naive reading is that 16 kHz needs resampling; it does not, because WASAPI resamples
internally.

This is the measurement that removed a whole component. WebRTC's detector accepts only
10/20/30 ms frames at 8/16/32/48 kHz and Whisper wants 16 kHz, so had the device refused
16 kHz the capture path would have needed a resampler and a repacker — two pieces of
signal-handling code, both easy to get subtly wrong, on the path where a mistake is
inaudible and shows up as bad transcription. Capture is instead opened at exactly
`blocksize=480` (30 ms) and every callback yields one legal frame.

### Opening the device costs ~840 ms, so it is opened once

A blocking 500 ms capture took **1 344 ms** wall-clock end to end. The ~844 ms difference is
device open. That is longer than many buyer utterances, so a stream opened per utterance
would insert most of a second between the buyer finishing a sentence and the agent
noticing. The microphone is opened once and `pause()`/`resume()` gate delivery instead,
which costs nothing.

### `sounddevice` licence review

| Package | Version | Licence | Notes |
| --- | --- | --- | --- |
| `sounddevice` | 0.5.6 | **MIT** (`license_expression` in package metadata) | pure-Python `cffi` binding |
| PortAudio | V19.7.0-devel | **MIT** | bundled in the wheel |

Permissive with no distribution obligation, unlike the `piper-tts` extra
(GPL-3.0-or-later). Wheels are published as `py3-none-win_amd64` (also win32/arm64), so
there is no source build. Runtime dependency closure is `cffi` alone — no numpy, no
PyTorch, and no model weights, so installing it downloads nothing at import or capture time.

**Not measured:** capture on a machine with a physically attached microphone. Every device
enumerated here is RDP remote-audio, so open latency and the resampling path may differ on
local hardware. Nothing in the design depends on the specific number.

## Stance detection, measured 2026-09-04

Rule-based intent detection was checked against 15 sentences — four stances plus a
no-stance control, in English, Hindi and Telugu. **15/15 correct**, including the three
controls (`"We sell toys."`, `"हम कपड़े बेचते हैं।"`, `"మేము బొమ్మలు అమ్ముతాము."`) which must
return no stance rather than a default one.

Detection is a word-bounded vocabulary match, the same machinery business signals use, and
deliberately **not** the looser matching safety detection uses. The failure directions are
not symmetric: over-matching a safety phrase costs a polite refusal, over-matching a stance
makes the agent answer a concern nobody raised.

**Known limitation, measured and accepted:** there is no negation window, so
*"it is not expensive for us"* is read as an objection. Priority ordering removes the case
that actually costs money — `"It is expensive but let's start."` resolves to `READY`, not
`OBJECTING`, so a decided buyer is closed rather than re-qualified.

### Budget extraction: a hedge broke it, and was found by use rather than by review

`"Our budget is around 150000 rupees"` extracted **nothing**. The pattern required digits to
follow the cue with only punctuation between, so the single most common way a person states
a budget — with a hedge — filled no slot. The buyer answered the question, the answer was
discarded, the agent asked again, hit `MAX_ASKS_PER_SLOT` and closed the conversation
without a budget. `MAX_ASKS_PER_SLOT` was introduced in PR 39 to bound exactly this symptom;
this was its cause, one layer down, still live.

Fixed with a **closed list** of hedges in three languages rather than a permissive gap:

| Input | Before | After |
| --- | --- | --- |
| `Our budget is around 150000 rupees.` | miss | `budget is around 150000` |
| `budget is about 2 lakh` | miss | `budget is about 2 lakh` |
| `budget is up to 50000` | miss | `budget is up to 50000` |
| `हमारा बजट लगभग 150000 रुपये है` | miss | `बजट लगभग 150000` |
| `మా బడ్జెట్ దాదాపు 150000 రూపాయలు` | miss | `బడ్జెట్ దాదాపు 150000` |
| `budget is not decided, we sold 500 units last month` | miss | **miss** (required) |
| `no budget yet but we shipped 900 orders` | miss | **miss** (required) |

The last two rows are why the list is closed. "Allow up to two words between the cue and the
number" would read those as budgets of 500 and 900, and the failure directions are not
comparable: a missed budget costs one more question, an invented one is quoted back to the
buyer and shapes a proposal.

## Language switching (2026-09-04)

The conversation used to be told its language once and never revisited it. This measured
what that costs when the buyer changes language, and whether auto-detect is a usable
alternative. One qualifying sentence per language, synthesised with Piper, transcribed
three ways with `faster-whisper` `small`/int8 on 8 CPU cores.

- **matched** — decoder forced to the language actually spoken (the old best case)
- **stale** — decoder forced to the language declared *before* the buyer switched (the bug)
- **auto** — no hint at all (the candidate)

| Spoken | Arm | Hint | Detected | Prob. | Script | CER% | Transcript |
| --- | --- | --- | --- | --- | --- | --- | --- |
| en | matched | `en` | en | 1.00 | Latn | 28.6 | We run a retail shop and our budget is 50,000 rupees. |
| en | stale | `hi` | hi | 1.00 | Latn | 44.9 | ॐ ृ Woo-Run a retail shop ृ In our budget is 50,000 rupees |
| en | auto | – | **en** | 1.00 | Latn | **28.6** | We run a retail shop and our budget is 50,000 rupees. |
| hi | matched | `hi` | hi | 1.00 | Deva | 18.4 | हमारी दुकान है और हमारा बज़त पचा साजार रुबाई है |
| hi | stale | `en` | en | 1.00 | Latn | 100.0 | **Our shop and our budget is Rs. 50,000.** |
| hi | auto | – | **hi** | 0.96 | Deva | **18.4** | हमारी दुकान है और हमारा बज़त पचा साजार रुबाई है |
| te | matched | `te` | te | 1.00 | Telu | 247.5 | మారం moist పిత౿క్లి పిని బిమిస్ … |
| te | stale | `en` | en | 1.00 | Latn | 100.0 | **Our budget is Rs. 50,000** |
| te | auto | – | **te** | 0.96 | Telu | **110.0** | కాంరికింటాటిలూన సిడిరూడియాలేదినాంవాంనిం… |

Two conclusions, and the second is the one that decided the design.

**Auto-detect is free.** It matched a correct forced hint exactly on English (28.6%) and
Hindi (18.4%), beat it on Telugu (110% against 247%), and identified the language correctly
in every case at 0.96–1.00. There is no accuracy argument for forcing.

**A stale hint does not fail — it fabricates.** The bolded rows are the danger. Hindi and
Telugu speech forced to `en` came back as fluent, well-formed English the buyer never said,
labelled `en` at probability **1.00**, in Latin script. Every signal a caller could use to
notice the switch — the script, the reported language, the confidence — was erased, and the
budget extractor would happily have taken "Rs. 50,000" out of a sentence nobody uttered.
That is worse than garbage: garbage is visible.

So the voice loop *expects* a language without forcing it, and `--fixed-language` is
available for a caller that genuinely owns the language and wants the old behaviour.

Telugu remains poor at `small` under every arm. That is a model-size and voice question,
tracked separately; nothing here improves it, and auto-detect is simply the least bad.

### Detection thresholds

Hysteresis of **2 consecutive turns** before a detected switch, and **0** for a request.
One turn is too eager — a single borrowed word would move the reply language, the voice
and the transcriber at once. Three means the buyer has been answered twice in a language
they abandoned. A request bypasses it entirely: a person who asks and is then answered
twice more in the old language has been ignored, and knows it.

Romanised Indic text needs **2 distinct markers** from a closed token list before it is
read as Hinglish. One is not evidence — *"Namaste, we run a retail shop"* is an English
sentence containing a greeting.

## Thinking out loud (2026-09-04)

How long is the buyer actually left in silence between finishing a sentence and hearing a
reply? Measured on the shipped local path, Piper voices resident (a first pass that reloaded
the voice per synthesis inflated everything by ~2.4 s and was discarded):

| Spoken | Audio | Transcribe | Plan | Reply TTS | **Gap** |
| --- | --- | --- | --- | --- | --- |
| en | 4.0 s | 3,982 ms | 25 ms | 501 ms | **4,507 ms** |
| hi | 4.1 s | 4,453 ms | 6 ms | 95 ms | **4,553 ms** |
| te | 4.1 s | **37,692 ms** | 1 ms | 92 ms | **37,785 ms** |

**Transcription is the entire gap** — 88% of it in English, 97.8% in Hindi. Planning costs
1–25 ms and synthesising the reply with a resident voice costs 92–501 ms. That decides where
a backchannel has to hook in: it must start when the *endpointer closes the utterance*,
because by the time a transcript exists almost the whole silence has already been spent.

**Telugu at 37.7 s is a separate finding, not a backchannel problem.** `small` loops badly on
Telugu at this sentence length. No filler policy hides a 38-second wait; it is recorded here
because it is the single worst latency number this project has measured.

### Filler cost

| Register | Filler | Synth | Spoken | All-in | Headroom |
| --- | --- | --- | --- | --- | --- |
| en | `Hmm.` | 50 ms | 0.37 s | 421 ms | 4,086 ms |
| en | `Got it.` | 58 ms | 0.57 s | 627 ms | 3,880 ms |
| en | `Let me see.` | 47 ms | 0.81 s | 859 ms | 3,648 ms |
| hi | `अच्छा।` | 52 ms | 0.70 s | 749 ms | 3,804 ms |
| hi | `समझ गया।` | 53 ms | 0.88 s | 935 ms | 3,618 ms |
| mixed | `Achcha.` | 37 ms | 0.75 s | 791 ms | 3,762 ms |
| mixed | `Samajh gaya.` | 56 ms | 1.07 s | 1,124 ms | 3,429 ms |
| te | `అలాగా.` | 42 ms | 0.53 s | 576 ms | 37,209 ms |

Every candidate fits several times over, which is what makes a *second* filler on a long
wait affordable rather than a gamble. Thresholds are 700 ms for the first and 2,500 ms for
the second, capped at two per turn.

### What it buys

Reconstructing a full turn from those measurements and reporting the longest single stretch
of dead air — the thing that makes a pause feel like a dropped call rather than a beat:

| Spoken | Gap | Longest silence, off | Longest silence, on | Improvement |
| --- | --- | --- | --- | --- |
| en | 4,156 ms | 4,156 ms | **1,428 ms** | −2,728 ms |
| hi | 4,304 ms | 4,304 ms | **1,103 ms** | −3,201 ms |

Roughly 1.1–1.4 s is an ordinary conversational pause. Rendered turns are written to
`turn-en.wav` / `turn-hi.wav` by the probe so the part a number cannot judge can be listened
to.

### Receipt, never assent

A filler is chosen **before the buyer's sentence has been transcribed**, so it has to be safe
against whatever they just said. That rules out the most natural-sounding candidates:

| Considered | Verdict |
| --- | --- |
| `Hmm.` `Got it.` `अच्छा।` `Achcha.` `అలాగా.` | **kept** — assert only that we heard |
| `Ok.` `Yes.` `Sure.` `हाँ।` `Theek hai.` | **rejected** — assert agreement |

If the untranscribed sentence was *"so you'll do it for fifty thousand?"*, an agreeing filler
has committed the agent, out loud, to a number nobody quoted. A test asserts no shipped
phrase appears in the rejected set, in every language and register.

---

## Two lanes on one CPU (PR 43)

The proposal was two models cooperating: a fast one for conversation, a slower one for
strategy. Measured, the naive form of that is **negative value**, and the mitigation everyone
reaches for first makes it worse.

### Running both at once costs the turn path 3.4x

Qwen2.5-0.5B answering a turn, while Phi-3.5-mini generates in the background. 16 logical
CPUs, both int4 on CPU, `probe_dual_contention.py` and `probe_dual_quality_and_cap.py`.

| Slow lane | Turn path p50 | p95 | vs idle |
| --- | --- | --- | --- |
| idle | 453 ms | 494 ms | 1.00x |
| generating, all threads | 1,504 ms | 1,755 ms | **3.37x** |
| generating, capped to 4 threads | 1,582 ms | 1,777 ms | **3.59x** |
| generating, capped to 2 threads | 2,146 ms | 2,419 ms | **4.87x** |

Capping threads is the intuitive fix and it is the wrong one: the same work spread over
fewer cores takes longer, so the slow lane overlaps *more* turns. Background throughput fell
from 6.9 to 4.8 tokens/second at the same time.

### Preemption is free, so exclusion is affordable

`probe_preemption.py`:

| Measurement | Result |
| --- | --- |
| Time from asking the slow lane to stop, until it had | **0.1 ms** |
| First turn after the stop | 241 ms |
| Idle baseline for comparison | 247 ms (**0.98x**) |
| Slow lane time-to-first-token | 2,338 ms |
| Slow lane throughput | 5.6 tokens/second |

Verified end to end with both real models loaded (`probe_two_lane_end_to_end.py`): the turn
path ran at **0.99x** its idle baseline while preempting a live deliberation, the preempted
plan was discarded whole, and the plan produced afterwards was a usable site outline.

### How the lanes talk: measured, not chosen

`probe_lane_protocol.py`. Every hop of the A2A round trip is a real generation.

| Option | Cost | Needs both lanes running? |
| --- | --- | --- |
| A2A negotiation (3 generations) | **12,976 ms** | yes, so +3.37x on the turn path |
| Streaming the plan | first field readable 2,852 ms, last 6,736 ms | yes, so +3.37x |
| Shared state | **0.162 microseconds** | no |

Streaming is worse than the total suggests. At 2,852 ms a consumer has `competitors` and
neither `differentiator` (5,533 ms) nor `pages` (6,736 ms) — acting then means acting on a
plan whose pages have not been decided. That is the misconception risk, arriving early.

Shared state wins by roughly eight orders of magnitude and is the only option that does not
require the lanes to overlap. See `docs/ARCHITECTURE.md` for how ownership makes overwriting
impossible rather than merely prevented.

---

## Which model understands all three languages (PR 43)

Every language-model accuracy number this project had was measured in English. The product
claims English, Hindi and Telugu. `probe_trilingual_models.py`, one field, few-shot, the same
six meanings expressed in each register so a per-language score is a property of the language.

| Model | Licence | en | hi | te | Hinglish | Overall | p50 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Qwen2.5-0.5B | Apache-2.0 | 3/6 | 3/6 | 2/6 | 3/6 | 46% | 648 ms |
| Qwen3-0.6B | Apache-2.0 | 5/6 | 5/6 | **1/6** | 3/6 | 58% | 1,016 ms |
| Phi-3.5-mini | MIT | 5/6 | 5/6 | **2/6** | 5/6 | **71%** | 4,338 ms |

**Telugu is unsupported by every commercially-licensed model small enough for this hardware.**
1/6 and 2/6 are at or below guessing among five enum values. The two failures differ in a way
that decides the design: Qwen3 answers `none` for Telugu — it declines — while Phi answers
`business_type` for four of six, which fills a qualification slot the buyer never gave and
stops the agent ever asking. A confident wrong answer is worse than no answer, so Telugu is
not asked at all.

### Asking for two things at once collapses a small model

`probe_shipped_understanding.py` ran the **shipped** understanding path, not a copy of it.

| Schema | Model | acknowledge | buyer_intent | Distinct answers |
| --- | --- | --- | --- | --- |
| topic + stance | Qwen2.5-0.5B | 3/8 | 1/8 | **1** — a constant function |
| topic + stance | Phi-3.5-mini | 5/8 | 2/8 | 4 |
| topic only | Qwen2.5-0.5B | 6/12 bare, 7/12 few-shot | not asked | 3-4 |
| topic only | Phi-3.5-mini | 6/12 bare, **10/12** few-shot | not asked | 5 |

Qwen answered `none`/`stalling` to all eight turns. `STALLING` is in `ANSWERABLE_OBJECTIONS`,
so with a model configured **every reply became "answer the stall"** — including the turn
where the buyer said *"Yes, let us go ahead with the proposal."* Removing the stance field
is what made the same model useful.

Few-shot examples are worth their cost on the larger model only: Phi went 6/12 to 10/12 for
1,138 ms to 3,701 ms, Qwen 6/12 to 7/12 for 256 ms to 556 ms.

### Licences must come from the source

Checked against the HuggingFace API on 2026-09-04, because a search summary asserted two of
these and was wrong about both.

| Model | Claimed by search | Actual | Verdict |
| --- | --- | --- | --- |
| `google/gemma-3-1b-it` | Apache-2.0 | `gemma`, **gated=manual** | refused |
| `sarvamai/sarvam-1` | non-commercial | **no licence declared** | refused |
| `sarvamai/sarvam-m` | non-commercial | `apache-2.0` | permitted, but **23.6B** — not CPU-runnable |
| `Qwen/Qwen3-0.6B`, `Qwen3-1.7B` | Apache-2.0 | `apache-2.0`, ungated | permitted |
| `nvidia/Nemotron-Mini-4B-Instruct` | permissive | NVIDIA Community Model License, `language: [en]` | English only |
| `ai4bharat/indic-conformer-600m-multilingual` | MIT | `mit` | permitted — **ASR, a future option for Telugu** |

NVIDIA was checked specifically. Their small instruct models declare English only, and
Parakeet ASR covers Hindi but not Telugu, so nothing in that family closes the Telugu gap.

### One token budget cannot fit two schemas

Found only by running the real model end to end. The adapter's `max_new_tokens` defaulted to
96; a site plan measures 118 tokens. Every deliberation failed with `Unterminated string`,
having stopped inside `"differentiator"`. Constrained decoding guarantees valid JSON for a
*finished* answer, so a truncated one is an unparseable fragment rather than a shorter plan.
Budgets are now per schema: 32 tokens for a topic, 320 for a plan.

## Deciding the language before the buyer stops (2026-09-05)

[Thinking out loud](#thinking-out-loud-2026-09-04) measured the endpoint-to-audible gap at
**4,507 ms** in English, of which transcription is **3,982 ms**. This section is about where
that 3,982 ms actually goes, and how 1,576 ms of it was removed without changing a transcript.

### The shipped default runs the encoder twice

`probe_stt_cost_breakdown.py`, one 7.17 s English clip, `small` int8 CPU, beam 1, medians of
three fully-drained runs (faster-whisper's `segments` is a lazy generator, so timing
`transcribe()` alone measures nothing):

| Stage | Measured |
| --- | ---: |
| `_decode_pcm`, the pure-Python PCM conversion | **12.1 ms** |
| `transcribe(language=None)` - the shipped default | **3,857 ms** |
| `transcribe(language="en")` | **2,235 ms** |
| `detect_language()` alone | **1,682 ms** |

The transcripts were byte-identical. `3,857 - 2,235 = 1,622`, which is `detect_language` to
within noise: the default `speech_stt_language = ""` means Whisper encodes the audio once to
identify the language and once to decode it. The PCM conversion, the obvious suspect for a
pure-Python loop over 64,000 samples, is 0.3% of the total and not worth touching.

### Forcing the language is unsafe, and confidence will not tell you

The obvious fix - force the language the conversation is already in - was measured and
**rejected**. `probe_forced_language_safety.py`, three languages, every combination:

| audio | forced | mean conf | CER % | what came back |
| --- | --- | ---: | ---: | --- |
| en | en | 0.749 | 0.0 | correct |
| hi | hi | 0.622 | 0.0 | correct |
| te | te | **0.316** | 0.0 | correct |
| hi | **en** | **0.616** | 88.5 | *"We run a retail shop and our budget is 50,000 rupees."* |
| te | en | 0.511 | 284.2 | *"We will also run Retail Shop. Our budget is Rs. 50,000."* |

A wrong forced language does not produce a damaged transcript. It produces a **fluent,
plausible translation** of what the buyer said, in a language they were not speaking - which
also destroys the language signal the conversation uses to choose its reply. And it cannot be
filtered out: the worst offender scored **0.616 while the correct Telugu transcript scored
0.316**, so any confidence threshold rejects truth before it rejects fabrication.
`SEPARABLE BY CONFIDENCE ALONE: False`.

### So the saving has to come from starting earlier, not from guessing

Detection cost is **flat** - 1,631-1,758 ms for every prefix length tried - because Whisper
pads every clip to a 30 s mel window. It cannot be made cheaper. It can only be moved off the
critical path into the time the buyer is still talking.

`probe_language_floor_sweep.py`, nine utterances (three per language), sweeping prefix length
against the probability floor. `accepted-WRONG` is the column that matters: those are the
fabrications above.

| prefix | floor | accepted-correct | accepted-WRONG | rejected |
| ---: | ---: | ---: | ---: | ---: |
| 1.5 s | 0.5 | 6 | **1** | 2 |
| 1.5 s | 0.8 | 5 | **1** | 3 |
| **2.0 s** | 0.5 | 8 | 0 | 1 |
| **2.0 s** | **0.7** | **6** | **0** | 3 |
| 2.5 s | 0.7 | 6 | 0 | 3 |
| 3.0 s | 0.7 | 7 | 0 | 1 |

**1.5 s is unsafe at every floor**: Telugu was identified as **Malayalam at probability
0.90**, high enough to clear any threshold anyone would actually set. 2.0 s admitted no wrong
language at any floor from 0.5 to 0.9.

The shipped operating point is **2.0 s prefix, 0.7 floor**. 0.7 rather than the 0.5 already
used for *reporting* a language, because this floor decides whether to **impose** one on the
decoder, and the two failure modes are not comparable. The nearest wrong detection sat at
0.42, so 0.7 leaves 0.28 of margin, and everything it rejects simply transcribes the way it
always did.

A second, independent guard sits underneath the floor: a detected language outside
`{en, hi, te}` is discarded outright. That is what catches the Malayalam case, which no floor
would have.

### End to end, on the shipped path

`probe_early_detection_end_to_end.py` drives the real `SpeechTurnPipeline` with the real
WebRTC detector and the real adapter, delivering frames at their true 30 ms cadence. Timed
from the last frame of speech to the transcript existing - the gap the buyer sits through:

| language | audio | before | after | saved | same transcript |
| --- | ---: | ---: | ---: | ---: | :---: |
| en | 8.37 s | **3,983 ms** | **2,407 ms** | **1,576 ms (-40%)** | yes |
| hi | 8.46 s | 4,844 ms | 3,389 ms | 1,455 ms (-30%) | yes |

The English "before" of 3,983 ms independently reproduces the 3,982 ms measured on
2026-09-04 by a different probe, which is the strongest evidence available that this is the
real path and not a bench artifact.

**The cadence is the whole mechanism, and the negative case proves it.** Re-run with
`--instant`, feeding the same audio with no delay so there is no speech left to overlap:

| language | before | after | saved |
| --- | ---: | ---: | ---: |
| en | 3,436 ms | 3,373 ms | 63 ms |
| hi | 4,323 ms | 4,403 ms | **-79 ms** |

Nothing to overlap, nothing gained - and nothing lost beyond noise. An utterance that ends
before detection finishes falls back to exactly the previous behaviour, which is also what
happens below the probability floor and for any language outside the map.

### What this does not fix

Telugu. Transcription there is 37,692 ms and the 1.6 s this saves is 4% of it. That number is
untouched and remains the worst in the project.

## Telugu is a decoder pathology, and no configuration fixes it (2026-09-05)

Telugu transcription at **37,692 ms** for a 4.1 s clip has been the worst number in this
project since it was first measured, filed as "`small` loops badly on Telugu". A loop is a
generation problem, and faster-whisper exposes the standard controls for one, so it was worth
testing before anyone retracted a language.

`probe_telugu_loop.py`, one 6.82 s Telugu clip, `small` int8 CPU, beam 1:

| configuration | ms | xRT | chars | compression |
| --- | ---: | ---: | ---: | ---: |
| baseline (shipped) | **37,533** | 5.5 | 244 | 1.57 |
| `condition_on_previous_text=False` | 35,873 | 5.3 | 45 | 1.74 |
| **`no_repeat_ngram_size=3`** | **2,216** | **0.3** | 23 | 1.05 |
| `repetition_penalty=1.1` | 25,011 | 3.7 | 67 | 1.84 |
| `no_repeat` + `no_condition` | 2,388 | 0.4 | 23 | 1.05 |
| `compression_ratio_threshold=1.8` | 34,543 | 5.1 | 103 | 1.56 |

It **is** a decoder pathology: `no_repeat_ngram_size=3` is a **16.9x speedup**.

And it does not matter, because every configuration returns nonsense. Against a reference of
*"మేము రిటైల్ షాప్ నడుపుతాము మా బడ్జెట్ యాభై వేల రూపాయలు"*:

- baseline: `మరింరIsn claiming the jammals from the charity sponsor మింటి మారా ...`
- `no_repeat_ngram_size=3`: `మార్ల్ నికా సి వా్్ క్.`
- `condition_on_previous_text=False`: `𝘰𝘰𝘸𝘴𝘦𝘷𝘢𝘰𝘸𝘳𝘗𝘴𝘵, 𝘅, ...`

Whisper `small` cannot transcribe Telugu. The 37 seconds was the model flailing, and the knob
converts thirty-seven seconds of nonsense into two seconds of nonsense.

### The knob cannot be taken for the languages that do work

Run on **deliberately repetitive English** - a buyer insisting, and a price stated twice,
which is ordinary sales speech:

| configuration | transcript |
| --- | --- |
| baseline | `No, no, no. 50,000 rupees is our budget. **50,000 rupees.** And we really, really need the website by June.` |
| `no_repeat_ngram_size=3` | `No, no, no. 50,000 rupees is our budget. **50 thousand rupees.** And we really, really need the website by June.` |

Forbidding a repeated trigram forces the decoder to a different surface form for the second
mention. Nothing was lost semantically *here*, but the budget extractor reads numbers out of
exactly these strings and already misses hedged variants (`"around 150000 rupees"` extracts
nothing). Rewriting what a buyer said, to buy latency for a language the model cannot do
anyway, is not a trade worth making. English cost is unchanged at ~1,930 ms in every
configuration, so there is no speedup to gain there either.

### What shipped instead

An utterance whose language is **confidently** identified as one the transcriber cannot serve
is declined, in the ~1.7 s the identification costs, with the outcome
`language-unsupported`. This is the treatment this project already gives an unconfigured
transcriber: say so, rather than emit text nobody should act on.

Confidence matters. The decline fires only on a hint that already cleared the 0.7 floor from
[the early-detection work](#deciding-the-language-before-the-buyer-stops); an uncertain guess
still transcribes, and still pays the 37 s. Telugu **text** turns are unaffected - the
conversation engine handles Telugu text, and this is about speech only.

## Answering "what is p95 turn latency" (2026-09-05)

Every latency figure in this document came from a probe script run by hand, because the
running service measured `transcribe_ms` and `engine_ms`, sent them to whichever browser
asked, and discarded them. Turn stages are now recorded in-process and exposed at
`/metrics`, so the question is answerable from real traffic rather than from a bench.

Stages are recorded **separately** - `detect_language`, `transcribe`, `plan`, `synthesize`,
`total` - because a single turn-latency number hides the only term that has ever mattered:
transcription was 3,982 ms of a 4,507 ms English turn.

> **Correction (PR 46).** That sentence was not true when it was written. Five stages were
> *declared*; three were *recorded*. `detect_language` and `synthesize` had no call site at
> all, which meant the early-detection work of PR 44 was invisible in production and the
> reply-audio path was unmeasured. Both are recorded from PR 46 onwards.

Bucket edges stop at 30 s deliberately. A conventional set topping out at 10 s would have put
Telugu's 37.7 s in `+Inf`, hiding the worst number in the project inside the one bucket nobody
reads.

Verified against a live server: one real turn produced
`pitchbot_turns_total{disposition="continue",language="en"} 1`,
`pitchbot_turn_stage_ms_count{language="en",stage="plan"} 1`, and
`pitchbot_metrics_dropped_series_total 0`.

## What the buyer actually waits for (2026-09-05)

`probe_time_to_first_audio.py`. The server reports one latency number per spoken turn:

    turn_latency_ms = end_silence_ms + transcribe_ms + engine_ms

That is assembled from parts and stops when the reply *text* exists. But the buyer is
waiting to hear a voice, and reply audio is synthesised in a background task outside that
number. The hypothesis going in was that a meaningful amount of the wait was therefore
invisible.

**The hypothesis was wrong, and that is the useful result.** Real WebRTC detector, real
faster-whisper with the PR 44 early detection, the real engine, real Piper through the real
`ReplyAudio` framing, frames delivered at their true 30 ms cadence, median of 3:

| lang | reported | transcript | reply text | first audio | unreported |
|------|---------:|-----------:|-----------:|------------:|-----------:|
| en   | 2,646 ms | 2,417 ms   | 2,420 ms   | **2,587 ms** | **-59 ms** |
| hi   | 3,205 ms | 3,041 ms   | 3,043 ms   | **3,160 ms** | **-45 ms** |

Synthesis reaches its first frame in ~167 ms, and the reported number *overstates* the real
wait by ~2%. So there was no hidden latency to recover, and no case for restructuring the
reply path. Two things follow that are worth keeping:

- **Transcription is 91% of the buyer's wait** (2,417 ms of 2,646 ms in English). It was the
  problem before this probe and it still is; nothing else is close.
- The `synthesize` stage is recorded from PR 46 not because it is slow but because nothing
  could have told us if it became slow. A longer reply, a heavier voice or a loaded CPU all
  move it, and it sits outside every number the server previously reported.

Also visible in the Hindi row: `पचास हजा रुब़ए` came back garbled enough that the budget was
not extracted, while English `50,000 rupees` was. The reply differs accordingly - Hindi asked
what the business sells, English acknowledged the budget. That is an STT-quality gap, not a
latency one, and it is not addressed here.

## The new metric immediately found something (2026-09-05)

`verify_live_stages.py` drove one real spoken turn against a running server, frames at their
true 30 ms cadence, with `webrtc` + `faster-whisper` + `piper` all configured. `synthesize`
recorded on the first turn. **`detect_language` recorded nothing at all.**

That is not a plumbing fault. The server's endpointer split ~16 s of buyer speech into four
short utterances - *"We run a retail shop and hide our butt."*, *"and our budget is around
50th"*, *"thousand rupees for the first days of the"*, *"website."* - and early detection
needs roughly `early_detection_seconds` (2.0 s) of buffered speech **plus** its own flat
~1.6 s to finish before the endpoint arrives. None of those four utterances lasted long
enough, so every detection was started and then abandoned.

`verify_detect_language_ms.py` confirms the other half against the real adapter, by giving it
one utterance long enough to land:

| early detection | `detect_language_ms` | `transcribe_ms` |
|---|---:|---:|
| 2.0 s (shipped) | **1,644 ms** | **2,266 ms** |
| off (pre-PR-44) | `None` | 4,337 ms |

Two things follow. The 1,644 ms matches the flat detection cost measured in PR 44 exactly,
and the 4,337 -> 2,266 ms drop re-confirms that win independently, on a different sentence,
with identical transcripts.

And the operational finding, which is the point of having the metric: **early detection pays
off on long utterances and is wasted on short ones**, and short ones are what a real
endpointer produces from ordinary speech. The work is bounded - one abandoned detection pass
per utterance, documented in `_cancel_detection` - but it is not free, and until this metric
existed there was no way to see which case a deployment was actually in. Whether
`early_detection_seconds` should be lowered, or detection skipped for utterances unlikely to
reach the threshold, is now a question that can be answered from traffic instead of guessed.
Not changed here: it is a tuning decision that deserves its own measurement.

> **Retracted (PR 47).** "Short ones are what a real endpointer produces from ordinary
> speech" was wrong, and it was wrong because of a bug rather than because of speech. The
> socket path counted every 30 ms frame as 250 ms, so `max_utterance_ms` fired after 2.4 s of
> real audio and *manufactured* the short utterances this paragraph generalised from. With
> the frame duration measured rather than assumed, the same 8.4 s of speech is one utterance,
> and early detection lands on the first turn. No tuning of `early_detection_seconds` is
> called for. The metric still did its job - it made a bug visible that had been mistaken for
> a property of the world.

## One sentence, four replies: the endpointer's clock was 8.3x fast (2026-09-05)

`SpeechTurnPipeline.frame_duration_ms` defaults to **250 ms** because the browser client
calls `MediaRecorder.start(250)`, and `SimulatorService.create_speech_pipeline` never passed
a value. Every threshold the endpointer owns is a sum of frame durations:

| threshold | configured | reached after (30 ms PCM frames) | real audio |
|---|---:|---:|---:|
| `min_speech_ms` | 200 | 1 frame | 30 ms |
| `barge_in_speech_ms` | 300 | 2 frames | 60 ms |
| `end_silence_ms` | 700 | 3 frames | 90 ms |
| `max_utterance_ms` | 20,000 | 80 frames | **2.4 s** |

So a buyer speaking continuously was cut off after 2.4 s, the agent took the floor, the
buyer's continued speech was classified as barge-in, and the cycle repeated. Measured against
a live server with `webrtc` + `faster-whisper` + `piper`, feeding 8.4 s of one English
sentence at a true 30 ms cadence:

| | before | after |
|---|---|---|
| utterances | **4** | **1** |
| replies | 4, buyer interrupted 3x mid-clause | 1 |
| transcript | *"We run a retail shop and hide our butt."* + 3 fragments | *"We run a retail shop in Hyderabad, and our budget is around 50,000 rupees for the first phase of the website."* |
| transcription | 4,510 + 4,875 + 3,327 + 3,238 = **16,951 ms** | **1,866 ms** (9.1x less) |
| `detect_language` recorded | never | **yes, first turn** |
| budget extracted | no | yes |

`dropped_frames` was 0 throughout: nothing failed, and nothing logged. The only symptom was a
conversation that behaved badly.

Three earlier findings were consequences of this one, and are corrected above and below:

- PR 46's "early detection does not land on short utterances" — the short utterances were
  manufactured by this bug.
- The four-fragment transcript quoted in PR 46 as evidence about endpointing.
- The apparent need to tune `end_silence_ms` or the VAD aggressiveness.

The fix measures rather than assumes: mono 16-bit PCM carries its own duration, so it is
computed from the byte count and trusted only when it lands on a frame length WebRTC's
detector accepts (10, 20 or 30 ms) — exactly the set for which "these bytes are PCM at this
rate" is safe to read. Encoded frames from `MediaRecorder`, and the length-proxy frames the
benchmark sources emit, keep the configured value, because their byte count says nothing
about how long they last.

### Why the tests did not catch it

`tests/test_speech_turn_taking.py` builds frames of 1,024 bytes, which is 32 ms of PCM — not
a length WebRTC accepts, and therefore not measurable. Every existing test was, without
intending to be, a test of the fallback path. That is why the whole suite passed while the
running product cut buyers off mid-sentence, and it is the argument for the live checks this
project keeps insisting on.

## Two hypotheses that were wrong first (2026-09-05)

Both were measured and abandoned before the frame-duration bug was found. They are recorded
because each closes off a direction that looks obviously worth trying.

### Whisper's remaining decoding knobs are already taken

`probe_transcription_knobs.py`. The adapter sets `beam_size=1`, `compute_type=int8` and
`vad_filter=False`, and leaves `temperature`, `without_timestamps` and
`condition_on_previous_text` at their defaults. `temperature` looked like a hidden cost: it is
a fallback ladder that re-decodes a whole segment up to six times when confidence thresholds
fail, which is exactly what audio the model finds hard should trigger.

It does not trigger. Every variant landed within 3% of baseline, on both English and Hindi:

| variant | en | hi |
|---|---:|---:|
| baseline (shipped) | 1,900 ms | 2,447 ms |
| `temperature=0` only | 1,882 ms (0.99x) | 2,426 ms (0.99x) |
| `without_timestamps` | 1,900 ms (1.00x) | 2,376 ms (0.97x) |
| `condition_on_previous_text=False` | 1,878 ms (0.99x) | 2,421 ms (0.99x) |
| all three | 1,913 ms (1.01x) | 2,393 ms (0.98x) |

No knob is worth taking, and none is worth the risk: `without_timestamps` moved Hindi CER from
20.7% to 17.2%, which is a *transcript change*, and a latency win that changes what the buyer
is recorded as having said is not a win.

### Transcription cost is nearly flat in utterance length

`probe_transcription_cost_shape.py`. Whisper pads every clip to a 30 s window before the
encoder runs, so cost is dominated by a fixed term:

| audio | median | per audio second |
|---|---:|---:|
| 3.2 s | 1,833 ms | 579 ms |
| 5.7 s | 1,908 ms | 335 ms |
| 9.2 s | 2,030 ms | 221 ms |
| 16.1 s | 2,245 ms | 139 ms |

A 5x longer utterance costs 22% more. **The number of utterances is what drives the bill, not
their length** — which is why the frame-duration bug was expensive as well as rude: four
utterances where there should have been one is roughly four times the transcription work.

The same probe priced the model sizes, and confirms `small` is the floor for anything but
English:

| model | en | | hi | | script |
|---|---:|---:|---:|---:|---|
| `tiny` | 418 ms | 34.8% CER | 2,310 ms | 103.4% CER | 0% Devanagari |
| `base` | 681 ms | 20.9% CER | 764 ms | 87.9% CER | 0% Devanagari (returns Arabic script) |
| `small` | 1,979 ms | 17.4% CER | 2,615 ms | 20.7% CER | 100% Devanagari |

`base` is 2.9x faster than `small` on English but 3.5 points worse, and produces Arabic script
for Hindi — so a per-language model choice remains possible for English only, and is not taken
here on the strength of one sentence.

## One slow callback blocked every other session (2026-09-05)

`probe_callback_contention.py`. `CallbackService` guarded all of its state with a single
`asyncio.Lock`, and every public method held that lock across an adapter call - the
scheduler for `schedule`/`cancel`, the telephony provider once per due callback in
`dispatch_due`, and once per callback in `remove_by_prefix`. Those are network calls.

Measured with a 200 ms adapter, which is optimistic for a telephony dial:

| scenario | before | after |
|---|---:|---:|
| 1 session schedules once | 215 ms | 200 ms |
| 2 sessions, concurrently | 412 ms | 206 ms |
| 5 sessions, concurrently | 1,027 ms | 204 ms |
| 10 sessions, concurrently | **2,057 ms** | **205 ms** |
| unrelated `schedule` during a 1-callback dispatch batch | 393 ms | 190 ms |
| unrelated `schedule` during a 5-callback batch | 1,221 ms | 189 ms |
| unrelated `schedule` during a 10-callback batch | **2,241 ms** | **190 ms** |

Concurrent scheduling was exactly serial - ten sessions cost ten times one call. After
keying the lock by callback id it is flat at one call, a **10.0x** improvement at ten
sessions, and an unrelated caller no longer waits behind a batch at all: its wait is
constant regardless of how many callbacks the batch contains.

The batch itself is still sequential (2,054 ms for ten dials) and deliberately so. Dialing a
batch in parallel is a decision about what a telephony provider will accept, not a locking
question, and nothing here measured that.

Two things made this safe to change rather than delicate:

- **Serialising two operations on the same callback is a real requirement; on different
  callbacks it never was.** The lock is keyed by callback id, so the requirement is kept and
  the accident is dropped. Only one lock is ever held at a time, so there is no acquisition
  order to get wrong.
- **Nothing else needs a lock.** Every state transition in the service runs to completion
  without awaiting, and asyncio does not interleave code that does not await - including the
  capacity check, which reads the records and the pending schedules and inserts in one
  synchronous step.

The two hardest existing tests - concurrent admission against capacity, and a cancel claim
beating a concurrent dispatch - both still pass unchanged, which is the evidence that the
per-callback lock preserves the semantics the service was written for.

It does open one genuinely new race, because a batch no longer holds a claim over callbacks
it has not reached: a cancel can now land while an earlier callback in the same batch is
still dialing. `_dispatch_one` re-reads the record and refuses to dispatch anything that is
no longer `SCHEDULED`. That guard **survived mutation testing on first attempt** - the
pre-existing cancel test excludes the record at snapshot time, so the guard never fired -
and a test for exactly that interleaving was added.

## The browser was never heard (2026-09-06)

`probe_browser_path_with_real_vad.py`. `apps/web/audio-transport.js` recorded with
`MediaRecorder`, which produces WebM/Opus, and sent a chunk every 250 ms.
`WebRtcVoiceActivityDetector` accepts **only** 320, 640 or 960 bytes of mono 16-bit PCM.
Nothing in `src/pitchbot` decodes Opus - a search for opus / webm / ffmpeg / av. / soundfile
/ pydub finds one comment and no code.

Fed browser-shaped frames, the real detector rejected every one:

    3814 bytes -> PermanentAdapterError: webrtcvad requires [320, 640, 960] bytes ...
    120 browser-shaped frames through the pipeline
      utterances produced : 0
      frames dropped      : 120
      turn-taking state   : idle

Every frame was rejected, counted as a detector failure and treated as silence. The pipeline
is right to survive that - a detector fault must not end a call - but the buyer was never
heard, and nothing said so. `docs/TRY_IT_LOCALLY.md` told the reader to install
`webrtc-vad` and open the page, which is a combination that cannot work.

**Why the suite passed:** `MockVoiceActivityDetector` is *designed* to accept encoded-length
proxies - see the Opus variable-bitrate note in `adapters/mocks.py` - so every audio-socket
test exercised a detector that accepts anything. The same shape of blind spot as PR 47,
where every turn-taking test used a 1,024-byte frame that was not measurable.

### The browser now captures PCM

An `AudioWorklet` regroups the audio thread's 128-sample blocks into 480-sample (30 ms)
int16 frames, and the `AudioContext` is asked for 16 kHz so the browser does the resampling.
Verified in three independent places, because each covers what the others stub:

**1. The worklet's arithmetic** (`verify_pcm_worklet.js`, real worklet code in Node):

| check | result |
|---|---|
| frames of exactly 960 bytes | 278/278 |
| max per-sample delta after a round trip | **0** - nothing lost or reordered across block boundaries |
| a +2.5 sample | saturates at 32767 rather than wrapping |
| absent input | keeps the processor alive |

**2. Those bytes through the real server** (`verify_browser_frames_end_to_end.py`):

| | WebM/Opus (before) | PCM worklet (after) |
|---|---:|---:|
| frames the detector accepted | 0/120 | **278/278** |
| frames dropped | 120 | **0** |
| utterances | 0 | **1** |
| transcript | - | the full sentence |
| `detect_language_ms` | - | 1,550 ms |

**3. A real browser with a fake microphone** (`verify_browser_live.js`, headless Chrome fed a
WAV as its capture device): 2,655 frames sent, **every one 960 bytes**, peak 21,813 and max
RMS 7,763 - real signal, not silence - with **0 dropped** and a stable socket.

The third check does not reach a transcript, and that is a limitation of the harness rather
than of the product: Chrome's fake device loops the file, so 79.7 s of audio arrived from an
8.37 s WAV and there is never the trailing silence an endpointer needs. The second check
covers that final hop with the same bytes.

### What the change to PCM broke, and why it was already broken

At 250 ms the browser sent 4 frames a second; at 30 ms it sends **33**. Each frame appended
one `AUDIO_METADATA` event to the session timeline, and that made two things fail:

- The audio-chunk cap (2,000) was reached after 60 s of speech, and the socket was closed
  with `1013`. The browser reconnected and hit it again: **83 sockets in one run**.
- More quietly, `events` is a `deque(maxlen=200)` **shared with the conversation**. A spoken
  conversation's own turns were evicted by its own microphone - in six seconds at 33 fps, and
  in fifty seconds at the old 4 fps. The rate change did not introduce this; it exposed it.

Frames are still counted individually, which is what the capacity guard reads, but a timeline
entry is appended for the first frame and then every 500 (about 15 s of speech), carrying
cumulative counts. The event is evidence that audio arrived and was not retained - a property
of the stream, not of one frame - and the timeline goes back to being a record of the
conversation.
## A female voice, without weakening the licence gate (2026-09-06)

The agent's voice was `en_US-joe-medium`: **male**, `medium` quality, chosen in PR 33 for
being CC0 rather than for how it sounds. Reported by the owner as "male and very robotic".

Two things were conflated in that report, and only one is about the speaker. Piper publishes
each voice at a **quality tier**, and a `high` model is larger and carries more prosody than a
`medium` one - so "robotic" is partly the tier. The catalogue (`voices.json`, 244 KB, fetched
2026-09-06) has exactly three `high` English voices; two of them are female.

Licences verified from each voice's upstream MODEL_CARD, not from the catalogue index.
`median F0` is measured (see below), because "female" was otherwise inferred from a first name:

| voice | quality | median F0 | licence | commercial | attribution |
|---|---|---|---|---|---|
| `en_US-joe-medium` *(previous, male)* | medium | 104 Hz | CC0-1.0 | yes | no |
| **`en_US-ljspeech-high`** *(female)* | **high** | **236 Hz** | **public domain** | **yes** | **no** |
| `en_GB-cori-high` *(female)* | **high** | 202 Hz | public domain | yes | no |
| `en_US-kristin-medium` *(unverified)* | medium | 160 Hz | public domain | yes | no |
| `en_GB-alba-medium` *(female)* | medium | 203 Hz | CC-BY-4.0 | yes | yes |
| `en_GB-southern_english_female-low` | low | - | CC-BY-SA-4.0 | yes | yes |
| `te_IN-padmavathi-medium` *(female)* | medium | 197 Hz | CC-BY-4.0 | yes | yes |
| `hi_IN-priyamvada-medium` *(female)* | medium | 204 Hz | CC-BY-NC-SA-4.0 | **NO** | yes |
| `en_GB-jenny_dioco-medium` | medium | - | *"See URL"* - unresolved | **NO** | - |

### The label was checked, and one of them did not survive it

`verify_voice_pitch.py` estimates the fundamental frequency of a synthesised sentence per
voiced frame by autocorrelation and reports the median. Adult male speech sits near 85-155 Hz
and adult female speech near 175-255 Hz, which is enough to check a *label* - it cannot
establish a speaker's identity and is not meant to.

Six of the seven labels held. **`en_US-kristin-medium` did not**: at 160 Hz it falls inside
the band where the measurement cannot separate the two, so it is recorded as **unverified**
rather than female. The label had come from the first name, which is not evidence.

The first version of that script also had to be corrected: it set the male ceiling and the
female floor to the same 165 Hz, which made its own "ambiguous" verdict unreachable and would
have reported this borderline voice as confidently *male*. A check that cannot express
uncertainty manufactures it in the opposite direction.

The two voices actually recommended are unambiguous - `en_US-ljspeech-high` at 236 Hz and
`en_GB-cori-high` at 202 Hz, against the outgoing `en_US-joe-medium` at 104 Hz.

So the requirement was satisfiable without any trade: female **and** a higher quality tier
**and** a better licence than the voice it replaces. `public domain` is recorded as its own
`VoiceLicense` rather than reusing `CC0`, because they are not the same claim - CC0 is a
waiver instrument with text to point at, "public domain" is the publisher's assertion about
the training data - even though they behave identically at the gate.

`en_GB-jenny_dioco-medium` is the counter-example that keeps the gate honest: its MODEL_CARD
says only "See URL", so it is not in the catalogue at all. Deny-by-default means an unread
licence and a denied licence behave the same.

### Hindi is unchanged, and still blocked

All three published `hi_IN` voices remain non-commercial or unresolved, including the female
`hi_IN-priyamvada-medium` (CC-BY-NC-SA-4.0). Telugu is still the only Indic language this
project can *speak* commercially. Hindi **text** is unaffected.

### A correction about the samples

Every audio sample generated by a probe in this project passes
`SynthesisConfig(noise_scale=0.0, noise_w_scale=0.0)`, because `docs/BENCHMARKS.md` requires a
corpus item's SHA-256 to cover the exact file and VITS duration sampling is otherwise
non-deterministic. Zeroing those is exactly what removes the natural variation in timing, so
**probe audio is flatter than the product**. `speech_tts_deterministic` defaults to `False`,
so a real deployment never sounded as mechanical as a probe sample did.

## What a better-sounding voice costs, and whether anything beats Piper (2026-09-06)

Prompted by "can GitHub repos help with the voice - voicebox or something faster".

Two candidates were eliminated before any code ran:

- **Meta Voicebox** - the weights were never released. It is research-only, and every GitHub
  project using the name is unrelated to Meta's model.
- **Coqui XTTS-v2** - Coqui Public Model License, non-commercial. Fails the licence gate, and
  the project is discontinued.

That leaves **Kokoro-82M** (Apache-2.0) as the credible CPU-class alternative, and it is
interesting for a reason beyond speed: it publishes Hindi voices, and every Piper `hi_IN`
voice is non-commercial, which is a standing blocker in this project. Search results describe
it as "blazing fast" and "faster than Piper" while conceding that "direct head-to-head
benchmarks are scarce", so `probe_tts_alternatives.py` measured it here. `kokoro-onnx` was
used rather than the PyTorch package: it installs on onnxruntime with no torch, and the whole
environment is 179 MB.

Median of 3, same sentence, both engines warmed first, CPU:

| engine / voice | tier | first audio | total | realtime |
|---|---|---:|---:|---:|
| piper `en_US-joe-medium` *(outgoing)* | medium | **126 ms** | 362 ms | 16.5x |
| piper `en_US-kristin-medium` | medium | 157 ms | 433 ms | 15.7x |
| piper `en_GB-alba-medium` | medium | **182 ms** | 452 ms | 13.7x |
| piper `en_US-ljspeech-high` | **high** | **448 ms** | 1,508 ms | 4.2x |
| piper `en_GB-cori-high` | **high** | 477 ms | 1,496 ms | 4.3x |
| piper `hi_IN-priyamvada-medium` | medium | 156 ms | 269 ms | 22.3x |
| kokoro `af_heart` (en) | - | **2,683 ms** | 2,683 ms | 2.5x |
| kokoro `hf_alpha` (hi) | - | 2,187 ms | 2,187 ms | 2.5x |

### Kokoro is not faster here. It is 6x slower, and it cannot start early

2,683 ms to first audio against Piper's 126 ms. The gap is worse than the totals suggest,
because `kokoro-onnx` returns the whole clip from one call: first audio and last audio are the
same moment. Piper yields sentence by sentence, so it can begin speaking before the rest of
the reply exists - which is the difference between a pause and a silence in a conversation.

Kokoro's Hindi remains the one thing it offers that Piper cannot: an Apache-2.0 voice for a
language where every Piper voice is non-commercial. At 2,187 ms to first audio that is a trade
of latency for licence, not an improvement, and it is **not taken here**. It is recorded
because it is the only route to commercial Hindi speech found so far.

### The quality tier is not free, and this corrects the PR that introduced it

`high` was chosen for sounding less mechanical. It costs **+322 ms to first audio** over the
voice it replaces (448 ms against 126 ms) and drops realtime factor from 16.5x to 4.2x, so one
CPU serves roughly a quarter as many concurrent calls.

That is a real trade and it is the owner's to make, but it must be made in the open: PRs 44,
46 and 47 each fought for a few hundred milliseconds of exactly this kind of time, and a voice
change that silently returns 322 ms of it would undo part of that work without saying so.

The low-latency female option is **`en_GB-alba-medium`**: 182 ms, only 56 ms more than the
outgoing male voice, CC-BY-4.0, and confirmed female at 203 Hz. The high-fidelity option is
`en_US-ljspeech-high` at 448 ms. Both are documented; neither is hidden behind the other.

## What "real time" means to a person, not to a CPU (2026-09-06)

Every latency figure in this document has been reported as a *system* quantity - milliseconds,
or a realtime factor. Neither says whether a conversation feels natural, and the realtime
factor in particular is misleading: 16.5x versus 4.2x describes how many concurrent calls one
CPU could serve, not what a single buyer experiences. A buyer experiences exactly one number -
**the gap between finishing their sentence and hearing a reply** - so that is the number that
needs a target.

### The target comes from measurement, not preference

**Human-human conversation.** Stivers et al., *Universals and cultural variation in turn-taking
in conversation*, PNAS 106(26):10587-10592 (2009), measured turn transitions across ten
languages from unrelated families and found the **median gap is about 200 ms**, with
cross-language variation inside a ~250 ms band. Speakers subjectively believe other cultures
pace conversation very differently; measured, they barely differ. 200 ms is close to a human
universal, and it is *shorter* than the time it takes to plan an utterance - which is why
listeners predict the end of a turn rather than react to it.

**Interactive voice systems.** ITU-T Recommendation G.114 sets one-way mouth-to-ear delay
targets: **under 150 ms** is transparent, **150-400 ms** is usable but degrades interactivity
and callers begin to notice, and **over 400 ms** is unacceptable for interactive conversation.

Taken together, a defensible budget for a spoken sales agent:

| band | budget | meaning |
|---|---|---|
| human-equivalent | **~200 ms** | indistinguishable from a person taking their turn |
| good | **< 400 ms** | G.114's outer limit for interactive speech |
| tolerable | 400 ms - 1 s | noticeably slow; a person would fill it with a sound |
| broken | **> 1 s** | reads as a failure, not a pause |

### Where PitchBot actually sits

Measured end to end by `probe_time_to_first_audio.py` on the shipped English path -
real detector, real transcriber with early detection, real engine, real voice:

| stage | measured | share | multiples of the 200 ms human gap |
|---|---:|---:|---:|
| waiting out `end_silence_ms` to decide the buyer stopped | 700 ms | 27% | **3.5x** |
| transcription | ~1,717 ms | 66% | **8.6x** |
| planning the reply | ~3 ms | 0.1% | 0.02x |
| synthesis to first audio (`medium` voice) | ~167 ms | 6% | 0.8x |
| **total** | **~2,587 ms** | | **12.9x** |

So the agent answers about **thirteen times slower than a person would**, and **6.5x past
G.114's outer limit** for interactive voice. Two consequences follow that were not visible
while latency was reported as a system quantity:

**1. The endpointer alone blows the budget.** `end_silence_ms` is 700 ms, so before a single
instruction runs the agent is already 1.75x past G.114's 400 ms ceiling and 3.5x past the
human gap. No amount of model optimisation reaches "near real time" while a fixed 700 ms wait
precedes it. Humans do not wait for silence; they *predict* the end of a turn from syntax and
prosody, which is what turn-end prediction models exist to do. Lowering the threshold trades
against interrupting a buyer who is only pausing - a real trade, and one this project has not
yet measured.

**2. The `high` voice costs 1.6 human turn-gaps.** The +322 ms that `en_US-ljspeech-high` adds
over a `medium` voice is not a rounding error at this scale: it is **more than the entire
human conversational gap**, spent on timbre. That reframes the choice recorded above - it is
not "nicer voice, slightly slower", it is "nicer voice, at the cost of one and a half turns'
worth of human-scale latency".

### The mitigation this project already built, and does not use

The literature is consistent that **filled pauses and backchannels reduce *perceived* delay
even when measured delay is unchanged** - see *Real-time Latency Reduction With A Filler-based
Conversational Approach*, and *Improving Impressions of Response Delay in AI-based Spoken
Dialogue Systems* (IEEE, 2024). A listener who hears "hmm, okay" is not waiting; a listener who
hears nothing is.

`src/pitchbot/speech/backchannel.py` implements exactly this, with phrases in English, Hindi
and Telugu, and `SpeechTurnPipeline` exposes an `on_thinking` hook to fire it.

**It was wired only in `cli/talk.py`.** `SimulatorService.create_speech_pipeline` never passed
`on_thinking`, so the WebSocket path - the browser, and every deployment built on it - had no
filler at all and spent the full ~2.6 s in complete silence. The one research-backed
mitigation in the codebase was connected to the path a developer uses and not to the path a
buyer uses.

Two numbers bound the fix. `FIRST_AFTER_MS` is 700 ms, measured *after* the endpoint, so even
on the CLI the earliest filler lands ~1,400 ms after the buyer stops - 7x the human gap. A
filler that is meant to cover a 2.6 s silence should probably start nearer the endpoint than
that, and the endpoint wait itself is unfillable by construction.

**Closed, in this PR, for the socket path only.** `ThinkingFiller`
(`simulator/speech_output.py`) now carries the backchannel onto the WebSocket, gated by
`PITCHBOT_SPEECH_BACKCHANNEL_ENABLED` and inert without a configured voice. It fills the
silence; it shortens nothing. Transcription is still 66% of the wait, `end_silence_ms` is
still 27%, and the measured 2,587 ms is unchanged - the literature on filled pauses is about
*perceived* delay, and this repository does not have a way to measure that.

`FIRST_AFTER_MS` is deliberately **not** changed. Lowering it is a guess without a listener,
and 700 ms is the one number here with a stated rationale - below it a person would not have
said anything either. It is left as the next question rather than answered by assertion.

Three hazards were found while wiring it, and each is a property of the socket rather than of
the filler:

- **The floor.** The browser hands the floor back when playback ends. A filler that reported
  playback would release the floor the *reply* takes moments later, so barge-in would stop
  working for that turn. Fillers are therefore marked `filler: true` and played without being
  reported.
- **The word being chopped.** `ReplyAudioSender.start` aborts whatever is streaming, which
  tells the client to discard what it buffered. On a fast turn that clips a filler
  mid-syllable, which sounds like a fault where a completed one sounds like a person. The
  reply now drains the filler first (bounded at 1.5 s, because the receive loop is the only
  thing classifying buyer audio) and the browser schedules the reply behind it (capped at 2 s
  of queued audio, so a stuck stream costs a beat and not a minute).
- **The metric.** A filler is spoken before the reply has been planned. Counting it as
  `TurnStage.SYNTHESIZE` would have reported a synthesis time for a turn that did not have a
  reply yet, quietly making the one number a voice product is judged on meaningless.

Verified by mutating the wiring rather than by reading it: ten mutations, all ten caught.
Four of them were not caught on the first pass, and each was a test that could not see the
damage it was written to prevent - a stub synthesiser that finished inside one event-loop
tick, so "the reply did not chop the filler off" passed even with the wait removed; a socket
test that *hung* instead of failing when the filler stopped being sent at all; a
double-`start` whose second call cancelled the first before its synthesiser was ever
iterated, so counting what was said missed it; and a missing stop signal that changes nothing
about what is spoken and adds the full settle timeout to every short turn.

## Transcription is 66% of the turn, and four ways to shrink it do not work (2026-09-06)

The latency budget above makes transcription the largest term in the spoken turn: ~1,717 ms
of ~2,587 ms. `probe_transcription_cost_shape.py` had already left one door open, recording
that a per-language model choice "remains possible for English only, and is not taken here on
the strength of one sentence". This is that measurement, plus three other candidates. All
four fail, and the failures are worth more than another number: they say the 1,717 ms is
**structural to Whisper `small` on CPU**, not a tuning oversight.

Corpus: 8 B2B sales turns per language, synthesised with Piper at deterministic settings and
transcribed back on `small`/int8, 16 logical cores. Synthesised speech is cleaner than a
microphone, so absolute CER is optimistic - the comparison between models is the point, and
every model sees identical audio. `probe_per_language_model.py`.

### First, the scorer was wrong, and it mattered

The earlier one-sentence reading spelled digits out character by character, so a transcriber
writing "50,000" where the reference said "fifty thousand" was charged **25% CER for hearing
it perfectly**. Both `base` and `small` did exactly that on the first corpus sentence. The
scorer now collapses any run of number tokens - digits or words - to a single `<num>`.

This is the same trap a previous session hit in Arabic, where a flawless transcription scored
52% purely for writing "14.000". It is worth stating as a rule: **a transcription benchmark
without number normalisation is measuring formatting, not hearing.**

### 1. A smaller model for English - REFUTED

| model | median ms | median CER | worst CER | Hindi script |
|---|---:|---:|---:|---|
| `tiny` | 314 ms | 25.7% | 47.2% | latin (wrong) |
| `base` | 619 ms | 6.5% | 32.3% | arabic (wrong) |
| `small` | 1,855 ms | **0.0%** | 13.0% | devanagari |

`base` is **3.0x faster** and its median looks tolerable, which is exactly why the median is
the wrong statistic. Per sentence, `base` mangles **3 of 8** turns that `small` transcribes
perfectly:

| said | `base` heard |
|---|---|
| "We need it live before the festival season in October" | "We needed life before the first of all season and October" |
| "Who else have you built something like this for?" | "You also rebuilt something like this for." |
| "The decision will be made by me and my brother, we are partners." | "The touch of the beam made by me and my brother..." |

A sales agent that mishears three turns in eight is not a faster agent, it is a broken one.

**Near miss worth recording.** `avg_logprob` separates the failures cleanly on this corpus -
the three bad transcripts scored -0.45, -0.50 and -1.14, the five good ones -0.24 to -0.36 -
so a confidence-gated cascade (`base` first, re-run on `small` when it is unsure) would cost
about 1,315 ms on average against 1,855 ms today. It is not taken: the threshold would be
fitted on the same eight sentences that motivate it, it needs a second resident model, and
its fallback path (2,474 ms) is *slower* than doing nothing. Named, not shipped.

### 2. Shrinking Whisper's 30 s window with `chunk_length` - REFUTED

Cost is flat in utterance length because Whisper pads every clip to a 30 s window, so
avoiding the padding looked like free money. It is not available: `chunk_length` changes
segmentation, not the encoder window.

| `chunk_length` | median ms | median CER | worst CER |
|---:|---:|---:|---:|
| 30 (default) | 2,079 ms | 0.0% | 13.0% |
| 20 | 2,059 ms | 0.0% | 13.0% |
| 15 | 1,972 ms | 0.0% | 13.0% |
| 10 | 2,059 ms | 0.0% | 13.0% |
| 5 | 1,976 ms | 0.0% | **76.1%** |

Every value is within noise of 30, and at 5 the accuracy collapses while the latency does
not move.

### 3. Transcribing during the endpoint wait - REFUTED

The endpointer spends 700 ms confirming the buyer stopped, and the CPU is idle for all of it.
Starting the transcription when silence *begins*, concurrently with that wait, would recover
up to 700 ms with - so the argument went - no accuracy cost at all, because the audio differs
only by trailing silence and Whisper pads to 30 s anyway.

The premise is false. The same speech transcribed with 0 / 300 / 400 / 700 / 1500 ms of
trailing silence appended:

| language | sentences identical at every padding |
|---|---|
| English | 4 / 5 |
| Hindi | **1 / 5** |

And the English disagreement is not cosmetic: at 700 ms the model returns *"...on what's
happening **and it's** getting hard to manage"*, at every other padding it drops those words.
A speculation launched at 400 ms would therefore answer a **different sentence** from the one
the endpoint would have produced, sometimes a worse one. Whatever it saves, it is not free,
and "free" was the entire case for it.

### 4. CPU threading - REFUTED (already optimal)

| `cpu_threads` | median ms | speedup |
|---:|---:|---:|
| 0 (default) | 1,729 ms | 1.00x |
| 2 | 2,754 ms | 0.63x |
| 4 | 1,711 ms | 1.01x |
| 8 | 1,611 ms | 1.07x |
| 16 | 1,697 ms | 1.02x |

The default already picks well. 8 threads is 7% faster on this machine - inside run-to-run
noise, and a machine-specific number not worth pinning.

### What the four refutations leave

Transcription latency on this hardware is not reachable by decoder knobs, model size,
window size, concurrency or threading. Reducing it means changing the engine class, which is
a licence-and-language question rather than a tuning one. Meanwhile the mitigation that
*is* available is perceptual, and is already shipped: the backchannel.

## A supported language can hold the decoder for 28 seconds (2026-09-06)

Found while running the corpus above, and the reason this PR exists at all.

`small`/int8, Hindi, **a supported language in the shipped configuration**:

| clip | audio | median | observed range |
|---|---:|---:|---|
| English, 8 sentences | 3.1-6.3 s | 1,855 ms | 1,860-2,079 ms |
| Hindi, "हम एक रिटेल दुकान..." | 5.8 s | 2,491 ms | - |
| Hindi, "क्या आप हमारे लिए..." | **3.2 s** | **11,455 ms** | **11,983-28,656 ms** |

The slow clip is the **shortest** one. It is not a runaway output - one segment, 40
characters, compression ratio 1.24 - the decoder simply searched, reproducibly, across five
separate runs at different paddings.

Nothing bounded it. `max_audio_seconds` (120 s) bounds how much audio may be *submitted*, and
that bound could never have caught this: cost is nearly flat in length, 16.1 s of speech costs
2,245 ms, and the offending clip was 3.2 s. The input was never large, only slow.

The cost is worse than a slow reply. The socket's receive loop waits inside
`SpeechTurnPipeline.push`, so for the whole 28 seconds the agent is **deaf** - it cannot
classify a frame, cannot notice a barge-in, and cannot be interrupted. Against the budget
above, 28,656 ms is **143x** the ~200 ms gap a person leaves between turns and 72x G.114's
ceiling.

`DEFAULT_TRANSCRIBE_TIMEOUT_MS` is 6,000 ms, chosen because the two regimes are an order of
magnitude apart rather than adjacent: every healthy transcription measured here costs
1.9-2.5 s regardless of audio length, and the live endpointer caps an utterance at 20 s, so
~2.5 s is the worst healthy case the socket can produce. 6 s clears it by better than 2x and
still cuts the pathology by 2-5x. There is nothing between 2.5 s and 11 s to tune against.

**The deadline recovers the turn, not the CPU.** `asyncio.to_thread` cannot be interrupted, so
the abandoned decode keeps running until it finishes on its own - which is the argument for a
generous default rather than an aggressive one, since every timeout leaves a worker competing
with whatever runs next. That property is asserted directly in
`tests/test_speech_transcribe_timeout.py` rather than left as a comment.

## Releasing the turn is only half a fix (2026-09-06)

The deadline above stops a flailing decoder holding the conversation. It does not, on its
own, tell the buyer anything.

Traced through the shipped socket path: an utterance that produces no transcript takes the
early return in `_handle_utterance`, which sends a JSON `utterance` message and **nothing
else** - no reply, no audio. So the sequence a buyer actually experienced on a timeout was:
speak, hear a filler at 700 ms, hear a second filler at 2.5 s, then **silence**, forever.

Silence is the one response a voice product cannot use, because it is indistinguishable
from a fault in every layer beneath it - the microphone, the socket, the browser, the call.
A buyer cannot tell a dropped turn from a dropped call.

`speech/recovery.py` answers it out loud, in the session language, and the set of outcomes
that get an answer is deliberately two:

| outcome | answered? | why |
|---|---|---|
| `transcription-timed-out` | **yes** | the buyer definitely spoke - an utterance only endpoints after `min_speech_ms` - and the decoder definitely failed |
| `transcriber-unavailable` | **yes** | same shape: speech captured, component did not read it |
| `no-speech-recognized` | no | may be a cough, a door, a chair. An agent that says "sorry?" to a cough is worse than one that ignores it, and this outcome cannot tell them apart |
| `low-confidence` | no | a judgement about a transcript that *exists*, not a failure to produce one |
| `oversize` | no | the buyer ran past the cap; asking them to repeat a too-long speech is the wrong remedy |
| `language-unsupported` | no | the agent has just decided it cannot serve this language; answering anyway contradicts the decision it made a millisecond earlier |

**The phrasing owns the failure.** Every line says the agent missed it, never that the buyer
was unclear - the buyer may have spoken perfectly and the decoder timed out anyway. Blaming
a listener for a fault in the machine is untrue, and in a sales call it is expensive.

Hinglish gets a romanised line rather than a redirect to Devanagari, for the same reason the
reply tables carry a `MIXED` entry: switching a Hinglish speaker into literary Hindi reads as
correcting them, and an apology is the worst moment to do that.

### A label map had already drifted, silently

`OUTCOME_LABELS` in `apps/web/app.js` is `UtteranceOutcome` written out by hand in
JavaScript, and it renders straight at the buyer with `|| payload.outcome` as the fallback.
`language-unsupported` was added to the enum in an earlier change and **never labelled**, so
that outcome had been showing the raw identifier. Adding `transcription-timed-out` would
have done it a second time.

No import can catch that - one side is Python, the other a JavaScript object literal - so it
is now a test that parses the real `app.js` and asserts the two sets match exactly, in both
directions. A label for an outcome that no longer exists fails it too.

## Hindi can be spoken commercially after all (2026-09-06)

PitchBot has never been able to say a Hindi word aloud in a deployment that sells anything.
Every published Piper Hindi voice reviewed on 2026-09-03 is CC-BY-NC-SA or points at an IITM
licence that returns 403, and this project treats an unread licence and a denied one
identically. English and Telugu are cleared. Hindi was simply unavailable - and that is a
*structural* hole rather than a missing file, because one synthesiser served every language,
so a language its engine could not license was unspeakable.

A survey of the alternatives left exactly one candidate that clears the licence gate **and**
runs on CPU without torch: **Supertonic 3**. Verified at source rather than from metadata.

### Why "verified at source" is not pedantry here

The most popular project in that survey, `debpalash/VoiceStudio` (19.4k stars), defaults to
`k2-fsa/OmniVoice`. Its Hugging Face API `cardData.license` field is **empty**, so a check
reading metadata alone passes it. Its model card says, verbatim:

> "Our code is released under the Apache 2.0 License. **The pre-trained model is licensed
> under the CC-BY-NC** due to constraints from its training data (e.g., Emilia)."

Apache code, non-commercial weights, and nothing in the machine-readable field to say so.
That is this project's per-checkpoint rule proving itself.

Supertonic 3, read the same way: sample code MIT; weights **BigScience OpenRAIL-M**, which
permits commercial use subject to Attachment A. Clause (e) forbids disseminating generated
content "without expressly and intelligibly disclaiming that the information and/or content
is machine generated", and clause (g) forbids impersonation without consent. Those are
obligations on the deployment, which is why the provider is off by default and names them in
its startup log. 31 languages including `hi`; **no `te`**.

### Measured on this hardware

`probe_supertonic_hindi.py`, 8 Hindi sales turns, 16 logical cores, CPU only. Intelligibility
is checked by transcribing the output back through `faster-whisper` `small`, not by ear -
nobody here can judge Hindi by listening.

| `total_steps` | median ms | Hindi CER | worst CER | verdict |
|---:|---:|---:|---:|---|
| 2 | 509 ms | 46.2% | 75.8% | too few steps |
| 4 | 658 ms | 21.9% | 31.4% | about Piper's quality |
| **8** | **1,130 ms** | **13.2%** | 26.5% | **better than Piper, and legal** |
| 16 | 2,048 ms | 13.5% | 21.2% | 1.8x the cost for nothing |

The comparison is `hi_IN-pratham-medium` at **18.3%** CER through the same transcriber - the
voice this project may not ship. So at 8 steps Supertonic is *more* intelligible than the
Hindi voice PitchBot could never use.

English is 0.0% median CER at every step count, so the dial only matters for Hindi.

**What it costs.** ~1,130 ms per sentence against Piper's 126-448 ms, and no within-sentence
streaming: `synthesize()` returns one complete waveform. The adapter therefore splits on
sentence boundaries and yields each as it lands, which is how Piper already behaves and is
the only reason a multi-sentence reply starts speaking before its last sentence exists.
Against the latency budget this is expensive; against the alternative - *no Hindi speech at
all* - it is the only option on the table.

Footprint: four ONNX graphs, `pip install supertonic` pulls **no torch**, cold load ~10.3 s
including the download.

### The measurement was wrong the first time, and the tell was English

The first run scored Hindi at 72-87% CER and **English at 64%** - against ~0% for Piper
through the same transcriber. A model being sixty times worse than its own published numbers
is not a finding, it is a broken measurement.

Supertonic emits **44,100 Hz**; `faster-whisper` assumes 16,000. Handing it the raw array
plays the speech 2.76x too slow, and the transcriber scores mush. Resampling with
`scipy.signal.resample_poly` moved Hindi from 72.1% to **13.2%** and English from 64.2% to
**0.0%** - the same audio, correctly interpreted.

Worth stating as a rule, because it would have produced a false rejection of the only viable
Hindi option: **when a candidate scores far worse than its own published numbers on a
language you can check, suspect the harness before the model.** English was the control, and
it is what exposed the bug.

## Hinglish had no voice at all (2026-09-06)

`LanguageCode.MIXED` is a first-class language in this product. It has its own reply table,
its own backchannel phrases and its own recovery line, all deliberately romanised with the
English business nouns kept, because answering a Hinglish speaker in literary Devanagari
reads as correcting them.

None of it could be said out loud. `MIXED` appeared **nowhere** in the TTS layer - no voice
mapped, none documented - and `PiperVoiceRegistry.resolve` refuses an unmapped language. In
the socket path that refusal is caught and reported as a stream with zero frames, so the
browser quietly spoke the reply in its own voice: the exact situation the server-side voice
provider exists to replace, reached silently.

An operator *could* map `mixed=<voice>`. Nothing said which, and the answer is not obvious -
the text is Latin script but the words are Hindi, so an English phonemiser reads the letters
and a Hindi phonemiser expects Devanagari.

`probe_hinglish_voice.py`, on the product's own Hinglish reply lines. Each line is
synthesised, transcribed back forcing `hi`, and scored against the **Devanagari** a listener
should hear - the question is whether the Hindi words survive, not the Latin spelling.

| candidate | median ms | median CER | worst CER | commercial? |
|---|---:|---:|---:|---|
| `piper en_US-joe-medium` | 134 ms | 49.9% | 64.1% | yes |
| `piper en_US-ljspeech-high` | 609 ms | 54.5% | 70.4% | yes |
| `piper hi_IN-pratham-medium` | 196 ms | 43.4% | 59.5% | **no** |
| supertonic `lang=en` | 1,080 ms | 38.6% | 43.2% | yes |
| **supertonic `lang=hi`** | 1,305 ms | **21.2%** | 37.0% | yes |

Twice as intelligible as the best Piper option, and 6.7x slower. The Piper Hindi voice in
that table is CC-BY-NC-SA, so the best *legal* alternative scores 49.9%.

What that difference sounds like, for *"Aapka budget kitna soch rahe hain?"* (should be
आपका बजट कितना सोच रहे हैं?):

| candidate | transcribed back as |
|---|---|
| `piper en_US-joe-medium` | अखग भज़ट कितनिसाख राहें |
| `piper hi_IN-pratham-medium` | आपका बज्द कितने से क्वेहें |
| supertonic `lang=en` | आपका बजँत कितना सोक रेहें |
| **supertonic `lang=hi`** | **अपका बज़त कितना सुख्रे हैं** |

The winner is recognisably the sentence. The best legal Piper option is not.

### The first timings were four times too slow, and it was the harness again

The probe loaded the ONNX voice on every call, so ~2 s of file I/O sat inside what was
supposed to be a synthesis measurement: `en_US-joe-medium` read 2,391 ms instead of 134 ms.
The CER column was unaffected - loading does not change the audio - but the latency column
was meaningless, and it was the column the decision would have been argued over. The product
loads a voice once and keeps it resident; the probe now does too.

That is twice in two days that a measurement, not a model, was the thing that was wrong.
Both times the tell was a number that made no sense next to a known-good baseline.

### Transliterating Hinglish to Devanagari makes it worse (2026-09-06)

The obvious next move after routing Hinglish to the Hindi frontend: the words are Hindi, so
write them in Devanagari before synthesising and the phonemiser should stop guessing.

The **gap is real**. Same six sentences, same engine, same transcriber - only the input
script changes:

| input | synth ms | audio | median CER |
|---|---:|---:|---:|
| romanised *(shipped)* | 1,059 ms | 3.34 s | 21.9% |
| ITRANS -> Devanagari | 984 ms | 3.24 s | **35.6%** |
| hand-written Devanagari *(ceiling)* | 897 ms | 2.68 s | **11.0%** |

So a correct transliteration would be worth **11 points** - it would roughly halve the error
and bring Hinglish to the same quality as native Hindi.

**`indic-transliteration` (ITRANS) is not that transliterator.** It is 13.7 points *worse*
than shipping the romanised text unchanged, and the reason is structural rather than a
tuning problem:

| romanised | ITRANS gives | should be |
|---|---|---|
| `aapka budget kitna soch rahe hain` | आप्क बुद्गेत् कित्न सोच् रहे हैन् | आपका बजट कितना सोच रहे हैं |
| `theek hai aapka business samajh gaya` | थीक् है आप्क बुसिनेस्स् समझ् गय | ठीक है आपका बिज़नेस समझ गया |

Two failures, both inherent:

1. **The implicit schwa.** ITRANS is a *strict* scheme: a bare consonant means halant. Informal
   romanisation relies on the reader supplying the vowel, so `kitna` becomes कित्न rather than
   कितना and almost every word ends in a dead consonant.
2. **English loanwords.** Hinglish deliberately keeps `budget`, `website`, `payment` in
   English - that is the register, and the reply tables say so explicitly. Transliterating
   them phonetically as Sanskrit produces बुद्गेत्, which is not a word in any language.

Informal romanised Hindi to Devanagari is a **transliteration model** problem, not a mapping
table. AI4Bharat's IndicXlit exists for it, but it is a torch model and this project's TTS
environment is deliberately torch-free.

Recorded rather than attempted: the 11-point prize is now measured, and so is the fact that
the cheap route to it does not work.

### `speed` is not a latency dial (2026-09-06)

Supertonic exposes `speed`, and at ~1,130 ms per sentence it is the slowest thing in the
speech path, so it looked like the obvious lever. It is not:

| speed | synth ms | audio s | median CER |
|---:|---:|---:|---:|
| 1.00 | 852 ms | 2.79 s | 13.1% |
| **1.05** | 873 ms | 2.68 s | **12.1%** |
| 1.15 | 848 ms | 2.44 s | 16.8% |
| 1.30 | 692 ms | 2.16 s | 33.8% |

Going 1.00 to 1.15 saves **4 ms** of synthesis and costs **4.7 points** of CER. The rate
changes how much audio is produced, not how fast it is produced, so it shortens playback
while the buyer is still waiting the same time to hear anything.

1.05 is both the library default and the measured minimum, which is why it is pinned in
`DEFAULT_SPEED` with the table above and **not** exposed as configuration: a knob whose
entire measured range is worse than its default is not configuration.

### Loading a voice stalls the event loop; synthesising through it does not (2026-09-06)

The end-to-end check in PR 51 reported `mixed: first 3,415 ms`. The adapter synthesises one
sentence at a time and yields each as it lands, so that number should have been one
sentence - about 1 s. The extra two seconds were the model being loaded lazily, inside the
first buyer's turn.

Measured by `probe_preload_gap.py` with the weights already on disk, against a task that
ticks every 5 ms and records how late each tick actually was:

| event-loop lateness | median | worst |
|---|---:|---:|
| idle | 10.9 ms | 11.7 ms |
| **during load** (1,358 ms of work) | 60.9 ms | **488.7 ms** |
| during synthesis (972 ms of work) | 10.8 ms | 11.5 ms |

Synthesis is **indistinguishable from idle**. Loading is not: it holds the GIL in bursts
despite running under `asyncio.to_thread`, which moves it off the loop's stack but not out
of its way. The loop carries the audio socket, so a 489 ms stall is 489 ms in which the
buyer's frames are not read and barge-in cannot fire.

| first Hinglish turn | time to first audio |
|---|---:|
| lazy (load + first sentence) | 2,329 ms |
| preloaded (first sentence only) | **972 ms** |

This is the same shape Piper showed on 2026-09-03 (2,561 ms to load a voice, ~110 ms to
synthesise through a resident one), which is why `preload_speech_providers` exists at all.

**The routing wrapper had silently switched it off.** `preload_speech_providers` decides by
`isinstance(provider, Preloadable)` on whatever `build_text_to_speech` returned, and once a
single language is routed that object is `LanguageRoutedTextToSpeech`. It forwarded
`synthesize` and nothing else, so the check was `False` and **Piper stopped being
preloaded** - putting its ~2.5 s voice load back into the first English or Telugu turn, in
a deployment whose only change was enabling Hindi.

A wrapper that forwards one method of a protocol does not merely fail to add a capability;
it removes one the wrapped object had. Audited for others of the same shape: `Preloadable`,
`RetunableTranscriber` and `EarlyDetectingTranscriber` are the only capabilities detected by
`isinstance` on an adapter, and the transcriber has no wrapper - the pipeline holds it
directly. The synthesiser was the only place the defect could exist, and it did.

### The backchannel was counting from the wrong instant (2026-09-07)

`FIRST_AFTER_MS` reads 700 and is documented as *"a beat of silence after someone stops
speaking is normal turn-taking"*. Measured on the real pipeline in **audio time** - frames
times frame duration, which is exactly reproducible and is what the buyer experiences
(`probe_filler_timing.py`):

| counting from the buyer's last speech frame | before | after |
|---|---:|---:|
| endpointer closes the utterance | 720 ms | 720 ms |
| `on_thinking` fires (filler clock starts) | 720 ms | 720 ms |
| **first filler spoken** | **1,420 ms** | **920 ms** |
| second filler spoken | 3,220 ms | 3,200 ms |
| reply audio ready (measured whole turn) | 2,587 ms | 2,587 ms |

The clock started at `on_thinking`, which the pipeline fires when the endpointer *closes*
the utterance - and an utterance only closes after `end_silence_ms` (700 ms) of trailing
silence. So a threshold that read 700 delivered **1,420 ms**, twice its own value and 7.1x
the ~200 ms gap Stivers et al. (PNAS 2009) measured between human turns.

**Both halves of that docstring were true and they were not the same instant.**

The endpointer already tracked the trailing silence and discarded it at the boundary, so
`SpeechSegment` now carries it. Measured rather than assumed to be `end_silence_ms`: a
`MAX_DURATION` close arrives with the buyer possibly still mid-sentence, where the honest
offset is zero and crediting 700 ms of silence that never happened would make the filler
interrupt someone still talking.

#### Crediting the silence broke the other half of the same docstring

*"It also keeps the filler off any path that is already fast."* Against buyer-silence alone
every spoken turn clears the beat the instant we learn there is work - including one whose
reply is milliseconds away - because 700 ms of the threshold is spent before the filler
task exists.

That matters because a filler is not free to abandon: `settle` waits for one to finish
rather than chopping it mid-word, so a filler starting just before the reply does not cover
the wait, it **extends** it by the filler's own length (0.37-1.07 s measured).

So there are two clocks, and a filler must satisfy both: enough silence for the buyer to
feel a gap, and enough work for us to be sure there is one. `MIN_WORK_MS = 200` is the same
human turn-gap - the beat a person takes before deciding someone else's pause needs filling
- and it is capped at `first_after_ms` so it is a floor and never the binding constraint.
With no silence credited (a typed turn) the wait is exactly `first_after_ms`, unchanged.

`SECOND_AFTER_MS` moved 2,500 -> 3,200 to keep the position it actually had. 2,500 measured
from a close that was already 720 ms late put it at 3,220 ms; left alone once the reference
frame was corrected it would have fired **87 ms before the reply was ready**.

### The dominant latency term was documented as configuration and was not (2026-09-07)

`TurnTakingConfig` has described itself, since it was written, as *"configuration rather
than a constant so it can be tuned against measurements once a real detector is
benchmarked"*. Nothing ever built it from `Settings`. `SimulatorService` takes a
`turn_taking` parameter and neither branch of `_build_service` passed it, so every
deployment ran the dataclass defaults - which is what a constant is.

The scale of what was unreachable, against the measured ~2,587 ms spoken turn:

| term | cost | share | configurable before |
|---|---:|---:|---|
| `end_silence_ms` | 700 ms | **27%** | **no** |
| transcription | ~1,717 ms | 66% | model, device, beam size, timeout - yes |
| plan + synthesise | ~150 ms | 6% | voice, engine, steps - yes |

`Settings` carried 26 speech knobs - down to `speech_stt_beam_size` - and not one for turn
taking, while the largest term after transcription sat behind a dataclass default.

This is also the only honest way to move that number. It cannot be fitted here: fitting it
needs recordings of real speakers pausing mid-thought, and every corpus this project has is
synthesised, with no natural pauses to fit to. That is why it has stayed on the backlog as
*blocked* rather than being guessed at. A deployment with real traffic can find its own,
and the trade-off is stated where they will read it - lower and the agent interrupts people
who were still thinking, higher and every reply feels sluggish.

All five thresholds are exposed rather than only the dominant one, since the same argument
applies to each and `TurnTakingConfig` already validates them. Defaults are unchanged and
asserted to be, so wiring it moves nothing by itself.
