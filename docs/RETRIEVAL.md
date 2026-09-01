# Deterministic BM25 Retrieval

## Implemented baseline

PitchBot provides dependency-free BM25 retrieval over either one durable conversation session or one lead's temporal knowledge graph. It supports Unicode letter, combining-mark, and number tokens for English, Hindi, and Hinglish without selecting a vector model or adding a network service.

The runtime path is:

1. `ConversationJournal.facts_for_retrieval` loads the bounded lead stream and performs full replay validation.
2. Only the session's current facts are projected into an immutable snapshot with lead, session, aggregate-version, fact, source-span, turn-version, language, and occurrence provenance.
3. `Bm25Index` validates corpus/query bounds and ranks exact lexical matches deterministically.
4. The journal rechecks aggregate type, active privacy state, and unchanged version before any result is returned.

`Bm25Index` defaults to `session` scope for backward compatibility. Explicit `lead` scope allows documents from multiple sessions only when all documents belong to the same lead.

`LeadKnowledgeBm25Retriever` builds the temporal lead graph, indexes only current and conflicting claims, excludes superseded claims, and returns the original temporal claim with rank, matched terms, status, and provenance. Conflicting claims remain separate results; retrieval does not choose a winner.

There is no runtime cache. Every journal or lead search rebuilds from authoritative retained events so a later anonymization, deletion, corruption, or concurrent write cannot silently reuse an older index.

## Bounds and timeout behavior

- At most 1,000 documents, 256 tokens or 4 KiB per document.
- At most 64 unique query tokens or 4 KiB per query.
- `top_k` is between 1 and 20.
- The cooperative deadline is between 1 and 200 ms.
- A scoring timeout returns no partial results and no document count.

The current synchronous journal load cannot be preempted mid-database call, so 200 ms is a cooperative scoring deadline, not a hard real-time guarantee. Runtime speech integration must add an asynchronous wall-clock timeout before placing retrieval on its optional 200 ms path.

## Safety and privacy

- Retrieval cannot read storage tables directly or bypass journal replay validation.
- Session scope rejects different leads or sessions; explicit lead scope permits multiple sessions but still rejects mixed leads.
- Results contain structured fact values and source provenance; they do not authorize actions.
- Raw buyer turns, prompts, operation fingerprints, repetition digests, and model-generated summaries are not indexed.
- Missing, partial, malformed, anonymized, deleted, or changed histories fail closed.
- BM25 results remain derived context. The append-only journal remains authoritative.

## Evaluation

`evals/corpora/retrieval-cases.json` contains six reviewed synthetic cases spanning English, Hindi, Hinglish, apparel, toys, books, food, import/export, plastics, and distinct buyer personas.

`evals/corpora/graph-retrieval-cases.json` adds six lead-scoped temporal cases covering unresolved conflicts, explicit supersession, equal cross-session observations, the same language/industry/persona diversity, and a zero-tolerance superseded-claim exposure gate. It runs the production `LeadKnowledgeBm25Retriever` against deterministic immutable graphs without storage, network, or model dependencies.

```powershell
pitchbot-bench validate-retrieval-suite evals/corpora/retrieval-cases.json
pitchbot-bench run-retrieval evals/corpora/retrieval-cases.json benchmark-results/bm25.json --run-id bm25-local-1 --git-revision <commit>
pitchbot-bench validate-graph-retrieval-suite evals/corpora/graph-retrieval-cases.json
pitchbot-bench run-graph-retrieval evals/corpora/graph-retrieval-cases.json benchmark-results/graph-bm25.json --run-id graph-bm25-local-1 --git-revision <commit>
pitchbot-bench validate-evaluation benchmark-results/bm25.json
pitchbot-bench render-evaluation benchmark-results/bm25.json benchmark-results/bm25.html
```

The outputs reuse the versioned privacy-minimized evaluation schema. They retain allowlisted language, industry, persona, and test tags for slice analysis and record recall, reciprocal rank, timeout rate, hardware labels, latency, and—for graph retrieval—excluded-claim rate. Queries, claims/documents, opaque gold identifiers, and retrieved content are excluded. Quality, timeout, and exclusion metrics gate the artifact; latency is informational because shared CI hardware is not a benchmark target.

## Deferred

Synonyms, stemming, transliteration, query expansion, vector search, reciprocal-rank fusion, HNSW, FAISS, `sqlite-vec`, BGE models, persistent indexes, runtime caching, HTTP exposure, and speech-path integration require later measured and reviewed milestones.
