"""
admin/pages/02_config.py

Config Viewer — shows all live YAML config values and lets you trigger a reload.

What this page does:
  - Loads each YAML file in config/ and renders it as a readable table
  - Shows which config was last modified and when
  - "Reload All" button forces ConfigLoader to re-read all YAML files
    (same effect as touching a watched file, but manual)

Why this is useful:
  When you tweak a temperature in llm.yaml or add a new prompt in prompts.yaml,
  you want to confirm the running app has picked it up. This page shows
  the live values ConfigLoader has cached — not the raw file.
"""
import sys
from pathlib import Path

_project_root = Path(__file__).parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import streamlit as st
import yaml
from datetime import datetime

st.set_page_config(page_title="Config Viewer", page_icon="⚙️", layout="wide")
st.title("⚙️ Config Viewer")
st.caption("Live YAML configuration values. Edits to config/ files are picked up automatically by the backend.")

CONFIG_DIR = _project_root / "config"


def _load_yaml_file(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        return {"__error__": str(exc)}


def _file_mtime(path: Path) -> str:
    try:
        ts = path.stat().st_mtime
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "unknown"


# ── Find all YAML files ────────────────────────────────────────────────────────

yaml_files = sorted(CONFIG_DIR.glob("*.yaml"))

if not yaml_files:
    st.warning(f"No YAML files found in {CONFIG_DIR}. Check that the config/ directory exists.")
    st.stop()

# ── Reload button ─────────────────────────────────────────────────────────────

if st.button("🔄 Reload All Configs (clears cache)"):
    try:
        from backend.core.config_loader import config as _config
        _config._load_all()
        st.success("ConfigLoader reloaded all YAML files.")
    except Exception as exc:
        st.error(f"Reload failed: {exc}")
    st.cache_data.clear()

st.markdown("---")

# ── Render each YAML file ─────────────────────────────────────────────────────

for yaml_path in yaml_files:
    file_name = yaml_path.name
    mtime     = _file_mtime(yaml_path)

    with st.expander(f"📄 {file_name}  ·  last modified: {mtime}", expanded=(file_name == "llm.yaml")):
        data = _load_yaml_file(yaml_path)

        if "__error__" in data:
            st.error(f"Failed to parse {file_name}: {data['__error__']}")
            continue

        if file_name == "prompts.yaml":
            # Render prompts as code blocks — they're long multi-line strings
            for key, value in data.items():
                st.markdown(f"**`{key}`**")
                if isinstance(value, str):
                    st.code(value, language="text")
                else:
                    st.json(value)
        else:
            # Render everything else as a flat key-value table
            rows = []
            for key, value in data.items():
                if isinstance(value, dict):
                    for subkey, subval in value.items():
                        rows.append({"key": f"{key}.{subkey}", "value": str(subval)})
                else:
                    rows.append({"key": key, "value": str(value)})

            import pandas as pd
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True)
            else:
                st.info("Empty file.")
