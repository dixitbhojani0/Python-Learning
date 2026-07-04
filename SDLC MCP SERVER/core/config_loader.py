"""
core/config_loader.py

Loads YAML config files at startup and provides typed access.
Hot-reloads within ~1 second when any YAML file changes (via watchdog).
"""
import logging
import threading
import yaml
from pathlib import Path
from typing import Any
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).parent.parent / "config"

CONFIG_FILES = {
    "prompts":      "prompts.yaml",
    "mcp_registry": "mcp_registry.yaml",
    "security":     "security.yaml",
}


class ConfigLoader:
    def __init__(self):
        self._configs: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._load_all()
        self._start_watcher()

    def _load_all(self):
        with self._lock:
            for key, filename in CONFIG_FILES.items():
                self._load_one(key, filename)
        logger.info("ConfigLoader: all YAML configs loaded from %s", CONFIG_DIR)

    def _load_one(self, key: str, filename: str):
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

    def _start_watcher(self):
        class _ReloadHandler(FileSystemEventHandler):
            def __init__(self, loader: "ConfigLoader"):
                self._loader = loader

            def on_modified(self, event):
                if event.is_directory:
                    return
                changed_file = Path(event.src_path).name
                for key, filename in CONFIG_FILES.items():
                    if filename == changed_file:
                        logger.info("ConfigLoader: detected change in %s — hot reloading", changed_file)
                        self._loader._load_one(key, filename)
                        break

        observer = Observer()
        observer.schedule(_ReloadHandler(self), str(CONFIG_DIR), recursive=False)
        observer.daemon = True
        observer.start()
        logger.info("ConfigLoader: watching %s for changes", CONFIG_DIR)

    def get_prompt(self, key: str, **kwargs) -> str:
        with self._lock:
            template = self._configs.get("prompts", {}).get(key, "")
        if not template:
            logger.warning("ConfigLoader: prompt key '%s' not found in prompts.yaml", key)
            return ""
        if kwargs:
            try:
                return template.format(**kwargs)
            except KeyError as e:
                logger.error("ConfigLoader: missing format key %s for prompt '%s'", e, key)
                return template
        return template

    def get_mcp_registry(self) -> dict:
        with self._lock:
            return self._configs.get("mcp_registry", {}).get("connectors", {})

    def get_mcp_registry_raw(self) -> dict:
        with self._lock:
            return self._configs.get("mcp_registry", {})

    def get_security_config(self) -> dict:
        with self._lock:
            return self._configs.get("security", {})

    def get_write_verbs(self) -> frozenset[str]:
        verbs = self.get_security_config().get("tool_safety", {}).get("write_verbs", [])
        return frozenset(str(v).lower() for v in verbs) if verbs else frozenset()


config = ConfigLoader()
