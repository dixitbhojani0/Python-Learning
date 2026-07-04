"""
connectors/base_connector.py

Abstract base for all MCP connectors.
"""
from abc import ABC, abstractmethod


class BaseMCPConnector(ABC):
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
        ...
