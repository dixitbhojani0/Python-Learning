"""
admin/pages/01_rag_manager.py

RAG Manager page — Qdrant chunk browser + ingest trigger.

What this page does:
  - Shows how many chunks are in Qdrant, broken down by doc_type
  - Lets you browse the raw chunk text (useful for debugging retrieval)
  - Lets you trigger a fresh ingest via the scripts/ingest.py CLI
  - Shows the last ingest log output inline

Why this is useful:
  After changing sprint docs or ADRs, you need to re-ingest so the
  new content appears in retrieval results. This page makes that a
  one-click operation instead of dropping to a terminal.
"""
import subprocess
import sys
from pathlib import Path

_project_root = Path(__file__).parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import streamlit as st

st.set_page_config(page_title="RAG Manager", page_icon="📚", layout="wide")
st.title("📚 RAG Manager")
st.caption("Browse and manage Qdrant vector store chunks.")

# ── Load Qdrant data ──────────────────────────────────────────────────────────

@st.cache_data(ttl=30)
def _load_chunks(collection: str = "sdlc_docs") -> list[dict]:
    """Scroll all chunks from the Qdrant collection and return as list of dicts."""
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(host="localhost", port=6333, timeout=5)

        results, _next = client.scroll(
            collection_name=collection,
            limit=500,
            with_payload=True,
            with_vectors=False,
        )
        chunks = []
        for point in results:
            payload = point.payload or {}
            chunks.append({
                "id":       str(point.id),
                "text":     payload.get("text", "")[:200] + "…",
                "source":   payload.get("source", "unknown"),
                "doc_type": payload.get("doc_type", "unknown"),
                "chunk_type": payload.get("chunk_type", "child"),
                "parent_id":  payload.get("parent_id", ""),
            })
        return chunks
    except Exception as exc:
        st.error(f"Cannot connect to Qdrant: {exc}")
        return []


@st.cache_data(ttl=30)
def _get_collections() -> list[str]:
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(host="localhost", port=6333, timeout=5)
        return [c.name for c in client.get_collections().collections]
    except Exception:
        return []


# ── Collection selector ───────────────────────────────────────────────────────

collections = _get_collections()
if not collections:
    st.warning("Qdrant is not reachable or has no collections. Start Docker and run ingest first.")
    st.stop()

selected_collection = st.selectbox("Collection", options=collections, index=0)

chunks = _load_chunks(selected_collection)

# ── Summary metrics ───────────────────────────────────────────────────────────

if chunks:
    import pandas as pd

    df = pd.DataFrame(chunks)

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Chunks", len(df))
    col2.metric("Doc Types", df["doc_type"].nunique())
    col3.metric("Sources", df["source"].nunique())

    st.markdown("---")

    # ── Breakdown by doc_type ─────────────────────────────────────────────────

    st.subheader("Breakdown by Document Type")
    type_counts = df.groupby("doc_type").size().reset_index(name="count")
    type_counts = type_counts.sort_values("count", ascending=False)
    st.bar_chart(type_counts.set_index("doc_type")["count"])

    # ── Breakdown by chunk_type ───────────────────────────────────────────────

    st.subheader("Breakdown by Chunk Type")
    chunk_type_counts = df.groupby("chunk_type").size().reset_index(name="count")
    st.dataframe(chunk_type_counts, use_container_width=True)

    # ── Browseable chunk table ────────────────────────────────────────────────

    st.markdown("---")
    st.subheader("Browse Chunks")

    filter_doc_type = st.selectbox(
        "Filter by doc_type",
        options=["(all)"] + sorted(df["doc_type"].unique().tolist()),
    )
    filter_source = st.selectbox(
        "Filter by source",
        options=["(all)"] + sorted(df["source"].unique().tolist()),
    )

    filtered = df.copy()
    if filter_doc_type != "(all)":
        filtered = filtered[filtered["doc_type"] == filter_doc_type]
    if filter_source != "(all)":
        filtered = filtered[filtered["source"] == filter_source]

    st.dataframe(
        filtered[["id", "source", "doc_type", "chunk_type", "text"]],
        use_container_width=True,
        height=400,
    )
else:
    st.info("No chunks found in this collection. Run the ingest script below.")

# ── Ingest trigger ────────────────────────────────────────────────────────────

st.markdown("---")
st.subheader("Trigger Ingest")

use_llm = st.checkbox(
    "Use LLM context prefix (slower, uses Groq API — leave unchecked for fast mode)",
    value=False,
)
st.caption("The ingest script always calls `mark_stale()` before each directory, so old chunks are automatically replaced.")

if st.button("▶ Run Ingest Now", type="primary"):
    ingest_script = _project_root / "scripts" / "ingest.py"

    cmd = [sys.executable, str(ingest_script)]
    if not use_llm:
        cmd.append("--no-llm")

    with st.spinner("Running ingest… this may take 30–60 seconds."):
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(_project_root),
        )

    if result.returncode == 0:
        st.success("Ingest completed successfully.")
        st.cache_data.clear()
    else:
        st.error(f"Ingest failed (exit code {result.returncode}).")

    if result.stdout:
        with st.expander("Ingest output (stdout)"):
            st.code(result.stdout, language="text")
    if result.stderr:
        with st.expander("Ingest output (stderr)"):
            st.code(result.stderr, language="text")
