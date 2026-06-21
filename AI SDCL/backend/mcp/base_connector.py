"""
backend/mcp/base_connector.py

Abstract base class for all MCP connectors.

Every connector (Jira, Slack, GitHub) extends this and implements is_available().
Specific tool methods (search_tickets, search_messages, etc.) are defined on
concrete classes — the base class only enforces identity and availability.

Why not abstract methods for each tool?
  Jira doesn't have search_messages. Slack doesn't have search_tickets.
  Forcing every connector to implement every method would result in NotImplementedError
  stubs everywhere. Instead, agents call the method they know is on the connector
  they requested: mcp.get("jira").search_tickets(...) — typed by the caller, not the base.
"""
from abc import ABC, abstractmethod


class BaseMCPConnector(ABC):
    """Identity + availability contract for all MCP connectors."""

    def __init__(self, name: str, connector_config: dict):
        self._name   = name
        self._config = connector_config

    @property
    def connector_name(self) -> str:
        return self._name

    @property
    def config(self) -> dict:
        return self._config

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the connector can handle requests right now."""
        ...
