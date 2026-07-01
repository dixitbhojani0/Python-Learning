"""
backend/orchestrator/rag_helpers.py

Shared RAG + LLM utility functions used by the cross_source node.

Kept separate from graph.py so the node logic is easy to find and test
without wading through graph construction or classification code.
"""
import logging

from backend.core.config_loader import config
from backend.core.context_builder import count_tokens
from backend.core.metrics import faithfulness_score
from backend.core.prompt_safety import safety_guard
from backend.orchestrator.state import SDLCState
from backend.providers.factory import LLMFactory
from backend.rag.retriever import HybridRetriever, RetrievedChunk

logger = logging.getLogger(__name__)


def trim_rag_to_budget(rag_section: str, other_sections: list[str]) -> str:
    """
    I2: Trim rag_section so the total prompt fits within the configured token budget.

    `other_sections` = all other prompt parts (persona, query, history, etc.).
    Returns the (possibly shortened) rag_section. Character-proportional trim is
    accurate enough — English token density is ~0.75 tokens/char (±10%).
    """
    budget = (
        config.get_llm_config()
        .get("primary", {})
        .get("context_budget", {})
        .get("total_input_tokens", 8000)
    )
    other_tokens = sum(count_tokens(s) for s in other_sections if s)
    rag_tokens   = count_tokens(rag_section)
    available    = budget - other_tokens - 200  # 200-token safety buffer for separators/overhead
    if rag_tokens <= available or available <= 0 or not rag_section:
        return rag_section
    ratio   = max(0.0, available) / rag_tokens
    trimmed = rag_section[:int(len(rag_section) * ratio)]
    logger.warning(
        "trim_rag_to_budget: RAG %d→%d tokens (budget=%d other=%d)",
        rag_tokens, count_tokens(trimmed), budget, other_tokens,
    )
    return trimmed


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
        # sanitize + XML-wrap each fact before injection (OWASP LLM07 — stored injection defence)
        facts_text = "\n".join(f"- {safety_guard.safe_user_content(f)}" for f in semantic_facts)
        semantic_section = f"## Project Knowledge (from past conversations)\n{facts_text}"

    # I2: enforce token budget — trim RAG section if prompt would overflow
    rag_section = trim_rag_to_budget(rag_section, [persona, semantic_section, query])

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

    # ── C1: Faithfulness gate — score before returning, retry once if low ─────
    if chunks and response_text.strip():
        chunk_dicts = [{"text": c.text, "parent_text": c.parent_text} for c in chunks]
        faith = await faithfulness_score(query, response_text, chunk_dicts, provider)
        logger.info("rag_and_generate: faithfulness=%.3f", faith)
        if faith < 0.30:
            grounded_prompt = (
                prompt
                + "\n\nIMPORTANT: Your previous draft contained unverified claims. "
                "Respond ONLY using facts explicitly stated in the retrieved context above."
            )
            retry_tokens: list[str] = []
            async for token in provider.generate(grounded_prompt, system_prompt, 0.0, max_tokens):
                retry_tokens.append(token)
            retry_text = "".join(retry_tokens).strip()
            if retry_text:
                retry_faith = await faithfulness_score(query, retry_text, chunk_dicts, provider)
                if retry_faith >= faith:
                    response_text = retry_text
                    faith = retry_faith
            if faith < 0.30:
                response_text = "Based on limited context — " + response_text

    return response_text, confidence, chunks
