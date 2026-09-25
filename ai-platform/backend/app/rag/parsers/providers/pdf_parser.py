"""
backend/app/rag/parsers/providers/pdf_parser.py

pypdf (pure Python, no further dependencies — PyPDF2 was deprecated and
merged back into this project) for basic text extraction. Deliberately not
pdfplumber: that trades this simplicity for table/layout preservation this
platform's plain-text chunker (rag/chunking.py) can't use anyway — it's a
real upgrade path if a future need for structured/tabular extraction
actually shows up, not something to reach for ahead of that.
"""
from __future__ import annotations

import io

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from backend.app.rag.parsers.base import BaseDocumentParser, DocumentParseError
from backend.app.rag.parsers.registry import DocumentParserRegistry


class PdfParser(BaseDocumentParser):
    def parse(self, raw_bytes: bytes) -> str:
        try:
            reader = PdfReader(io.BytesIO(raw_bytes))
            if reader.is_encrypted:
                raise DocumentParseError("PDF is password-protected; cannot extract text without a password.")
            return "\n\n".join(page.extract_text() or "" for page in reader.pages)
        except PyPdfError as exc:
            raise DocumentParseError(f"Could not read PDF: {exc}") from exc


DocumentParserRegistry.register("pdf", PdfParser)
