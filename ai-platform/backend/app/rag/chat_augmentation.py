"""
backend/app/rag/chat_augmentation.py

The wiring between retrieval and chat (§N + §Q): given already-retrieved
chunks, build the system-prompt context block and the citation list for one
chat turn. Kept as a small pure function, deliberately separate from
chat_routes.py's DB/config glue, so its branches (no chunks retrieved, one
chunk, several) are unit-testable without a database or the HTTP stack.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.app.rag.retrieval import ScoredChunk


@dataclass
class RagAugmentation:
    system_prompt: str = ""
    citations: list[dict] = field(default_factory=list)


def build_rag_augmentation(results: list[ScoredChunk]) -> RagAugmentation:
    """
    Empty `results` (RAG disabled, or a tenant with nothing ingested yet)
    returns a no-op augmentation — plain chat, no citations event, not an
    empty-but-present context block that would confuse the model.
    """
    if not results:
        return RagAugmentation()

    context_block = "\n\n".join(scored.chunk.content for scored in results)
    system_prompt = (
        "Use the following context if it is relevant to the user's question. "
        "If it is not relevant, answer normally without mentioning it.\n\n"
        f"Context:\n{context_block}"
    )
    citations = [
        {"chunk_id": str(scored.chunk.id), "document_id": str(scored.chunk.document_id)} for scored in results
    ]
    return RagAugmentation(system_prompt=system_prompt, citations=citations)
