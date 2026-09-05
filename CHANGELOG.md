# Changelog

All notable changes to PitchBot are documented here.

## Unreleased

### Added

- Python 3.12 project and pinned development dependency lock.
- FastAPI health endpoint.
- Default-off configuration for all external side effects.
- Ruff, mypy, pytest, pre-commit, dependency audit, and CI configuration.
- Contribution, security, branching, progress, and test-report documentation.
- Target component, sequence, deployment, and data-flow architecture.
- Compliance/privacy gates, threat model, operational rollback guidance, source register, and architecture decision records.
- Immutable domain contracts and Alembic-managed append-only journey storage.
- Durable aggregate versions, suppression history, privacy-operation audit, redacted export, confirmed anonymization/deletion, and retention controls.
- Provider-neutral speech, model, channel, scheduler, research, artifact, object-storage, and clock contracts.
- Deterministic bounded mocks with strict idempotency, fault injection, network denial, retries, timeouts, and circuit breaking.
- Same-origin browser simulator with AI disclosure, text turns, explicit action previews, deterministic replay/failures/latency, bounded history, interruption, and session cleanup.
- Bounded metadata-only `MediaRecorder`/WebSocket transport with Opus preference, backpressure, chunk limits, and capped reconnects.
- Versioned VAD/STT/TTS/model candidate and synthetic corpus registries with license/provenance gates.
- Unicode-aware WER/CER, VAD overlap, real-time factor, structured-output, duration-regression, timing, environment, and manifest validation utilities.
- Deterministic multilingual conversation safety, bounded fact/revision extraction, evidence-grounded intent classification, and synthetic adversarial/persona cases.
- Deny-by-default mock action authorization, minimized follow-ups, fake-time callback lifecycle, and six-industry structured sample-deck previews.
- Strict evaluation-run contracts, generated JSON Schema, and dependency-free local HTML reports.
- Restart-safe append-only conversation journaling with incremental transitions, typed-input retry reconciliation, rollback-safe persistence, optimistic concurrency, and fail-closed replay.
- Shared wall-clock retrieval budgets enforced cooperatively across graph projection, indexing, scoring, and ranking, with version-preserving timeouts and no partial results.
- Customer-confirmation provenance on temporal fact claims, retained across supersession and derived only from confirmed journal revisions.
- Graph retrieval evaluation now projects reviewed corpora through the production temporal builder and gates on projection fidelity.
- Paraphrase-resistant safety detection for opt-out, internal-instruction extraction, and prompt injection using bounded-window intent templates.
- Non-authoritative lead recall on simulator turns: budgeted graph-aware retrieval of the lead's own prior claims, run after the durable commit, skipped on safety signals, non-continuing dispositions, and durable replay, run off the event loop with a per-session failure budget, and rendered read-only in the browser demo.
- Streaming speech turn-taking: a `VoiceActivityDetector` contract and deterministic mock, an endpointing/barge-in state machine, and a transcription pipeline wired into the simulator audio WebSocket so a spoken utterance becomes an ordinary turn. No speech-to-text provider is selected, so utterances report `transcriber-unavailable` by default; audio is buffered only for the utterance in flight, byte-capped, and never persisted.
- Synthetic voice-activity structural benchmark: a deterministic, dependency-free audio generator that emits a reproducible WAV plus ground-truth-labeled byte frames from a seed, a hash-verified VAD corpus regenerated at run time rather than committed as binary, and a `run-speech` command that scores per-language/condition/vertical F1 through the existing detector contract and gates the corpus in CI. Its gate fails closed — it validates that the required VAD metrics are present and returns a non-zero exit code on failure, rather than inheriting the shared runners' fail-open, always-return-zero behaviour. Voice activity is the only speech dimension measurable without a model; STT and TTS remain blocked pending reviewed real audio.
- First real speech provider behind the unchanged `VoiceActivityDetector` contract: a `py-webrtcvad` adapter (`pip install "pitchbot[webrtc-vad]"`, MIT AND BSD-3-Clause, ~19 KiB, no model weights, no download, CPU-only), plus `run-speech --detector webrtc` which feeds it the corpus's real PCM instead of the byte-length proxy the mock consumes, and records the exact package version, algorithm, and license in the run's configuration. The dependency is optional and stays that way: `pitchbot` imports and all 479 pre-existing tests pass with it absent, and it is not re-exported from `pitchbot.adapters`. **No VAD provider is selected.** Measured on the synthetic corpus the detector fails the `min_f1 = 0.85` gate in every aggressiveness mode (best: mean F1 0.9036, min 0.8276) with recall 1.0000 everywhere, because the corpus's speech/non-speech distinction is an amplitude distinction — a twelve-line RMS threshold scores a perfect 1.0000 on it, and the whole deficit is WebRTC's deliberate speech-tail hangover landing on digital-zero "silence". The corpus therefore cannot rank VAD quality; see `docs/BENCHMARKS.md` for the evidence and the specific corpus changes required.
- First provider that produces speech, behind the unchanged `TextToSpeechAdapter` contract: an opt-in Piper adapter (`pip install "pitchbot[piper-tts]"`). It streams one chunk per sentence via `asyncio.to_thread` so the event loop keeps serving (measured worst stall 19 ms while synthesising 27 s of audio), always terminates a stream with exactly one `is_final` chunk — including for empty, whitespace-only, and punctuation-only text, which Piper answers with zero chunks — and offers `DETERMINISTIC_SYNTHESIS` for byte-reproducible output, which the default is not. `preload()` exists because loading a voice stalls the loop for ~2.1 s even on a worker thread (the ONNX session construction holds the GIL), so that cost belongs at startup rather than mid-call. The dependency is optional and stays that way: with `piper-tts` uninstalled the suite is 530 passed / 36 skipped with zero failures, and the module is not re-exported from `pitchbot.adapters`. **No text-to-speech provider or voice is selected**, and the adapter is not wired into the simulator's speech response path. The license review this repository required before using Piper returned a blocking finding: the runtime is **GPL-3.0-or-later** (never vendored or redistributed), and **no published Piper Hindi voice is cleared for commercial use** — `hi_IN-pratham` and `hi_IN-priyamvada` are CC BY-NC-SA 4.0 and `hi_IN-rohan`'s license document did not respond — while the commonly used `en_US-amy-low` inherits CC BY-NC-SA 4.0 from its RyanSpeech base. `PiperVoiceRegistry` is therefore deny-by-default and refuses non-commercial or unverifiable voices unless a caller explicitly opts in for local evaluation; voices are operator-supplied and never downloaded. See `docs/BENCHMARKS.md` for the full per-voice table.

- First provider that recognises speech, behind the unchanged `SpeechToTextAdapter` contract: an opt-in `faster-whisper` adapter (`pip install "pitchbot[faster-whisper]"`). Licences are permissive throughout and were chased to the upstream model — package and CTranslate2 MIT, `Systran/faster-whisper-*` weights MIT, `openai/whisper-*` Apache-2.0 — and an unreviewed model identifier is refused at construction. Weights are never downloaded: the adapter passes `local_files_only=True` unless a caller explicitly opts in, so a missing model is a clear error rather than a silent multi-hundred-megabyte fetch mid-call or in CI. It is deliberately **utterance-batch rather than chunk-streamed**, because Whisper encodes a padded 30-second window and cost is therefore ~constant per call — measured with `small` on CPU/int8, twelve times the audio cost 1.9× the time, and that 1.9× appears only where a clip crosses into a second window. Real-time factor is consequently a misleading metric here; the number that matters is **~2.1 s latency after end-of-speech**, and it is recorded as this model's floor. Non-final partials are cumulative and exactly one final chunk carries the complete transcript, which is load-bearing because `SpeechTurnPipeline` keeps only the last final. Audio at any rate other than 16 kHz is refused before the model loads rather than silently reinterpreted as pitch-shifted speech; `confidence` is `exp(avg_logprob)`, a real decoder quantity that may be thresholded; and a weakly-detected language is reported as `unknown` rather than guessed, because Whisper labels even digital silence as `en`. `small` is the default because `tiny` and `base` emit the wrong *script* for Hindi (romanised Latin and Urdu respectively), which no tuning fixes. The dependency is optional and stays that way: with `faster-whisper` uninstalled the suite is 563 passed / 31 skipped with zero failures, and the module is not re-exported from `pitchbot.adapters`. **No speech-to-text provider is selected** and no word-error-rate is claimed — the only available speech is synthesised, which cannot separate recognition quality from synthesis quality.

- Corrected the VAD corpus requirement list in `docs/BENCHMARKS.md` using measurement rather than reasoning. PR 30 listed five properties a corpus would need before it could select a VAD provider, written as hypotheses because no real speech synthesis existed in the repository at the time; PR 33 added it, so items 1-3 were prototyped and scored against an **oracle-tuned** RMS threshold — the best threshold energy detection could achieve on that exact corpus, chosen with hindsight. Item 2 was wrong as written: "non-speech at speech energy" in *isolation* — loud broadband noise, a 440 Hz tone, 50 Hz hum — is rejected no better by py-webrtcvad than by the threshold (−0.003 each), so those constructions make a corpus **worse** at discriminating, consistent with PR 30's own note that real VADs reject low-energy stationary noise rather than loud broadband signals. The requirement now specifies **adjacency**: the only construction that separated the two clearly (**+0.056**) was speech immediately abutting loud non-speech at the same level, where an energy threshold has no information at all to locate the transition. **No corpus is added and no provider is selected** — aggregated, the best detector mode reached mean F1 0.8243 against the threshold's 0.8137 while *losing* on worst case (0.7033 vs 0.7135), with three of four modes losing outright, so this is evidence about corpus design rather than a ranking.

- Measured the second local Whisper runtime and extended language coverage from two languages to six across five scripts, in `docs/BENCHMARKS.md`. **`whisper.cpp` runs locally and is not adopted**: `pywhispercpp` (MIT) has a prebuilt cp312 Windows wheel with no PyTorch dependency and works correctly, but took 23.27s against `faster-whisper`'s 13.98s over the same six clips — ~1.7× slower with essentially identical transcripts and no offsetting advantage, so it is recorded as a viable fallback rather than a replacement (caveat: ggml ran default f16 while CTranslate2 ran int8). **`small` produced the correct script in all six languages** — English, Spanish, Arabic, Russian, Hindi, Chinese — confirming the wrong-script failure mode is a property of `tiny`/`base` rather than of Whisper at this size, though quality varies enormously and Chinese is poor. **Character error rate without number normalisation is close to meaningless for this content**: the references spell numbers as words while the model writes digits, so normalising numerals takes English and Spanish from ~29% and ~26% CER to **0.0%**, and dissolves an apparent 52.4%-vs-4.8% runtime disagreement on Arabic that was purely a formatting choice — concrete justification for the existing rule that WER/CER normalisation must be versioned before any baseline is reported. Also recorded that three of four newly reviewed Piper voices report unresolved or `Unknown` licences, making the catalogue largely licence-unclear rather than merely non-commercial in Hindi.
- Speech providers are now selectable from configuration, which is what makes the Piper and faster-whisper adapters actually reachable from the running application — until now `_build_service` never passed `speech_detector` or `speech_transcriber`, so both were reachable only from tests and PitchBot could not listen even with the extras installed. `PITCHBOT_SPEECH_VAD_PROVIDER` and `PITCHBOT_SPEECH_STT_PROVIDER` default to `mock` and `none`, reproducing exactly the pre-adapter behaviour, and **naming a provider whose optional extra is absent is a startup error rather than a silent downgrade** — falling back would leave an operator believing speech works while every utterance is dropped. Provider names are validated in `Settings` so a typo fails at import, while extra-availability is checked in the factory so settings never import adapters. Model weights are loaded once during a FastAPI `lifespan` rather than during the first buyer utterance: measured end to end, a lazily-loaded transcriber made the first spoken turn take **5,384 ms**, dropping to **3,502 ms** once the 1.61 s load moved to startup — it holds the GIL, so it stalls the audio socket barge-in depends on. `/health` now reports which providers are running. With both providers configured, Piper-synthesised speech driven frame by frame through `SpeechTurnPipeline` produced a real buyer turn (*"I want to order 50 units of the blue cotton shirts."*, confidence 0.753) — the first time the speech path works with real models. No provider is selected and every default is unchanged.
- The server can now speak the reply in its own voice, over the same audio socket that carries the buyer's speech. Until now the reply was spoken by the **browser's** `speechSynthesis`, which is not so much a missing capability as the wrong one: its voices vary by browser and OS, Hindi is frequently absent, and on several platforms synthesis is performed by a **remote service** — so a product answering `audio_retained: false` on its own socket was, on those clients, sending the agent's words to a third party. `PITCHBOT_SPEECH_TTS_PROVIDER` defaults to `none`, leaving that fallback exactly as it was, and enabling `piper` maps each language to one operator-supplied voice with **no fallback**, because serving Hindi through an English voice produces confident audio in the wrong language rather than an error. Two measurements shaped the design. Piper emits **one chunk per sentence**, 80 KB to 352 KB with the largest carrying 7.99 s of audio — too large to write (it exceeds the 256 KB bound the inbound side of the same socket enforces) and impossible to abandon part-way, so barge-in would only take effect on a sentence boundary; the stream is therefore re-cut into 32 KB frames that are each a whole number of 16-bit samples, since an odd-length frame byte-shifts every later sample in the client's `Int16Array` and turns the reply into noise. And synthesis runs at **~19× realtime** once the voice is resident (1,052 ms produced 20.75 s of audio), so the whole reply is ready long before any of it plays: there is nothing to gain from pacing the send, but the 1,052 ms would have blinded the interruption detector on every turn had it run inline, so it runs as a background task with all socket writes serialised and cancellation as the abort path. Every stream is terminated, including one that synthesised to nothing, because the client hands the floor back when playback ends and a stream with no terminator would mute the buyer until `agent_floor_ms` expired. The browser plays the PCM gaplessly through WebAudio and keeps `speechSynthesis` as the fallback, including when synthesis produced nothing, so the buyer never loses the answer. Verified by closing the loop rather than counting bytes: the PCM that arrived over the WebSocket was fed back through `faster-whisper` and returned the reply verbatim modulo punctuation. Also fixed while building this: `PiperVoiceRegistry` gates on licence at `resolve` rather than at construction, so a non-commercial voice started cleanly and refused the **first buyer turn in that language** — the factory now resolves every mapped language once at build time. The dependency is optional and stays that way: with `piper-tts` uninstalled the suite is 641 passed / 27 skipped with zero failures. **No text-to-speech provider or voice is selected**, and no reviewed Piper Hindi voice permits commercial use, so a bilingual deployment still has no licensed voice for half its buyers.

- The agent now says something relevant instead of the same sentence every turn. Every ordinary turn previously returned one fixed string per language — `Thanks. What matters most next: features, budget, timeline, or the decision process?` — no matter what the buyer had said or how many times they had already answered it. A deterministic reply planner now reads the four slots the conversation has actually filled (business type, requested features, budget, timeline), acknowledges what was just learned, and asks for the most useful missing one. It needs **no model**, costs ~1 ms, works offline, and improves every deployment. Running it immediately exposed a worse loop than the one being fixed: the shipped budget extractor is a regex requiring digits, so `our budget is around two lakh rupees` fills no slot and the agent asked for the budget on every remaining turn — a slot is now abandoned after two attempts. The rendered reply is composed of fixed per-language phrases only, so no buyer text can reach the agent's own words; that is a safety property, not a style choice.
- First implementation of `ModelAdapter`, which had existed since the adapter contracts were written with only mocks behind it: an opt-in local language model run on CPU through `onnxruntime-genai` (MIT, depends only on numpy and onnxruntime — no PyTorch). It was chosen over `llama-cpp-python` for one decisive reason: PyPI hosts **only source tarballs** for llama-cpp-python at every version ever published, so installing it on Windows means a CMake/MSVC build. Output is **structurally constrained** to a registered JSON schema rather than parsed hopefully out of prose — measured, the same unguided prompt returned enum values that do not exist, so constraint removes an entire class of failure and the retry loop that would otherwise be needed. Three measurements shaped it: compiling the JSON-schema grammar costs ~1.9 s inside the generator constructor, is independent of schema size and is never cached, but `rewind_to(0)` costs ~1 ms and preserves guidance, so reusing one generator cut per-turn cost 5.3x; `enable_ff_tokens` defaults to **False**, and enabling it removed 14 generated tokens and ~540 ms with identical answers. The model feeds the same planner, so it improves *understanding* and can never change how a reply is composed or introduce a question nobody wrote. **No model is selected**: measured on an 8-core CPU, Qwen2.5-0.5B answers in 950 ms but scored 1/10, while Phi-3.5-mini scored 10/10 at ~5.2 s per turn — too slow for the spoken path, which is why this is off by default. Licence review found the **Qwen2.5 family is licence-split** (0.5B/1.5B Apache-2.0, **3B non-commercial**), that a quantised re-upload does not relicense what it converts, and that among licence-clean small models Hindi coverage is weak — the one model officially naming Hindi is licence-disqualified.

### Fixed

- The agent says something while it thinks, instead of going silent for four and a half seconds. Measured on the shipped local path with voices resident, the gap between a buyer finishing a sentence and the reply becoming audible is **4,507 ms** in English and **4,553 ms** in Hindi — and transcription is essentially all of it (3,982 ms and 4,453 ms respectively), while planning costs 1–25 ms and synthesising the reply costs 92–501 ms. That measurement dictates where this hooks in: because the wait *is* transcription, a filler can only cover it if it starts when the **endpointer closes the utterance**, before anyone knows what was said, so `SpeechTurnPipeline` now calls `on_thinking` immediately before awaiting the transcriber. One short phrase is said after 700 ms and at most one more after 2,500 ms, taking the longest single stretch of dead air from **4,156 ms to 1,428 ms** in English and **4,304 ms to 1,103 ms** in Hindi — roughly an ordinary conversational pause. Being chosen before the transcript exists is also what constrains *what* may be said: **a filler may assert receipt, never assent.** "Hmm", "got it", `अच्छा।` and `అలాగా.` say only *I heard you*; "ok", "yes" and "theek hai" say *I agree*, and if the sentence still being transcribed was *"so you'll do it for fifty thousand?"*, the agent has committed out loud to a number nobody quoted — so the most natural-sounding candidates are deliberately absent and a test enforces it in every language. Only the microphone is muted for a filler, never the turn-taking machine, because this runs while the pipeline is awaiting inside `push` and `agent_started_speaking` would move that machine underneath the utterance being transcribed; the filler is shielded from cancellation so a reply arriving mid-word does not clip a syllable. `--no-backchannel` restores silence. Also measured and recorded: **Telugu transcription took 37.7 s for a 4.1 s clip** — `small` loops at that sentence length, which no filler policy can hide and which is the worst latency figure this project has measured.
- Hinglish is answered in Hinglish. `LanguageCode.MIXED` used to redirect to the Hindi phrase table, so a buyer typing *"aapka budget kitna hai"* was answered in formal Devanagari. That is not a comprehension failure — a Hinglish writer reads Hindi fluently — it is a **register** failure, and in an Indian B2B conversation switching someone into literary Hindi reads as correcting them rather than answering them. `MIXED` is now a first-class language with its own phrase table, inside `supported_languages()` and held to the same import-time completeness checks as the others, so it cannot be half-added. Which words stay English is the point rather than a shortcut: `budget`, `website`, `catalogue`, `payment`, `demo` and `proposal` are the words the buyer themself used, and translating them to `बजट`/`प्रस्ताव` would be more internally consistent and less like anything a person says. Adding it immediately exposed a live gap of exactly the shape Telugu shipped with: safety detection already handled romanised Hinglish but **stance detection did not**, so a Hinglish buyer could refuse contact yet could not object to a price, compare a vendor, stall or agree — `INTENT_PHRASES` now carries romanised entries. Running the shipped example then exposed a second: the romanised marker list was too thin (48 tokens) and the switch landed three turns late, so it was expanded to 89 and the switch now lands on the second Hinglish turn as designed.

- The conversation follows a buyer who changes language mid-call, whether or not they say so. Until now `process_turn(language=)` was set once by the caller and never revisited, so a buyer who opened in English and moved to Hindi — ordinary on an Indian B2B call — was answered in English for the rest of the conversation, in an English voice, through a transcriber still forced to English. That last part is why this was a correctness bug and not a politeness one: **a Whisper decoder forced to the wrong language does not fail, it fabricates**. Measured, Hindi speech forced to `en` returned *"Our shop and our budget is Rs. 50,000."* — fluent English the buyer never said, reported as `en` at probability **1.00**, in Latin script, with every signal of the switch erased and the budget extractor ready to take a number out of a sentence nobody uttered. Auto-detect was then measured to cost **nothing**: identical character error rate to a correct forced hint on English (28.6%) and Hindi (18.4%), better on Telugu (110% against 247%), and the right label at 0.96–1.00 on all three. So the transcriber now *expects* a language rather than forcing one, and the expectation is used only for Telugu script repair. Detection reads three signals in priority order — an explicit request in any script, script evidence, then a closed list of romanised Indic markers — with the transcriber's own label as a last resort used only when the text says nothing, because a transcriber given a language to expect reports it back and is therefore least reliable on precisely the turn a buyer switches. **The implicit path is the primary one**, since almost nobody announces a change: two consecutive turns in another language move the conversation, in either direction, and one turn deliberately does not — a single borrowed word must not move the reply language, the voice and the transcriber at once, and *"Namaste, we run a retail shop"* is an English sentence. A request is obeyed immediately instead, because someone who asks and is answered twice more in the old language has been ignored. The switch is acknowledged first, in the language switched *to*. The language is resolved **before** the safety branches, so an opt-out spoken in a newly adopted language is answered in that language — the turn that ends the relationship is the worst one to get wrong. Hysteresis lives in conversation state and is carried through both the checkpoint and the journal event (both now schema `"2"`, additive with defaults), so a restart mid-switch does not make the buyer start convincing it again. `--fixed-language` and `ConversationEngine(detect_language_switch=False)` restore the old behaviour for a caller that owns the language itself. Three latent defects were found and fixed on the way: `SpeechTurnPipeline._language` was assigned and never read, so the parameter promised to steer transcription and did nothing; `UtteranceResult.language` was computed for every utterance and discarded by the CLI, leaving the transcriber's evidence unreachable on the one path where speech is the only input; and the Telugu request table used citation forms, which Telugu's agglutinated case endings do not contain, so `ఇంగ్లీషులో` ("in English") matched nothing and every Telugu-language request for English was silently missed — found by running the shipped example script, not by a test written from the same assumption as the code.

- `run-speech` real-time factor was unmeasurable on Windows. Cases were timed with `time.monotonic_ns`, whose resolution there is 15.625 ms, while a per-case VAD pass costs well under a millisecond — so every `speech.real_time_factor` quantised to zero and `--max-rtf`, the only gate that can reject a candidate too heavy for the target box, silently had nothing to check. The runner now times with `time.perf_counter_ns` (monotonic, ~200 ns observed). The emitted artifact format is unchanged.
- `run-speech` recorded the wrong detector. The hashed `configuration_sha256` named `mock-voice-activity-detector` unconditionally, so a run using an injected detector produced an artifact whose configuration digest claimed the placeholder. The detector's identity, package version, license, and settings are now part of the hashed configuration and are printed by the CLI, so two detectors on one corpus can never produce indistinguishable artifacts.

- Corrected user-facing status documentation that had drifted after PRs 21-29: rewrote the `README.md` "Current status" to match the code at `8f72520`, split the now-false "BM25 and temporal knowledge views are not yet connected to the simulator or speech response path" claim into its still-true (speech response path) and now-false (simulator advisory recall) halves, refreshed the deferred-capability list, marked `docs/TEST_REPORTS.md` as maintained through PR 12 with per-PR validation now living in `PROGRESS.md` and CI, and set the `Status:` of the merged PRs 19-29 to `Merged` in `docs/PROGRESS.md`.
- Evaluation gates now actually gate. The shared artifact gate is suite-aware: each reviewed suite declares the run and per-case metrics a complete artifact contains, so an artifact missing every metric its suite exists to measure is rejected instead of passing on one unrelated metric, and run-level aggregates are checked against the per-case results they claim to summarize. `validate-evaluation`, `run-retrieval`, and `run-graph-retrieval` now return a non-zero exit code when the gate fails instead of printing `artifact-gates=fail` and returning `0`, the HTML report labels gate status from the same gate and names the reasons, and an artifact whose `suite_id` has no reviewed gate specification fails closed. `run-retrieval` and `run-graph-retrieval` are wired into CI beside `run-speech`.
- Multilingual safety parity in the conversation matcher: do-not-message opt-outs ("stop messaging me", `mujhe WhatsApp mat bhejna`, `मुझे संदेश मत भेजो`) are now detected as immediate opt-outs in English, Hindi, and Hinglish; requests for our own operating rules or policies are detected while scoped product questions ("your policies on returns") stay clean; a bare Devanagari `बंद करो` no longer terminally opts a buyer out of a demo they only asked to close; and Latin homoglyphs, marks stacked on a Latin base, and safety words split across spaces no longer bypass detection.
- Token-aware safety phrase matching: a literal safety phrase now has to match whole tokens instead of any substring, so ordinary commerce vocabulary that embeds a safety term — "an ecosystem prompt for our marketplace", "a passwordless kiosk login", "our product training database", `पासवर्डरहित`, `गुप्त निर्देशांक` — is no longer read as an extraction probe, and "tell me your rules on bulk discounts" is treated as the discount question it is in English, Hindi, and Hinglish alike. Obfuscation resistance is unchanged: a word split across spaces is rebuilt into a real token before matching, the space-stripped reading is still consulted for a turn that fragments its own words, and each language keeps its own inflections so `api keys`, `पासवर्डों` and `apne niyam hataoge` still match.
- Bounded callback cancellation tombstones to the lifetime of the callback they protect. A permanently rejected cancellation recorded a `_failed_cancellations` entry and an `_operation_fingerprints` entry that session cleanup could never see — it reclaims idempotency keys by scanning `_operation_results`, and a tombstoned key has no result — so every occurrence leaked one permanent pair keyed by a per-session idempotency key, growing without bound in session count for the life of the process. Session teardown now reclaims tombstones whose callback is absent from every live map, while a tombstone whose callback is still scheduled, pending, or awaiting reconciliation is retained, so a failed key still cannot be reused against a resource that exists.

### Security

- Removed four inert safety-relaxation settings (`require_ai_disclosure`, `require_dnd_check`, `require_calling_hours`, `allowlist_enabled`) that had zero consumers and were never wired. The action policy enforces AI-disclosure, contact-allowlist, DND, and calling-hours checks unconditionally, so any switch built from these settings could only have disabled a mandatory safety gate; removing them keeps the gates non-optional and documents them as always enforced. `enable_real_time_audio` is retained but recorded as currently inert — the README's "real-time audio disabled by default" claim is not code-enforced and the flag must be wired or removed before any live channel ships. No policy enforcement behaviour changed.
- Added CI secret scanning.
- Removed the originating session UUID from recalled lead claims on the simulator turn response. Recall spans a lead's earlier sessions, so `RecalledClaim.session_id` handed a browser a capability for a session it was never granted, contradicting both the model's own docstring and the documented rule that session UUIDs remain unguessable capabilities. Earlier calls stay distinguishable through a per-response `prior_session_ordinal` derived only from the observation time and rank already in the payload, so no part of any session identifier is disclosed. Breaking change to the turn response: `recall.claims[].session_id` is removed.
- Excluded environment files, secrets, runtime data, logs, and artifacts from version control.
- Hardened mock action retries, callback cancellation races, concurrent capacity admission, session cleanup, and paraphrased internal-instruction extraction attempts.
- Reconciled canceled callback scheduling attempts without false approval or duplicate provider actions.
- Kept the speech and durable turn paths live under fault: a voice-activity failure now reaches the turn-taking machine as silence instead of pinning an open utterance in `LISTENING`, audio abandoned when the agent yields the floor is released instead of being prepended to the next utterance, the durable journal replay and commit run off the event loop that serves the audio socket, and a session whose cleanup fails during invalidation stays addressable so a delete can reclaim its callback and deck capacity.
- Bounded the work one lead's history can cost a recall: the projection replay is linear in the lead's events instead of quadratic in sessions times events, `max_history_events_per_lead` now reaches the journal and refuses an over-long history before a single row is read rather than projecting a truncated view, and a wall-clock budget is checked while events are decoded and replayed so the worker stops itself instead of being abandoned mid-read.
- Added Telugu as a third supported language, chosen by measurement. Two Piper `te_IN` voices are CC-BY-4.0, making Telugu the only Indic language this project can currently ship a voice for -- every published Hindi voice is non-commercial or unresolved. Whisper transcribes Telugu speech into Devanagari 100% of the time at both `small` and `medium` while naming the language correctly, so `pitchbot.speech.scripts` transliterates declared-Telugu transcripts back, taking character error rate from 100% to 41%. The `initial_prompt` script anchor that appears to fix this is not used: it corrects the alphabet and destroys the words. See `docs/BENCHMARKS.md`.
- Added `pitchbot-talk`, an interactive terminal conversation that runs the real engine and prints why each reply was chosen -- known slots, missing slots, phase, lead temperature and turn latency. Works with no optional extra installed; `--speak` adds Piper synthesis played through the operating system's own audio player, and `--understand` adds the local model. Documented end to end in `docs/TRY_IT_LOCALLY.md` with runnable scripts under `examples/`.
- Fixed a Telugu opt-out that was never detected: a buyer asking not to be contacted was answered with the next qualifying question, because the safety vocabulary had English and Hindi entries only. `tests/test_language_coverage.py` now drives opt-out, abuse, disclosure, safety-reply and phrase-completeness assertions from `supported_languages()`, so a language added without its refusals fails at the point it is added.
- Fixed business-vocabulary matching reading `a booking form` as the *books* business type and `toyota` as *toys*. Matching is now word-bounded with a restricted suffix set that keeps `payments` matching `payment` and `किताबों` matching `किताब` while refusing derivational endings; the broader `_INFLECTIONAL_SUFFIXES` remains correct for safety detection, where over-matching is the safe bias.
- Restructured the reply phrase tables into one `LanguagePhrases` block per language instead of four parallel maps, so half-adding a language is unwriteable rather than merely untested.
- Added a complete local **voice loop**: `pitchbot-talk --listen` holds the whole conversation by microphone in English, Hindi and Telugu, with nothing leaving the machine. The detector, endpointer, transcriber and synthesiser already existed and had never been connected, because there was no capture source. Frames are produced at exactly the 30 ms of 16 kHz mono PCM the WebRTC detector accepts — measured, PortAudio opens that rate directly, so no resampling or repacking code exists. The device is opened once (opening costs ~844 ms, longer than many utterances) and gated with pause/resume. Turn-taking is half duplex because there is no acoustic echo cancellation; the capture queue is bounded and discards the oldest frame under back-pressure, so a stall can neither retain call audio nor resume on speech that has already ended. New optional `microphone` extra: `sounddevice`, MIT, bundling PortAudio, also MIT.
- Added selling to the reply planner. It now answers a price objection, a competitor comparison and a stall each in their own words, states what matters for the buyer's specific vertical at the moment it learns it, and closes on agreement rather than asking another question. A stated commitment outranks a concern raised in the same breath, and an objection is answered *in addition to* continuing rather than instead of it.
- Fixed `Intent` being dead in two independent places: it was computed by the planner and handed to a renderer that never read it, **and** it was produced only by the optional language model, so the default configuration — the one the entire test suite runs in — could not observe that a buyer had objected or agreed at all. Stance detection now lives in the rules and needs no dependency; a model still wins when installed. Measured 15/15 across four stances plus a no-stance control in three languages.
- Fixed the sales vocabulary existing in three unlinked copies (`conversation/rules.py`, `actions/policy.py`, `actions/decks.py`), which meant a vertical added to the extractor produced facts the action policy silently discarded and the deck builder silently dropped, with every test passing because each copy was internally consistent. It is now defined once in `pitchbot.domain.catalog`, and the per-language phrase tables fail at import if a vertical cannot be pitched or an objection cannot be answered.
- Fixed a hedge word breaking budget extraction: `Our budget is around 150000 rupees` filled no slot because the pattern required digits immediately after the cue, so the buyer answered, the answer was discarded, the agent asked again, hit `MAX_ASKS_PER_SLOT` and closed without a budget. Hedges are a closed list in three languages rather than a permissive gap, which would have read `budget is not decided, we sold 500 units` as a budget of 500.
- Fixed agreement producing the same sentence twice: "Okay, let's start." returned the identical closing question the buyer had just answered. A `confirm` phrase now commits to the next step.
- Added `examples/sales-{en,hi,te}.txt`, replayable conversations in which a buyer pushes back on price, shops around, hesitates and then agrees.

### Added (PR 43)

- Two-lane deliberation: a preemptible background model that plans the buyer''s site while
  the turn path is idle, and yields to it in 0.1 ms.
- `Briefing` shared state with single-writer-per-field ownership, version-stamped
  conclusions, and refusal of overtaken results.
- Website outline and deck-mock rendering from a plan, labelled a draft in the buyer''s
  language.
- Per-schema token budgets for constrained decoding.

### Fixed (PR 43)

- A configured language model made **every** reply answer a stall objection, including at
  the moment a buyer agreed to buy.
- A model could fill a qualification slot from a turn that contained no information about
  it, permanently retiring that question.
- The model was consulted for Telugu, where it scores at or below guessing.
- Site plans were truncated by a token cap chosen for a much shorter answer.
- `SimulatorService` drives the slow lane after each turn, refuses to share one model
  adapter between the lanes, and awaits any running deliberation before closing a session.
- `site_outline` and `deck_preview_slides` on the service, so a plan is reachable.

### Fixed (PR 43, second pass)

- Closing a session leaked its briefing: `close_session` dropped conversation state but not
  the observations beside it, retaining every finished conversation for the process
  lifetime.

### Added (PR 44)

- API key authentication on every simulator endpoint and the audio socket, matched in
  constant time and registered as a router-level dependency so an endpoint added later is
  closed by default. Browsers authenticate the WebSocket with a subprotocol, because a
  browser cannot set a header on one and a query parameter would be written to every access
  log the connection passes through.
- Per-credential token-bucket rate limiting. A turn costs seconds of CPU, so an anonymous
  caller in a loop did not degrade the service, it stopped it.
- Early language detection: the language is identified from a 2.0 s prefix while the buyer
  is still speaking, so transcription no longer pays for its own detection pass. Measured
  end to end on the shipped path, English fell from 3,983 ms to 2,407 ms and Hindi from
  4,844 ms to 3,389 ms, with identical transcripts.
- `authentication_enforced` on `/health`, so a server reporting "ok" while wide open is
  visible rather than assumed.

### Changed (PR 44)

- `app_env` other than `local` now **refuses to start** without a credential. A warning
  would have scrolled past once at startup and never been seen again.

### Deferred (PR 44)

- Waiting briefly for a detection that is nearly finished at the endpoint. It is currently
  abandoned outright, which wastes an almost-complete pass, but any wait risks adding to the
  gap it exists to shrink and needs its own measurement.
- Cancelling a detection stops the coroutine, not the worker thread beneath it: Whisper
  offers no mid-inference stop, so one pass finishes and is discarded.
- Telugu is untouched. Transcription there is 37,692 ms; 1.6 s is 4% of it.
- Rate limiting is per process, so N workers means N times the configured budget.

### Added (PR 45)

- Structured JSON logging with session and turn correlation ids carried in `contextvars`,
  so a log line is attributable to one conversation. Nineteen log statements previously
  carried no session at all.
- **Redaction by field name in the formatter**, not by convention: `transcript`, `reply`,
  `text`, `api_key` and similar are replaced with `[redacted]` while the key stays visible,
  so a withheld value is distinguishable from an absent one. This service promises
  `audio_retained: false`; leaking the same text into stdout through a convenience field
  would have broken that promise somewhere nobody looks.
- A dependency-free metrics registry with **bounded label cardinality** - labels come from
  closed sets and a ceiling refuses new series rather than growing - exposed as Prometheus
  text at an **authenticated** `/metrics`.
- Per-stage turn timings (`detect_language`, `transcribe`, `plan`, `synthesize`, `total`),
  because a single latency number hides the only term that has ever mattered.
- Uvicorn's own loggers are routed through the same formatter at startup. Without this the
  process emitted two formats at once, and the unstructured half was every request line.
- `language-unsupported` as an utterance outcome, and `UnsupportedLanguageError`, which is
  deliberately **not** an `AdapterError` because nothing failed.

### Changed (PR 45)

- An utterance whose language is confidently identified as one the transcriber cannot serve
  is declined in ~1.7 s instead of transcribed over 37,533 ms into text nobody should act on.
  Telugu is the measured case and the default; set
  `PITCHBOT_SPEECH_STT_UNSUPPORTED_LANGUAGES=` to restore the previous behaviour. Telugu
  **text** turns are unaffected.

### Fixed (PR 45)

- `/health` reported whether authentication was enforced from a name bound at import, so the
  field meant to make an open server visible was a snapshot of startup that could be wrong
  rather than visibly stale. Found by a test, not by review.

### Deferred (PR 45)

- Access-log lines carry no correlation id: uvicorn logs them after the request context has
  exited, so correlating them needs middleware rather than a `contextvars` block.
- `synthesize` is defined as a stage but not yet recorded; reply audio is produced by a
  background task on the socket path.
- Metrics are per process and in memory. Two workers report two independent registries, and a
  restart loses history - fine for a scrape target, not a substitute for a time-series store.
- Telugu below the confidence floor still costs 37.7 s, because refusing on an uncertain guess
  would silence a buyer the model might have understood.

### Added (PR 46)

- Per-callback locking in `CallbackService`. A single service-wide lock was held across every
  adapter call, and those are network calls, so two sessions scheduling two *different*
  callbacks waited for each other. Measured with a 200 ms adapter, ten concurrent sessions
  took 2,057 ms rather than the ~205 ms the work needs (**10.0x**), and an unrelated
  `schedule` queued **2,241 ms** behind a ten-callback dispatch batch. Both are now flat at
  one adapter call. Serialising two operations on the same callback is a real requirement and
  is kept; serialising operations on different callbacks never was.
- `TurnStage.DETECT_LANGUAGE` and `TurnStage.SYNTHESIZE` are recorded. Both were declared and
  never called, which left PR 44's early-detection work invisible in production and the
  reply-audio path - the only thing standing between the buyer and hearing a voice -
  unmeasured. `UtteranceResult.detect_language_ms` reports the detection duration as data and
  is `None` when no hint landed, so an abandoned detection cannot look like time well spent.
- `real_time_audio_enabled` in `GET /health`, so which way a deployment is configured is
  answerable without reading its environment.

### Changed (PR 46)

- `PITCHBOT_ENABLE_REAL_TIME_AUDIO` now gates the simulator's audio WebSocket. It was inert:
  the flag existed, `README.md` and `.env.example` both promised "real-time audio disabled by
  default", and the socket was mounted and reachable regardless. It is deny-by-default like
  every other speech capability. **The browser demo must now set it**; the `pitchbot-talk`
  voice loop captures the microphone directly and is unaffected.
- `dispatch_due` re-reads each record under that callback's lock and refuses to dispatch
  anything no longer `SCHEDULED`. A batch is no longer a claim over callbacks it has not
  reached, so a cancel may land while an earlier callback in the batch is still dialing.

### Fixed (PR 46)

- A cancel landing mid-batch could have been silently undone by the batch's stale snapshot,
  placing a call the buyer asked not to receive. This race only became reachable once
  callbacks stopped sharing one lock, and it is closed by the re-read above.

### Deferred (PR 46)

- A dispatch batch still dials sequentially (2,054 ms for ten 200 ms dials). Parallelising it
  is a question about what a telephony provider will accept, not a locking one, and nothing
  here measured that.
- Transcription is **91% of the buyer's wait** (2,417 ms of 2,646 ms in English, measured end
  to end to the first audio frame). Unchanged by this PR and still the dominant term.
- Access-log correlation still needs middleware: uvicorn logs after the request context exits.
- Hindi numerals transcribe poorly enough that the budget is not extracted where the English
  equivalent is - an STT-quality gap, not a latency one.
- Metrics remain per process and in memory.

### Found by the new metric (PR 46)

- **Early language detection does not land on short utterances.** A live turn produced four
  utterances from ~16 s of speech, none long enough for detection (2.0 s of buffered audio
  plus a flat ~1.6 s pass) to finish before the endpoint, so every one was started and
  abandoned. On a single long utterance it lands and works exactly as PR 44 measured
  (`detect_language_ms` 1,644 ms; `transcribe_ms` 4,337 -> 2,266 ms with early detection on).
  The wasted work is bounded at one pass per utterance. Whether to lower
  `early_detection_seconds`, or skip detection for utterances unlikely to reach it, is now
  answerable from traffic rather than guesswork - and is deliberately not changed here.

### Fixed (PR 47)

- **The endpointer counted a 30 ms microphone frame as 250 ms.**
  `SpeechTurnPipeline.frame_duration_ms` defaults to 250 because the browser client calls
  `MediaRecorder.start(250)`, and `SimulatorService.create_speech_pipeline` never passed a
  value, so every threshold the endpointer owns was scaled by **8.3x** on the socket path:
  `max_utterance_ms` fired after 2.4 s of real speech rather than 20 s, `end_silence_ms` after
  90 ms rather than 700, and `barge_in_speech_ms` after 60 ms rather than 300.

  A buyer speaking one continuous sentence was cut off, the agent took the floor, the buyer's
  continued speech was classified as barge-in, and the cycle repeated. Measured live on 8.4 s
  of one English sentence: **four utterances and four replies became one**, the transcript went
  from *"We run a retail shop and hide our butt."* plus three fragments to the whole sentence,
  transcription fell from **16,951 ms to 1,866 ms** (9.1x), the budget was extracted where it
  previously was not, and `detect_language` recorded for the first time.

  Frame duration is now derived from the frame: mono 16-bit PCM carries its own duration, and
  it is trusted only when it lands on a length WebRTC's detector accepts (10, 20 or 30 ms).
  Encoded `MediaRecorder` frames and benchmark length-proxies keep the configured value,
  because their byte count says nothing about how long they last.

  `dropped_frames` was 0 throughout and nothing was logged - the only symptom was a
  conversation that behaved badly. The suite passed because every existing turn-taking test
  uses 1,024-byte frames, which is 32 ms of PCM and therefore not measurable, so all of them
  were unintentionally testing the fallback path.

### Retracted (PR 47)

- PR 46 reported that **early language detection does not land on short utterances**, and that
  short utterances are what a real endpointer produces. Both halves were artefacts of the bug
  above: the short utterances were manufactured by a 2.4 s cutoff. With frame duration
  measured, the same speech is one utterance and detection lands on the first turn. No tuning
  of `early_detection_seconds` is called for.

### Measured and not taken (PR 47)

- **Whisper's remaining decoding knobs are already exhausted.** `temperature`,
  `without_timestamps` and `condition_on_previous_text` all move latency by less than 3%; the
  temperature fallback ladder never fires. `without_timestamps` additionally *changed* the
  Hindi transcript, and a latency win that changes what the buyer said is not a win.
- **Transcription cost is nearly flat in utterance length** - 1,833 ms for 3.2 s of audio
  against 2,245 ms for 16.1 s, because Whisper pads every clip to a 30 s window. The number of
  utterances drives the bill, not their length.
- **`base` is 2.9x faster than `small` on English** (681 ms vs 1,979 ms) but 3.5 points worse
  on CER, and returns Arabic script for Hindi. A per-language model choice stays open for
  English; it is not taken on one sentence of evidence.

### Deferred (PR 47)

- The browser sends WebM/Opus, which WebRTC's detector cannot process at all - it accepts only
  10/20/30 ms PCM. The browser path therefore still depends on a detector that can, and that
  mismatch is untouched here.
- Transcription remains the dominant term in a turn, now that it is measured once per sentence
  rather than four times.

### Fixed (PR 48)

- **The browser was never heard.** `apps/web/audio-transport.js` recorded with
  `MediaRecorder` (WebM/Opus) while `WebRtcVoiceActivityDetector` accepts only 320/640/960-byte
  mono 16-bit PCM, and nothing in the server decodes Opus. Every frame was rejected, counted as
  a detector failure and treated as silence: **120 frames in, 0 utterances out**, turn-taking
  never leaving `idle`. `docs/TRY_IT_LOCALLY.md` documented exactly that combination.

  The browser now captures raw PCM through an `AudioWorklet` (`apps/web/pcm-worklet.js`),
  regrouping the audio thread's 128-sample blocks into 480-sample (30 ms) int16 frames, with
  the `AudioContext` asked for 16 kHz so the browser resamples. Verified three ways: the
  worklet's arithmetic in Node (278/278 frames exactly 960 bytes, **max per-sample delta 0**),
  those exact bytes through the real server pipeline (**278/278 accepted, 0 dropped, 1
  utterance, full transcript**), and a real headless Chrome fed a WAV as its microphone (2,655
  frames, every one 960 bytes, real signal, 0 dropped).

  A browser that will not resample to 16 kHz now fails loudly instead of sending frames whose
  byte count misrepresents their duration - the mistake PR 47 fixed on the server side.

- **A microphone could evict the conversation from its own timeline.** Each audio frame
  appended an `AUDIO_METADATA` event, and `events` is a `deque(maxlen=200)` shared with turns
  and outcomes. At the browser's new 33 frames a second the timeline was entirely audio within
  six seconds; at the old 4 a second it took fifty. The audio-chunk cap also closed the socket
  with `1013` after 60 s, and the client reconnected into the same wall - 83 sockets in one run.

  Frames are still counted individually for the capacity guard, but a timeline entry is
  appended for the first frame and then every 500 (~15 s), carrying cumulative chunk and byte
  counts. `acknowledged_sequence` on the audio socket is now the frame's own sequence rather
  than the sequence of an event that no longer exists per frame.

### Deferred (PR 48)

- The live-browser check does not reach a transcript: Chrome's fake capture device loops the
  WAV, so no trailing silence ever arrives to endpoint on. The end-to-end hop is covered by
  feeding the worklet's own bytes to the real pipeline instead.
- The agent's voice is `en_US-joe-medium`, chosen for being CC0 rather than for how it sounds.
  It is male and flat. `en_GB-alba-medium` (female, CC BY 4.0) and `te_IN-padmavathi-medium`
  (female, CC BY 4.0) are licence-clean alternatives; every reviewed `hi_IN` female voice is
  non-commercial, so Hindi remains an owner decision.
