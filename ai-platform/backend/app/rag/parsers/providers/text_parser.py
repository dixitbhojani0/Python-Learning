"""
backend/app/rag/parsers/providers/text_parser.py

Plain text and Markdown — no library needed, just a UTF-8 decode. Markdown's
syntax characters (#, *, -, etc.) are left in place rather than stripped:
they're still meaningful words/structure for chunking and retrieval, and
stripping them would need a real Markdown parser for something cosmetic.
"""
from __future__ import annotations

from backend.app.rag.parsers.base import BaseDocumentParser, DocumentParseError
from backend.app.rag.parsers.registry import DocumentParserRegistry


class TextParser(BaseDocumentParser):
    def parse(self, raw_bytes: bytes) -> str:
        try:
            return raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DocumentParseError(f"Could not decode file as UTF-8 text: {exc}") from exc


DocumentParserRegistry.register("txt", TextParser)
DocumentParserRegistry.register("md", TextParser)
