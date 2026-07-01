"""
backend/core/config_loader.py

Loads all YAML config files at startup and provides typed access.
Hot-reloads within ~1 second when any YAML file changes (via watchdog).

Usage:
    from backend.core.config_loader import config
    prompt = config.get_prompt("system_prompt")
    agents = config.get_agents()
"""
import yaml
import logging
import threading
from pathlib import Path
from typing import Any
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logger = logging.getLogger(__name__)

# Import deferred to break potential circular imports at module load.
# prompt_safety only imports stdlib (logging, re) so there is no cycle,
# but we import lazily to keep module-level side effects predictable.
def _get_safety_guard():
    from backend.core.prompt_safety import safety_guard  # noqa: PLC0415
    return safety_guard


# ── Path to the config directory (relative to project root)
CONFIG_DIR = Path(__file__).parent.parent.parent / "config"

# ── All YAML files we load
CONFIG_FILES = {
    "prompts":      "prompts.yaml",
    "agents":       "agents.yaml",
    "llm":          "llm.yaml",
    "mcp_registry": "mcp_registry.yaml",
    "mcp_clients":  "mcp_clients.yaml",   # outbound MCP host connections (admin-editable)
    "rag_sources":  "rag_sources.yaml",
    "redis":        "redis.yaml",
    "chunking":     "chunking.yaml",
    "security":     "security.yaml",
}


class ConfigLoader:
    """
    Singleton config loader. Reads all YAML files once at startup,
    then watches for file changes and hot-reloads automatically.

    Design principle: agents and components never read YAML files directly.
    They always call config.get_*(). This way, hot-reload is transparent.
    """

    def __init__(self):
        self._configs: dict[str, Any] = {}
        self._lock = threading.RLock()        # thread-safe reads during reload
        self._load_all()
        self._start_watcher()

    def _load_all(self):
        """Load every YAML file listed in CONFIG_FILES."""
        with self._lock:
            for key, filename in CONFIG_FILES.items():
                self._load_one(key, filename)
        logger.info("ConfigLoader: all YAML configs loaded from %s", CONFIG_DIR)

    def _load_one(self, key: str, filename: str):
        """Load a single YAML file into memory."""
        filepath = CONFIG_DIR / filename
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                self._configs[key] = yaml.safe_load(f) or {}
            logger.debug("ConfigLoader: loaded %s", filename)
        except FileNotFoundError:
            logger.error("ConfigLoader: config file not found: %s", filepath)
            self._configs[key] = {}
        except yaml.YAMLError as e:
            logger.error("ConfigLoader: YAML parse error in %s: %s", filename, e)
            # Keep old config on parse error — don't break a running app

    def _start_watcher(self):
        """
        Use watchdog to watch the config directory.
        When any YAML file changes, reload it within ~1 second.
        This means you can edit prompts.yaml and the running app picks it up
        without a restart.
        """
        class _ReloadHandler(FileSystemEventHandler):
            def __init__(self, loader: "ConfigLoader"):
                self._loader = loader

            def on_modified(self, event):
                if event.is_directory:
                    return
                changed_file = Path(event.src_path).name
                # Find which config key this file belongs to
                for key, filename in CONFIG_FILES.items():
                    if filename == changed_file:
                        logger.info("ConfigLoader: detected change in %s — hot reloading", changed_file)
                        self._loader._load_one(key, filename)
                        break

        observer = Observer()
        observer.schedule(_ReloadHandler(self), str(CONFIG_DIR), recursive=False)
        observer.daemon = True      # dies with the main process
        observer.start()
        logger.info("ConfigLoader: watching %s for changes", CONFIG_DIR)

    # ─────────────────────────────────────────────
    #  Public API — typed accessors
    # ─────────────────────────────────────────────

    def get_prompt(self, key: str, **kwargs) -> str:
        """
        Get a prompt template by key from prompts.yaml.
        Optionally format it with kwargs.

        Example:
            config.get_prompt("ticket_proposal", title="CORS bug", assignee="DevOps")
        """
        with self._lock:
            template = self._configs.get("prompts", {}).get(key, "")
        if not template:
            logger.warning("ConfigLoader: prompt key '%s' not found in prompts.yaml", key)
            return ""
        if kwargs:
            try:
                # Sanitize all string kwargs before .format() to prevent prompt injection
                # via user-derived content (query, Jira titles, Slack messages, etc.).
                # Non-string values (int, float, dict) are passed through unchanged.
                guard = _get_safety_guard()
                safe_kwargs = {
                    k: guard.sanitize(str(v)) if isinstance(v, str) else v
                    for k, v in kwargs.items()
                }
                return template.format(**safe_kwargs)
            except KeyError as e:
                logger.error("ConfigLoader: missing format key %s for prompt '%s'", e, key)
                return template
        return template

    def get_agents(self) -> dict:
        """Returns the full agents config dict from agents.yaml."""
        with self._lock:
            return self._configs.get("agents", {}).get("agents", {})

    def get_agent(self, name: str) -> dict:
        """Returns config for a specific agent by name."""
        return self.get_agents().get(name, {})

    def get_mcp_registry(self) -> dict:
        """Returns MCP connector definitions from mcp_registry.yaml."""
        with self._lock:
            return self._configs.get("mcp_registry", {}).get("connectors", {})

    def get_mcp_registry_raw(self) -> dict:
        """
        Returns the full top-level mcp_registry.yaml dict (not just the connectors sub-dict).

        Use this to read global registry settings such as `max_concurrent_calls`.
        get_mcp_registry() only exposes the `connectors` section; this exposes everything.
        """
        with self._lock:
            return self._configs.get("mcp_registry", {})

    def get_mcp_clients(self) -> dict:
        """
        Returns the `mcpServers` dict from mcp_clients.yaml — admin-managed
        outbound MCP host connections. Shape matches the Claude Desktop /
        Cursor / Antigravity standard. Empty by default; populated via the
        admin UI (or hand-edited and hot-reloaded by the watchdog).
        """
        with self._lock:
            return self._configs.get("mcp_clients", {}).get("mcpServers", {}) or {}

    def get_llm_config(self) -> dict:
        """Returns LLM provider config from llm.yaml."""
        with self._lock:
            return self._configs.get("llm", {})

    def get_rag_config(self) -> dict:
        """Returns RAG pipeline config from rag_sources.yaml."""
        with self._lock:
            return self._configs.get("rag_sources", {})

    def get_redis_config(self) -> dict:
        """Returns Redis config from redis.yaml."""
        with self._lock:
            return self._configs.get("redis", {})

    def get_chunking_config(self) -> dict:
        """Returns chunking strategy config from chunking.yaml."""
        with self._lock:
            return self._configs.get("chunking", {})

    def get_security_config(self) -> dict:
        """Returns security config from security.yaml (injection patterns, etc.)."""
        with self._lock:
            return self._configs.get("security", {})

    def get_write_verbs(self) -> frozenset[str]:
        """
        Returns the set of write-action verb substrings from security.yaml.

        Used by backend/mcp/constants.py to classify tool names as read or write.
        Falls back to an empty frozenset if the key is absent (constants.py has
        its own hardcoded fallback, so the system stays safe).
        """
        verbs = self.get_security_config().get("tool_safety", {}).get("write_verbs", [])
        return frozenset(str(v).lower() for v in verbs) if verbs else frozenset()

    def get_routing_config(self) -> dict:
        """
        Returns the routing & tool-gathering tuning block from llm.yaml.

        Keys: routing.semantic_router.{min_score, margin}
              routing.tool_gathering.{max_iterations, max_parallel}
              memory.{recent_window, summary_threshold}
              evaluation.reflect_faithfulness_threshold
        """
        with self._lock:
            return self._configs.get("llm", {})

    def get_temperature(self, task: str) -> float:
        """
        Get temperature for a specific task type.
        Falls back to 0.1 if task not found.

        Example:
            temp = config.get_temperature("response_generation")  # → 0.4
        """
        llm = self.get_llm_config()
        return llm.get("primary", {}).get("temperatures", {}).get(task, 0.1)

    def get_confidence_thresholds(self) -> dict:
        """Returns RAG confidence threshold values."""
        rag = self.get_rag_config()
        return rag.get("retrieval", {}).get("confidence", {})

    def get_agent_triggers(self, agent_name: str) -> list[str]:
        """Returns keyword triggers for an agent (fallback classifier only)."""
        agent = self.get_agent(agent_name)
        return agent.get("trigger_keywords", [])

    def get_agent_routing_description(self, agent_name: str) -> str:
        """Returns the routing_description for an agent (read by LLM supervisor)."""
        return self.get_agent(agent_name).get("routing_description", "")

    def is_agent_enabled(self, agent_name: str) -> bool:
        """Check if an agent is enabled in config."""
        return self.get_agent(agent_name).get("enabled", False)


# ── Module-level singleton — import this everywhere
config = ConfigLoader()
