"""
scripts/test_mcp_universal.py

Universal-MCP conformance test. Connects to the running SDLC MCP server with the
VANILLA `mcp` SDK client over streamable-HTTP — the exact transport Claude Desktop
and Cursor use — using ZERO app code. If this passes, any MCP host can use the
server, which is what "universal" means.

It checks all three MCP primitives:
    Tools     — discovery + read/write ToolAnnotations
    Resources — the sprint resource template + a live read
    Prompts   — the 4 starter prompts + a rendered get

Run (server must be up — `docker compose up -d mcp-server`, or
`python -m backend.mcp_server.server` locally):

    python scripts/test_mcp_universal.py
    MCP_SERVER_URL=http://127.0.0.1:8100/mcp python scripts/test_mcp_universal.py
"""
import asyncio
import os
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8100/mcp")


def _check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    return ok


async def main() -> int:
    print(f"Connecting to MCP server at {URL} with the vanilla MCP SDK client...\n")
    results: list[bool] = []

    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # ── 1. TOOLS + annotations ────────────────────────────────────────
            print("Primitive 1/3 — TOOLS")
            tools = (await session.list_tools()).tools
            by_name = {t.name: t for t in tools}
            results.append(_check("tools/list returns tools", len(tools) >= 20, f"{len(tools)} tools"))

            read_t = by_name.get("jira_search_tickets")
            write_t = by_name.get("jira_create_ticket")
            results.append(_check(
                "read tool has readOnlyHint=True",
                bool(read_t and read_t.annotations and read_t.annotations.readOnlyHint is True),
            ))
            results.append(_check(
                "write tool has destructiveHint=True",
                bool(write_t and write_t.annotations and write_t.annotations.destructiveHint is True),
            ))

            ping = await session.call_tool("ping", {"name": "universal-test"})
            ping_text = ping.content[0].text if ping.content else ""
            results.append(_check("tools/call ping round-trips", "pong" in ping_text, ping_text))

            # ── 2. RESOURCES ──────────────────────────────────────────────────
            print("\nPrimitive 2/3 — RESOURCES")
            templates = (await session.list_resource_templates()).resourceTemplates
            tmpl_uris = [t.uriTemplate for t in templates]
            results.append(_check(
                "resource template advertised",
                any("jira://sprint/" in u for u in tmpl_uris),
                ", ".join(tmpl_uris) or "none",
            ))
            res = await session.read_resource("jira://sprint/default/current")
            body = res.contents[0].text if res.contents else ""
            results.append(_check("resources/read returns sprint JSON", body.strip().startswith("{"),
                                  body[:60].replace("\n", " ")))

            # ── 3. PROMPTS ────────────────────────────────────────────────────
            print("\nPrimitive 3/3 — PROMPTS")
            prompts = (await session.list_prompts()).prompts
            pnames = {p.name for p in prompts}
            want = {"sprint_risk_review", "blocker_analysis", "release_readiness", "pr_review"}
            results.append(_check("prompts/list advertises starter prompts", want <= pnames,
                                  ", ".join(sorted(pnames)) or "none"))
            got = await session.get_prompt("pr_review", {"repo": "acme/api", "pr_number": "42"})
            rendered = got.messages[0].content.text if got.messages else ""
            results.append(_check("prompts/get renders with args", "42" in rendered and "acme/api" in rendered,
                                  rendered[:60].replace("\n", " ")))

    passed = sum(results)
    total = len(results)
    verdict = "UNIVERSAL MCP OK (all 3 primitives)" if passed == total else "SOME CHECKS FAILED"
    print(f"\n{'='*50}\n{passed}/{total} checks passed -- {verdict}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as exc:  # connection refused etc.
        print(f"\nERROR: could not run conformance test — {exc}")
        print("Is the MCP server running? Start it with:")
        print("    docker compose up -d mcp-server     (or)")
        print("    python -m backend.mcp_server.server")
        sys.exit(2)
