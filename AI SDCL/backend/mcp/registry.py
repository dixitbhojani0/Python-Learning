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

Adding a new connector (Plugin Registry pattern):
  1. Create the new connector class (e.g. LinearConnector)
  2. Call MCPRegistry.register("linear", LinearConnector, MockLinearConnector)
     at the bottom of its file.
  3. Import the connector file in backend/mcp/__init__.py so self-registration runs.
  Zero changes to this file.

Usage (in an agent):
    mcp = MCPRegistry()
    tickets = await mcp.get("jira").search_tickets("dashboard blocked", "SDLC")

    # Or call multiple connectors in parallel (safe, respects semaphore):
    jira_data, slack_data = await mcp.call_parallel([
        ("jira",  "search_tickets",  {"query": q, "project": project}),
        ("slack", "search_messages", {"query": q}),
    ])
"""
import asyncio
import logging
from typing import Any, Type

from backend.core.config_loader import config
from backend.mcp.base_connector import BaseMCPConnector

logger = logging.getLogger(__name__)

# Default cap — read from mcp_registry.yaml if present, else 3.
_DEFAULT_MAX_CONCURRENT = 3


class MCPRegistry:
    """
    Instantiates and manages all MCP connectors.

    Connector selection is automatic:
      - If the real connector reports is_available() = True → use it
      - Otherwise fall back to the registered mock connector

    This means switching from mock → real is zero-code: just set the right
    environment variables and restart.
    """

    # ── Plugin Registry ─────────────────────────────────────────────────────
    # Maps connector type name → RealClass
    # Populated by connector files calling MCPRegistry.register() at import time.
    _registry: dict[str, Type[BaseMCPConnector]] = {}

    @classmethod
    def register(
        cls,
        connector_type: str,
        real_class: Type[BaseMCPConnector],
    ) -> None:
        """
        Register a connector type.

        Called at the bottom of each connector file:
            MCPRegistry.register("jira", JiraConnector)

        Must be called before MCPRegistry() is instantiated (i.e. at module import time).
        """
        cls._registry[connector_type] = real_class
        logger.debug("MCPRegistry: registered connector type '%s'", connector_type)

    def __init__(self) -> None:
        # Ensure all connector self-registrations have run before we try to build them.
        # The import triggers register() calls in each connector file.
        import backend.mcp.connectors  # noqa: F401

        connectors_cfg  = config.get_mcp_registry()
        # Read global concurrency cap from mcp_registry.yaml > max_concurrent_calls.
        # Falls back to _DEFAULT_MAX_CONCURRENT (3) if the key is absent so existing
        # deployments without the new config key continue to work unchanged.
        # We access the raw config dict via get_security_config-style helper — the
        # top-level mcp_registry key is not exposed by get_mcp_registry() which only
        # returns the `connectors` sub-dict. Using the internal _configs is lint-flagged
        # (SLF001); the cleanest fix is a dedicated accessor on ConfigLoader.
        raw_reg = config.get_mcp_registry_raw()
        max_concurrent = int(raw_reg.get("max_concurrent_calls", _DEFAULT_MAX_CONCURRENT))
        self._semaphore = asyncio.Semaphore(max_concurrent)
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
        """
        Instantiate a connector from the registry.

        Fail loud if the real connector reports is_available() = False.
        """
        connector_type = cfg.get("type", name)

        if connector_type not in self._registry:
            logger.warning(
                "MCPRegistry: unknown connector type '%s' for '%s' — "
                "make sure the connector file is imported in backend/mcp/connectors/__init__.py",
                connector_type, name,
            )
            return None

        real_class = self._registry[connector_type]

        try:
            real = real_class(name=name, connector_config=cfg)
        except Exception:
            logger.exception("MCPRegistry: failed to build connector '%s'", name)
            return None

        if real.is_available():
            logger.info(
                "MCPRegistry: using REAL %s connector (%s credentials configured)",
                name, connector_type.upper(),
            )
            return real

        # Fail loud on missing live credentials.
        raise RuntimeError(
            f"MCP connector '{name}' ({connector_type}) is enabled but has no live "
            f"credentials. Set the required env vars."
        )

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
                   e.g. [("jira", "search_tickets", {"query": "...", "project": "SDLC"})]

        Returns:
            list of results in the same order as calls.
            If a connector raises, its result is the Exception object (not re-raised).
            Callers must check: if not isinstance(result, Exception): use(result)
        """
        async def _one_call(connector_name: str, method: str, kwargs: dict) -> Any:
            async with self._semaphore:
                connector = self.get(connector_name)
                fn = getattr(connector, method)
                return await fn(**kwargs)

        tasks   = [_one_call(name, method, kwargs) for name, method, kwargs in calls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, (name, method, _) in enumerate(calls):
            if isinstance(results[i], Exception):
                logger.warning(
                    "MCPRegistry.call_parallel: %s.%s failed — %s",
                    name, method, results[i],
                )

        return list(results)
