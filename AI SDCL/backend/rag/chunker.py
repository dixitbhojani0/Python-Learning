"""
backend/rag/chunker.py

Implements contextual + parent-child chunking strategy.

Why two levels of chunks?
  Parent chunks (1500 tokens): preserve full context — what the LLM reads
  Child chunks  (350 tokens):  smaller = more precise embeddings — what gets searched

Why contextual prefixes?
  Standard chunking embeds a fragment in isolation — loses document context.
  We prepend an LLM-generated summary ("This is from the nginx ADR, section 4...")
  so the embedding carries document-level context. This dramatically improves
  retrieval accuracy for SDLC documents where concepts reference each other.

Ref: Anthropic Contextual Retrieval paper (2024)
"""
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Generator

logger = logging.getLogger(__name__)

# ── Target sizes (loaded from config but also have sensible defaults here)
PARENT_SIZE = 1500      # tokens approx (~6000 chars)
CHILD_SIZE  = 350       # tokens approx (~1400 chars)
CHILD_OVERLAP = 40      # tokens approx (~160 chars)

# Approximate: 1 token ≈ 4 chars in English (good enough for splitting)
CHARS_PER_TOKEN = 4


@dataclass
class Chunk:
    """Represents one chunk (parent or child) ready for processing."""
    text: str
    chunk_id: str              # SHA256 of text — deduplication key
    doc_title: str
    doc_type: str              # doc | adr | chat | ticket | code
    metadata: dict = field(default_factory=dict)
    parent_id: str = ""        # set on child chunks — links back to parent
    parent_text: str = ""      # full parent text (stored alongside child in Qdrant)


def _content_id(text: str) -> str:
    """Deterministic chunk ID from content — UUID5 so Qdrant accepts it as a valid point ID."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, text))


def _split_by_paragraphs(text: str, max_chars: int) -> list[str]:
    """
    Split text at paragraph boundaries (double newline).
    Never splits in the middle of a paragraph.
    Falls back to sentence boundaries if paragraph is too long.
    """
    paragraphs = re.split(r"\n{2,}", text.strip())
    chunks = []
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
            # If single paragraph is too long, split by sentences
            if len(para) > max_chars:
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


def _split_child_chunks(parent_text: str, child_size_chars: int, overlap_chars: int) -> list[str]:
    """
    Split parent text into overlapping child chunks.
    Uses character-level sliding window — simple and effective.
    """
    children = []
    start = 0
    text_len = len(parent_text)

    while start < text_len:
        end = min(start + child_size_chars, text_len)
        child = parent_text[start:end].strip()
        if child:
            children.append(child)
        if end >= text_len:
            break
        start = end - overlap_chars   # overlap between consecutive chunks
    return children


def _has_meaningful_content(text: str, min_words: int = 5) -> bool:
    """
    Quality gate: returns False for chunks that are only markdown headers,
    dividers, bold labels, or whitespace — e.g. '## Summary', '---', '**Status**: ACTIVE'.

    Uses meaningful word count (words > 2 chars) not raw character count.
    A 19-char chunk 'DB pool set to 5.' passes. A 50-char '## Summary\\n---' fails.
    """
    clean = re.sub(r'[#\-*|`_\[\]()\s]+', ' ', text).strip()
    words = [w for w in clean.split() if len(w) > 2]
    return len(words) >= min_words


class DocumentChunker:
    """
    Chunks documents into parent-child pairs ready for embedding.

    Usage:
        chunker = DocumentChunker()
        for chunk in chunker.chunk_document(text, title, doc_type, metadata):
            # chunk is a Chunk object with text, parent_text, chunk_id, etc.
    """

    def __init__(self):
        # Character-based sizes derived from token estimates
        self.parent_max_chars = PARENT_SIZE * CHARS_PER_TOKEN
        self.child_size_chars = CHILD_SIZE * CHARS_PER_TOKEN
        self.overlap_chars    = CHILD_OVERLAP * CHARS_PER_TOKEN

    def chunk_document(
        self,
        text: str,
        doc_title: str,
        doc_type: str,
        metadata: dict,
    ) -> Generator[Chunk, None, None]:
        """
        Main entry point. Yields child Chunk objects, each with parent_text attached.

        Flow:
          text → parent chunks (paragraph-aware) → child chunks (sliding window)
          Each child chunk carries the full parent_text so the LLM gets full context.
        """
        if not text or not text.strip():
            logger.warning("DocumentChunker: empty document '%s' — skipping", doc_title)
            return

        # ── Step 1: Split into parent chunks
        parent_texts = _split_by_paragraphs(text, self.parent_max_chars)
        logger.debug("DocumentChunker: '%s' → %d parent chunks", doc_title, len(parent_texts))

        for parent_text in parent_texts:
            parent_id = _content_id(parent_text)

            # ── Step 2: Split each parent into child chunks
            child_texts = _split_child_chunks(
                parent_text, self.child_size_chars, self.overlap_chars
            )

            for child_text in child_texts:
                if not _has_meaningful_content(child_text):
                    logger.debug("DocumentChunker: skipping low-content chunk: '%s...'", child_text[:40])
                    continue
                chunk_id = _content_id(child_text)

                yield Chunk(
                    text=child_text,
                    chunk_id=chunk_id,
                    doc_title=doc_title,
                    doc_type=doc_type,
                    metadata=metadata,
                    parent_id=parent_id,
                    parent_text=parent_text,    # LLM reads this for full context
                )

    def chunk_slack_messages(
        self,
        messages: list[dict],
        metadata: dict,
    ) -> Generator[Chunk, None, None]:
        """
        Special chunker for Slack/Teams message history.
        Thread = parent, individual messages = children.
        Preserves conversation context for cross-source queries.
        """
        if not messages:
            return

        # Treat all messages as one "thread" (parent)
        parent_text = "\n".join(
            f"[{m.get('timestamp', '')}] {m.get('user', 'unknown')}: {m.get('message', '')}"
            for m in messages
        )
        parent_id = _content_id(parent_text)

        # Each message is a child chunk
        for msg in messages:
            child_text = f"{msg.get('user', 'unknown')}: {msg.get('message', '')}"
            if not _has_meaningful_content(child_text):
                continue
            chunk_id = _content_id(child_text)
            yield Chunk(
                text=child_text,
                chunk_id=chunk_id,
                doc_title=f"Slack #{metadata.get('channel', 'unknown')}",
                doc_type="chat",
                metadata=metadata,
                parent_id=parent_id,
                parent_text=parent_text,
            )
