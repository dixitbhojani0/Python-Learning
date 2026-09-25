"""
backend/app/rag/parsers/providers/csv_parser.py

stdlib csv — no dependency needed. Each row is rendered as
"header: value; header: value; ..." rather than a raw comma-joined line, so
a chunk boundary landing mid-table still keeps each row's fields legible and
semantically self-contained for retrieval (a bare "Acme Corp,42,active" chunk
means nothing on its own; "name: Acme Corp; count: 42; status: active" does).
The first row is always treated as the header — a real limitation for a
headerless CSV, not a silent one: rows would render with positional "col_N"
labels instead of real names in that case.
"""
from __future__ import annotations

import csv
import io

from backend.app.rag.parsers.base import BaseDocumentParser, DocumentParseError
from backend.app.rag.parsers.registry import DocumentParserRegistry


class CsvParser(BaseDocumentParser):
    def parse(self, raw_bytes: bytes) -> str:
        try:
            text = raw_bytes.decode("utf-8-sig")  # -sig strips a BOM if Excel added one
        except UnicodeDecodeError as exc:
            raise DocumentParseError(f"Could not decode file as UTF-8 text: {exc}") from exc

        rows = list(csv.reader(io.StringIO(text)))
        if not rows:
            return ""

        header, data_rows = rows[0], rows[1:]
        header = [h.strip() or f"col_{i}" for i, h in enumerate(header)]

        lines = []
        for row in data_rows:
            pairs = "; ".join(f"{col}: {val}" for col, val in zip(header, row))
            if pairs:
                lines.append(pairs)
        return "\n".join(lines)


DocumentParserRegistry.register("csv", CsvParser)
