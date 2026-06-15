import logging
import os
import time
import streamlit as st
import pandas as pd

from config import settings
from factory import ComponentFactory
from ingestion import DocumentLoader, RecursiveChunker, QdrantVectorStore
from retrieval import PromptAugmentor, GroqGenerator
from pipeline import IngestionPipeline, RetrievalPipeline
from core.models import QueryResult

logging.basicConfig(
    level=logging.INFO,
    format="%(name)s — %(levelname)s — %(message)s",
)

st.set_page_config(
    page_title="RAG Demo (LangChain)",
    page_icon="🦜",
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
.strategy-badge {
    background: #1e3a5f;
    border: 1px solid #3b82f6;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 0.85em;
    color: #93c5fd;
}
</style>
""", unsafe_allow_html=True)

RANK_ICONS  = ["🟢", "🔵", "🟡", "🟠", "🔴", "⚪", "⚪", "⚪", "⚪", "⚪"]
RANK_LABELS = ["Best Match", "2nd Match", "3rd Match", "4th Match", "5th Match",
               "6th Match", "7th Match", "8th Match", "9th Match", "10th Match"]
SAMPLE_QUESTIONS = [
    "What is RAG and how does it reduce hallucinations?",
    "How does Python's async/await work?",
    "What is the difference between REST and GraphQL?",
    "What are embeddings in AI?",
    "How do virtual environments work in Python?",
]

STRATEGY_DESCRIPTIONS = {
    "basic":       "Single embedding → cosine search (fastest)",
    "multi_query": "3 paraphrases → merge results (better recall)",
    "hyde":        "Hypothetical doc → embed doc → search (best for short queries)",
}


# ── Dependency Wiring (cached — runs once per session) ───────────────────────

@st.cache_resource
def build_pipelines() -> tuple[IngestionPipeline, RetrievalPipeline, QdrantVectorStore, ComponentFactory]:
    """Wire all dependencies via ComponentFactory (reads settings from .env)."""
    vector_store = QdrantVectorStore(settings)
    factory      = ComponentFactory(settings, vector_store)

    ingestion = IngestionPipeline(
        loader=DocumentLoader(settings),
        chunker=RecursiveChunker(settings),
        embedder=factory.embedder,
        vector_store=vector_store,
    )

    retrieval = RetrievalPipeline(
        strategy=factory.build_retriever_strategy(),
        augmentor=PromptAugmentor(),
        generator=factory.build_generator(),
        memory=factory.build_memory(),
        reranker=factory.build_reranker(),
        reranker_top_n=settings.reranker_top_n,
    )

    return ingestion, retrieval, vector_store, factory


# ── Runtime strategy swap (respects cache — swaps without full reload) ────────

def _get_retrieval_pipeline(
    vector_store: QdrantVectorStore,
    factory: ComponentFactory,
    strategy_key: str,
) -> RetrievalPipeline:
    """Return a RetrievalPipeline using the chosen strategy.

    We create a lightweight new pipeline for each strategy change rather than
    reloading the model — embedder + vector_store are shared from cache.
    """
    from retrieval.strategies import BasicRetrieverStrategy, MultiQueryStrategy, HyDEStrategy
    from retrieval.strategies.basic import BasicRetrieverStrategy
    from retrieval.strategies.multi_query import MultiQueryStrategy
    from retrieval.strategies.hyde import HyDEStrategy
    from retrieval import PromptAugmentor, GroqGenerator

    _MAP = {
        "basic":       BasicRetrieverStrategy,
        "multi_query": MultiQueryStrategy,
        "hyde":        HyDEStrategy,
    }
    strategy_cls = _MAP.get(strategy_key, BasicRetrieverStrategy)
    strategy = strategy_cls(factory.embedder, vector_store, settings)
    augmentor = PromptAugmentor()
    return RetrievalPipeline(
        strategy=strategy,
        augmentor=augmentor,
        generator=GroqGenerator(settings, augmentor),
        memory=factory.build_memory(),
        reranker=factory.build_reranker(),
        reranker_top_n=settings.reranker_top_n,
    )


# ── Sidebar ───────────────────────────────────────────────────────────────────

def render_sidebar(
    ingestion: IngestionPipeline,
    vector_store: QdrantVectorStore,
) -> tuple[int, bool, bool, str]:
    with st.sidebar:
        st.title("🦜 RAG — LangChain")
        st.caption("Flexible 8-step pipeline demo")
        st.divider()

        st.subheader("⚙️ Config")
        top_k       = st.slider("Top-K Chunks", 1, 10, settings.top_k_default)
        show_prompt = st.checkbox("Show Augmented Prompt")
        show_embeds = st.checkbox("Show Embedding Vectors")
        st.divider()

        st.subheader("🔀 Retrieval Strategy")
        strategy_key = st.radio(
            "Strategy",
            options=list(STRATEGY_DESCRIPTIONS.keys()),
            format_func=lambda k: f"{k}",
            index=list(STRATEGY_DESCRIPTIONS.keys()).index(settings.retriever_strategy),
            label_visibility="collapsed",
        )
        st.caption(STRATEGY_DESCRIPTIONS[strategy_key])

        if settings.memory_enabled:
            st.caption("💬 Conversation memory: ON")
        if settings.reranker_enabled:
            st.caption(f"🔍 Reranker: {settings.reranker_provider}")
        st.divider()

        st.subheader("📄 Ingestion Pipeline")
        already_indexed = vector_store.is_indexed()

        col1, col2 = st.columns(2)
        with col1:
            index_clicked = st.button(
                "✅ Indexed" if already_indexed else "▶ Index",
                use_container_width=True,
                type="primary",
                disabled=already_indexed,
            )
        with col2:
            reindex_clicked = st.button("♻️ Re-index", use_container_width=True)

        if index_clicked or reindex_clicked:
            with st.spinner("Running ingestion pipeline…"):
                result = ingestion.run(settings.docs_dir, force=reindex_clicked)
            if result.total_chunks:
                st.success(f"{result.total_chunks} chunks from {result.total_files} files")
                for s in result.doc_stats:
                    st.markdown(f"- **{s.file}** — {s.chunks} chunks")
            else:
                st.info("Already indexed. Use Re-index to refresh.")

        if already_indexed:
            st.caption(f"Qdrant · {vector_store.count()} chunks stored")

        st.divider()
        st.subheader("📤 Upload Documents")
        uploaded = st.file_uploader(
            "PDF · TXT · MD · CSV",
            type=["pdf", "txt", "md", "csv"],
            accept_multiple_files=True,
        )
        if uploaded:
            for f in uploaded:
                with open(os.path.join(settings.docs_dir, f.name), "wb") as out:
                    out.write(f.read())
            st.success(f"Saved {len(uploaded)} file(s) — click Re-index to embed.")

        st.divider()
        st.caption(
            f"Stack: LangChain · FastEmbed · Groq LLaMA-3.3-70B · Qdrant\n\n"
            f"Embedder: `{settings.embedding_provider}` · "
            f"LLM: `{settings.llm_provider}`"
        )

    return top_k, show_prompt, show_embeds, strategy_key


# ── Render helpers ────────────────────────────────────────────────────────────

def render_pipeline_banner(strategy_key: str) -> None:
    cols = st.columns(8)
    steps = [
        "1️⃣ Load", "2️⃣ Chunk", "3️⃣ Embed", "4️⃣ Store",
        "5️⃣ Embed Q", "6️⃣ Search", "7️⃣ Augment", "8️⃣ Generate",
    ]
    for col, label in zip(cols, steps):
        with col:
            st.caption(label)
    st.markdown(
        f'Strategy: <span class="strategy-badge">{strategy_key}</span> — '
        f'{STRATEGY_DESCRIPTIONS[strategy_key]}',
        unsafe_allow_html=True,
    )


def render_retrieved_chunks(result: QueryResult) -> None:
    st.subheader("📦 Step 6 — Retrieved Chunks")
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
    if not result.query_embedding:
        return
    st.subheader("🔢 Embedding Preview (first 20 dims)")
    top = result.retrieved_chunks[0]
    df = pd.DataFrame({
        "query": result.query_embedding[:20],
        f"chunk ({top.source})": top.embedding[:20],
    })
    st.line_chart(df)


def render_answer(result: QueryResult, show_prompt: bool) -> None:
    if show_prompt:
        st.subheader("📝 Step 7 — Augmented Prompt")
        st.code(result.prompt, language="text")
    st.subheader("💬 Step 8 — Generated Answer")
    st.markdown(f'<div class="answer-box">{result.answer}</div>', unsafe_allow_html=True)
    sources = ", ".join({c.source for c in result.retrieved_chunks})
    st.caption(f"Sources: {sources}")

    if result.conversation_history:
        with st.expander(f"💬 Conversation history ({len(result.conversation_history)} prior turns)"):
            for t in result.conversation_history:
                st.markdown(f"**You:** {t.question}")
                st.markdown(f"**AI:** {t.answer}")
                st.divider()


def render_inspect_tab(vector_store: QdrantVectorStore) -> None:
    grouped = vector_store.get_all()
    if not grouped:
        st.info("No chunks stored yet. Run the ingestion pipeline first.")
        return

    total = sum(len(v) for v in grouped.values())
    c1, c2, c3 = st.columns(3)
    c1.metric("Files Indexed", len(grouped))
    c2.metric("Total Chunks", total)
    c3.metric("Avg Chunks / File", f"{total / len(grouped):.1f}")

    st.divider()
    st.subheader("📁 Files")
    st.dataframe(
        [{"File": s, "Chunks": len(c)} for s, c in sorted(grouped.items())],
        use_container_width=True,
        hide_index=True,
    )

    st.divider()
    st.subheader("🔎 Browse Chunks")
    selected = st.selectbox("Select file", sorted(grouped.keys()))
    chunks = grouped[selected]
    keyword = st.text_input("Filter by keyword (optional)")
    if keyword.strip():
        chunks = [c for c in chunks if keyword.lower() in c["text"].lower()]
        st.caption(f"{len(chunks)} matching chunk(s)")

    for chunk in chunks:
        with st.expander(f"Chunk #{chunk['chunk_index']} — {len(chunk['text'].split())} words"):
            st.write(chunk["text"])


# ── Main ──────────────────────────────────────────────────────────────────────

ingestion_pipeline, retrieval_pipeline, vector_store, factory = build_pipelines()
top_k, show_prompt, show_embeds, strategy_key = render_sidebar(ingestion_pipeline, vector_store)

st.title("🦜 RAG Demo — LangChain Edition")
st.markdown("Clean 8-step pipeline: **Ingestion** (1–4) + **Retrieval** (5–8)")
render_pipeline_banner(strategy_key)
st.divider()

tab_ask, tab_inspect = st.tabs(["🔍 Ask", "🗃️ Inspect DB"])

with tab_ask:
    if not vector_store.is_indexed():
        st.info("👈 Click **Index** in the sidebar to run the ingestion pipeline first.")
        st.stop()

    if "query_input" not in st.session_state:
        st.session_state["query_input"] = ""

    st.markdown("**Try a sample question:**")
    cols = st.columns(len(SAMPLE_QUESTIONS))
    for i, (col, q) in enumerate(zip(cols, SAMPLE_QUESTIONS)):
        with col:
            if st.button(q[:35] + "...", key=f"s{i}", use_container_width=True):
                st.session_state["query_input"] = q
                st.rerun()

    query = st.text_input("Or type your own:", key="query_input", placeholder="e.g. What is RAG?")

    if st.button("🚀 Ask", type="primary"):
        if not query.strip():
            st.warning("Please enter a question.")
        else:
            # Build a fresh pipeline with the selected strategy (embedder/store reused from cache)
            active_pipeline = _get_retrieval_pipeline(vector_store, factory, strategy_key)

            with st.status("Running retrieval pipeline…", expanded=True) as status:
                st.write(f"⚡ Steps 5+6 — {strategy_key} retrieval…")
                time.sleep(0.1)
                st.write("📝 Step 7 — Augmenting prompt…")
                st.write("🤖 Step 8 — Generating answer via Groq LCEL chain…")
                result = active_pipeline.run(query, top_k=top_k)
                status.update(label="Pipeline complete!", state="complete", expanded=False)

            render_retrieved_chunks(result)
            if show_embeds:
                render_embedding_chart(result)
            render_answer(result, show_prompt)

with tab_inspect:
    render_inspect_tab(vector_store)
