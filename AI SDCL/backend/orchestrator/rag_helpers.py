"""
backend/orchestrator/rag_helpers.py

Shared RAG + LLM utility functions used by the cross_source node.

Kept separate from graph.py so the node logic is easy to find and test
without wading through graph construction or classification code.
"""
import logging

from backend.core.config_loader import config
from backend.orchestrator.state import SDLCState
from backend.providers.factory import LLMFactory
from backend.rag.retriever import HybridRetriever, RetrievedChunk

logger = logging.getLogger(__name__)


def format_rag_context(chunks: list[RetrievedChunk]) -> str:
    """Format retrieved chunks into a context block for the LLM prompt."""
    if not chunks:
        return ""
    lines = ["## Retrieved Context (Sprint Docs, ADRs, Jira History, Slack)"]
    for i, chunk in enumerate(chunks, 1):
        content = chunk.parent_text or chunk.text
        lines.append(f"\n### Source {i}: {chunk.source} ({chunk.doc_type})")
        lines.append(content)
    return "\n".join(lines)


async def rag_and_generate(
    state: SDLCState,
    retriever: HybridRetriever,
) -> tuple[str, float, list[RetrievedChunk]]:
    """
    Core RAG + LLM call — used by the cross_source node.

    Returns: (response_text, confidence, chunks)
    """
    query      = state["query"]
    project    = state["project_id"]
    user_role  = state["user_role"]

    # ── RAG retrieval
    chunks, confidence = retriever.retrieve(query, project)

    # ── Build prompt
    system_prompt = config.get_prompt("system_prompt")
    persona       = config.get_prompt(f"persona_{user_role}") or config.get_prompt("persona_developer")
    rag_section   = format_rag_context(chunks)

    # Inject long-term semantic facts if available (accumulated across past sessions)
    semantic_facts = state.get("semantic_context", [])
    semantic_section = ""
    if semantic_facts:
        facts_text = "\n".join(f"- {f}" for f in semantic_facts)
        semantic_section = f"## Project Knowledge (from past conversations)\n{facts_text}"

    prompt = "\n\n".join(filter(None, [
        persona,
        semantic_section,
        rag_section,
        f"## Current Question\n{query}",
    ]))

    # ── Call LLM
    provider    = LLMFactory.get_provider()
    temperature = config.get_temperature("response_generation")
    max_tokens  = (
        config.get_llm_config()
        .get("primary", {})
        .get("max_tokens", {})
        .get("response", 1024)
    )

    tokens: list[str] = []
    async for token in provider.generate(prompt, system_prompt, temperature, max_tokens):
        tokens.append(token)

    response_text = "".join(tokens) or "I don't have enough information to answer that confidently."
    return response_text, confidence, chunks
