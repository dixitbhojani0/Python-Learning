"""GET/POST/DELETE /api/v1/knowledge — the approved knowledge base document registry.

Documents live in backend/knowledge.json. A document indexed within the last
PROCESSING_WINDOW_S seconds reports status "processing", then "indexed" — so
uploads and reindexing show a realistic lifecycle in the admin panel.

ponytail: file-backed registry — a real RAG stack replaces this module's storage
with the vector store's own catalogue (and wires real ingestion/embedding);
the response shape and the frontend stay the same.
"""

import json
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import BACKEND_DIR

router = APIRouter()

KNOWLEDGE_FILE = BACKEND_DIR / "knowledge.json"
PROCESSING_WINDOW_S = 20
EMBEDDING_MODEL = "text-embedding-3-large"


def _load() -> list[dict]:
    try:
        return json.loads(KNOWLEDGE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HTTPException(503, "knowledge.json is missing from the backend folder") from None
    except json.JSONDecodeError as e:
        raise HTTPException(
            503, f"knowledge.json has a mistake on line {e.lineno}: {e.msg}"
        ) from None


def _save(docs: list[dict]) -> None:
    KNOWLEDGE_FILE.write_text(
        json.dumps(docs, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _status(doc: dict) -> str:
    indexed_at = datetime.fromisoformat(doc["indexedAt"].replace("Z", "+00:00"))
    age = (datetime.now(UTC) - indexed_at).total_seconds()
    return "processing" if age < PROCESSING_WINDOW_S else "indexed"


@router.get("/knowledge")
def list_documents() -> dict:
    docs = [{**d, "status": _status(d)} for d in _load()]
    return {
        "documents": docs,
        "embeddingModel": EMBEDDING_MODEL,
        "totalChunks": sum(d["chunks"] for d in docs),
    }


class UploadRequest(BaseModel):
    name: str
    sizeKb: int = 0  # noqa: N815 — the /api/v1 JSON contract uses camelCase field names


@router.post("/knowledge")
def upload_document(req: UploadRequest) -> dict:
    if not req.name.strip():
        raise HTTPException(400, "Document name must not be empty")
    docs = _load()
    doc = {
        "id": max((d["id"] for d in docs), default=0) + 1,
        "name": req.name.strip(),
        "category": "Pending review",
        "sizeKb": max(req.sizeKb, 1),
        "chunks": max(4, req.sizeKb // 48),
        "indexedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    docs.append(doc)
    _save(docs)
    return {**doc, "status": "processing"}


@router.post("/knowledge/{doc_id}/reindex")
def reindex_document(doc_id: int) -> dict:
    docs = _load()
    for doc in docs:
        if doc["id"] == doc_id:
            doc["indexedAt"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            _save(docs)
            return {**doc, "status": "processing"}
    raise HTTPException(404, "Document not found")


@router.delete("/knowledge/{doc_id}")
def delete_document(doc_id: int) -> dict:
    docs = _load()
    remaining = [d for d in docs if d["id"] != doc_id]
    if len(remaining) == len(docs):
        raise HTTPException(404, "Document not found")
    _save(remaining)
    return {"ok": True}
