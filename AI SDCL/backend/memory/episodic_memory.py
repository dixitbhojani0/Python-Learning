"""
backend/memory/episodic_memory.py

Episodic memory — the 4th memory layer (Solution Document Section 11).

Where semantic memory stores standalone FACTS ("Alice owns auth"), episodic
memory stores ordered EVENTS with timestamps:
    [ticket SDLC-42 created] → [reviewer assigned to PR-5] → [release approved]

This is what lets the assistant answer "how did we resolve the last auth
incident?" with the full sequence of actions, not just the end state.

Storage: Qdrant collection "episodic_memory" (separate from RAG + semantic).
  Each event = one point: embedding of the event text + metadata
  metadata: text, event_type, project_id, actor, ref, session_id, created_at

This mirrors SemanticMemory's storage approach (reuses the same Qdrant client and
embedding model) so it adds zero new infrastructure. All methods degrade
gracefully — any Qdrant failure returns empty / no-op and never propagates.

Write path: HITL approval handler records an event per approved action.
Read path:  retrieve_timeline (chronological) + search_events (semantic).
"""
import logging
import uuid
from datetime import datetime

from backend.core.settings import settings

logger = logging.getLogger(__name__)

# Reuse a dedicated collection; name overridable via settings without code change.
_COLLECTION = getattr(settings, "QDRANT_COLLECTION_EPISODIC", "episodic_memory")
_VECTOR_DIM = 384      # all-MiniLM-L6-v2 output dimension
_TOP_K      = 5        # events returned per semantic search


def _get_client():
    from qdrant_client import QdrantClient
    return QdrantClient(url=settings.QDRANT_URL)


def _get_embed_model():
    """Reuse the same sentence-transformer model used by the RAG retriever."""
    from backend.rag.retriever import _get_embed_model as _rag_embed
    return _rag_embed()


def _ensure_collection(client) -> None:
    from qdrant_client.models import VectorParams, Distance
    existing = [c.name for c in client.get_collections().collections]
    if _COLLECTION not in existing:
        client.create_collection(
            collection_name=_COLLECTION,
            vectors_config=VectorParams(size=_VECTOR_DIM, distance=Distance.COSINE),
        )
        logger.info("EpisodicMemory: created Qdrant collection '%s'", _COLLECTION)


class EpisodicMemory:
    """
    Ordered event store.

    record_event(...)               — append one event (write)
    retrieve_timeline(project_id)   — most recent events, newest first (read)
    search_events(query, project)   — semantically relevant past events (read)
    """

    def __init__(self) -> None:
        self._client = None

    def _get_or_create_client(self):
        if self._client is None:
            try:
                self._client = _get_client()
                _ensure_collection(self._client)
            except Exception:
                logger.exception("EpisodicMemory: Qdrant connection failed")
                self._client = None
        return self._client

    async def record_event(
        self,
        text: str,
        event_type: str,
        project_id: str,
        actor: str = "",
        ref: str = "",
        session_id: str = "",
    ) -> bool:
        """
        Store one event. Returns True if stored, False on any failure / no-op.

        Called from the HITL approval handler after an action succeeds. Designed
        to be non-blocking: a failure here must never affect the user's response.
        """
        text = (text or "").strip()
        if not text:
            return False
        client = self._get_or_create_client()
        if client is None:
            return False
        try:
            model     = _get_embed_model()
            embedding = model.encode([text], show_progress_bar=False)[0].tolist()
            created   = datetime.now().isoformat()
            # Unique per event — timestamp keeps repeated identical actions distinct.
            point_id  = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{project_id}:{ref}:{created}:{text}"))

            from qdrant_client.models import PointStruct
            client.upsert(
                collection_name=_COLLECTION,
                points=[PointStruct(
                    id=point_id,
                    vector=embedding,
                    payload={
                        "text":       text,
                        "event_type": event_type,
                        "project_id": project_id,
                        "actor":      actor,
                        "ref":        ref,
                        "session_id": session_id,
                        "created_at": created,
                    },
                )],
            )
            logger.info("EpisodicMemory: recorded [%s] '%s...' (project='%s')", event_type, text[:60], project_id)
            return True
        except Exception:
            logger.exception("EpisodicMemory.record_event: failed — event not stored")
            return False

    def retrieve_timeline(self, project_id: str, limit: int = 10) -> list[dict]:
        """Return the most recent events for a project, newest first."""
        client = self._get_or_create_client()
        if client is None:
            return []
        try:
            from qdrant_client.models import Filter, FieldCondition, MatchValue
            points, _ = client.scroll(
                collection_name=_COLLECTION,
                scroll_filter=Filter(
                    must=[FieldCondition(key="project_id", match=MatchValue(value=project_id))]
                ),
                limit=200,
                with_payload=True,
            )
            events = [p.payload for p in points if p.payload]
            events.sort(key=lambda e: e.get("created_at", ""), reverse=True)
            return events[:limit]
        except Exception:
            logger.exception("EpisodicMemory.retrieve_timeline: error")
            return []

    def search_events(self, query: str, project_id: str) -> list[dict]:
        """Return events semantically relevant to the query (for 'how did we…' lookups)."""
        client = self._get_or_create_client()
        if client is None:
            return []
        try:
            from qdrant_client.models import Filter, FieldCondition, MatchValue
            model     = _get_embed_model()
            embedding = model.encode([query], show_progress_bar=False)[0].tolist()
            results = client.search(
                collection_name=_COLLECTION,
                query_vector=embedding,
                query_filter=Filter(
                    must=[FieldCondition(key="project_id", match=MatchValue(value=project_id))]
                ),
                limit=_TOP_K,
                with_payload=True,
            )
            return [r.payload for r in results if r.payload]
        except Exception:
            logger.exception("EpisodicMemory.search_events: error")
            return []


# ── Module-level singleton ────────────────────────────────────────────────────
episodic_memory = EpisodicMemory()
