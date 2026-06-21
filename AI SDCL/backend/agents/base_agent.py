"""
backend/agents/base_agent.py

Abstract base class for all SDLC Assistant agents.
Ensures consistency in execution signature, prompt loading, and payload response formats.
"""
from abc import ABC, abstractmethod
from typing import Any
from pydantic import BaseModel, Field

from backend.orchestrator.state import SDLCState

class AgentPayload(BaseModel):
    """
    Standardized result returned by every agent.
    Helps the orchestrator route, measure performance, and track sources.
    """
    agent_name: str = Field(description="Name of the agent that produced this result")
    confidence: float = Field(default=0.0, description="RAG or retrieval confidence score (0.0-1.0)")
    summary: str = Field(description="Brief summary of agent findings (max 200 tokens)")
    structured: dict[str, Any] = Field(default_factory=dict, description="Agent-specific structured data (Jira tickets, PRs, Slack messages)")
    sources: list[str] = Field(default_factory=list, description="List of data sources queried (e.g., ['jira', 'slack', 'rag'])")
    hitl_required: bool = Field(default=False, description="True if this result proposes a system action requiring human approval")
    hitl_proposal: dict[str, Any] = Field(default_factory=dict, description="Structured proposal details for Human-in-the-Loop review")


class BaseAgent(ABC):
    """
    Abstract Base Class for all agents (e.g., RiskAgent, TicketAgent, CrossSourceAgent).
    Injects dependencies like RAG, LLM, and MCP connectors.
    """

    def __init__(self, mcp_registry: Any, retriever: Any, llm: Any, config_loader: Any):
        """
        Args:
            mcp_registry: The instantiated MCP connector registry
            retriever: The HybridRetriever instance
            llm: The instantiated ChatOpenAI / ChatGroq client
            config_loader: The singleton ConfigLoader
        """
        self.mcp = mcp_registry
        self.retriever = retriever
        self.llm = llm
        self.config = config_loader

    @abstractmethod
    async def run(self, state: SDLCState) -> AgentPayload:
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
