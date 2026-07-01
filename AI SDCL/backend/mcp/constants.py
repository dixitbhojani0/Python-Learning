"""
backend/mcp/constants.py

Shared MCP tool-safety constants — the single source of truth for
write-verb classification used by BOTH the MCP server (ToolAnnotations)
and the MCP client (autonomous-loop gate).

Why here, not in client.py?
  The MCP server (mcp_server/server.py) needs is_write_tool() to stamp
  ToolAnnotations at startup.  Importing it from mcp_client/client.py
  created a cross-layer dependency (server → client).  Moving it here
  (mcp/ — the shared connector layer) breaks the cycle: both client and
  server import from this neutral module.

Write-verb list is loaded from config/security.yaml (tool_safety.write_verbs)
at first call, with the tuple below as a hardcoded fallback so the system
boots even if security.yaml is missing or unparseable.
"""
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

# ── Hardcoded fallback ────────────────────────────────────────────────────────
# Must stay in sync with the write_verbs list in config/security.yaml.
# The config-loaded set (below) takes precedence when available.
_WRITE_VERBS_FALLBACK: frozenset[str] = frozenset({
    "create", "update", "delete", "assign", "reassign", "deassign",
    "approve", "reject", "request_changes", "merge", "send", "post", "close",
})


@lru_cache(maxsize=1)
def _load_write_verbs() -> frozenset[str]:
    """
    Load write verbs from config/security.yaml > tool_safety > write_verbs.

    lru_cache(maxsize=1) means this is computed once per process — the same
    config-reload guarantee the rest of the system relies on. If the key is
    absent or the config file fails, fall back to the hardcoded set.
    """
    try:
        from backend.core.config_loader import config  # deferred — avoids circular import at module load
        sec = config.get_security_config()
        verbs = sec.get("tool_safety", {}).get("write_verbs", [])
        if verbs:
            result = frozenset(str(v).lower() for v in verbs)
            logger.debug("mcp.constants: loaded %d write verbs from security.yaml", len(result))
            return result
    except Exception:
        logger.warning("mcp.constants: could not load write_verbs from config — using fallback", exc_info=True)
    return _WRITE_VERBS_FALLBACK


def is_write_tool(name: str) -> bool:
    """
    Return True if a tool name denotes a state-changing (write) action.

    Checks whether any configured write verb appears as a substring of the
    (lowercase) tool name.  This is the canonical write-tool classifier shared
    by:
      - mcp_client/client.py  — excludes write tools from the autonomous gather loop
      - mcp_server/server.py  — stamps ToolAnnotations.destructiveHint on every tool

    Fail-safe direction: an unrecognised tool containing a write verb is
    treated as a write tool (excluded from autonomous use) rather than silently
    allowed through.
    """
    n = name.lower()
    return any(v in n for v in _load_write_verbs())
