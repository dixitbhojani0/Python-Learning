"""
backend/app/rag/chunking.py

Fixed-size sliding-window chunking by word count — the simplest thing that
works, not the parent-child hierarchical chunker ai-sdlc-assistant uses
(that's a real upgrade path once retrieval quality is actually measured
against a golden set, §22 of the blueprint — not something to build ahead
of having that measurement).
"""
from __future__ import annotations


def chunk_text(text: str, *, chunk_size: int = 200, overlap: int = 20) -> list[str]:
    """
    Split `text` into overlapping windows of `chunk_size` words, sliding by
    `chunk_size - overlap` words each step. Returns [] for blank input —
    ingesting an empty document produces zero chunks, not one empty chunk.
    """
    if overlap >= chunk_size:
        raise ValueError(f"overlap ({overlap}) must be smaller than chunk_size ({chunk_size})")

    words = text.split()
    if not words:
        return []

    stride = chunk_size - overlap
    chunks = []
    for start in range(0, len(words), stride):
        window = words[start : start + chunk_size]
        chunks.append(" ".join(window))
        if start + chunk_size >= len(words):
            break
    return chunks
