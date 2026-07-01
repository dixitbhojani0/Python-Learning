"""
backend/rag/pipeline.py

Full RAG ingestion pipeline — orchestrates reading, chunking, embedding, and storage.

Run this script to populate the vector store:
    python scripts/ingest.py
    python scripts/ingest.py --no-llm   (skip LLM calls, faster)

Flow per document:
  1. Read file (detected by extension/doc_type)
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
import asyncio
import concurrent.futures
import logging
import time
import uuid
from pathlib import Path

from backend.core.config_loader import config
from backend.providers.base_llm import BaseLLMProvider
from backend.providers.factory import LLMFactory
from backend.rag.chunker import Chunk, ChunkingStrategyFactory, DocumentChunker
from backend.rag.loaders import extract_pdf_images, load_pdf_tables, load_pdf_with_pypdf, load_slack_json
from backend.rag.retriever import embed_texts_batch
from backend.rag.validators import validate_embedding, validate_metadata
from backend.rag.vector_store import VectorStore

logger = logging.getLogger(__name__)

# ── Ingestion throttle constants ──────────────────────────────────────────────
_BATCH_SIZE  = 10    # number of LLM prefix calls per batch before sleeping
_BATCH_SLEEP = 2.0   # seconds to sleep between batches (keeps Groq at ~25 req/min)

# Where extracted document images are saved (served read-only by the API).
# Lives under data/ so the docker-compose volume mount persists it across restarts.
_IMAGES_DIR = Path(__file__).parents[2] / "data" / "images"


# ── LLM sync bridge ───────────────────────────────────────────────────────────

def _provider_generate_sync(
    provider: BaseLLMProvider,
    prompt: str,
    system: str,
    temperature: float,
    max_tokens: int,
) -> str:
    """
    Call an async LLM provider synchronously from the ingestion pipeline.

    Why not asyncio.run() directly?
      asyncio.run() raises RuntimeError if called from inside a running event loop
      (which happens when admin.py calls ingest_directory from a FastAPI async route).

    Solution: spin up a dedicated worker thread with its own brand-new event loop.
      The thread has no existing loop, so asyncio.run() inside it always succeeds.
      Works correctly from both:
        - CLI (scripts/ingest.py) — no event loop at all
        - FastAPI routes (admin.py) — event loop exists but is on a different thread
    """
    def _run_in_new_loop() -> str:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            resp = loop.run_until_complete(
                provider.generate_text(prompt, system, temperature, max_tokens)
            )
            return resp.text.strip()
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_run_in_new_loop).result()


def _generate_context_prefix(provider: BaseLLMProvider, doc_title: str, doc_type: str, chunk_text: str) -> str:
    """
    Ask the LLM to write a 1-2 sentence context description for this chunk.
    Prepended to the chunk before embedding so the vector carries document context.

    Retry logic: up to 3 attempts with exponential backoff (2s → 4s).
    On permanent failure: return "" — chunk is stored without prefix.
    """
    llm_cfg = config.get_llm_config()
    primary  = llm_cfg.get("primary", {})
    temperature = primary.get("temperatures", {}).get("chunk_contextualization", 0.0)
    max_tokens  = primary.get("max_tokens",   {}).get("chunk_context", 100)
    system      = "You are a technical documentation assistant. Be concise and precise."

    prompt = config.get_prompt(
        "chunk_context_generator",
        doc_title=doc_title,
        doc_type=doc_type,
        chunk_text=chunk_text[:500],
    )
    for attempt in range(3):
        try:
            return _provider_generate_sync(provider, prompt, system, temperature, max_tokens)
        except Exception as e:
            if attempt < 2:
                wait = 2 ** (attempt + 1)
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
                                     metadata={"project": "SDLC", "source": "local"})
        print(f"Ingested {count} chunks")
    """

    def __init__(self, use_llm_context: bool = True):
        """
        Args:
            use_llm_context: If True, call LLM to generate context prefix per chunk.
                             If False (--no-llm flag), embed chunk text directly.
        """
        self.chunker         = DocumentChunker()
        self.vector_store    = VectorStore()
        self.use_llm_context = use_llm_context
        self._provider       = LLMFactory.get_provider() if use_llm_context else None
        self.total_chunks    = 0

    def ingest_file(self, filepath: str | Path, metadata: dict) -> int:
        """
        Ingest a single file into the vector store.

        Args:
            filepath: Path to the file
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
        doc_title = metadata.get("doc_title") or filepath.stem.replace("_", " ").title()

        strategy     = ChunkingStrategyFactory.resolve(doc_type, filepath.suffix)
        split_method = ChunkingStrategyFactory.get_split_method(strategy)

        logger.debug(
            "Pipeline: '%s' → doc_type=%s strategy=%s split_method=%s",
            filepath.name, doc_type, strategy, split_method,
        )

        if split_method == "message_per_chunk":
            return self._ingest_slack_json(filepath, metadata, doc_type)
        elif filepath.suffix.lower() == ".pdf":
            # PDFs bypass unstructured (can segfault on certain files) and use
            # pypdf for prose text + pdfplumber for tables — more reliable and
            # produces better table fidelity than unstructured's coordinate guessing.
            return self._ingest_pdf(filepath, doc_title, doc_type, metadata)
        elif split_method == "element_aware":
            return self._ingest_with_unstructured(filepath, doc_title, doc_type, metadata)
        else:
            text = filepath.read_text(encoding="utf-8")
            return self._ingest_text(text, doc_title, doc_type, metadata)

    def _ingest_text(self, text: str, doc_title: str, doc_type: str, metadata: dict) -> int:
        """
        Ingest plain text / markdown documents.

        Flow: chunk_document() → LLM prefixes (batched) → batch embed → validate → store
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
        messages = load_slack_json(filepath)
        if not messages:
            return 0

        channel = filepath.stem
        meta    = {**metadata, "channel": channel, "type": doc_type}
        chunks  = list(self.chunker.chunk_slack_messages(messages, meta))
        if not chunks:
            return 0

        count = self._ingest_chunks(chunks, filepath.stem)
        self.total_chunks += count
        logger.info("Pipeline: ingested %d Slack message chunks from '%s'", count, filepath.name)
        return count

    def _ingest_with_unstructured(self, filepath: Path, doc_title: str, doc_type: str, metadata: dict) -> int:
        """
        Ingest any rich document (PDF, DOCX, HTML, PPTX, etc.) via unstructured.io.

        unstructured.partition() auto-detects the file type and returns a list of
        typed elements: Title, NarrativeText, ListItem, Table, Image, Header, Footer.

        Falls back to pypdf plain-text extraction if unstructured is not installed
        or if partition() fails (e.g. corrupted PDF).
        """
        try:
            from unstructured.partition.auto import partition
            from unstructured.documents.elements import Image, Header, Footer, PageBreak, Table
            from unstructured.documents.elements import ElementMetadata as _EMeta
        except ImportError:
            logger.error(
                "Pipeline: unstructured is not installed — cannot ingest '%s'. "
                "Run: pip install unstructured",
                filepath.name,
            )
            return 0

        try:
            elements = partition(filename=str(filepath))
        except Exception as exc:
            logger.warning(
                "Pipeline: unstructured.partition() failed for '%s': %s",
                filepath.name, exc,
            )
            if filepath.suffix.lower() == ".pdf":
                logger.info("Pipeline: falling back to pypdf for '%s'", filepath.name)
                return self._ingest_pdf_with_pypdf(filepath, doc_title, doc_type, metadata)
            return 0

        # Filter TOC elements: table-of-contents dot leaders ("Chapter 1 ......... 12")
        # are noise — they match TOC queries but never contain real answers.
        def _is_toc(text: str) -> bool:
            if not text or len(text) < 20:
                return False
            return text.count(".") / len(text) > 0.25

        toc_skipped = 0
        filtered_elements = []
        for el in elements:
            if el.text and _is_toc(el.text):
                toc_skipped += 1
            else:
                filtered_elements.append(el)

        if toc_skipped:
            logger.info("Pipeline: '%s' — skipped %d TOC entries (dot leaders)", filepath.name, toc_skipped)
        elements = filtered_elements

        # ── PDF tables: replace unstructured's coordinate-guessed (often garbled) Table
        # elements with pdfplumber's mathematically-exact ones. Non-PDF files are
        # unaffected; PDFs without tables are unaffected (pdf_tables stays []).
        if filepath.suffix.lower() == ".pdf":
            pdf_tables = load_pdf_tables(filepath)
            if pdf_tables:
                elements = [el for el in elements if not isinstance(el, Table)]
                for page_num, md in pdf_tables:
                    tbl = Table(text=md)
                    tbl.metadata = _EMeta(page_number=page_num)
                    elements.append(tbl)
                logger.info(
                    "Pipeline: '%s' — replaced unstructured tables with %d pdfplumber table(s)",
                    filepath.name, len(pdf_tables),
                )

        # Images are no longer discarded: for PDFs they are extracted, saved, and
        # OCR'd into dedicated image chunks AFTER text chunking (see below), so they
        # become both searchable (OCR text) and showable (image_path metadata).

        # Count element types for observability
        skipped_types: dict[str, int] = {}
        text_element_count = 0
        for el in elements:
            if isinstance(el, (Image, Header, Footer, PageBreak)):
                t = type(el).__name__
                skipped_types[t] = skipped_types.get(t, 0) + 1
            elif el.text and el.text.strip():
                text_element_count += 1

        if skipped_types:
            logger.info(
                "Pipeline: '%s' — skipped elements (no text content): %s",
                filepath.name, skipped_types,
            )

        if text_element_count == 0:
            logger.warning(
                "Pipeline: '%s' produced no extractable text — "
                "may be a scanned image document with no text layer.",
                filepath.name,
            )
            return 0

        logger.info(
            "Pipeline: unstructured extracted %d elements (%d with text) from '%s'",
            len(elements), text_element_count, filepath.name,
        )

        chunks = list(self.chunker.chunk_from_elements(elements, doc_title, doc_type, metadata))

        # Add image chunks (PDF only) — saved + OCR'd, searchable and showable.
        if filepath.suffix.lower() == ".pdf":
            image_chunks = self._build_image_chunks(filepath, doc_title, doc_type, metadata)
            if image_chunks:
                logger.info("Pipeline: '%s' — added %d image chunk(s)", filepath.name, len(image_chunks))
                chunks.extend(image_chunks)

        if not chunks:
            logger.warning("Pipeline: no chunks produced from '%s'", doc_title)
            return 0

        count = self._ingest_chunks(chunks, doc_title)
        self.total_chunks += count
        logger.info("Pipeline: ingested %d chunks from '%s'", count, doc_title)
        return count

    def _build_image_chunks(self, filepath: Path, doc_title: str, doc_type: str, metadata: dict) -> list[Chunk]:
        """
        Extract embedded images, save + OCR them, and build one Chunk per image.

        Each image chunk:
          - text        = OCR text when meaningful, else a "Image on page N" placeholder
                          (so the image is still findable by document/page).
          - metadata    = image_id, image_path, page_number, element_type="Image",
                          is_image=True — image_path is what the API serves to the UI.
          - parent_id == chunk_id → atomic (no separate parent point created).
        """
        records = extract_pdf_images(filepath, _IMAGES_DIR)
        chunks: list[Chunk] = []
        for rec in records:
            ocr  = (rec.get("ocr_text") or "").strip()
            if len(ocr) <= 30:
                # No meaningful OCR text — placeholder "Image on page N" embeds close
                # to document-title queries and crowds out real content in retrieval.
                # Skip the chunk; the image file is still saved for the API to serve.
                continue
            text = ocr
            chunk_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, rec["image_id"]))
            chunks.append(Chunk(
                text=text,
                chunk_id=chunk_id,
                doc_title=doc_title,
                doc_type=doc_type,
                metadata={
                    **metadata,
                    "is_image":      True,
                    "image_id":      rec["image_id"],
                    "image_path":    rec["image_path"],
                    "page_number":   rec["page_number"],
                    "element_type":  "Image",
                    "section_title": doc_title,
                    "has_ocr":       bool(ocr),
                },
                parent_id=chunk_id,
                parent_text=text,
            ))
        return chunks

    def _ingest_pdf(self, filepath: Path, doc_title: str, doc_type: str, metadata: dict) -> int:
        """
        PDF ingestion without unstructured — avoids native segfaults on certain PDFs.

        Prose text:  pypdf  (page-level text extraction, always reliable)
        Tables:      pdfplumber (coordinate-based — exact cell text, no garbling)
        Images:      same extract_pdf_images() path as before

        All three are combined into one _ingest_chunks() call so LLM prefix
        generation and batch embedding happen in a single pass.
        """
        all_chunks: list = []

        # ── Prose text via pypdf
        full_text = load_pdf_with_pypdf(filepath)
        if full_text:
            all_chunks.extend(self.chunker.chunk_document(full_text, doc_title, doc_type, metadata))

        # ── Tables via pdfplumber — each table is its own isolated chunk set
        for page_num, md in load_pdf_tables(filepath):
            table_meta = {**metadata, "page_number": page_num, "element_type": "Table"}
            all_chunks.extend(self.chunker.chunk_document(md, doc_title, doc_type, table_meta))

        # ── Image chunks (OCR, same as the unstructured path)
        all_chunks.extend(self._build_image_chunks(filepath, doc_title, doc_type, metadata))

        if not all_chunks:
            logger.warning("Pipeline: no content extracted from '%s'", doc_title)
            return 0

        count = self._ingest_chunks(all_chunks, doc_title)
        self.total_chunks += count
        logger.info("Pipeline: ingested %d chunks from '%s' (pypdf+pdfplumber)", count, doc_title)
        return count

    def _ingest_pdf_with_pypdf(self, filepath: Path, doc_title: str, doc_type: str, metadata: dict) -> int:
        """
        Fallback: extract PDF text with pypdf and run through standard text chunking.
        """
        full_text = load_pdf_with_pypdf(filepath)
        if not full_text:
            return 0
        return self._ingest_text(full_text, doc_title, doc_type, metadata)

    def _ingest_chunks(self, chunks: list[Chunk], doc_title: str) -> int:
        """
        Core batch ingestion method — used by all _ingest_* methods.

        Steps:
          1. Generate LLM context prefixes in batches of _BATCH_SIZE (throttled)
          2. Build text_to_embed = prefix + child_text for each chunk
          3. Batch embed all texts in one model.encode() call
          4. Validate embedding + metadata for each chunk
          5. Store valid chunks in Qdrant
          6. Store unique parent chunks as separate Qdrant points (excluded from search)

        Returns: number of chunks successfully stored
        """
        # ── Step 1: Generate LLM context prefixes (batched + throttled)
        _ingest_cfg   = config.get_llm_config().get("ingestion", {})
        _batch_size   = int(_ingest_cfg.get("batch_size",      _BATCH_SIZE))
        _batch_sleep  = float(_ingest_cfg.get("batch_sleep_sec", _BATCH_SLEEP))
        prefixes: list[str] = []
        if self.use_llm_context and self._provider:
            logger.info("Pipeline: generating LLM context prefixes for %d chunks (batched)...", len(chunks))
            for i, chunk in enumerate(chunks):
                prefix = _generate_context_prefix(self._provider, doc_title, chunk.doc_type, chunk.text)
                prefixes.append(prefix)
                if (i + 1) % _batch_size == 0 and (i + 1) < len(chunks):
                    logger.debug("Pipeline: throttle — sleeping %.1fs after batch %d", _batch_sleep, (i + 1) // _batch_size)
                    time.sleep(_batch_sleep)
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

        # ── Steps 4 & 5: Validate each chunk and store valid ones
        count = 0
        valid_chunks_with_embeddings: list[tuple[Chunk, list[float]]] = []

        for chunk, prefix, embedding in zip(chunks, prefixes, embeddings):
            full_metadata = {
                **chunk.metadata,
                "doc_title":      chunk.doc_title,
                "type":           chunk.doc_type,
                "parent_id":      chunk.parent_id,
                "prev_chunk_id":  chunk.prev_chunk_id,
                "next_chunk_id":  chunk.next_chunk_id,
                "context_prefix": prefix,
                "is_parent":      False,
            }

            if not validate_embedding(embedding):
                logger.warning("Pipeline: invalid embedding for chunk '%s...' — skipping", chunk.text[:40])
                continue
            if not validate_metadata(full_metadata):
                continue

            self.vector_store.upsert(
                text=chunk.text,
                embedding=embedding,
                parent_text=chunk.parent_text,
                metadata=full_metadata,
                chunk_id=chunk.chunk_id,
            )
            count += 1
            valid_chunks_with_embeddings.append((chunk, embedding))

        # ── Step 6: Store unique parent chunks as separate Qdrant points
        # Tagged is_parent=True so they're excluded from search() but queryable
        # by ID via VectorStore.get_by_parent_id() for sibling-chunk lookups.
        _CHILD_ONLY = frozenset({
            "chunk_index", "total_chunks", "document_position",
            "chunk_size", "element_type", "page_number", "section_title",
        })
        unique_parents: dict[str, tuple[str, dict]] = {}
        for chunk, _ in valid_chunks_with_embeddings:
            if chunk.parent_id and chunk.parent_id not in unique_parents:
                if chunk.parent_id == chunk.chunk_id:
                    continue
                parent_meta = {k: v for k, v in chunk.metadata.items() if k not in _CHILD_ONLY}
                unique_parents[chunk.parent_id] = (chunk.parent_text, parent_meta)

        if unique_parents:
            p_ids   = list(unique_parents.keys())
            p_texts = [unique_parents[pid][0] for pid in p_ids]
            p_metas = [unique_parents[pid][1] for pid in p_ids]
            logger.debug("Pipeline: embedding %d unique parent chunks for '%s'", len(p_ids), doc_title)
            p_embs  = embed_texts_batch(p_texts)
            for pid, ptext, pmeta, pemb in zip(p_ids, p_texts, p_metas, p_embs):
                if validate_embedding(pemb) and validate_metadata(pmeta):
                    self.vector_store.upsert_parent(pid, ptext, pemb, pmeta)

        return count

    def build_cross_document_links(
        self,
        project:        str,
        min_similarity: float = 0.75,
        top_k:          int   = 3,
    ) -> int:
        """
        Post-ingest step: find the top-K most similar chunks from OTHER sources
        for every chunk in the project, and write their IDs into related_chunk_ids.

        Run once after all documents are ingested (admin → POST /admin/build-links).
        Takes ~10ms per chunk (small Qdrant collection).

        Returns: number of chunks that received at least one cross-document link.
        """
        logger.info("build_cross_document_links: scanning project='%s'...", project)
        all_records = self.vector_store.scroll_chunks(project, with_vectors=True)

        if not all_records:
            logger.info("build_cross_document_links: no chunks for project='%s'", project)
            return 0

        linked_count = 0
        for record in all_records:
            chunk_id = str(record.id)
            source   = record.payload.get("source", "")
            vector   = record.vector

            if vector is None:
                continue
            if isinstance(vector, dict):
                vector = next(iter(vector.values()), None)
                if vector is None:
                    continue

            related = self.vector_store.search_related(
                query_embedding=list(vector),
                exclude_source=source,
                project=project,
                top_k=top_k,
                min_score=min_similarity,
            )

            if related:
                self.vector_store.update_payload(
                    chunk_id,
                    {"related_chunk_ids": [r["id"] for r in related]},
                )
                linked_count += 1

        logger.info(
            "build_cross_document_links: linked %d/%d chunks (project=%s min_sim=%.2f)",
            linked_count, len(all_records), project, min_similarity,
        )
        return linked_count

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

        supported = ChunkingStrategyFactory.supported_extensions()
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
