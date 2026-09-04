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
