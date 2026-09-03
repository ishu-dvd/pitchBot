# Architecture

## Status

This document describes the target architecture. The implementation includes the audited FastAPI foundation, default-off configuration, typed domain contracts, Alembic-managed local persistence, provider contracts, deterministic mocks, resilience primitives, a browser simulator with opt-in durable conversation turns, speech/runtime benchmark manifests and metrics, privacy-minimized evaluation snapshots and static reports, deterministic conversation/classification, privacy-validated BM25 fact retrieval, a conservative temporal lead knowledge view, and guarded in-memory follow-up, callback, and deck previews.

It also includes a **complete local voice loop** — microphone capture, voice-activity detection, endpointing, transcription, reply planning and speech synthesis — and a **reply planner that sells** rather than only qualifying: it answers objections, pitches the buyer's vertical, and closes on agreement, in English, Hindi and Telugu, with no optional package installed.

No production model is selected. Components marked as planned must not be represented as working capabilities.

## Principles

- Local-first and zero-cost for development and evaluation.
- Provider-neutral interfaces for speech, models, channels, storage, scheduling, artifacts, and clocks.
- Deterministic policy code authorizes every external action.
- Models propose structured outputs; they never directly execute side effects.
- Append-only lead journeys preserve evidence and requirement revisions.
- English, Hindi, Telugu, and code-switching are first-class evaluation dimensions.
- External effects fail closed and remain disabled by default.
- **A new language or vertical is a data change.** Vocabulary lives once, in `pitchbot.domain.catalog`; extraction, action allowlisting and reply planning all read it.
- **Buyer text never reaches a reply.** Replies are composed from fixed per-language phrases indexed by enum members and catalogue keys, so no turn can be reflected back into the agent's own words.

## Component view

```mermaid
flowchart LR
    Buyer[Buyer or test participant]
    Operator[Operator]
    UI[Browser simulator / data-call UI]
    CLI[pitchbot-talk terminal]
    Mic[Microphone capture]
    API[FastAPI control plane]
    Conversation[Conversation state machine]
    Planner[Reply planner - sales moves]
    Catalog[(Sales catalogue - verticals, features, stances)]
    Policy[Policy and compliance engine]
    Extractor[Fact, stance and evidence extraction]
    Classifier[Hot / Warm / Cold / Review]
    Store[(Lead journey store)]
    Scheduler[Persisted scheduler]
    Actions[Guarded action dispatcher]
    Speech[STT / TTS / VAD adapters]
    Model[Local model adapter]
    Mock[Mock channels]
    Live[Official live adapters - disabled]
    Evals[Replay and evaluation harness]

    Buyer <--> UI
    Buyer <--> CLI
    Operator --> UI
    CLI <--> Mic
    Mic --> Speech
    UI <--> API
    CLI --> Conversation
    API --> Conversation
    Conversation --> Extractor
    Extractor --> Classifier
    Extractor --> Catalog
    Conversation --> Planner
    Planner --> Catalog
    Conversation <--> Speech
    Conversation <--> Model
    Conversation --> Policy
    Classifier --> Policy
    Policy --> Catalog
    Policy --> Actions
    Conversation --> Store
    Classifier --> Store
    Actions --> Store
    Scheduler --> Policy
    Actions --> Mock
    Actions -. gated .-> Live
    Evals --> API
    Evals --> Store
```

The catalogue edge is the load-bearing one. Extraction, action allowlisting and reply
planning previously each held their own copy of the vocabulary, so a vertical added to one
was silently discarded by the others.

## Simulated-call sequence

```mermaid
sequenceDiagram
    actor Buyer
    participant UI as Browser simulator
    participant API as FastAPI
    participant Policy
    participant Conversation
    participant Store
    participant Mock as Mock channel

    Buyer->>UI: Speak or type
    UI->>API: Authenticated turn event
    API->>Policy: Validate session and limits
    Policy-->>API: Allow or reject
    API->>Store: Preflight operation and durable version
    API->>Conversation: Process turn
    Conversation-->>API: Reply and typed action proposal
    API->>Policy: Authorize proposal
    alt Approved mock action
        Policy-->>API: Approved for preview
        API->>Mock: Execute idempotently
        Mock-->>API: Return idempotent preview
    else Blocked or review required
        Policy-->>API: Block reason
    end
    API->>Store: Commit minimized accepted-turn transition
    API-->>UI: Reply action status and redacted events
    UI-->>Buyer: Render or play response
```

## Spoken turn: the local voice loop

Everything below runs on one machine with no network call. The microphone is the piece that
was missing until recently: the detector, endpointer and transcriber all existed and had
only ever been fed recorded audio.

```mermaid
sequenceDiagram
    actor Buyer
    participant Mic as Microphone (sounddevice)
    participant VAD as WebRTC VAD
    participant Turn as Turn-taking machine
    participant STT as faster-whisper
    participant Engine as Conversation engine
    participant Planner as Reply planner
    participant TTS as Piper
    participant Speaker

    Buyer->>Mic: Speaks
    loop every 30 ms
        Mic->>VAD: One 960-byte frame at 16 kHz
        VAD->>Turn: speech / not speech
    end
    Turn-->>STT: Utterance closed on trailing silence
    STT-->>Engine: Transcript, language, confidence
    Engine->>Engine: Safety check, then extract facts and stance
    Engine->>Planner: Known slots, stance, vertical
    Planner-->>Engine: Answer objection, pitch, ask or close
    Engine-->>TTS: Reply composed from fixed phrases
    Note over Mic,TTS: Capture is paused for the whole reply
    TTS-->>Speaker: Audio
    Speaker-->>Buyer: Hears the reply
```

Three properties of this loop are deliberate and constrain the code:

**Frames are produced at exactly the size the detector accepts.** WebRTC's VAD takes only
10, 20 or 30 ms of mono 16-bit PCM at 8/16/32/48 kHz. Capture is opened at 16 kHz with a
block size of one frame, so no resampling or repacking code exists to get wrong. The
pipeline must be constructed with a matching `frame_duration_ms`; a mismatch does not fail
loudly, it silently misreports every duration the endpointer reasons about.

**Turn-taking is half duplex.** There is no acoustic echo cancellation, so a microphone left
open while the agent speaks hears the agent and endpoints on it. Capture is paused for the
duration of each reply. The cost is that a buyer cannot interrupt — the pipeline supports
barge-in, but enabling it here would fire on our own voice.

**Audio is never retained.** Frames are handed to the pipeline and forgotten, the capture
queue is bounded, and it discards the *oldest* frame under back-pressure so that a stall
cannot accumulate call audio and cannot resume on speech that has already ended.

## Reply planning: qualifying versus selling

The planner chooses a **sales move**, not just a slot. Before this it had exactly one move —
ask for the next missing slot — which is a questionnaire: a buyer who objected to a price
received the same next question as one who had said nothing.

| Move | When | Why it exists |
| --- | --- | --- |
| `ANSWER_OBJECTION` | The buyer pushed back on price, is comparing, or is stalling | Not answering reads as nothing the buyer says changes anything |
| `PITCH` | The vertical has just become known | Says something specific about their business at the only moment it is new |
| `ASK` | A slot is missing and unasked | Ordinary qualification |
| `CLOSE` | The buyer agreed, or nothing is left to ask | A buyer who has said yes must not be asked another question |

Two rules matter more than the table:

- **An objection is answered *and* the conversation still moves.** Answering then falling
  silent trades one failure for another. The stance sets emphasis, not whether the rest of
  the turn happens.
- **A stated commitment outranks a concern in the same breath.** "It is expensive but let us
  start" closes, because making a decided buyer wait is the expensive mistake.

The stance is read by the rules, so this works with no model installed. A local model, when
present, supplies a stance it reads from a sentence matching no phrase, and wins.

## Deployment profiles

```mermaid
flowchart TB
    subgraph LocalFull[local-full: authoritative development profile]
        Browser[Browser]
        LocalAPI[FastAPI]
        Worker[Scheduler / worker]
        LocalModels[Local speech and model runtimes]
        SQLite[(SQLite)]
        Mocks[Mock channel adapters]
        Browser <--> LocalAPI
        LocalAPI <--> LocalModels
        LocalAPI --> SQLite
        Worker --> SQLite
        Worker --> Mocks
    end

    subgraph HostedDemo[hosted-demo: optional constrained profile]
        Static[Static UI and docs]
        DemoAPI[Sleeping/free CPU API]
        Synthetic[(Synthetic data only)]
        Static <--> DemoAPI
        DemoAPI --> Synthetic
    end

    subgraph LiveDisabled[live-disabled: future profile]
        Gate[Compliance and operator activation]
        Official[Official telephony / WhatsApp adapters]
        Gate -. required .-> Official
    end
```

### `local-full`

Target for complete development and deterministic evaluation on existing hardware. It may use Docker Compose later, but no container packaging exists yet.

### `hosted-demo`

Optional synthetic-data-only demonstration. Free hosting can sleep, cold-start, change quotas, or disappear. It is not an availability commitment and cannot enable external side effects.

### `live-disabled`

Contains only official provider integrations after a separate review. Activation requires secrets outside source control, allowlisted participants, consent and contact-policy checks, operator approval, and usage caps.

## Data flow and trust boundaries

```mermaid
flowchart LR
    Internet[Untrusted buyer audio text and URLs]
    Boundary[API validation boundary]
    Content[Untrusted-content isolation]
    Engine[Conversation and policy services]
    Data[(Minimized local records)]
    Outbound[Outbound action gate]
    Provider[Official external provider]
    Operator[Trusted operator]

    Internet --> Boundary
    Boundary --> Content
    Content --> Engine
    Engine --> Data
    Engine --> Outbound
    Operator --> Outbound
    Outbound -. disabled by default .-> Provider
```

Data entering from transcripts, websites, prior notes, models, and providers is untrusted. It cannot alter system instructions, credentials, policies, or tool authorization.

The implemented conversation engine treats buyer text only as untrusted conversation data. Explicit opt-outs stop immediately; abuse receives at most one neutral redirection; requests for internal information or instruction bypass are refused without extraction or action authority. Classification uses explicit budget, timeline, decision, next-step, rejection, and need evidence. Language, accent, frustration, synthetic persona, and protected or sensitive traits are excluded.

The implemented action policy separately verifies disclosure, synthetic consent, contact eligibility, opt-out, conversation disposition, classification state, and quota. Callback dispatch rechecks policy at fake-time execution. Current adapters are in-memory mocks only; an approved preview never implies a live call, message, durable schedule, or generated binary file.

## Provider boundaries

Planned interfaces:

- Streaming STT, TTS, optional STS, and VAD.
- Structured local-model completion.
- Telephony and WhatsApp messaging/calling where officially supported.
- Scheduling and replaceable clocks.
- Lead/event storage and object storage.
- Safe web research and artifact generation.

Contract tests must ensure swapping a provider does not change domain or policy behavior.

The interfaces, disabled external adapters, deterministic mocks, retry/timeout helper, circuit breaker, and clocks are implemented. Concrete provider integrations remain planned. See [Provider contracts and deterministic mocks](PROVIDER_CONTRACTS.md).

## Availability and latency

- Measure latency by capture, VAD, STT, reasoning, policy, TTS, and playback stages.
- Bound queues and apply backpressure instead of buffering without limit.
- Persist schedules and idempotency keys before attempting actions.
- Fail closed for policy/provider uncertainty; degrade to text or operator review where safe.
- Do not advertise real-time latency until measured on labeled target hardware.

The simulator implements the capture/VAD/endpointing stages and reports per-stage
durations for endpointing, transcription, and the conversation engine. No speech model
is selected, so those durations are an accounting of the implemented path, not a
measured end-to-end latency claim.

### Real-time critical path

```mermaid
sequenceDiagram
    actor Buyer
    participant Audio as VAD / streaming STT
    participant Turn as Conversation engine
    participant Retrieval as Deadline-bound retrieval
    participant Policy as Safety and action policy
    participant Voice as Streaming TTS
    participant Journal as Append-only journal
    participant Telemetry as Local telemetry

    Buyer->>Audio: Speech frames
    Audio-->>Turn: Stable partial / final text
    par Required turn processing
        Turn->>Policy: Classify safety and authorization
    and Optional context
        Turn->>Retrieval: Query with hard deadline
        alt Context returned in time
            Retrieval-->>Turn: Cited, access-filtered context
        else Timeout or retrieval failure
            Retrieval-->>Turn: No context
        end
    end
    Turn->>Journal: Append idempotent turn decision
    Turn-->>Voice: Professional response chunks
    Voice-->>Buyer: First audio, then stream
    Turn-->>Telemetry: Stage timings and bounded labels
```

The audio, turn, policy, and journal legs of this path are implemented in the simulator; streaming TTS and the local telemetry sink remain planned, and the browser uses native speech synthesis for playback.

Retrieval is optional on the speech path. Its initial design target is 50 ms with a 200 ms hard deadline; timeout falls back to current conversation state and must not delay first audio. These are design budgets, not measured claims. Safety policy and durable acceptance cannot be bypassed to meet latency.

### Conversation memory and planned retrieval

```mermaid
flowchart LR
    Turn[Validated conversation turn] --> Journal[(Append-only event journal)]
    Journal --> Facts[Versioned fact projector]
    Facts --> Graph[(Temporal knowledge graph)]
    Facts --> SessionLexical[Session BM25 index]
    Graph --> LeadLexical[Lead BM25 index]
    Facts --> Vector[Vector adapter]
    Query[Current buyer intent] --> SessionLexical
    Query --> LeadLexical
    Query --> Vector
    SessionLexical --> Fusion[Reciprocal-rank fusion]
    LeadLexical --> Fusion
    Vector --> Fusion
    Graph --> Filter[Recency, consent, tenant and provenance filters]
    Fusion --> Filter
    Filter --> Context[Cited bounded context]
    Context --> Conversation[Conversation / deck workflow]
```

The implemented journal writes one versioned `conversation.turn-accepted.v1` event per accepted turn to the lead's existing aggregate/event stream. Each event contains a journal-computed, session-bound HMAC-SHA-256 request fingerprint, the response, bounded session policy/scalar state, and only the facts/evidence/classification produced by that turn. Exact retries recover the persisted response, conflicting reuse is rejected, stale live state cannot fork a session, and optimistic lead-version checks prevent lost updates. Replay folds validated transitions without rerunning conversation rules or actions.

Raw buyer turns are not copied into events. Only session-bound HMAC-SHA-256 digests of normalized turns are retained for repetition detection; restart requires the same operator-managed digest key. Structured allowlisted facts and evidence remain available under the lead aggregate's privacy lifecycle and are not copied into later events. Missing, partially purged, anonymized, oversized, malformed, out-of-sequence, or unsupported history fails replay closed. Simulator journal wiring is implemented; minimized transcript/source-span retention remains later reviewed work.

The append-only event repository remains authoritative. Derived BM25, vector, and graph views are rebuildable and never become the source of consent, suppression, action, or requirement truth. BM25 is the first deterministic baseline. `sqlite-vec` and HNSW remain adapter candidates; FAISS and BGE-M3 require measured scale, latency, quality, and license evidence before selection.

The implemented BM25 baseline rebuilds an immutable single-session index from current facts obtained only through full journal replay. It enforces corpus, query, result, and cooperative deadline bounds, attaches aggregate-version and turn provenance, and revalidates active privacy state and unchanged aggregate version before returning results. It is not yet connected to the speech or simulator response path.

The implemented temporal view fully replays every session in a lead stream, creates validity intervals and supersession edges only from explicit same-session revisions, and marks unresolved different values across sessions as conflicting. Graph-aware BM25 rebuilds that view without a cache, indexes current and conflicting claims from one lead, excludes superseded claims, preserves claim status and provenance, and revalidates privacy/version after ranking or timeout. Organization/product/competitor entities, persistent graph storage, structural graph queries, and automatic conflict resolution remain planned.

The graph retrieval evaluation suite invokes that production ranking/filtering path over deterministic immutable temporal graphs. Reviewed cases gate full relevant-claim recall, rank quality, zero superseded-claim exposure, and zero timeout rate while keeping latency informational and omitting queries, claims, gold identifiers, and retrieved values from run artifacts.

Facts are temporal and provenance-bearing: observed claims remain distinct from buyer-confirmed facts, revisions supersede rather than overwrite prior values, and every retrieval result must identify its source event. Conversation-derived improvements are aggregated offline after privacy filtering and evaluation; the running system does not rewrite its own prompts, policies, or models.

## Evaluation and observability

```mermaid
flowchart LR
    Replay[Synthetic replay / benchmark] --> Snapshot[Versioned evaluation JSON]
    Snapshot --> Validator[Strict bounded validator]
    Validator --> Report[Dependency-free static HTML report]
    Validator --> Gate[Reviewed suite gates]
    Runtime[Future runtime stages] --> OTel[OpenTelemetry]
    OTel --> LocalTrace[Optional local Phoenix]
    Snapshot -. optional import .-> LocalRun[Optional local MLflow]
    Gate --> Human[Adversarial review and human approval]
    Human --> Promotion[Reviewed PR promotion]
```

Evaluation snapshots are the portable source of run evidence. They contain corpus/suite hashes, exact code revision, hardware, finite metrics, bounded case labels, and machine-readable failure codes; they intentionally exclude raw transcripts, prompts, contact details, and audio. The generated HTML has no script or network dependency and applies a restrictive content security policy.

An artifact reporting passing thresholds is not deployment authorization. Promotion also requires a reviewed suite manifest, comparison against the accepted baseline, safety and regression review, and human approval. Optional MLflow or Phoenix interfaces may be added later for local visualization; neither is required to validate artifacts, and no telemetry exporter may send data externally by default.
