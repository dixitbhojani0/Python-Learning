"""
backend/app/rag/parsers/providers/docx_parser.py

python-docx — the standard, actively-maintained library for .docx (a zipped
XML format; no stdlib equivalent exists, unlike .txt/.md/.csv). Extracts
paragraph text only, not tables/headers/footers/images — a real, scoped
limitation, not a silent one: a heavily tabular document loses that
structure here the same way it would in a plain chunker anyway.
"""
from __future__ import annotations

import io

import docx

from backend.app.rag.parsers.base import BaseDocumentParser, DocumentParseError
from backend.app.rag.parsers.registry import DocumentParserRegistry


class DocxParser(BaseDocumentParser):
    def parse(self, raw_bytes: bytes) -> str:
        # .docx is a zip archive with a specific internal package structure.
        # A malformed input fails in genuinely different ways depending on
        # WHAT'S wrong — confirmed empirically, not guessed at up front:
        # not a zip at all raises zipfile.BadZipFile; a valid zip missing the
        # docx package parts raises docx.opc.exceptions.PackageNotFoundError;
        # a valid zip with SOME but not all expected parts (e.g. missing
        # [Content_Types].xml) raises a bare KeyError from inside zipfile
        # itself, not a python-docx exception at all. There's no single
        # clean "this isn't a docx" exception type to catch, so this
        # deliberately catches Exception broadly and re-raises as the one
        # error type every parser in this registry promises to raise.
        try:
            document = docx.Document(io.BytesIO(raw_bytes))
        except Exception as exc:
            raise DocumentParseError(f"Could not read .docx file: {exc}") from exc
        return "\n\n".join(p.text for p in document.paragraphs if p.text.strip())


DocumentParserRegistry.register("docx", DocxParser)
