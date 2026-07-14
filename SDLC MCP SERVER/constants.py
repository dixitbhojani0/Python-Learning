"""
constants.py

Shared MCP tool-safety constants — canonical source for write-verb classification.
Used by server.py to stamp ToolAnnotations.destructiveHint on every tool.
"""
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

_WRITE_VERBS_FALLBACK: frozenset[str] = frozenset({
    "create", "update", "delete", "assign", "reassign", "deassign",
    "approve", "reject", "request_changes", "merge", "send", "post", "close",
    "comment",   # jira_add_comment writes to Jira
})


@lru_cache(maxsize=1)
def _load_write_verbs() -> frozenset[str]:
    try:
        from core.config_loader import config
        sec = config.get_security_config()
        verbs = sec.get("tool_safety", {}).get("write_verbs", [])
        if verbs:
            result = frozenset(str(v).lower() for v in verbs)
            logger.debug("constants: loaded %d write verbs from security.yaml", len(result))
            return result
    except Exception:
        logger.warning("constants: could not load write_verbs from config — using fallback", exc_info=True)
    return _WRITE_VERBS_FALLBACK


def is_write_tool(name: str) -> bool:
    """Return True if tool name contains a write verb (state-changing action)."""
    n = name.lower()
    return any(v in n for v in _load_write_verbs())
