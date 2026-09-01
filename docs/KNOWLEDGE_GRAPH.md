# Temporal Lead Knowledge View

## Implemented scope

PitchBot can rebuild an immutable lead-scoped knowledge view from durable conversation events. It uses no graph database, persistent projection, model, embedding, or network service.

The view contains:

- Lead-to-session relations.
- Session-to-fact observation relations.
- Explicit fact-to-fact supersession relations.
- Fact validity versions and occurrence times.
- Source session, language, fact identifiers, source spans, confidence, and structured value.
- `current`, `superseded`, or `conflicting` claim status.

## Conservative temporal semantics

Only an explicit revision produced inside the same conversation session closes a prior fact's validity interval and creates a `superseded-by` relation. The replacement remains current unless later revised.

Current claims with the same key and different canonical values across sessions are marked `conflicting`. PitchBot does not silently select the newest value because separate sessions may describe different business contexts or an unresolved buyer correction. Equal values across sessions remain separate current observations.

`confirmed_by_customer` is carried only when an explicit revision marks its replacement as confirmed. The current deterministic extractor does not infer confirmation.

## Construction and validation

1. `ConversationJournal.knowledge_source` reads the bounded lead stream.
2. Every conversation session in that stream is fully replayed and validated.
3. Fact and revision identifiers, same-turn replacements, prior fact references, keys, sessions, order, and one-time supersession are checked.
4. `TemporalKnowledgeGraphBuilder` constructs claims and relations deterministically.
5. The journal rechecks aggregate type, active privacy state, and unchanged version before returning the graph.

The builder defaults to 1,000 sessions, 1,000 claims, 1,000 revisions, and 3,000 relations and rejects unsafe configured maxima. Claim and revision capacities are independent and checked before semantic processing of an over-capacity turn. Missing, partial, malformed, anonymized, deleted, changed, or oversized history fails closed.

## Privacy and authority

There is no runtime graph cache. Each build starts from the authoritative journal, so privacy lifecycle changes invalidate future builds. Raw buyer turns, operation fingerprints, repetition digests, prompts, and action state are absent.

The view is derived context only. It cannot authorize outreach, resolve consent or suppression, modify requirements, or override the event journal.

## Lead retrieval

The graph-aware BM25 path rebuilds this view for each request and indexes only `current` and `conflicting` claims. It never indexes a `superseded` claim. Results retain the full temporal claim, including status, source session, fact/source identifiers, language, confidence, and validity provenance.

The graph build and index construction count toward the same cooperative 1–200 ms deadline as scoring. Timeout returns no partial results or indexed-claim count. Privacy state and aggregate version are rechecked after scoring and on timeout.

## Deferred

Automatic cross-session conflict resolution, organization/product/competitor entities, extraction-version metadata, confidence fusion, persistent graph storage, structural graph queries, hybrid/vector retrieval, HTTP exposure, and runtime RAG remain separately reviewed milestones.
