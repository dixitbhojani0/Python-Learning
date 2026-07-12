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
import asyncio
import json
import logging
import uuid
from datetime import datetime

import numpy as np

from backend.core.config_loader import config
from backend.core.settings import settings as _settings

logger = logging.getLogger(__name__)


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
        self._init_lock = asyncio.Lock()  # prevents double-init under concurrent cold-start

        redis_cfg = config.get_redis_config()
        rag_cfg   = config.get_rag_config()

        self._redis_url     = _settings.REDIS_URL
        self._key_prefix    = redis_cfg.get("key_prefixes", {}).get("semantic_cache", "cache:query:")
        self._default_ttl   = redis_cfg.get("ttl", {}).get("semantic_cache", 3600)
        self._sim_threshold = rag_cfg.get("retrieval", {}).get("cache_similarity_threshold", 0.92)

    async def _get_client(self):
        """Return (or create) the async Redis client."""
        if self._client is not None:
            return self._client
        async with self._init_lock:
            if self._client is None:  # re-check after acquiring lock
                try:
                    from redis.asyncio import Redis
                    self._client = Redis.from_url(self._redis_url, decode_responses=True)
                    logger.info("SemanticCache: connected to Redis at %s", self._redis_url)
                except Exception:
                    logger.exception("SemanticCache: failed to connect to Redis")
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
            role_prefix = f"{self._key_prefix}{user_role}:" if user_role else self._key_prefix
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

            if best_score >= self._sim_threshold and best_response:
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
        ttl: int | None = None,
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

        effective_ttl = ttl if ttl is not None else self._default_ttl

        try:
            from backend.rag.retriever import _get_embed_model
            model     = _get_embed_model()
            embedding = model.encode([query], show_progress_bar=False)[0].tolist()

            role_prefix = f"{self._key_prefix}{user_role}:" if user_role else self._key_prefix
            key         = f"{role_prefix}{uuid.uuid4()}"

            await client.hset(key, mapping={
                "query":      query,
                "role":       user_role,
                "embedding":  json.dumps(embedding),
                "response":   response,
                "created_at": datetime.now().isoformat(),
            })
            await client.expire(key, effective_ttl)

            logger.info(
                "SemanticCache: stored role='%s' query='%s...' (ttl=%ds)",
                user_role, query[:50], effective_ttl,
            )

        except Exception:
            logger.exception("SemanticCache.set_cached: error — response not cached")

    async def invalidate(self, query: str, user_role: str = "") -> bool:
        """
        Delete all cache entries semantically similar to this query for this role.

        Called after HITL approve/reject — the world state just changed (a ticket was
        created, a release was approved, a reviewer was assigned), so any cached answer
        about that topic is now stale and must not be served again.

        Returns True if at least one entry was deleted.
        """
        client = await self._get_client()
        if client is None:
            return False

        try:
            from backend.rag.retriever import _get_embed_model
            model           = _get_embed_model()
            query_embedding = model.encode([query], show_progress_bar=False)[0].tolist()

            role_prefix = f"{self._key_prefix}{user_role}:" if user_role else self._key_prefix
            keys        = await client.keys(f"{role_prefix}*")
            if not keys:
                return False

            deleted = 0
            for key in keys:
                entry = await client.hgetall(key)
                if not entry or "embedding" not in entry:
                    continue
                stored_emb = json.loads(entry["embedding"])
                similarity = _cosine_similarity(query_embedding, stored_emb)
                if similarity >= self._sim_threshold:
                    await client.delete(key)
                    deleted += 1
                    logger.info(
                        "SemanticCache.invalidate: deleted key='%s' similarity=%.3f role='%s'",
                        key, similarity, user_role,
                    )

            if deleted:
                logger.info(
                    "SemanticCache.invalidate: %d stale entr%s removed for role='%s' query='%s...'",
                    deleted, "y" if deleted == 1 else "ies", user_role, query[:50],
                )
            return deleted > 0

        except Exception:
            logger.exception("SemanticCache.invalidate: error — cache not invalidated")
            return False


# ── Module-level singleton — shared by graph nodes and chat route ─────────────
semantic_cache = SemanticCache()
