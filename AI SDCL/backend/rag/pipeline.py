"""
backend/rag/pipeline.py

Full RAG ingestion pipeline — reads documents, chunks them,
adds contextual prefixes via LLM, embeds, and stores in Qdrant.

Run this script to populate the vector store:
    python scripts/ingest.py
    python scripts/ingest.py --no-llm   (skip Groq API calls, faster)

Flow per document:
  1. Read file (markdown, text, JSON)
  2. Detect document type → choose chunking strategy
  3. DocumentChunker splits into parent+child pairs (quality gate applied inside)
  4. LLM generates 1-2 sentence context prefix per child chunk (batched + throttled)
  5. Batch embed: all (prefix + child_text) vectors in one model.encode() call
  6. Validate: embedding dimension + metadata required fields
  7. Store in Qdrant: vector + child text + parent text + metadata

Why the LLM context prefix?
  A chunk "pool exhausted after config change" could be about anything.
  With prefix: "This is from ADR-001 (nginx config), describing DB pool failure modes."
  The embedding now knows what document and section this came from.

Why batch embedding?
  model.encode([text1, text2, ...]) is ~5x faster than calling encode(text) in a loop.
  SentenceTransformer processes texts in parallel internally when given a list.

Why batch throttle for LLM mode?
  Groq free tier: ~30 requests/minute. 200 chunks = 200 API calls = rate limit in ~1 min.
  Processing in batches of 10 with 2s sleep keeps throughput at ~25 req/min.
"""
import json
import logging
import time
from pathlib import Path

from langchain_groq import ChatGroq

from backend.core.settings import settings
from backend.core.config_loader import config
from backend.rag.chunker import DocumentChunker, Chunk
from backend.rag.retriever import embed_texts_batch
from backend.rag.vector_store import VectorStore

logger = logging.getLogger(__name__)

# ── Ingestion throttle constants (read from config in production — hardcoded here for clarity)
_BATCH_SIZE  = 10    # number of LLM prefix calls per batch before sleeping
_BATCH_SLEEP = 2.0   # seconds to sleep between batches (keeps Groq at ~25 req/min)

# ── Required metadata fields — chunks missing these are silently broken at retrieval time
_REQUIRED_METADATA = {"project", "source", "type"}


# ── Validation helpers ────────────────────────────────────────────────────────

def _validate_embedding(embedding: list[float], expected_dim: int = 384) -> bool:
    """
    Sanity-check the embedding vector before storing it.

    All-zero vector: happens when embed_text() receives empty/whitespace input.
    It matches EVERYTHING equally under cosine similarity — corrupts search.

    Wrong dimension: model mismatch between ingestion and query time.
    Would cause all searches to return wrong results silently.
    """
    if len(embedding) != expected_dim:
        return False
    if all(v == 0.0 for v in embedding):
        return False
    return True


def _validate_metadata(metadata: dict) -> bool:
    """
    Check that all required metadata fields are present before storing.

    'project' — used by Qdrant pre-filter on every search. Missing = chunk invisible.
    'source'  — used by mark_stale() to clean up old chunks on re-ingestion.
    'type'    — used by agents to filter by document type (e.g. only 'version_policy').
    """
    missing = _REQUIRED_METADATA - metadata.keys()
    if missing:
        logger.warning("Pipeline: chunk missing required metadata fields: %s", missing)
        return False
    return True


# ── LLM helpers ──────────────────────────────────────────────────────────────

def _build_llm() -> ChatGroq:
    """Build LLM client for contextual prefix generation. temperature=0.0 — deterministic."""
    llm_cfg = config.get_llm_config()
    primary = llm_cfg.get("primary", {})
    return ChatGroq(
        api_key=settings.GROQ_API_KEY,
        model=primary.get("model", settings.GROQ_MODEL),
        temperature=primary.get("temperatures", {}).get("chunk_contextualization", 0.0),
        max_tokens=primary.get("max_tokens", {}).get("chunk_context", 100),
    )


def _generate_context_prefix(llm: ChatGroq, doc_title: str, doc_type: str, chunk_text: str) -> str:
    """
    Ask the LLM to write a 1-2 sentence context description for this chunk.
    Prepended to the chunk before embedding so the vector carries document context.

    Retry logic: up to 3 attempts with exponential backoff (2s → 4s).
    Why? Groq free tier occasionally returns 429 (rate limit) or 503 (overloaded).
    On permanent failure: return "" — chunk is still stored, just without prefix.
    """
    prompt = config.get_prompt(
        "chunk_context_generator",
        doc_title=doc_title,
        doc_type=doc_type,
        chunk_text=chunk_text[:500],    # limit input to avoid token explosion
    )
    for attempt in range(3):
        try:
            response = llm.invoke(prompt)
            return response.content.strip()
        except Exception as e:
            if attempt < 2:
                wait = 2 ** (attempt + 1)   # 2s then 4s
                logger.warning(
                    "Pipeline: LLM prefix attempt %d/3 failed: %s — retrying in %ds",
                    attempt + 1, e, wait,
                )
                time.sleep(wait)
            else:
                logger.warning("Pipeline: LLM prefix failed after 3 attempts — storing chunk without prefix")
    return ""


# ── Main pipeline class ───────────────────────────────────────────────────────

class RAGPipeline:
    """
    Orchestrates the full ingestion pipeline.

    Usage:
        pipeline = RAGPipeline()
        count = pipeline.ingest_file("data/sprint_docs/sprint_12_plan.md",
                                     metadata={"project": "antlog", "source": "local"})
        print(f"Ingested {count} chunks")
    """

    def __init__(self, use_llm_context: bool = True):
        """
        Args:
            use_llm_context: If True, call Groq LLM to generate context prefix per chunk.
                             If False (--no-llm flag), embed chunk text directly.
        """
        self.chunker = DocumentChunker()
        self.vector_store = VectorStore()
        self.use_llm_context = use_llm_context
        self._llm = _build_llm() if use_llm_context else None
        self.total_chunks = 0

    def ingest_file(self, filepath: str | Path, metadata: dict) -> int:
        """
        Ingest a single file into the vector store.

        Args:
            filepath: Path to the file (.md, .txt, or .json for Slack mock)
            metadata: Must include 'project' and 'source' keys at minimum

        Returns:
            Number of chunks ingested
        """
        filepath = Path(filepath)
        if not filepath.exists():
            logger.error("Pipeline: file not found: %s", filepath)
            return 0

        logger.info("Pipeline: ingesting '%s'", filepath.name)

        doc_type  = self._detect_doc_type(filepath, metadata)
        doc_title = filepath.stem.replace("_", " ").title()

        if filepath.suffix == ".json":
            return self._ingest_slack_json(filepath, metadata, doc_type)
        else:
            text = filepath.read_text(encoding="utf-8")
            return self._ingest_text(text, doc_title, doc_type, metadata)

    def _ingest_text(self, text: str, doc_title: str, doc_type: str, metadata: dict) -> int:
        """
        Ingest plain text / markdown documents.

        Flow:
          chunk_document() → collect all chunks → LLM prefixes (batched) →
          batch embed → validate each → store valid ones
        """
        chunks = list(self.chunker.chunk_document(text, doc_title, doc_type, metadata))
        if not chunks:
            logger.warning("Pipeline: no chunks produced from '%s'", doc_title)
            return 0

        count = self._ingest_chunks(chunks, doc_title)
        self.total_chunks += count
        logger.info("Pipeline: ingested %d chunks from '%s'", count, doc_title)
        return count

    def _ingest_slack_json(self, filepath: Path, metadata: dict, doc_type: str) -> int:
        """
        Ingest Slack mock JSON file.
        Format: list of {user, message, timestamp} objects.
        Thread = parent chunk, individual messages = children.
        """
        with open(filepath, encoding="utf-8") as f:
            messages = json.load(f)

        channel = filepath.stem
        meta = {**metadata, "channel": channel, "type": "chat"}

        chunks = list(self.chunker.chunk_slack_messages(messages, meta))
        if not chunks:
            return 0

        count = self._ingest_chunks(chunks, filepath.stem)
        self.total_chunks += count
        logger.info("Pipeline: ingested %d Slack message chunks from '%s'", count, filepath.name)
        return count

    def _ingest_chunks(self, chunks: list[Chunk], doc_title: str) -> int:
        """
        Core batch ingestion method — used by both _ingest_text and _ingest_slack_json.

        Steps:
          1. Generate LLM context prefixes in batches of _BATCH_SIZE (throttled)
          2. Build text_to_embed = prefix + child_text for each chunk
          3. Batch embed all texts in one model.encode() call
          4. Validate embedding + metadata for each chunk
          5. Store valid chunks in Qdrant

        Returns: number of chunks successfully stored
        """
        # ── Step 1: Generate LLM context prefixes (batched + throttled)
        prefixes: list[str] = []
        if self.use_llm_context and self._llm:
            logger.info("Pipeline: generating LLM context prefixes for %d chunks (batched)...", len(chunks))
            for i, chunk in enumerate(chunks):
                prefix = _generate_context_prefix(self._llm, doc_title, chunk.doc_type, chunk.text)
                prefixes.append(prefix)
                # After each batch of _BATCH_SIZE, sleep to stay within Groq rate limit
                if (i + 1) % _BATCH_SIZE == 0 and (i + 1) < len(chunks):
                    logger.debug("Pipeline: throttle — sleeping %.1fs after batch %d", _BATCH_SLEEP, (i + 1) // _BATCH_SIZE)
                    time.sleep(_BATCH_SLEEP)
        else:
            prefixes = [""] * len(chunks)

        # ── Step 2: Build texts to embed (prefix + child text)
        texts_to_embed = [
            f"{prefix}\n\n{chunk.text}".strip() if prefix else chunk.text
            for prefix, chunk in zip(prefixes, chunks)
        ]

        # ── Step 3: Batch embed all at once (~5x faster than one-by-one)
        logger.info("Pipeline: batch embedding %d texts...", len(texts_to_embed))
        embeddings = embed_texts_batch(texts_to_embed)

        # ── Step 4 & 5: Validate each chunk and store valid ones
        count = 0
        for chunk, prefix, embedding in zip(chunks, prefixes, embeddings):
            # Build full metadata payload
            full_metadata = {
                **chunk.metadata,
                "doc_title":      chunk.doc_title,
                "type":           chunk.doc_type,
                "parent_id":      chunk.parent_id,
                "context_prefix": prefix,
            }

            # Validate embedding (catches all-zero vectors and dimension mismatches)
            if not _validate_embedding(embedding):
                logger.warning(
                    "Pipeline: invalid embedding for chunk '%s...' — skipping",
                    chunk.text[:40],
                )
                continue

            # Validate metadata (catches missing project/source/type)
            if not _validate_metadata(full_metadata):
                continue

            self.vector_store.upsert(
                text=chunk.text,
                embedding=embedding,
                parent_text=chunk.parent_text,
                metadata=full_metadata,
                chunk_id=chunk.chunk_id,
            )
            count += 1

        return count

    def ingest_directory(self, directory: str | Path, metadata: dict) -> int:
        """
        Ingest all supported files in a directory (non-recursive).

        Args:
            directory: Path to directory
            metadata:  Base metadata applied to all files (must include 'project', 'source')

        Returns:
            Total chunks ingested from all files
        """
        directory = Path(directory)
        if not directory.exists():
            logger.error("Pipeline: directory not found: %s", directory)
            return 0

        supported = {".md", ".txt", ".json"}
        files = [f for f in directory.iterdir() if f.suffix in supported and f.is_file()]

        if not files:
            logger.warning("Pipeline: no supported files in %s", directory)
            return 0

        total = 0
        for filepath in files:
            total += self.ingest_file(filepath, metadata)
        return total

    @staticmethod
    def _detect_doc_type(filepath: Path, metadata: dict) -> str:
        """Infer document type from path and metadata."""
        if metadata.get("type"):
            return metadata["type"]
        path_str = str(filepath).lower()
        if "sprint" in path_str:
            return "doc"
        if "adr" in path_str:
            return "adr"
        if "slack" in path_str or "teams" in path_str:
            return "chat"
        if "ticket" in path_str or "jira" in path_str:
            return "ticket"
        return "doc"
