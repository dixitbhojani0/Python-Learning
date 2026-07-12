"""
registry.py

MCPRegistry — instantiates and manages all MCP connectors.
Agents call: registry.get("jira").search_tickets(query)
"""
import asyncio
import logging
from typing import Any, Type

from core.config_loader import config
from connectors.base_connector import BaseMCPConnector

logger = logging.getLogger(__name__)

_DEFAULT_MAX_CONCURRENT = 3


class MCPRegistry:
    _registry: dict[str, Type[BaseMCPConnector]] = {}

    @classmethod
    def register(cls, connector_type: str, real_class: Type[BaseMCPConnector]) -> None:
        cls._registry[connector_type] = real_class
        logger.debug("MCPRegistry: registered connector type '%s'", connector_type)

    def __init__(self) -> None:
        connectors_cfg = config.get_mcp_registry()
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

        logger.info(
            "MCPRegistry: %d connectors registered: %s",
            len(self._connectors),
            list(self._connectors.keys()),
        )

    def _build_connector(self, name: str, cfg: dict) -> BaseMCPConnector | None:
        connector_type = cfg.get("type", name)
        if connector_type not in self._registry:
            logger.warning(
                "MCPRegistry: unknown connector type '%s' for '%s' — "
                "make sure the connector file is imported in connectors/__init__.py",
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
            logger.info("MCPRegistry: using REAL %s connector", name)
            return real

        raise RuntimeError(
            f"MCP connector '{name}' ({connector_type}) is enabled but has no live credentials. "
            f"Set the required env vars or disable it in config/mcp_registry.yaml."
        )

    def get(self, name: str) -> BaseMCPConnector:
        if name not in self._connectors:
            raise KeyError(
                f"MCPRegistry: connector '{name}' not found. "
                f"Registered: {list(self._connectors.keys())}"
            )
        return self._connectors[name]

    def has(self, name: str) -> bool:
        return name in self._connectors and self._connectors[name].is_available()

    async def call_parallel(self, calls: list[tuple[str, str, dict]]) -> list[Any]:
        async def _one(connector_name: str, method: str, kwargs: dict) -> Any:
            async with self._semaphore:
                return await getattr(self.get(connector_name), method)(**kwargs)

        results = await asyncio.gather(
            *[_one(n, m, kw) for n, m, kw in calls],
            return_exceptions=True,
        )
        for i, (name, method, _) in enumerate(calls):
            if isinstance(results[i], Exception):
                logger.warning("MCPRegistry.call_parallel: %s.%s failed — %s", name, method, results[i])
        return list(results)
