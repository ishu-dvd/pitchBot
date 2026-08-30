# Architecture

## Status

This document describes the target architecture. The current implementation includes the audited FastAPI foundation, default-off configuration, typed domain contracts, Alembic-managed local persistence, provider contracts, deterministic mocks, resilience primitives, a process-local browser simulator, and speech/runtime benchmark manifests and metrics. Storage/adapters are not yet connected to simulator conversation flows, and no production model is selected. Components marked as planned must not be represented as working capabilities.

## Principles

- Local-first and zero-cost for development and evaluation.
- Provider-neutral interfaces for speech, models, channels, storage, scheduling, artifacts, and clocks.
- Deterministic policy code authorizes every external action.
- Models propose structured outputs; they never directly execute side effects.
- Append-only lead journeys preserve evidence and requirement revisions.
- English, Hindi, and code-switching are first-class evaluation dimensions.
- External effects fail closed and remain disabled by default.

## Component view

```mermaid
flowchart LR
    Buyer[Buyer or test participant]
    Operator[Operator]
    UI[Browser simulator / data-call UI]
    API[FastAPI control plane]
    Conversation[Conversation state machine]
    Policy[Policy and compliance engine]
    Extractor[Fact and evidence extraction]
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
    Operator --> UI
    UI <--> API
    API --> Conversation
    Conversation --> Extractor
    Extractor --> Classifier
    Conversation <--> Speech
    Conversation <--> Model
    Conversation --> Policy
    Classifier --> Policy
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
    API->>Conversation: Process turn
    Conversation->>Store: Append transcript facts and evidence
    Conversation-->>API: Reply and typed action proposal
    API->>Policy: Authorize proposal
    alt Approved mock action
        Policy-->>API: Approved for preview
        API->>Mock: Execute idempotently
        Mock-->>Store: Append outcome
    else Blocked or review required
        Policy-->>API: Block reason
        API->>Store: Append policy decision
    end
    API-->>UI: Reply action status and redacted events
    UI-->>Buyer: Render or play response
```

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
