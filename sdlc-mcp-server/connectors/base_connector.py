"""
connectors/base_connector.py

Abstract base for all MCP connectors. Also owns the HTTP plumbing every
connector shares: the timeout policy, Basic-auth header builder, and a
lazily-created pooled httpx client (one TCP/TLS pool per connector instead
of a fresh handshake per call).
"""
import base64
from abc import ABC, abstractmethod

import httpx

# Shared timeout policy for all outbound SaaS calls.
HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=25.0, write=5.0, pool=5.0)


def basic_auth_header(email: str, token: str) -> str:
    """Standard Atlassian Basic auth: base64(email:api_token)."""
    encoded = base64.b64encode(f"{email}:{token}".encode()).decode()
    return f"Basic {encoded}"


class BaseMCPConnector(ABC):
    def __init__(self, name: str, connector_config: dict):
        self._name   = name
        self._config = connector_config
        self._http: httpx.AsyncClient | None = None

    @property
    def connector_name(self) -> str:
        return self._name

    @property
    def config(self) -> dict:
        return self._config

    @property
    def http(self) -> httpx.AsyncClient:
        """Shared pooled client. Subclasses must set self._headers in __init__.
        Never closed explicitly — lives for the server process lifetime."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                headers=getattr(self, "_headers", {}),
                timeout=HTTP_TIMEOUT,
                follow_redirects=True,
            )
        return self._http

    @abstractmethod
    def is_available(self) -> bool:
        ...
