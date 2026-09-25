"""
Shared pytest fixtures. Importing backend.app.main (directly or transitively)
triggers LLM provider registration exactly once per test process — individual
tests that need a clean registry use the `isolated_registry` fixture below
instead of relying on process-wide state.
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from backend.app.adapters.llm.registry import LLMRegistry
from backend.tests.unit._mcp_test_server import PORT as MCP_TEST_SERVER_PORT

_MCP_TEST_SERVER_SCRIPT = Path(__file__).parent / "unit" / "_mcp_test_server.py"


@pytest.fixture
def isolated_registry():
    """
    Snapshot the registry, yield control, then restore it — so a test that
    calls LLMRegistry.reset() (or registers a throwaway provider) can never
    leak state into a test that runs after it, regardless of execution order.
    """
    snapshot = dict(LLMRegistry._registry)
    yield LLMRegistry
    LLMRegistry._registry = snapshot


def _wait_until_accepting(host: str, port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"nothing listening on {host}:{port} after {timeout}s")


@pytest.fixture(scope="session")
def mcp_test_server():
    """
    A real MCP server, hosted as a genuine separate OS process — see
    tests/unit/_mcp_test_server.py's own docstring for why not in-process.
    Opt in per test module with `pytestmark = pytest.mark.usefixtures("mcp_test_server")`,
    not autouse, since most of the suite has nothing to do with MCP.
    """
    process = subprocess.Popen([sys.executable, str(_MCP_TEST_SERVER_SCRIPT)])
    try:
        _wait_until_accepting("127.0.0.1", MCP_TEST_SERVER_PORT)
        yield
    finally:
        process.terminate()
        process.wait(timeout=5)
