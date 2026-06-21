"""
backend/mcp/registry.py

MCPRegistry — the single place where agents access external tool connectors.

Agents never instantiate connectors directly. They call:
    mcp.get("jira").search_tickets(query)
    mcp.get("slack").search_messages(query)

Why this indirection?
  1. Swap mock → real connector in one YAML line, zero agent code changes.
  2. The registry owns the concurrency cap — max N parallel MCP calls via asyncio.Semaphore.
  3. All connector failures are isolated here. One broken connector doesn't crash the agent.

Usage (in an agent):
    mcp = MCPRegistry()
    tickets = await mcp.get("jira").search_tickets("dashboard blocked", "antlog")

    # Or call multiple connectors in parallel (safe, respects semaphore):
    jira_data, slack_data = await mcp.call_parallel([
        ("jira",  "search_tickets",  {"query": q, "project": project}),
        ("slack", "search_messages", {"query": q}),
    ])
"""
import asyncio
import logging
from typing import Any

from backend.core.config_loader import config
from backend.mcp.base_connector import BaseMCPConnector
from backend.mcp.connectors.mock_github import MockGitHubConnector
from backend.mcp.connectors.mock_jira import MockJiraConnector
from backend.mcp.connectors.mock_slack import MockSlackConnector

logger = logging.getLogger(__name__)

# Default cap — read from mcp_registry.yaml if present, else 3.
_DEFAULT_MAX_CONCURRENT = 3


class MCPRegistry:
    """
    Instantiates and manages all MCP connectors.

    Phase 6: all connectors are mock (no real API keys needed).
    A future phase will check env vars and use real connectors when credentials exist.
    """

    def __init__(self) -> None:
        # get_mcp_registry() already unwraps the "connectors" key — returns the connector dict
        connectors_cfg = config.get_mcp_registry()
        self._semaphore = asyncio.Semaphore(_DEFAULT_MAX_CONCURRENT)
        self._connectors: dict[str, BaseMCPConnector] = {}

        for name, connector_cfg in connectors_cfg.items():
            if not connector_cfg.get("enabled", True):
                logger.debug("MCPRegistry: connector '%s' disabled in config", name)
                continue
            connector = self._build_connector(name, connector_cfg)
            if connector is not None:
                self._connectors[name] = connector
                logger.debug("MCPRegistry: registered connector '%s'", name)

        logger.info(
            "MCPRegistry: %d connectors registered: %s",
            len(self._connectors),
            list(self._connectors.keys()),
        )

    def _build_connector(self, name: str, cfg: dict) -> BaseMCPConnector | None:
        """Map connector type name → concrete class. All mock in Phase 6."""
        connector_type = cfg.get("type", name)
        try:
            if connector_type == "jira":
                return MockJiraConnector(name=name, connector_config=cfg)
            if connector_type == "slack":
                return MockSlackConnector(name=name, connector_config=cfg)
            if connector_type == "github":
                return MockGitHubConnector(name=name, connector_config=cfg)
            logger.warning("MCPRegistry: unknown connector type '%s' for '%s'", connector_type, name)
            return None
        except Exception:
            logger.exception("MCPRegistry: failed to build connector '%s'", name)
            return None

    def get(self, name: str) -> BaseMCPConnector:
        """
        Return a connector by name.

        Raises KeyError if the connector is not registered — this is intentional.
        If an agent requests a connector that doesn't exist, we want a loud failure
        (not silent wrong behavior) so the developer notices immediately.
        """
        if name not in self._connectors:
            raise KeyError(
                f"MCPRegistry: connector '{name}' not found. "
                f"Registered: {list(self._connectors.keys())}"
            )
        return self._connectors[name]

    def has(self, name: str) -> bool:
        """Return True if the named connector is registered and available."""
        return name in self._connectors and self._connectors[name].is_available()

    async def call_parallel(
        self,
        calls: list[tuple[str, str, dict]],
    ) -> list[Any]:
        """
        Call multiple connector methods in parallel, respecting the concurrency cap.

        Args:
            calls: list of (connector_name, method_name, kwargs)
                   e.g. [("jira", "search_tickets", {"query": "...", "project": "antlog"})]

        Returns:
            list of results in the same order as calls.
            If a connector raises, its result is the Exception object (not re-raised).
            Callers must check: if not isinstance(result, Exception): use(result)

        Why return_exceptions=True?
            Per resilience_standards.md: one failing connector must not crash all others.
            Jira being down should not prevent Slack from returning data.
        """
        async def _one_call(connector_name: str, method: str, kwargs: dict) -> Any:
            async with self._semaphore:
                connector = self.get(connector_name)
                fn = getattr(connector, method)
                return await fn(**kwargs)

        tasks = [_one_call(name, method, kwargs) for name, method, kwargs in calls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, (name, method, _) in enumerate(calls):
            if isinstance(results[i], Exception):
                logger.warning(
                    "MCPRegistry.call_parallel: %s.%s failed — %s",
                    name, method, results[i],
                )

        return list(results)
