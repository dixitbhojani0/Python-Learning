"""
backend/agents/base_agent.py

Abstract base class for all SDLC Assistant agents.
Ensures consistency in execution signature, prompt loading, and payload response formats.
"""
import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from backend.orchestrator.state import SDLCState

logger = logging.getLogger(__name__)


def parse_json_block(text: str) -> dict:
    """
    Extract a JSON object from LLM output — handles all reasoning model formats.

    Strips <think>...</think> blocks produced by Qwen3, DeepSeek R1, and similar
    chain-of-thought models before searching for the JSON payload.

    Search order:
      1. ```json ... ``` fenced block
      2. Raw { ... } object anywhere in the text
    """
    # Strip reasoning model thinking blocks (Qwen3, DeepSeek R1, etc.)
    clean = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    # Try fenced ```json block first (most reliable)
    match = re.search(r"```json\s*(.*?)\s*```", clean, re.DOTALL)
    if match:
        json_str = match.group(1)
    else:
        # Fall back to raw JSON object
        match = re.search(r"\{.*\}", clean, re.DOTALL)
        if not match:
            logger.warning("parse_json_block: no JSON found. Output (first 300 chars): %s", text[:300])
            return {}
        json_str = match.group(0)

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as exc:
        logger.warning("parse_json_block: JSON decode error — %s. Output (first 300 chars): %s", exc, text[:300])
        return {}


class AgentPayload(BaseModel):
    """
    Standardized result returned by every agent.
    Helps the orchestrator route, measure performance, and track sources.
    """
    agent_name:    str            = Field(description="Name of the agent that produced this result")
    confidence:    float          = Field(default=0.0,  description="RAG or retrieval confidence score (0.0-1.0)")
    summary:       str            = Field(description="Brief summary of agent findings (max 200 tokens)")
    structured:    dict[str, Any] = Field(default_factory=dict, description="Agent-specific structured data (Jira tickets, PRs, Slack messages)")
    sources:       list[str]      = Field(default_factory=list, description="List of data sources queried (e.g., ['jira', 'slack', 'rag'])")
    hitl_required: bool           = Field(default=False, description="True if this result proposes a system action requiring human approval")
    hitl_proposal: dict[str, Any] = Field(default_factory=dict, description="Structured proposal details for Human-in-the-Loop review")
    response:      str            = Field(default="", description="Plain-text response — convenience alias for structured['final_response']")


class BaseAgent(ABC):
    """
    Abstract Base Class for all agents (e.g., RiskAgent, TicketAgent, CrossSourceAgent).
    Injects dependencies like RAG, LLM, and MCP connectors.
    """

    # ── Hallucination prevention constants ─────────────────────────────────────
    # Agents abort when RAG confidence is below this threshold. Confidence is the
    # authoritative signal — presence of chunks alone is NOT sufficient because
    # irrelevant chunks are often returned for out-of-domain queries, causing the
    # LLM to hallucinate answers sourced from its training data.
    _LOW_CONFIDENCE_THRESHOLD: float = 0.20

    def __init__(self, mcp_registry: Any, retriever: Any, llm: Any, config_loader: Any):
        """
        Args:
            mcp_registry: The instantiated MCP connector registry
            retriever: The HybridRetriever instance
            llm: The instantiated ChatOpenAI / ChatGroq client
            config_loader: The singleton ConfigLoader
        """
        self.mcp      = mcp_registry
        self.retriever = retriever
        self.llm      = llm
        self.config   = config_loader

    @abstractmethod
    async def run(self, state: SDLCState) -> "AgentPayload":
        """
        Execute the agent's core reasoning logic based on current state.

        Args:
            state: The current conversation and context state of the graph.

        Returns:
            An AgentPayload containing the findings and structured outputs.
        """
        pass

    def _get_prompt(self, key: str, **kwargs: Any) -> str:
        """
        Helper method to retrieve and format a prompt from config/prompts.yaml.
        Enforces separation of prompt templates from Python code.
        """
        return self.config.get_prompt(key, **kwargs)

    def _low_confidence_guard(
        self,
        confidence: float,
        chunks: list,
        query: str,
        threshold: float | None = None,
    ) -> "AgentPayload | None":
        """
        Hallucination prevention guard.

        If RAG retrieval found no relevant chunks (or confidence is below threshold),
        return a helpful 'not found' AgentPayload INSTEAD of calling the LLM.

        Why: when confidence is near 0, the LLM has no grounding evidence and will
        hallucinate plausible-sounding but fabricated answers. It's better to tell
        the user honestly that we don't have the data than to invent it.

        Returns:
            AgentPayload — caller should return this immediately (abort path)
            None         — enough evidence found, proceed normally
        """
        effective_threshold = threshold if threshold is not None else self._LOW_CONFIDENCE_THRESHOLD
        agent_name = self.__class__.__name__

        # Confidence is the only gate. `len(chunks) > 0` is intentionally removed:
        # irrelevant chunks (coding_standards, version_policy) are returned for
        # out-of-domain queries and their presence alone does NOT mean the LLM has
        # grounding evidence — it just means it will hallucinate with citations.
        if confidence >= effective_threshold:
            return None   # enough evidence — let the LLM proceed

        logger.warning(
            "%s: low confidence guard triggered — confidence=%.3f, chunks=%d, threshold=%.3f. "
            "Returning 'not found' instead of risking hallucination.",
            agent_name, confidence, len(chunks), effective_threshold,
        )

        message = (
            f"I don't have enough information to answer that question:\n\n"
            f"> {query[:150]}\n\n"
            f"**Try one of these:**\n"
            f"- Ask by ticket ID: *\"SDLC-4 assignee?\"* or *\"what is the status of SDLC-12?\"*\n"
            f"- Be more specific: include the feature name, service, or team member\n"
            f"- Check sprint health: *\"what is the sprint risk?\"*\n"
            f"- Check blockers: *\"what are the blockers?\"*\n"
            f"- Create a ticket: *\"create ticket: [describe the issue]\"*"
        )

        return AgentPayload(
            agent_name=agent_name,
            confidence=confidence,
            summary="No relevant context found in knowledge base",
            # Persona runs so the reply is role-appropriate; the persona prompts now
            # forbid inventing status/metrics, so a "not found" reply stays brief.
            structured={"final_response": message},
            sources=[],
            hitl_required=False,
            hitl_proposal={},
            response=message,
        )

    def _guard_empty_llm(self, llm_output: str, query: str) -> str:
        """
        Guard: if the LLM returned an empty string (rate limit / timeout / quota exhausted),
        return an honest error message instead of passing empty text through the pipeline.

        An empty LLM output causes downstream agents to either show blank responses or
        fall into fallback JSON parsing, both of which are poor user experiences.
        """
        if llm_output.strip():
            return llm_output   # non-empty — return as-is

        agent_name = self.__class__.__name__
        logger.warning(
            "%s: LLM returned empty output for query='%s...' — "
            "likely rate limited or quota exhausted.",
            agent_name, query[:60],
        )
        return (
            "I'm temporarily unavailable — please try again in a moment.\n\n"
            "If the issue continues, contact your system administrator."
        )
