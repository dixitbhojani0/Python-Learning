"""
backend/memory/redis_cache.py

Semantic response cache backed by Redis.

"Semantic" means we don't match exact strings — we match by meaning.
  "What is blocking the dashboard?" and "What's holding up the dashboard feature?"
  are different strings but nearly identical in meaning (high cosine similarity).
  Both should return the same cached answer without calling the LLM again.

How it works:
  SET: embed(query) → 384-dim vector → store {embedding, response} in Redis hash
  GET: embed(new_query) → scan all cached embeddings → cosine similarity → hit if ≥ threshold

Why scan instead of vector search?
  For <500 cached entries (typical demo), scanning is fast (<10ms).
  Production upgrade: use RedisSearch with HNSW indexing for O(log n) lookup.

TTL: each cache entry expires after 1 hour (configurable in config/redis.yaml).
     After expiry, the same query will be re-answered and re-cached.
"""
import json
import logging
import os
import uuid
from datetime import datetime

import numpy as np

logger = logging.getLogger(__name__)

# ── Config (read from yaml via ConfigLoader would require async; use env + defaults here)
_REDIS_URL        = os.getenv("REDIS_URL", "redis://localhost:6379")
_KEY_PREFIX       = "cache:query:"
_DEFAULT_TTL      = 3600   # 1 hour — matches redis.yaml semantic_cache TTL
_SIM_THRESHOLD    = 0.92   # minimum cosine similarity to count as a cache hit


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    if denom == 0.0:
        return 0.0
    return float(np.dot(va, vb) / denom)


class SemanticCache:
    """
    Redis-backed semantic cache for LLM responses.

    One instance is created at module level (singleton) and shared across all requests.
    The Redis client is created lazily on first use so the module can be imported
    without a running Redis connection (tests, startup, etc.).
    """

    def __init__(self) -> None:
        self._client = None   # created lazily in _get_client()

    async def _get_client(self):
        """Return (or create) the async Redis client."""
        if self._client is None:
            try:
                from redis.asyncio import Redis
                self._client = Redis.from_url(_REDIS_URL, decode_responses=True)
                logger.info("SemanticCache: connected to Redis at %s", _REDIS_URL)
            except Exception:
                logger.exception("SemanticCache: failed to connect to Redis")
                self._client = None
        return self._client

    async def get_cached(self, query: str, user_role: str = "") -> str | None:
        """
        Look for a semantically similar cached response for this role.

        Cache is role-scoped: Developer and Manager asking the same question get
        different cached answers (different persona, different level of detail).
        Scans only keys matching cache:query:{role}:* so roles never cross-contaminate.

        Returns the cached response text if similarity ≥ 0.92, else None.
        """
        client = await self._get_client()
        if client is None:
            return None

        try:
            from backend.rag.retriever import _get_embed_model
            model           = _get_embed_model()
            query_embedding = model.encode([query], show_progress_bar=False)[0].tolist()

            # Scan only this role's cache namespace
            role_prefix = f"{_KEY_PREFIX}{user_role}:" if user_role else _KEY_PREFIX
            keys = await client.keys(f"{role_prefix}*")
            if not keys:
                return None

            best_score    = 0.0
            best_response = None

            for key in keys:
                entry = await client.hgetall(key)
                if not entry or "embedding" not in entry:
                    continue
                stored_emb = json.loads(entry["embedding"])
                similarity = _cosine_similarity(query_embedding, stored_emb)
                if similarity > best_score:
                    best_score    = similarity
                    best_response = entry.get("response")

            if best_score >= _SIM_THRESHOLD and best_response:
                logger.info(
                    "SemanticCache: HIT role='%s' similarity=%.3f for query='%s...'",
                    user_role, best_score, query[:50],
                )
                return best_response

            logger.debug(
                "SemanticCache: MISS role='%s' best_similarity=%.3f for query='%s...'",
                user_role, best_score, query[:50],
            )
            return None

        except Exception:
            logger.exception("SemanticCache.get_cached: error — treating as cache miss")
            return None

    async def set_cached(
        self,
        query: str,
        response: str,
        user_role: str = "",
        ttl: int = _DEFAULT_TTL,
    ) -> None:
        """
        Store a query + response in the role-scoped cache with a TTL.

        Key format: cache:query:{role}:{uuid}
        This ensures a Manager's cached answer is never returned to a Developer.
        """
        if not query.strip() or not response.strip():
            return

        client = await self._get_client()
        if client is None:
            return

        try:
            from backend.rag.retriever import _get_embed_model
            model     = _get_embed_model()
            embedding = model.encode([query], show_progress_bar=False)[0].tolist()

            role_prefix = f"{_KEY_PREFIX}{user_role}:" if user_role else _KEY_PREFIX
            key         = f"{role_prefix}{uuid.uuid4()}"

            await client.hset(key, mapping={
                "query":      query,
                "role":       user_role,
                "embedding":  json.dumps(embedding),
                "response":   response,
                "created_at": datetime.now().isoformat(),
            })
            await client.expire(key, ttl)

            logger.info(
                "SemanticCache: stored role='%s' query='%s...' (ttl=%ds)",
                user_role, query[:50], ttl,
            )

        except Exception:
            logger.exception("SemanticCache.set_cached: error — response not cached")


# ── Module-level singleton — shared by graph nodes and chat route ─────────────
semantic_cache = SemanticCache()
