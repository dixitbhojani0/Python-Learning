import os
import streamlit as st
import pandas as pd
import time
from pipeline import RAGPipeline
from models import QueryResult
from config import DOCS_DIR

st.set_page_config(
    page_title="RAG Demo",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
.answer-box {
    background: #0d1117;
    border: 2px solid #22c55e;
    border-radius: 10px;
    padding: 20px;
    font-size: 1.05em;
    line-height: 1.7;
}
</style>
""", unsafe_allow_html=True)

RANK_ICONS = ["🟢", "🔵", "🟡", "🟠", "🔴"]
RANK_LABELS = ["Best Match", "2nd Match", "3rd Match", "4th Match", "5th Match"]
SAMPLE_QUESTIONS = [
    "What is RAG and how does it reduce hallucinations?",
    "How does Python's async/await work?",
    "What is the difference between REST and GraphQL?",
    "What are embeddings in AI?",
    "How do virtual environments work in Python?",
]


@st.cache_resource
def get_pipeline() -> RAGPipeline:
    return RAGPipeline()


def render_sidebar(pipeline: RAGPipeline) -> tuple[int, bool, bool]:
    with st.sidebar:
        st.title("🔍 RAG Demo")
        st.caption("Retrieval-Augmented Generation")
        st.divider()

        st.subheader("⚙️ Pipeline Config")
        top_k = st.slider("Top-K Chunks to Retrieve", 1, 5, 3)
        show_prompt = st.checkbox("Show LLM Prompt", value=False)
        show_embeddings = st.checkbox("Show Embedding Vectors", value=False)
        st.divider()

        st.subheader("📄 Knowledge Base")
        already_indexed = pipeline.is_indexed()

        col_index, col_reindex = st.columns(2)
        with col_index:
            index_clicked = st.button(
                "🔄 Index" if not already_indexed else "✅ Indexed",
                use_container_width=True,
                type="primary",
                disabled=already_indexed,
            )
        with col_reindex:
            reindex_clicked = st.button("♻️ Re-index", use_container_width=True)

        if index_clicked or reindex_clicked:
            with st.spinner("Embedding documents..."):
                stats = pipeline.index(force=reindex_clicked)
            if stats:
                st.success(f"Indexed {pipeline.chunk_count()} chunks from {len(stats)} docs!")
                for s in stats:
                    st.markdown(f"- **{s.file}** — {s.chunks} chunks")
            else:
                st.info("Already indexed. Use Re-index to refresh.")

        if already_indexed:
            st.caption(f"Vector DB: ChromaDB · {pipeline.chunk_count()} chunks stored")

        st.divider()
        st.subheader("📤 Upload PDFs")
        uploaded_files = st.file_uploader(
            "Upload your documents",
            type=["pdf", "txt", "docx", "md", "csv", "xlsx"],
            accept_multiple_files=True,
        )
        if uploaded_files:
            saved = []
            for f in uploaded_files:
                dest = os.path.join(DOCS_DIR, f.name)
                with open(dest, "wb") as out:
                    out.write(f.read())
                saved.append(f.name)
            st.success(f"Saved: {', '.join(saved)}")
            st.info("Click **Re-index** above to embed your new files.")

        st.divider()
        st.caption("Stack: FastEmbed (local) · Groq LLaMA-3.3-70B · ChromaDB")

    return top_k, show_prompt, show_embeddings


def render_pipeline_steps() -> None:
    cols = st.columns(5)
    steps = [
        ("1️⃣ Docs", "Loaded & chunked"),
        ("2️⃣ Embed", "FastEmbed (local)"),
        ("3️⃣ Query", "Embed question"),
        ("4️⃣ Retrieve", "Top-K similarity"),
        ("5️⃣ Generate", "Groq LLaMA-3"),
    ]
    for col, (title, desc) in zip(cols, steps):
        with col:
            st.markdown(f"**{title}**\n{desc}")


def render_retrieved_chunks(result: QueryResult) -> None:
    st.subheader("📦 Step 4 — Retrieved Context Chunks")
    st.caption(f"Top {len(result.retrieved_chunks)} chunks by cosine similarity")

    for chunk in result.retrieved_chunks:
        r = chunk.rank - 1
        score_pct = int(chunk.score * 100)
        col_meta, col_score = st.columns([3, 1])
        with col_meta:
            st.markdown(
                f"{RANK_ICONS[r]} **{RANK_LABELS[r]}** — "
                f"`{chunk.source}` (chunk #{chunk.chunk_index})"
            )
        with col_score:
            st.metric("Similarity", f"{chunk.score:.4f}", delta=f"{score_pct}%")
        st.progress(chunk.score, text=f"Relevance: {score_pct}%")
        st.markdown(f"> {chunk.text[:400]}{'...' if len(chunk.text) > 400 else ''}")
        st.divider()


def render_embedding_chart(result: QueryResult) -> None:
    st.subheader("🔢 Embedding Preview (first 20 dims)")
    top_chunk = result.retrieved_chunks[0]
    df = pd.DataFrame({
        "query": result.query_embedding[:20],
        f"chunk ({top_chunk.source})": top_chunk.embedding[:20],
    })
    st.line_chart(df)


def render_inspect_tab(pipeline: RAGPipeline) -> None:
    grouped = pipeline.get_all_chunks()

    if not grouped:
        st.info("No chunks in the database yet. Index documents first.")
        return

    total_chunks = sum(len(v) for v in grouped.values())
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Files", len(grouped))
    col2.metric("Total Chunks", total_chunks)
    col3.metric("Avg Chunks / File", f"{total_chunks / len(grouped):.1f}")

    st.divider()

    # Files summary table
    st.subheader("📁 Indexed Files")
    table_data = [{"File": src, "Chunks": len(chunks)} for src, chunks in sorted(grouped.items())]
    st.dataframe(table_data, use_container_width=True, hide_index=True)

    st.divider()

    # Browse chunks per file
    st.subheader("🔎 Browse Chunks")
    selected_file = st.selectbox("Select a file to inspect", sorted(grouped.keys()))
    chunks = grouped[selected_file]

    keyword = st.text_input("Filter by keyword (optional)", placeholder="e.g. neural network")
    if keyword.strip():
        chunks = [c for c in chunks if keyword.lower() in c["text"].lower()]
        st.caption(f"{len(chunks)} chunk(s) matching '{keyword}'")

    for chunk in chunks:
        with st.expander(f"Chunk #{chunk['chunk_index']}  —  {len(chunk['text'].split())} words"):
            st.write(chunk["text"])


def render_answer(result: QueryResult, show_prompt: bool) -> None:
    if show_prompt:
        st.subheader("📝 Step 5a — Prompt Sent to LLM")
        st.code(result.prompt, language="text")

    st.subheader("💬 Step 5b — Generated Answer")
    st.markdown(f'<div class="answer-box">{result.answer}</div>', unsafe_allow_html=True)

    sources = ", ".join({c.source for c in result.retrieved_chunks})
    st.caption(f"Sources used: {sources}")


# ── Main ─────────────────────────────────────────────────────────────────────

pipeline = get_pipeline()
top_k, show_prompt, show_embeddings = render_sidebar(pipeline)

st.title("Retrieval-Augmented Generation")
st.markdown("Ask a question — watch the RAG pipeline work step by step.")
render_pipeline_steps()
st.divider()

tab_ask, tab_inspect = st.tabs(["🔍 Ask", "🗃️ Inspect DB"])

with tab_ask:
    if not pipeline.is_indexed():
        st.info("👈 Click **Index** in the sidebar first to build the knowledge base.")
        st.stop()

    if "query_input" not in st.session_state:
        st.session_state["query_input"] = ""

    st.markdown("**Try a sample question:**")
    cols = st.columns(len(SAMPLE_QUESTIONS))
    for i, (col, q) in enumerate(zip(cols, SAMPLE_QUESTIONS)):
        with col:
            if st.button(q[:35] + "...", key=f"sample_{i}", use_container_width=True):
                st.session_state["query_input"] = q
                st.rerun()

    query = st.text_input(
        "Or type your own question:",
        key="query_input",
        placeholder="e.g. What is RAG?",
    )

    if st.button("🚀 Ask", type="primary"):
        if not query.strip():
            st.warning("Please enter a question.")
        else:
            st.session_state["last_query"] = query
            st.divider()

            with st.status("Running RAG pipeline...", expanded=True) as status:
                st.write("🔢 Embedding your query...")
                time.sleep(0.2)
                st.write("🔍 Searching ChromaDB for similar chunks...")
                st.write("🤖 Sending context to Groq LLaMA-3.3-70B...")
                result = pipeline.query(query, top_k=top_k)
                status.update(label="Pipeline complete!", state="complete", expanded=False)

            render_retrieved_chunks(result)

            if show_embeddings:
                render_embedding_chart(result)

            render_answer(result, show_prompt)

with tab_inspect:
    render_inspect_tab(pipeline)
