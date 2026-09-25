"""
backend/app/rag/parsers/base.py

Same self-registering plugin shape as adapters/llm and adapters/embedding
(§30: a proven pattern, not a new one invented for this). A parser turns raw
uploaded bytes into plain text; everything downstream of that (chunking,
embedding, storage) doesn't know or care whether the text came from a pasted
textarea or an extracted PDF — the pipeline stays exactly as it was.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class DocumentParseError(Exception):
    """Raised when a file's bytes cannot be parsed as its claimed type (corrupt, password-protected, etc.)."""


class BaseDocumentParser(ABC):
    @abstractmethod
    def parse(self, raw_bytes: bytes) -> str:
        """Extract plain text from raw file bytes. Raises DocumentParseError on failure."""
        ...
