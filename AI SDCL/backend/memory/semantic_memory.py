"""
backend/memory/semantic_memory.py

Long-term semantic fact store — Qdrant-backed, no TTL.

Difference from the Redis semantic cache:
  Redis cache  → stores full LLM RESPONSES (expire in 1 hour, purpose: speed)
  Semantic memory → stores extracted PROJECT FACTS (never expire, purpose: knowledge)

What facts does it store?
  - Detected blockers: "Dashboard feature is blocked by the nginx CORS issue"
  - Team assignments: "Alice owns the auth service"
  - Sprint status: "Sprint 12 velocity is 34 points, below the 40-point capacity"
  - Resolutions: "The nginx CORS issue was resolved by adding the OPTIONS pre-flight handler"
  - Decisions: "Team decided to use Redis for session caching instead of in-memory"

Why store facts separately from RAG documents?
  RAG retrieves from static documents (sprint plans, ADRs, Slack JSON) ingested at a point in time.
  Semantic memory accumulates LIVE knowledge from conversations — things said by team members
  that never made it into a formal document. This fills the gap between "what was written" and
  "what was discussed and decided".

Storage: Qdrant collection "semantic_memory" (separate from the RAG collection)
  - Each fact = one Qdrant point: embedding + metadata
  - metadata: text, project_id, user_id, session_id, category, created_at

Retrieval: embed(query) → top-k similarity search → inject as semantic_context in SDLCState
Extraction: after each turn, LLM extracts 0-3 facts via `semantic_fact_extractor` prompt
"""
import logging
import uuid
from datetime import datetime
from typing import Any

from backend.core.settings import settings

logger = logging.getLogger(__name__)

_COLLECTION          = settings.QDRANT_COLLECTION_SEMANTIC
_VECTOR_DIM          = 384     # all-MiniLM-L6-v2 output dimension
_TOP_K               = 5       # facts to retrieve per query
_DEDUP_THRESHOLD     = 0.93    # don't store a fact if one this similar already exists
_SUPERSEDE_THRESHOLD = 0.82    # delete facts in [0.82, 0.93) — same topic, different claim
_FACT_MAX_AGE_DAYS   = 30      # ignore facts older than this on retrieval


def _get_client():
    """Return a Qdrant client — same URL as the RAG vector store."""
    from qdrant_client import QdrantClient
    from backend.core.settings import settings
    return QdrantClient(url=settings.QDRANT_URL)


def _get_embed_model():
    """Reuse the same sentence-transformer model used by the RAG retriever."""
    from backend.rag.retriever import _get_embed_model as _rag_embed
    return _rag_embed()


def _ensure_collection(client) -> None:
    """Create the semantic_memory Qdrant collection if it doesn't already exist."""
    from qdrant_client.models import VectorParams, Distance
    existing = [c.name for c in client.get_collections().collections]
    if _COLLECTION not in existing:
        client.create_collection(
            collection_name=_COLLECTION,
            vectors_config=VectorParams(size=_VECTOR_DIM, distance=Distance.COSINE),
        )
        logger.info("SemanticMemory: created Qdrant collection '%s'", _COLLECTION)


def _is_duplicate(client, embedding: list[float], project_id: str) -> bool:
    """
    Return True if a nearly-identical fact already exists for this project.

    Uses the Qdrant similarity search with a high threshold (0.93).
    Prevents accumulating slightly-rephrased versions of the same fact.
    """
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    try:
        results = client.search(
            collection_name=_COLLECTION,
            query_vector=embedding,
            query_filter=Filter(
                must=[FieldCondition(key="project_id", match=MatchValue(value=project_id))]
            ),
            limit=1,
            score_threshold=_DEDUP_THRESHOLD,
        )
        return len(results) > 0
    except Exception:
        return False   # if check fails, allow the write rather than silently dropping facts


def _find_supersedable(client, embedding: list[float], project_id: str) -> list[str]:
    """
    Return Qdrant point IDs that are related (≥ 0.82) but not identical (< 0.93).

    These are facts about the same topic that contradict the incoming fact.
    Example: "team uses OAuth" when we're storing "team switched to JWT" — cosine ~0.85.
    Returning them here lets the caller delete them before storing the new fact
    so the store converges to the latest truth rather than accumulating contradictions.
    """
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    try:
        results = client.search(
            collection_name=_COLLECTION,
            query_vector=embedding,
            query_filter=Filter(
                must=[FieldCondition(key="project_id", match=MatchValue(value=project_id))]
            ),
            limit=10,
            score_threshold=_SUPERSEDE_THRESHOLD,
            with_payload=False,
        )
        return [str(r.id) for r in results if r.score < _DEDUP_THRESHOLD]
    except Exception:
        return []


class SemanticMemory:
    """
    Long-term project knowledge store.

    Two public methods:
      extract_and_save(query, response, project_id, session_id, user_id)
          — calls the LLM to extract facts, stores each one in Qdrant

      retrieve_facts(query, project_id) -> list[str]
          — embeds query, searches Qdrant, returns fact texts ranked by relevance
    """

    def __init__(self) -> None:
        self._client = None

    def _get_or_create_client(self):
        if self._client is None:
            try:
                self._client = _get_client()
                _ensure_collection(self._client)
            except Exception:
                logger.exception("SemanticMemory: Qdrant connection failed")
                self._client = None
        return self._client

    async def extract_and_save(
        self,
        query: str,
        response: str,
        project_id: str,
        session_id: str = "",
        user_id: str = "",
    ) -> int:
        """
        Extract 0-3 key facts from one Q&A turn and store each in Qdrant.

        Returns the number of new facts stored (0 if extraction failed or all were duplicates).

        Called from chat.py after every response — designed to be non-blocking:
        any failure here must not propagate to the user's response.
        """
        client = self._get_or_create_client()
        if client is None:
            return 0

        try:
            facts = await self._extract_facts(query, response, project_id)
            if not facts:
                logger.debug("SemanticMemory: no facts extracted for project='%s'", project_id)
                return 0

            model   = _get_embed_model()
            stored  = 0

            for fact in facts:
                fact_text = fact.get("fact", "").strip()
                category  = fact.get("category", "general")
                if not fact_text or len(fact_text) < 10:
                    continue

                embedding = model.encode([fact_text], show_progress_bar=False)[0].tolist()

                # Deduplication — skip if semantically identical fact already exists
                if _is_duplicate(client, embedding, project_id):
                    logger.debug("SemanticMemory: duplicate fact skipped — '%s...'", fact_text[:60])
                    continue

                # Supersession — delete related-but-different facts before storing the new one.
                # Prevents contradictory facts (e.g. "uses OAuth" vs "switched to JWT")
                # from both surviving in the store. Last fact wins.
                to_delete = _find_supersedable(client, embedding, project_id)
                if to_delete:
                    from qdrant_client.models import PointIdsList
                    client.delete(
                        collection_name=_COLLECTION,
                        points_selector=PointIdsList(points=to_delete),
                    )
                    logger.info(
                        "SemanticMemory: superseded %d old fact(s) before storing '%s...' (project='%s')",
                        len(to_delete), fact_text[:50], project_id,
                    )

                point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{project_id}:{fact_text}"))

                from qdrant_client.models import PointStruct
                client.upsert(
                    collection_name=_COLLECTION,
                    points=[
                        PointStruct(
                            id=point_id,
                            vector=embedding,
                            payload={
                                "text":        fact_text,
                                "category":    category,
                                "project_id":  project_id,
                                "session_id":  session_id,
                                "user_id":     user_id,
                                "source_query": query[:200],
                                "created_at":  datetime.now().isoformat(),
                            },
                        )
                    ],
                )
                stored += 1
                logger.info(
                    "SemanticMemory: stored fact [%s] '%s...' (project='%s')",
                    category, fact_text[:60], project_id,
                )

            return stored

        except Exception:
            logger.exception("SemanticMemory.extract_and_save: error — facts not stored")
            return 0

    async def _extract_facts(
        self, query: str, response: str, project_id: str
    ) -> list[dict]:
        """
        Call the LLM with the semantic_fact_extractor prompt.
        Returns a list of {"fact": str, "category": str} dicts.
        Returns [] on any failure.
        """
        import json, re
        from backend.core.config_loader import config
        from backend.providers.factory import LLMFactory

        try:
            provider = LLMFactory.get_provider()

            prompt = config.get_prompt(
                "semantic_fact_extractor",
                query=query,
                response=response[:1000],   # cap to avoid token bloat
                project_id=project_id,
            )
            system = config.get_prompt("system_prompt")

            resp = await provider.generate_text(prompt, system, temperature=0.1, max_tokens=300)
            raw  = resp.text.strip()

            # Extract JSON array from the response
            match = re.search(r"\[.*\]", raw, re.DOTALL)
            if not match:
                logger.debug("SemanticMemory: no JSON array in extractor output")
                return []

            facts = json.loads(match.group(0))
            if not isinstance(facts, list):
                return []

            return facts[:3]   # cap at 3 facts per turn

        except Exception:
            logger.exception("SemanticMemory._extract_facts: LLM call failed")
            return []

    def retrieve_facts(self, query: str, project_id: str) -> list[str]:
        """
        Retrieve the top-k most relevant facts for this query.

        Synchronous — called inside graph.py's retrieve_memory_context node which
        runs in LangGraph's async executor. Since Qdrant client is sync, this is fine.

        Returns list of fact strings, ordered by relevance (most relevant first).
        Facts older than _FACT_MAX_AGE_DAYS are excluded — stale facts mislead more than they help.
        Returns [] on any error or if Qdrant is unavailable.
        """
        client = self._get_or_create_client()
        if client is None:
            return []

        try:
            from datetime import timedelta
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

            # ISO 8601 strings are lexicographically comparable — simple string cutoff works.
            cutoff = (datetime.now() - timedelta(days=_FACT_MAX_AGE_DAYS)).isoformat()
            facts  = [
                hit.payload.get("text", "")
                for hit in results
                if hit.payload.get("text")
                and hit.payload.get("created_at", "") >= cutoff
            ]
            logger.info(
                "SemanticMemory.retrieve_facts: project='%s' query='%s...' → %d facts (cutoff=%s)",
                project_id, query[:50], len(facts), cutoff[:10],
            )
            return facts

        except Exception:
            logger.exception("SemanticMemory.retrieve_facts: error — returning empty facts")
            return []


# ── Module-level singleton ────────────────────────────────────────────────────
semantic_memory = SemanticMemory()
