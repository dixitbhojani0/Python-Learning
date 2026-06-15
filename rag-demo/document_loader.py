import os
import csv
import pypdf
import docx
import pandas as pd
from models import Chunk, DocStats
from config import CHUNK_SIZE, CHUNK_OVERLAP

SUPPORTED_EXTENSIONS = {".txt", ".pdf", ".docx", ".md", ".csv", ".xlsx"}


def _extract_text(fpath: str) -> str:
    ext = os.path.splitext(fpath)[1].lower()
    extractors = {
        ".pdf":  _from_pdf,
        ".docx": _from_docx,
        ".xlsx": _from_xlsx,
        ".csv":  _from_csv,
        ".txt":  _from_text,
        ".md":   _from_text,
    }
    return extractors[ext](fpath)


def _from_text(fpath: str) -> str:
    with open(fpath, "r", encoding="utf-8") as f:
        return f.read()


def _from_pdf(fpath: str) -> str:
    reader = pypdf.PdfReader(fpath)
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


def _from_docx(fpath: str) -> str:
    doc = docx.Document(fpath)
    return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _from_csv(fpath: str) -> str:
    with open(fpath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = [", ".join(f"{k}: {v}" for k, v in row.items()) for row in reader]
    return "\n".join(rows)


def _from_xlsx(fpath: str) -> str:
    df = pd.read_excel(fpath, sheet_name=None)  # all sheets
    parts = []
    for sheet_name, sheet_df in df.items():
        parts.append(f"[Sheet: {sheet_name}]")
        parts.append(sheet_df.to_string(index=False))
    return "\n\n".join(parts)


def _chunk_text(text: str, source: str) -> list[Chunk]:
    words = text.split()
    chunks: list[Chunk] = []
    i = 0
    while i < len(words):
        chunk_words = words[i : i + CHUNK_SIZE]
        idx = len(chunks)
        chunks.append(Chunk(
            id=f"{source}__{idx}",
            text=" ".join(chunk_words),
            source=source,
            chunk_index=idx,
        ))
        i += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def load_documents(docs_dir: str) -> tuple[list[Chunk], list[DocStats]]:
    all_chunks: list[Chunk] = []
    stats: list[DocStats] = []

    for fname in sorted(os.listdir(docs_dir)):
        ext = os.path.splitext(fname)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            continue
        fpath = os.path.join(docs_dir, fname)
        text = _extract_text(fpath)
        chunks = _chunk_text(text, fname)
        all_chunks.extend(chunks)
        stats.append(DocStats(file=fname, chunks=len(chunks), chars=len(text)))

    return all_chunks, stats
