"""
backend/core/context_builder.py

Assembles the 7-slot prompt sent to the LLM on every request.
Measures tokens using tiktoken BEFORE sending — no surprises.

The 7 slots (in order):
  1. System prompt       ~200 tokens  (fixed)
  2. Persona instruction ~100 tokens  (fixed per role)
  3. Conversation summary~300 tokens  (compressed past turns)
  4. Recent messages     ~400 tokens  (last 3-5 raw messages)
  5. RAG context        ~1500 tokens  (top 7 retrieved chunks)
  6. Tool outputs        ~500 tokens  (normalized MCP data)
  7. User query          no cap       (current message — never truncated)

Total budget: ~8000 tokens input (safe for llama-3.3-70b on Groq)
"""
import logging
import tiktoken
from typing import Any

from backend.core.config_loader import config

logger = logging.getLogger(__name__)

# ── Tiktoken encoder — cl100k_base is used by GPT-4 and compatible with Groq models
# Groq's Llama tokenizer differs by ±3-5 tokens/message — well within our safety buffer
ENCODER = tiktoken.get_encoding("cl100k_base")

# ── Token budget per slot (guidelines, not hard caps)
SLOT_BUDGETS = {
    "system":   200,
    "persona":  100,
    "summary":  300,
    "recent":   400,
    "rag":      1500,
    "tools":    500,
    # "query" has no cap — always sent in full
}

TOTAL_INPUT_BUDGET = 8000   # tokens — stay under Groq context limit


def count_tokens(text: str) -> int:
    """Count tokens in a string using tiktoken. Never estimate — always measure."""
    if not text:
        return 0
    return len(ENCODER.encode(text))


class ContextBuilder:
    """
    Builds the final prompt by assembling all 7 slots.
    Gracefully compresses slots if total exceeds budget.
    """

    def build(
        self,
        user_role: str,
        current_query: str,
        conversation_summary: str = "",
        recent_messages: list[dict] = None,
        rag_chunks: list[dict] = None,
        mcp_data: dict = None,
    ) -> str:
        """
        Assemble all 7 slots into one prompt string.

        Args:
            user_role: "developer" | "manager" | "technical_leader" | "stakeholder"
            current_query: The user's current message
            conversation_summary: LLM-compressed summary of past turns
            recent_messages: Last 3-5 raw messages as [{"role": ..., "content": ...}]
            rag_chunks: Retrieved chunks from RAG pipeline [{"text": ..., "source": ...}]
            mcp_data: Normalized MCP tool outputs {"jira": {...}, "slack": {...}}

        Returns:
            Assembled prompt string ready for LLM
        """
        recent_messages = recent_messages or []
        rag_chunks = rag_chunks or []
        mcp_data = mcp_data or {}

        # ── Build each slot
        slots = {
            "system":  self._slot_system(),
            "persona": self._slot_persona(user_role),
            "summary": self._slot_summary(conversation_summary),
            "recent":  self._slot_recent(recent_messages),
            "rag":     self._slot_rag(rag_chunks),
            "tools":   self._slot_tools(mcp_data),
            "query":   current_query,             # Never truncated
        }

        # ── Measure total tokens
        total = sum(count_tokens(v) for v in slots.values())
        logger.debug("ContextBuilder: token counts per slot: %s | total: %d",
                     {k: count_tokens(v) for k, v in slots.items()}, total)

        # ── Graceful compression if over budget
        if total > TOTAL_INPUT_BUDGET:
            slots = self._compress(slots, total)

        return self._assemble(slots)

    # ─────────────────────────────────────────────
    #  Slot builders
    # ─────────────────────────────────────────────

    def _slot_system(self) -> str:
        return config.get_prompt("system_prompt")

    def _slot_persona(self, role: str) -> str:
        prompt_key = f"persona_{role}"
        persona = config.get_prompt(prompt_key)
        if not persona:
            logger.warning("ContextBuilder: no persona prompt for role '%s', using manager", role)
            persona = config.get_prompt("persona_manager")
        return persona

    def _slot_summary(self, summary: str) -> str:
        if not summary:
            return ""
        return f"## Conversation History Summary\n{summary}"

    def _slot_recent(self, messages: list[dict]) -> str:
        if not messages:
            return ""
        lines = ["## Recent Conversation"]
        for msg in messages[-5:]:           # keep last 5 at most
            role = msg.get("role", "user").capitalize()
            content = msg.get("content", "")
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def _slot_rag(self, chunks: list[dict]) -> str:
        if not chunks:
            return ""
        lines = ["## Retrieved Context (from Sprint Docs, Jira History, ADRs)"]
        for i, chunk in enumerate(chunks[:7], 1):      # max 7 chunks
            source = chunk.get("source", "unknown")
            text = chunk.get("parent_text") or chunk.get("text", "")
            score = chunk.get("score", 0.0)
            lines.append(f"\n### Source {i}: {source} (relevance: {score:.2f})")
            lines.append(text)
        return "\n".join(lines)

    def _slot_tools(self, mcp_data: dict) -> str:
        if not mcp_data:
            return ""
        lines = ["## Live Data from Connected Tools"]
        for tool, data in mcp_data.items():
            if data:
                lines.append(f"\n### {tool.upper()}")
                if isinstance(data, dict):
                    for k, v in data.items():
                        lines.append(f"- {k}: {v}")
                else:
                    lines.append(str(data))
        return "\n".join(lines)

    # ─────────────────────────────────────────────
    #  Assembly + compression
    # ─────────────────────────────────────────────

    def _assemble(self, slots: dict) -> str:
        """Join all slots in order with clear section separators."""
        parts = []
        order = ["system", "persona", "summary", "recent", "rag", "tools", "query"]
        for key in order:
            value = slots.get(key, "")
            if value:
                parts.append(value)
        return "\n\n---\n\n".join(parts)

    def _compress(self, slots: dict, total: int) -> dict:
        """
        Gracefully compress slots when over token budget.
        Priority of cuts (safest first):
          1. Drop lowest-scoring RAG chunks
          2. Drop oldest recent messages (keep minimum 2)
          3. Shorten conversation summary
        Never touch: system, persona, query
        """
        logger.warning(
            "ContextBuilder: over token budget (%d/%d tokens). Compressing...",
            total, TOTAL_INPUT_BUDGET
        )

        # Cut RAG from bottom (lowest reranker score — already sorted desc)
        rag_lines = slots["rag"].split("### Source ")
        while len(rag_lines) > 2 and total > TOTAL_INPUT_BUDGET:
            removed = rag_lines.pop()
            saved = count_tokens("### Source " + removed)
            total -= saved
            logger.debug("ContextBuilder: dropped 1 RAG chunk, saved %d tokens", saved)
        slots["rag"] = "### Source ".join(rag_lines)

        # Cut oldest recent messages
        if total > TOTAL_INPUT_BUDGET:
            lines = slots["recent"].split("\n")
            while len(lines) > 3 and total > TOTAL_INPUT_BUDGET:
                dropped = lines.pop(1)      # remove oldest (keep header at [0])
                total -= count_tokens(dropped)
            slots["recent"] = "\n".join(lines)

        return slots
