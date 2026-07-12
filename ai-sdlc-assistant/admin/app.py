"""
admin/app.py

Streamlit admin panel — home/landing page.

Run with:
    cd ai-sdlc-assistant
    streamlit run admin/app.py

Pages (auto-discovered from admin/pages/):
  01_rag_manager.py  — Qdrant chunk browser + ingest trigger
  02_config.py       — Live YAML config viewer
  03_sessions.py     — Recent conversation session log

This file is intentionally thin — it just sets page config and
shows a system health summary so the admin can see at a glance
whether the services are up.
"""
import sys
from pathlib import Path

# Add project root to path so backend imports work from admin/
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import streamlit as st

st.set_page_config(
    page_title="AI SDLC Assistant — Admin",
    page_icon="🛠️",
    layout="wide",
)

st.title("🛠️ AI SDLC Assistant — Admin Panel")
st.caption("Internal operations panel. Not for end-users.")

st.markdown("---")

# ── Service health checks ─────────────────────────────────────────────────────

st.subheader("Service Health")

col1, col2, col3 = st.columns(3)


def _check_qdrant() -> tuple[bool, str]:
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(host="localhost", port=6333, timeout=2)
        collections = client.get_collections().collections
        return True, f"{len(collections)} collection(s)"
    except Exception as exc:
        return False, str(exc)


def _check_redis() -> tuple[bool, str]:
    try:
        import redis
        r = redis.Redis(host="localhost", port=6379, socket_connect_timeout=2)
        r.ping()
        info = r.info("keyspace")
        key_count = sum(v.get("keys", 0) for v in info.values()) if info else 0
        return True, f"{key_count} key(s) in keyspace"
    except Exception as exc:
        return False, str(exc)


def _check_groq() -> tuple[bool, str]:
    try:
        import os
        key = os.getenv("GROQ_API_KEY", "")
        if not key:
            # Try loading from .env
            from dotenv import load_dotenv
            load_dotenv(_project_root / "ai-sdlc-assistant" / ".env")
            load_dotenv(_project_root / ".env")
            key = os.getenv("GROQ_API_KEY", "")
        return bool(key), "API key present" if key else "GROQ_API_KEY not set"
    except Exception as exc:
        return False, str(exc)


with col1:
    ok, msg = _check_qdrant()
    if ok:
        st.success(f"**Qdrant** ✅\n\n{msg}")
    else:
        st.error(f"**Qdrant** ❌\n\n{msg}")

with col2:
    ok, msg = _check_redis()
    if ok:
        st.success(f"**Redis** ✅\n\n{msg}")
    else:
        st.error(f"**Redis** ❌\n\n{msg}")

with col3:
    ok, msg = _check_groq()
    if ok:
        st.success(f"**Groq API** ✅\n\n{msg}")
    else:
        st.warning(f"**Groq API** ⚠️\n\n{msg}")

st.markdown("---")

# ── Quick navigation ──────────────────────────────────────────────────────────

st.subheader("Pages")
st.markdown("""
- **RAG Manager** — Browse Qdrant chunks by collection and doc type. Trigger re-ingest.
- **Config Viewer** — Inspect live YAML config values. Reload all configs.
- **Session Log** — Browse recent conversation turns stored in SQLite.

Use the sidebar to navigate between pages.
""")

st.markdown("---")
st.caption("Project: SDLC · Sprint 12 · AI SDLC Assistant Demo")
