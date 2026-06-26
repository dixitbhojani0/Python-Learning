"""
backend/rag/chunker.py

Implements contextual + parent-child chunking strategy.

Strategy (based on 2024-2025 RAG research):
  Parent chunks (~2000 tokens): preserve full context — what the LLM reads.
  Child chunks  (~512 tokens):  smaller = more precise embeddings — what gets searched.
  Overlap: ~10% of child size (~50 tokens) — prevents key sentences from being split.

Why two levels?
  Searching small child chunks gives high precision (exact concept match).
  Returning the parent gives high recall (the LLM gets full surrounding context).
  Searching small + reading large is the production-proven pattern (2025 benchmarks:
  0.88 precision / 0.85 recall vs 0.78/0.72 for semantic chunking).

Why contextual prefixes?
  Standard chunking embeds a fragment in isolation — loses document context.
  We prepend an LLM-generated summary ("This is from the nginx ADR, section 4...")
  so the embedding carries document-level context. Anthropic (2024) found this alone
  reduces retrieval failures by 35%; combined with BM25 hybrid → 49% reduction.

Metadata stored per chunk (essential for filtering and retrieval quality):
  source_file, doc_type, section_title, page_number, element_type,
  chunk_index, total_chunks, document_position (0.0-1.0), chunk_size,
  ingested_date, parent_id — all filterable in Qdrant for pre-search narrowing.

Ref: Anthropic Contextual Retrieval (2024), Firecrawl Best Chunking (2025),
     Unstructured.io Chunking Docs, Databricks RAG Guide.
"""
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Generator

logger = logging.getLogger(__name__)

# ── Target sizes (research consensus 2024-2025)
PARENT_SIZE   = 2000    # tokens approx (~8000 chars)  — context sent to LLM
CHILD_SIZE    = 512     # tokens approx (~2048 chars)  — what gets embedded & searched
CHILD_OVERLAP = 50      # tokens approx (~200 chars)   — ~10% of child, prevents split sentences

# Approximate: 1 token ≈ 4 chars in English (good enough for splitting heuristics)
CHARS_PER_TOKEN = 4


class ChunkingStrategyFactory:
    """
    Resolves the correct chunking strategy for a given doc_type + file extension.

    Reads from config/chunking.yaml via ConfigLoader (lazy import, hot-reloadable).
    Falls back to safe defaults so chunking always works even if config is missing.

    Resolution order:
      1. extension_routing  (.pdf → rich_document) — always wins over doc_type
      2. content_type_routing (doc_type="adr" → markdown_document)
      3. fallback: "markdown_document"

    Usage:
        strategy = ChunkingStrategyFactory.resolve("adr", ".md")   # → "markdown_document"
        strategy = ChunkingStrategyFactory.resolve("doc", ".pdf")  # → "rich_document"
        params   = ChunkingStrategyFactory.get_strategy_params(strategy)
    """

    _DEFAULT_STRATEGY = "markdown_document"

    # Hard-coded fallback sizes — identical to chunking.yaml defaults.
    # Used when config is unavailable (test environments, early startup).
    _FALLBACK_PARAMS: dict = {
        "split_method":        "paragraph_aware",
        "parent_size_tokens":  PARENT_SIZE,
        "child_size_tokens":   CHILD_SIZE,
        "overlap_tokens":      CHILD_OVERLAP,
        "min_words_per_chunk": 5,
    }

    @staticmethod
    def _get_config() -> dict:
        """Lazy import — avoids circular import at module load time."""
        try:
            from backend.core.config_loader import config  # noqa: PLC0415
            return config.get_chunking_config()
        except Exception:
            return {}

    @classmethod
    def resolve(cls, doc_type: str, extension: str = "") -> str:
        """Return the strategy name for this doc_type + file extension combination."""
        chunking     = cls._get_config()
        ext_routing  = chunking.get("extension_routing", {})
        type_routing = chunking.get("content_type_routing", {})

        if extension and extension in ext_routing:
            return ext_routing[extension]
        return type_routing.get(doc_type, cls._DEFAULT_STRATEGY)

    @classmethod
    def get_strategy_params(cls, strategy_name: str) -> dict:
        """Return the full parameter dict for a named strategy."""
        strategies = cls._get_config().get("strategies", {})
        return strategies.get(strategy_name, cls._FALLBACK_PARAMS)

    @classmethod
    def get_split_method(cls, strategy_name: str) -> str:
        """Return the split_method value for a strategy (e.g. 'paragraph_aware')."""
        return cls.get_strategy_params(strategy_name).get("split_method", "paragraph_aware")

    @classmethod
    def supported_extensions(cls) -> set:
        """Return all file extensions that have an explicit routing entry in config."""
        ext_routing = cls._get_config().get("extension_routing", {})
        if ext_routing:
            return set(ext_routing.keys())
        return {".md", ".txt", ".json", ".pdf", ".docx", ".html", ".pptx"}


@dataclass
class Chunk:
    """Represents one child chunk ready for embedding and storage."""
    text:          str
    chunk_id:      str          # UUID5 of text content — deterministic deduplication key
    doc_title:     str
    doc_type:      str          # doc | adr | chat | ticket | code
    metadata:      dict = field(default_factory=dict)
    parent_id:     str  = ""    # links child → parent (parent stored as separate Qdrant point)
    parent_text:   str  = ""    # full parent text — denormalized for single-lookup retrieval
    prev_chunk_id: str  = ""    # previous chunk in document order (empty for first chunk)
    next_chunk_id: str  = ""    # next chunk in document order (empty for last chunk)


def _content_id(text: str) -> str:
    """Deterministic UUID5 from content — Qdrant requires valid UUID point IDs."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, text))


def _extract_section_title(text: str) -> str:
    """
    Extract a section title from leading markdown headers (##, ###, ####).
    Returns empty string if no header found on the first non-empty line.
    """
    for line in text.strip().splitlines():
        line = line.strip()
        if line.startswith('#'):
            return re.sub(r'^#+\s*', '', line).strip()
        if line:
            break   # first non-empty non-header line → no title
    return ""


def _split_by_paragraphs(text: str, max_chars: int) -> list[str]:
    """
    Split text at paragraph boundaries (double newline).
    Never splits mid-paragraph. Falls back to sentence boundaries for long paragraphs.
    """
    paragraphs = re.split(r"\n{2,}", text.strip())
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) + 2 <= max_chars:
            current = (current + "\n\n" + para).strip()
        else:
            if current:
                chunks.append(current)
            if len(para) > max_chars:
                # Single paragraph too long — split by sentences
                sentences = re.split(r"(?<=[.!?])\s+", para)
                current = ""
                for sent in sentences:
                    if len(current) + len(sent) + 1 <= max_chars:
                        current = (current + " " + sent).strip()
                    else:
                        if current:
                            chunks.append(current)
                        current = sent
            else:
                current = para

    if current:
        chunks.append(current)
    return chunks


def _find_break_point(text: str, ideal_end: int) -> int:
    """
    Find the best character position to end a chunk near `ideal_end`.

    Priority (best → fallback):
      1. Sentence boundary (.  !  ?) in last 20% of window
      2. Paragraph boundary (\\n\\n) in last 20% of window
      3. Word boundary (space) in last 10% of window
      4. Hard cut at ideal_end (rare — only for pathological input)
    """
    search_from = max(0, int(ideal_end * 0.80))

    for punct in ('. ', '! ', '? ', '.\n', '!\n', '?\n'):
        pos = text.rfind(punct, search_from, ideal_end)
        if pos != -1:
            return pos + len(punct)

    pos = text.rfind('\n\n', search_from, ideal_end)
    if pos != -1:
        return pos + 2

    search_from_word = max(0, int(ideal_end * 0.90))
    pos = text.rfind(' ', search_from_word, ideal_end)
    if pos != -1:
        return pos + 1

    return ideal_end


def _split_child_chunks(parent_text: str, child_size_chars: int, overlap_chars: int) -> list[str]:
    """
    Sliding-window split of a parent into overlapping child chunks.
    Every chunk starts and ends at a clean sentence/paragraph/word boundary.
    Overlap carries the tail of the previous chunk to preserve context continuity.
    """
    children: list[str] = []
    start    = 0
    text_len = len(parent_text)

    while start < text_len:
        ideal_end = start + child_size_chars

        if ideal_end >= text_len:
            child = parent_text[start:].strip()
            if child:
                children.append(child)
            break

        end = _find_break_point(parent_text, ideal_end)
        if end <= start:
            end = ideal_end

        child = parent_text[start:end].strip()
        if child:
            children.append(child)

        overlap_start = max(start + 1, end - overlap_chars)
        word_start    = parent_text.find(' ', overlap_start)
        start         = (word_start + 1) if (word_start != -1 and word_start < end) else end

    return children


def _has_meaningful_content(text: str, min_words: int = 5) -> bool:
    """
    Quality gate: reject chunks that are only markdown headers, dividers,
    bold labels, or whitespace. Requires at least `min_words` words > 2 chars.

    '## Summary\\n---' → False
    'DB pool set to 50 connections.' → True
    """
    clean = re.sub(r'[#\-*|`_\[\]()\s]+', ' ', text).strip()
    words = [w for w in clean.split() if len(w) > 2]
    return len(words) >= min_words


def _is_noise_title(text: str) -> bool:
    """
    Return True when a Title element from unstructured.io is NOT a real section heading.

    PDFs with bold/colored formatting cause unstructured to misidentify labels,
    code snippets, and metadata strings as Title elements. Demoting these to
    NarrativeText keeps them inside their true section instead of fragmenting it.

    Detected noise categories:
      - Code snippets: contain braces or start with comment markers
      - All-caps labels: DIRTY, CLEAN (≤ 20 chars, fully uppercase)
      - Bare lowercase identifiers: login, password (no spaces, all lowercase)
      - Labels: text ending in colon with ≤ 3 words (Example:, Output:)
      - Metadata lines: contain pipe character (Version 1.0 | 18th July 2024)
      - Decorative: first character is non-alphanumeric (⪠, →, •)
    """
    t = text.strip()
    if not t:
        return True
    # Code snippets
    if '{' in t or '}' in t:
        return True
    if t.startswith('//') or t.startswith('/*'):
        return True
    # Decorative / special-character prefix
    if t and not (t[0].isalpha() or t[0].isdigit()):
        return True
    # Metadata lines with pipe separator
    if '|' in t:
        return True
    # Short labels ending with colon (Example:, Note:, Output:)
    if t.endswith(':') and len(t.split()) <= 3:
        return True
    # All-uppercase short labels (DIRTY, CLEAN)
    if t.isupper() and len(t) <= 20:
        return True
    # Bare lowercase identifier (login, password, etc.)
    if t.islower() and ' ' not in t:
        return True
    return False


class DocumentChunker:
    """
    Chunks documents into parent-child pairs ready for embedding.

    For standard text/markdown:
        chunk_document(text, title, doc_type, metadata)

    For unstructured elements (PDF, DOCX, HTML via unstructured.io):
        chunk_from_elements(elements, title, doc_type, metadata)
        — respects Title element boundaries, preserves page numbers and element types.

    For Slack/Teams message history:
        chunk_slack_messages(messages, metadata)
    """

    def __init__(self):
        # Read sizes from config/chunking.yaml; fall back to module constants
        # so chunking works in test environments where config may not be loaded.
        params = ChunkingStrategyFactory.get_strategy_params("markdown_document")
        parent_tokens  = params.get("parent_size_tokens", PARENT_SIZE)
        child_tokens   = params.get("child_size_tokens",  CHILD_SIZE)
        overlap_tokens = params.get("overlap_tokens",     CHILD_OVERLAP)

        self.parent_max_chars = parent_tokens  * CHARS_PER_TOKEN
        self.child_size_chars = child_tokens   * CHARS_PER_TOKEN
        self.overlap_chars    = overlap_tokens * CHARS_PER_TOKEN
        self._today           = date.today().isoformat()

    def _build_child_metadata(
        self,
        base_metadata:    dict,
        section_title:    str,
        chunk_index:      int,
        total_chunks:     int,
        chunk_size:       int,
        page_number:      int | None = None,
        element_type:     str = "NarrativeText",
    ) -> dict:
        """
        Build the full metadata dict stored alongside each chunk in Qdrant.

        All fields are filterable scalars — used for pre-search narrowing before
        vector similarity (e.g. only search doc_type='adr', or last 30 days).

        Fields:
          section_title    — heading of the section this chunk belongs to
          page_number      — PDF/DOCX page (None for markdown / text files)
          element_type     — unstructured element category (NarrativeText, Table, ListItem…)
          chunk_index      — 0-based position within the document
          total_chunks     — total child chunks in the document
          document_position— 0.0 (start) → 1.0 (end), enables positional boosting
          chunk_size       — character count of this child chunk
          ingested_date    — ISO date when this chunk was added to the vector store
        """
        position = round(chunk_index / max(total_chunks - 1, 1), 3)
        return {
            **base_metadata,
            "section_title":     section_title,
            "page_number":       page_number,
            "element_type":      element_type,
            "chunk_index":       chunk_index,
            "total_chunks":      total_chunks,
            "document_position": position,
            "chunk_size":        chunk_size,
            "ingested_date":     self._today,
        }

    def chunk_document(
        self,
        text:       str,
        doc_title:  str,
        doc_type:   str,
        metadata:   dict,
    ) -> Generator[Chunk, None, None]:
        """
        Chunk plain text / markdown documents.

        Flow: text → parent chunks (paragraph-aware) → child chunks (sliding window)
        Each child carries the full parent_text so the LLM gets broad context.
        Section titles are extracted from leading ## headers.
        """
        if not text or not text.strip():
            logger.warning("DocumentChunker: empty document '%s' — skipping", doc_title)
            return

        parent_texts = _split_by_paragraphs(text, self.parent_max_chars)
        logger.debug("DocumentChunker: '%s' → %d parent chunks", doc_title, len(parent_texts))

        # First pass: collect all children to know total_chunks before yielding
        all_children: list[tuple[str, str, str]] = []  # (child_text, parent_text, section_title)
        for parent_text in parent_texts:
            section_title = _extract_section_title(parent_text)
            child_texts   = _split_child_chunks(parent_text, self.child_size_chars, self.overlap_chars)
            for child_text in child_texts:
                if _has_meaningful_content(child_text):
                    all_children.append((child_text, parent_text, section_title))

        total_chunks = len(all_children)
        # Pre-compute all chunk IDs so prev/next pointers can be set in one pass
        chunk_ids = [_content_id(ct) for ct, _, _ in all_children]

        for idx, (child_text, parent_text, section_title) in enumerate(all_children):
            parent_id = _content_id(parent_text)

            yield Chunk(
                text=child_text,
                chunk_id=chunk_ids[idx],
                doc_title=doc_title,
                doc_type=doc_type,
                metadata=self._build_child_metadata(
                    base_metadata=metadata,
                    section_title=section_title,
                    chunk_index=idx,
                    total_chunks=total_chunks,
                    chunk_size=len(child_text),
                ),
                parent_id=parent_id,
                parent_text=parent_text,
                prev_chunk_id=chunk_ids[idx - 1] if idx > 0 else "",
                next_chunk_id=chunk_ids[idx + 1] if idx < total_chunks - 1 else "",
            )

    def chunk_from_elements(
        self,
        elements:   list,
        doc_title:  str,
        doc_type:   str,
        metadata:   dict,
    ) -> Generator[Chunk, None, None]:
        """
        Chunk documents parsed by unstructured.io (PDF, DOCX, HTML, PPTX…).

        Unlike chunk_document (which works on raw text), this method groups
        elements by section boundary (Title elements) BEFORE chunking. This ensures:
          - A chunk never mixes content from two different sections
          - Section title, page number, and element type are preserved as metadata
          - Tables are always isolated (never merged with prose)

        Flow:
          elements → group by Title boundary → per-section parent-child split
        """
        try:
            from unstructured.documents.elements import Title, Table, Image, Header, Footer, PageBreak
        except ImportError:
            logger.error("chunk_from_elements: unstructured not installed")
            return

        # Group elements into sections: each Title starts a new section
        sections: list[dict] = []
        current_section: dict = {
            "title": doc_title,
            "page":  None,
            "elements": [],
        }

        for el in elements:
            if isinstance(el, (Image, Header, Footer, PageBreak)):
                continue  # no text value

            page = getattr(getattr(el, "metadata", None), "page_number", None)

            if isinstance(el, Title):
                title_text = el.text.strip()
                if _is_noise_title(title_text):
                    # Demote noise title to NarrativeText — append content to current section
                    # instead of creating an artificial section boundary.
                    if title_text:
                        current_section["elements"].append(el)
                else:
                    # Valid section heading — start a new section
                    if current_section["elements"]:
                        sections.append(current_section)
                    current_section = {
                        "title":    title_text,
                        "page":     page,
                        "elements": [],
                    }
            elif isinstance(el, Table):
                # Tables are always isolated — never merged with prose
                if el.text and el.text.strip():
                    sections.append({
                        "title":    current_section["title"],
                        "page":     page,
                        "elements": [el],
                        "isolated": True,
                    })
            else:
                if el.text and el.text.strip():
                    if current_section["page"] is None:
                        current_section["page"] = page
                    current_section["elements"].append(el)

        if current_section["elements"]:
            sections.append(current_section)

        # First pass: build all children with metadata across all sections
        all_items: list[tuple[str, str, str, int | None, str]] = []
        # (child_text, parent_text, section_title, page_number, element_type)

        for section in sections:
            section_title = section["title"]
            page_number   = section["page"]
            is_isolated   = section.get("isolated", False)

            if is_isolated:
                # Table or other isolated element — use as its own parent+child
                el   = section["elements"][0]
                text = el.text.strip()
                etype = type(el).__name__
                all_items.append((text, text, section_title, page_number, etype))
                continue

            # Build section text from all element texts
            section_text = "\n\n".join(
                el.text.strip() for el in section["elements"] if el.text and el.text.strip()
            )
            if not section_text:
                continue

            # Optionally prepend section title as a header for context
            if section_title and section_title != doc_title:
                section_text = f"## {section_title}\n\n{section_text}"

            parent_texts = _split_by_paragraphs(section_text, self.parent_max_chars)
            for parent_text in parent_texts:
                child_texts = _split_child_chunks(parent_text, self.child_size_chars, self.overlap_chars)
                for child_text in child_texts:
                    if _has_meaningful_content(child_text):
                        all_items.append((child_text, parent_text, section_title, page_number, "NarrativeText"))

        total_chunks = len(all_items)
        # Pre-compute all chunk IDs so prev/next pointers can be set in one pass
        chunk_ids = [_content_id(ct) for ct, _, _, _, _ in all_items]

        for idx, (child_text, parent_text, section_title, page_number, element_type) in enumerate(all_items):
            parent_id = _content_id(parent_text)

            yield Chunk(
                text=child_text,
                chunk_id=chunk_ids[idx],
                doc_title=doc_title,
                doc_type=doc_type,
                metadata=self._build_child_metadata(
                    base_metadata=metadata,
                    section_title=section_title,
                    chunk_index=idx,
                    total_chunks=total_chunks,
                    chunk_size=len(child_text),
                    page_number=page_number,
                    element_type=element_type,
                ),
                parent_id=parent_id,
                parent_text=parent_text,
                prev_chunk_id=chunk_ids[idx - 1] if idx > 0 else "",
                next_chunk_id=chunk_ids[idx + 1] if idx < total_chunks - 1 else "",
            )

    def chunk_slack_messages(
        self,
        messages: list[dict],
        metadata: dict,
    ) -> Generator[Chunk, None, None]:
        """
        Chunk Slack/Teams message history.
        All messages = parent thread; each message = child chunk.
        """
        if not messages:
            return

        parent_text = "\n".join(
            f"[{m.get('timestamp', '')}] {m.get('user', 'unknown')}: {m.get('message', '')}"
            for m in messages
        )
        parent_id    = _content_id(parent_text)
        total_chunks = len(messages)

        for idx, msg in enumerate(messages):
            child_text = f"{msg.get('user', 'unknown')}: {msg.get('message', '')}"
            if not _has_meaningful_content(child_text):
                continue
            chunk_id = _content_id(child_text)
            yield Chunk(
                text=child_text,
                chunk_id=chunk_id,
                doc_title=f"Slack #{metadata.get('channel', 'unknown')}",
                doc_type=doc_type if (doc_type := metadata.get("type", "chat")) else "chat",
                metadata=self._build_child_metadata(
                    base_metadata=metadata,
                    section_title=metadata.get("channel", ""),
                    chunk_index=idx,
                    total_chunks=total_chunks,
                    chunk_size=len(child_text),
                    element_type="Message",
                ),
                parent_id=parent_id,
                parent_text=parent_text,
            )
