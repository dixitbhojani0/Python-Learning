"""
backend/app/api/mcp_routes.py

Phase 21: connect this tenant to external MCP (Model Context Protocol)
servers, discover their tools, and manually invoke one for testing —
"Connect + discover + manual test" was the explicitly confirmed scope for
this pass, not the larger "wire MCP tools into the autonomous chat loop"
option.

That larger option is a real architectural ceiling, not laziness: every
chat turn's tool use in this platform goes through tools/intent.py's
deterministic regex matching (see its own docstring), and none of the 3 LLM
adapters (mock/Gemini/Groq) implement real structured function-calling. A
regex can't be written ahead of time for an arbitrary tool an admin connects
at runtime from an external server. Making MCP tools chat-callable needs
that LLM function-calling rewrite first — a separate, much larger piece of
work the user explicitly deferred, not something this router silently
half-does.

Reuses "users:read"/"users:write" (server management is tenant-wide admin
config, the same call admin_routes.py's own tenant-config endpoint already
made) rather than a new permission namespace (§30).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.deps import Principal, get_scoped_session, require_permission
from backend.app.core.i18n import DEFAULT_LOCALE, t
from backend.app.db.models import McpServer
from backend.app.mcp.client import McpConnectionError, call_tool, discover_tools

router = APIRouter(prefix="/v1/admin/mcp-servers", tags=["mcp"])


class McpServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    url: str = Field(min_length=1, max_length=2048)
    auth_token: str | None = None
    enabled: bool = True


class McpServerOut(BaseModel):
    id: uuid.UUID
    name: str
    url: str
    enabled: bool
    created_at: datetime
    # auth_token is deliberately never serialized back to the client once saved —
    # same "write-only secret" convention as hashed_password never appearing in UserOut.

    model_config = {"from_attributes": True}


class McpToolOut(BaseModel):
    name: str
    description: str | None
    input_schema: dict[str, Any]


class DiscoverToolsOut(BaseModel):
    tools: list[McpToolOut]


class CallToolRequest(BaseModel):
    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class CallToolOut(BaseModel):
    is_error: bool
    content: list[dict[str, Any]]


def _locale(accept_language: str | None) -> str:
    return (accept_language or DEFAULT_LOCALE).split(",")[0].split("-")[0]


async def _get_server_or_404(session: AsyncSession, server_id: uuid.UUID, locale: str) -> McpServer:
    server = await session.get(McpServer, server_id)
    if server is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=t("admin.mcp_server_not_found", locale=locale))
    return server


@router.post("", response_model=McpServerOut, status_code=status.HTTP_201_CREATED)
async def create_mcp_server(
    body: McpServerCreate,
    principal: Principal = Depends(require_permission("users:write")),
    session: AsyncSession = Depends(get_scoped_session),
) -> McpServerOut:
    server = McpServer(
        id=uuid.uuid4(),
        tenant_id=principal.tenant_id,
        name=body.name,
        url=body.url,
        auth_token=body.auth_token,
        enabled=body.enabled,
    )
    session.add(server)
    await session.flush()
    return McpServerOut.model_validate(server)


@router.get("", response_model=list[McpServerOut])
async def list_mcp_servers(
    _principal: Principal = Depends(require_permission("users:read")),
    session: AsyncSession = Depends(get_scoped_session),
) -> list[McpServerOut]:
    servers = (await session.execute(select(McpServer).order_by(McpServer.created_at.desc()))).scalars().all()
    return [McpServerOut.model_validate(s) for s in servers]


@router.delete("/{server_id}")
async def delete_mcp_server(
    server_id: uuid.UUID,
    _principal: Principal = Depends(require_permission("users:write")),
    session: AsyncSession = Depends(get_scoped_session),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> dict:
    server = await _get_server_or_404(session, server_id, _locale(accept_language))
    await session.delete(server)
    return {"deleted": str(server_id)}


@router.post("/{server_id}/test-connection", response_model=DiscoverToolsOut)
async def test_mcp_server_connection(
    server_id: uuid.UUID,
    _principal: Principal = Depends(require_permission("users:read")),
    session: AsyncSession = Depends(get_scoped_session),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> DiscoverToolsOut:
    locale = _locale(accept_language)
    server = await _get_server_or_404(session, server_id, locale)
    try:
        tools = await discover_tools(server.url, server.auth_token)
    except McpConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=t("admin.mcp_connection_failed", locale=locale, detail=str(exc))
        ) from exc
    return DiscoverToolsOut(tools=[McpToolOut(**tool) for tool in tools])


@router.post("/{server_id}/call-tool", response_model=CallToolOut)
async def call_mcp_server_tool(
    server_id: uuid.UUID,
    body: CallToolRequest,
    _principal: Principal = Depends(require_permission("users:write")),
    session: AsyncSession = Depends(get_scoped_session),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> CallToolOut:
    locale = _locale(accept_language)
    server = await _get_server_or_404(session, server_id, locale)
    if not server.enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=t("admin.mcp_server_disabled", locale=locale))
    try:
        result = await call_tool(server.url, body.tool_name, body.arguments, server.auth_token)
    except McpConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=t("admin.mcp_connection_failed", locale=locale, detail=str(exc))
        ) from exc
    return CallToolOut(**result)
