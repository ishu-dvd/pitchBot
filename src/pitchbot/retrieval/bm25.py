from __future__ import annotations

import json
import math
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from time import monotonic_ns
from uuid import UUID

from pitchbot.conversation import ConversationFactSnapshot, ConversationJournal
from pitchbot.domain import JsonValue
from pitchbot.retrieval.models import (
    FactProvenance,
    LexicalDocument,
    RankedFact,
    RetrievalResponse,
    RetrievalScope,
)

MAX_DOCUMENTS = 1_000
MAX_DOCUMENT_BYTES = 4_096
MAX_DOCUMENT_TOKENS = 256
MAX_QUERY_BYTES = 4_096
MAX_QUERY_TOKENS = 64
MAX_RESULTS = 20
MAX_DEADLINE_MS = 200


def tokenize(text: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens: list[str] = []
    current: list[str] = []
    for character in normalized:
        category = unicodedata.category(character)
        if category[0] in {"L", "M", "N"}:
            current.append(character)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tuple(tokens)


def _render_value(value: JsonValue) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_bm25_request(query: str, top_k: int, deadline_ms: int) -> tuple[str, ...]:
    if not 1 <= top_k <= MAX_RESULTS:
        raise ValueError(f"BM25 top_k must be between 1 and {MAX_RESULTS}")
    if not 1 <= deadline_ms <= MAX_DEADLINE_MS:
        raise ValueError(f"BM25 deadline must be between 1 and {MAX_DEADLINE_MS} milliseconds")
    if not query.strip():
        raise ValueError("BM25 query must not be blank")
    if len(query.encode("utf-8")) > MAX_QUERY_BYTES:
        raise ValueError("BM25 query exceeds size limit")
    query_terms = tuple(dict.fromkeys(tokenize(query)))
    if not query_terms:
        raise ValueError("BM25 query must contain letters or numbers")
    if len(query_terms) > MAX_QUERY_TOKENS:
        raise ValueError("BM25 query exceeds token limit")
    return query_terms


@dataclass(frozen=True, slots=True)
class _IndexedDocument:
    document: LexicalDocument
    frequencies: Counter[str]
    length: int


class Bm25Index:
    def __init__(
        self,
        documents: Iterable[LexicalDocument],
        *,
        k1: float = 1.5,
        b: float = 0.75,
        scope: RetrievalScope | str = RetrievalScope.SESSION,
    ) -> None:
        if not math.isfinite(k1) or k1 <= 0:
            raise ValueError("BM25 k1 must be finite and positive")
        if not math.isfinite(b) or not 0 <= b <= 1:
            raise ValueError("BM25 b must be finite and between zero and one")
        try:
            validated_scope = RetrievalScope(scope)
        except ValueError:
            raise ValueError("BM25 scope must be session or lead") from None
        materialized = tuple(documents)
        if len(materialized) > MAX_DOCUMENTS:
            raise ValueError("BM25 document capacity exceeded")
        fact_ids = [item.provenance.fact_id for item in materialized]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("BM25 document fact identifiers must be unique")
        lead_ids = {item.provenance.lead_id for item in materialized}
        session_ids = {item.provenance.session_id for item in materialized}
        if len(lead_ids) > 1 or (
            validated_scope is RetrievalScope.SESSION and len(session_ids) > 1
        ):
            raise ValueError("BM25 documents must belong to one lead and session")

        indexed: list[_IndexedDocument] = []
        document_frequency: Counter[str] = Counter()
        for document in materialized:
            text = f"{document.key.replace('_', ' ')} {_render_value(document.value)}"
            if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
                raise ValueError("BM25 document exceeds size limit")
            terms = tokenize(text)
            if len(terms) > MAX_DOCUMENT_TOKENS:
                raise ValueError("BM25 document exceeds token limit")
            frequencies = Counter(terms)
            document_frequency.update(frequencies.keys())
            indexed.append(
                _IndexedDocument(
                    document=document,
                    frequencies=frequencies,
                    length=len(terms),
                )
            )
        self._documents = tuple(indexed)
        self._document_frequency = document_frequency
        self._average_length = (
            sum(item.length for item in indexed) / len(indexed) if indexed else 0.0
        )
        self._k1 = k1
        self._b = b

    @property
    def document_count(self) -> int:
        return len(self._documents)

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        deadline_ms: int = MAX_DEADLINE_MS,
        clock: Callable[[], int] = monotonic_ns,
    ) -> tuple[tuple[RankedFact, ...], float, bool]:
        query_terms = validate_bm25_request(query, top_k, deadline_ms)

        started = clock()
        deadline = started + deadline_ms * 1_000_000
        scored: list[tuple[float, UUID, tuple[str, ...], LexicalDocument]] = []
        document_count = len(self._documents)
        for indexed in self._documents:
            if clock() >= deadline:
                return (), max(0.0, (clock() - started) / 1_000_000), True
            score = 0.0
            matched: list[str] = []
            for term in query_terms:
                frequency = indexed.frequencies.get(term, 0)
                if frequency == 0:
                    continue
                matched.append(term)
                frequency_in_documents = self._document_frequency[term]
                inverse_frequency = math.log(
                    1
                    + (document_count - frequency_in_documents + 0.5)
                    / (frequency_in_documents + 0.5)
                )
                length_ratio = (
                    indexed.length / self._average_length if self._average_length else 0.0
                )
                denominator = frequency + self._k1 * (1 - self._b + self._b * length_ratio)
                score += inverse_frequency * (frequency * (self._k1 + 1)) / denominator
            if score > 0:
                scored.append(
                    (
                        score,
                        indexed.document.provenance.fact_id,
                        tuple(matched),
                        indexed.document,
                    )
                )
        scored.sort(key=lambda item: (-item[0], str(item[1])))
        results = tuple(
            RankedFact(
                rank=rank,
                score=score,
                matched_terms=matched,
                document=document,
            )
            for rank, (score, _, matched, document) in enumerate(
                scored[:top_k],
                start=1,
            )
        )
        return results, max(0.0, (clock() - started) / 1_000_000), False


class JournalBm25Retriever:
    def __init__(
        self,
        journal: ConversationJournal,
        *,
        clock: Callable[[], int] = monotonic_ns,
    ) -> None:
        self._journal = journal
        self._clock = clock

    def search(
        self,
        lead_id: UUID,
        session_id: UUID,
        query: str,
        *,
        top_k: int = 5,
        deadline_ms: int = MAX_DEADLINE_MS,
    ) -> RetrievalResponse:
        validate_bm25_request(query, top_k, deadline_ms)
        started = self._clock()
        snapshot = self._journal.facts_for_retrieval(lead_id, session_id)
        documents = self._documents(snapshot)
        index = Bm25Index(documents)
        elapsed_ns = max(0, self._clock() - started)
        remaining_ms = deadline_ms - elapsed_ns // 1_000_000
        if remaining_ms < 1:
            self._journal.validate_fact_snapshot(snapshot)
            return self._timeout_response(snapshot, started)
        results, _, timed_out = index.search(
            query,
            top_k=top_k,
            deadline_ms=int(remaining_ms),
            clock=self._clock,
        )
        if timed_out:
            self._journal.validate_fact_snapshot(snapshot)
            return self._timeout_response(snapshot, started)
        self._journal.validate_fact_snapshot(snapshot)
        if self._clock() - started >= deadline_ms * 1_000_000:
            return self._timeout_response(snapshot, started)
        return RetrievalResponse(
            lead_id=lead_id,
            session_id=session_id,
            aggregate_version=snapshot.aggregate_version,
            duration_ms=max(0.0, (self._clock() - started) / 1_000_000),
            indexed_document_count=index.document_count,
            timed_out=False,
            results=results,
        )

    def _timeout_response(
        self,
        snapshot: ConversationFactSnapshot,
        started: int,
    ) -> RetrievalResponse:
        return RetrievalResponse(
            lead_id=snapshot.lead_id,
            session_id=snapshot.session_id,
            aggregate_version=snapshot.aggregate_version,
            duration_ms=max(0.0, (self._clock() - started) / 1_000_000),
            indexed_document_count=0,
            timed_out=True,
            results=(),
        )

    @staticmethod
    def _documents(snapshot: ConversationFactSnapshot) -> tuple[LexicalDocument, ...]:
        return tuple(
            LexicalDocument(
                key=item.fact.key,
                value=item.fact.value,
                language=item.language,
                provenance=FactProvenance(
                    lead_id=snapshot.lead_id,
                    session_id=item.session_id,
                    aggregate_version=item.aggregate_version,
                    fact_id=item.fact.fact_id,
                    source_span_ids=item.fact.source_span_ids,
                    occurred_at=item.occurred_at,
                ),
            )
            for item in snapshot.facts
        )
