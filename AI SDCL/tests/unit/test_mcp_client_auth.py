"""
tests/unit/test_mcp_client_auth.py
Unit tests for backend/mcp_client/client.py _is_auth_error() and ainvoke_tool() —
the check + retry that let a stale service token (MCP server/Redis restarted,
wiping tokens our cache doesn't know about yet) self-heal on the next call
instead of failing for up to an hour.
No Docker, no real HTTP.
"""
import pytest
import httpx
from unittest.mock import AsyncMock, patch

from backend.mcp_client import client
from backend.mcp_client.client import _is_auth_error, ainvoke_tool


def _http_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://sdlc-mcp-server:8100/mcp")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


def test_bare_401_is_auth_error():
    assert _is_auth_error(_http_error(401)) is True


def test_bare_403_is_not_auth_error():
    assert _is_auth_error(_http_error(403)) is False


def test_401_nested_in_exception_group_is_auth_error():
    group = BaseExceptionGroup("unhandled errors in a TaskGroup", [_http_error(401)])
    assert _is_auth_error(group) is True


def test_unrelated_exception_group_is_not_auth_error():
    group = BaseExceptionGroup("unhandled errors in a TaskGroup", [ValueError("nope")])
    assert _is_auth_error(group) is False


# ── ainvoke_tool retry-on-401 ──────────────────────────────────────────────────

class _FakeTool:
    """Stand-in for a langchain BaseTool: first call 401s (stale token), second succeeds."""
    name = "jira_get_ticket"

    def __init__(self):
        self.calls = 0

    async def ainvoke(self, args):
        self.calls += 1
        if self.calls == 1:
            raise BaseExceptionGroup("unhandled errors in a TaskGroup", [_http_error(401)])
        return {"ticket": "SDLC-1"}


@pytest.mark.asyncio
async def test_ainvoke_tool_refreshes_and_retries_once_on_401():
    tool = _FakeTool()
    with patch.object(client, "reload_servers") as mock_reload, \
         patch.object(client, "_fetch_all_tools", new=AsyncMock(return_value=[tool])):
        result = await ainvoke_tool(tool, {"ticket_id": "SDLC-1"})

    assert result == {"ticket": "SDLC-1"}
    assert tool.calls == 2                # 401 once, then a real retry — not swallowed, not looped
    mock_reload.assert_called_once()       # stale client/token dropped, not just logged
    assert client._service_token is None   # forces a fresh token fetch on the next _load_servers()


@pytest.mark.asyncio
async def test_ainvoke_tool_reraises_non_auth_errors_without_retry():
    tool = _FakeTool()

    async def _boom(args):
        raise ValueError("unrelated failure")
    tool.ainvoke = _boom

    with patch.object(client, "reload_servers") as mock_reload:
        with pytest.raises(ValueError):
            await ainvoke_tool(tool, {})
    mock_reload.assert_not_called()        # non-auth errors must NOT trigger a token reset
