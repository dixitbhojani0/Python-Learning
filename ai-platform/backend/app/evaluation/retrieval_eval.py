"""
backend/app/evaluation/retrieval_eval.py

Top-1 retrieval accuracy over the golden dataset (§22 "golden datasets" /
"retrieval quality") — deliberately top-1, not "does the expected chunk
appear anywhere in top-k": with only a handful of documents in a tenant,
§N's retrieval (no relevance threshold — rag/retrieval.py) returns nearly
every chunk in top-k regardless of relevance, which would make an
in-top-k metric trivially 100% and prove nothing. Top-1 is real: an exact
match's cosine distance is 0 (the global minimum), so it wins rank 1 only
if the ranking itself is actually correct — a regression in the ordering
logic, not just presence, shows up as a metric drop here.

Scope: this measures retrieval *plumbing* correctness (did the right chunk
rank first among distinct candidates), not retrieval *quality* on
paraphrased/semantic queries — the mock embedding provider can't honestly
claim the latter (see mock_embedding_provider.py's docstring).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.evaluation.golden_dataset import GoldenCase
from backend.app.rag.ingestion import ingest_document
from backend.app.rag.retrieval import search_chunks


@dataclass
class CaseResult:
    query: str
    hit: bool


@dataclass
class RetrievalEvalResult:
    total: int
    hits: int
    case_results: list[CaseResult] = field(default_factory=list)

    @property
    def hit_rate(self) -> float:
        return self.hits / self.total if self.total else 0.0


async def run_retrieval_eval(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    cases: list[GoldenCase],
    embedding_provider: str = "mock",
    top_k: int = 3,
) -> RetrievalEvalResult:
    """`session` must already be tenant-scoped (§J), same discipline as every RAG/memory function."""
    document_id_by_content: dict[str, uuid.UUID] = {}
    for case in cases:
        result = await ingest_document(
            session,
            tenant_id=tenant_id,
            title=case.document_title,
            content=case.document_content,
            embedding_provider=embedding_provider,
        )
        document_id_by_content[case.document_content] = result.document.id
    await session.flush()

    case_results: list[CaseResult] = []
    for case in cases:
        results = await search_chunks(session, query=case.query, top_k=top_k, embedding_provider=embedding_provider)
        expected_document_id = document_id_by_content[case.document_content]
        hit = bool(results) and results[0].chunk.document_id == expected_document_id
        case_results.append(CaseResult(query=case.query, hit=hit))

    hits = sum(1 for r in case_results if r.hit)
    return RetrievalEvalResult(total=len(cases), hits=hits, case_results=case_results)
