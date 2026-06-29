"""
backend/mcp_server/server.py

The SDLC MCP server — a real Model Context Protocol server (JSON-RPC) exposed
over streamable-HTTP, built with FastMCP (from the official `mcp` SDK).

Run it as its own service:
    python -m backend.mcp_server.server

The host (FastAPI/LangGraph) talks to it via backend/mcp_client, which does
`tools/list` and lets the LLM choose + call tools. Because it's a separate
process over HTTP, server and host are decoupled and independently scalable.

STEP 0 (this file, now): one diagnostic `ping` tool only — just enough to prove
the protocol round-trips (tools/list → LLM picks → tools/call) before we wrap
any real connector. STEP 1 imports tool modules (Jira/GitHub) and registers them.

Config is env-driven (no hardcoded host/port):
    MCP_SERVER_HOST  (default 127.0.0.1)
    MCP_SERVER_PORT  (default 8100)
The streamable-HTTP endpoint is mounted at /mcp, so the client URL is
    http://{MCP_SERVER_HOST}:{MCP_SERVER_PORT}/mcp
"""
import logging
import os

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sdlc_mcp_server")

# ── Server config (env-driven) ────────────────────────────────────────────────
_HOST = os.getenv("MCP_SERVER_HOST", "127.0.0.1")
_PORT = int(os.getenv("MCP_SERVER_PORT", "8100"))

# FastMCP carries host/port in its own settings; the HTTP app mounts at /mcp.
mcp = FastMCP("sdlc-mcp", host=_HOST, port=_PORT)


# ── Diagnostic tool ───────────────────────────────────────────────────────────
# The docstring IS the tool description the LLM reads to decide whether to call
# it — this is the heart of "the LLM picks the tool", so write it like a spec.
@mcp.tool()
def ping(name: str = "world") -> str:
    """Health-check the SDLC MCP server. Returns a 'pong' greeting for `name`.

    Use this only to confirm the MCP server is reachable and tool-calling works.
    """
    logger.info("tool ping(name=%r) called", name)
    return f"pong: {name} — sdlc-mcp server is alive"


# ── Real tools (Step 1): wrap the existing connectors ─────────────────────────
# One shared MCPRegistry builds every connector (real when creds are set, mock
# otherwise) — the server serves real or mock data transparently. Each tool
# module delegates to it; the server just composes them (decoupling).
from backend.mcp.registry import MCPRegistry
from backend.mcp_server.tools import (
    jira_tools,
    github_tools,
    slack_tools,
    confluence_tools,
    teams_tools,
    drive_tools,
)

_registry = MCPRegistry()
# Read tools — safe for the autonomous gather loop.
for _module in (jira_tools, github_tools, slack_tools, confluence_tools, teams_tools, drive_tools):
    _module.register(mcp, _registry)
# Write tools — state-changing. Exposed on the server, but the client excludes
# them from the autonomous loop (is_write_tool); they run only via approved HITL.
for _module in (jira_tools, github_tools, slack_tools):
    _module.register_writes(mcp, _registry)


# ── ToolAnnotations (MCP March-2025 spec) ─────────────────────────────────────
# Stamp every tool with protocol-level read/write metadata so third-party hosts
# (Claude Desktop, Cursor) get the safety signal from `tools/list`, not just our
# app-level verb detection. We reuse the SAME classifier the client uses
# (is_write_tool) so the protocol hint can never drift from the autonomous-loop
# gate. destructiveHint=True is conservative: a few writes (create/send/post) are
# technically additive, but flagging all writes destructive is the safe direction
# for a host deciding whether to confirm with the user.
# ponytail: one loop here beats an annotations= kwarg on ~30 decorators.
from backend.mcp_client.client import is_write_tool


def _annotate_tools() -> None:
    for _tool in mcp._tool_manager.list_tools():
        write = is_write_tool(_tool.name)
        _tool.annotations = ToolAnnotations(
            title=_tool.name,
            readOnlyHint=not write,
            destructiveHint=write,
        )
    logger.info("Stamped ToolAnnotations on %d tools", len(mcp._tool_manager.list_tools()))


_annotate_tools()


# ── MCP Resources (the 2nd MCP primitive) ─────────────────────────────────────
# Tools are model-driven actions; a Resource is host-readable context addressed by
# URI. Exposing the current sprint as a resource is what makes the server
# MCP-"universal": a third-party host (Claude Desktop, Cursor) can attach
# `jira://sprint/SDLC/current` as context without the model having to call a tool.
# Backed by the SAME connector method jira_get_sprint_board uses — no new logic.
@mcp.resource("jira://sprint/{project}/current", mime_type="application/json")
async def current_sprint(project: str) -> dict:
    """Current sprint board for `project` (stats, completion %, risk).

    Read-only context resource. `project` is a Jira project key (e.g. "SDLC");
    pass "default" to use the server's configured default project.
    """
    proj = "" if project.lower() == "default" else project
    logger.info("resource jira://sprint/%s/current", project)
    return await _registry.get("jira").get_sprint_board(proj)


# ── MCP Prompts (the 3rd MCP primitive) ───────────────────────────────────────
# Expose the assistant's user-facing capabilities as MCP prompts so a third-party
# host can offer them as ready-made starter prompts. Each pulls its template from
# config/prompts.yaml (mcp_prompt_*) — the prompt text stays config-driven, not
# hardcoded, and only user-facing templates are exposed (not internal judges).
from backend.core.config_loader import config as _config


@mcp.prompt(title="Sprint risk review")
def sprint_risk_review(project: str = "default") -> str:
    """Assess current sprint delivery risk for a Jira project."""
    return _config.get_prompt("mcp_prompt_sprint_risk_review", project=project)


@mcp.prompt(title="Blocker analysis")
def blocker_analysis(project: str = "default") -> str:
    """List and prioritise everything currently blocking a project."""
    return _config.get_prompt("mcp_prompt_blocker_analysis", project=project)


@mcp.prompt(title="Release readiness")
def release_readiness(project: str = "default") -> str:
    """Go / no-go assessment of whether a project is ready to release."""
    return _config.get_prompt("mcp_prompt_release_readiness", project=project)


@mcp.prompt(title="PR review")
def pr_review(repo: str, pr_number: str) -> str:
    """Review a specific GitHub pull request and recommend approve / changes."""
    return _config.get_prompt("mcp_prompt_pr_review", repo=repo, pr_number=pr_number)


def main() -> None:
    """Start the MCP server over streamable-HTTP.

    Transport string differs across `mcp` SDK versions ("http" in current
    releases, "streamable-http" in older ones) — try both so the spike isn't
    blocked by a naming change. A valid transport blocks here serving requests;
    an invalid one raises immediately, so we fall through to the next candidate.
    """
    logger.info("Starting SDLC MCP server on http://%s:%d/mcp", _HOST, _PORT)
    last_err: Exception | None = None
    # "streamable-http" = the mcp SDK 1.x value; "http" = newer alias (fallback).
    for transport in ("streamable-http", "http"):
        try:
            mcp.run(transport=transport)  # blocks while serving
            return
        except (ValueError, KeyError) as err:
            last_err = err
            logger.warning("transport=%r not accepted (%s) — trying next", transport, err)
    raise RuntimeError(f"No supported streamable-HTTP transport found: {last_err}")


if __name__ == "__main__":
    main()
