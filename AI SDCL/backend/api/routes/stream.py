"""
backend/api/routes/stream.py

GET /api/stream/{stream_id} — Server-Sent Events (SSE) token streaming.

Why SSE instead of WebSocket?
  SSE is one-directional (server → client), which is exactly what we need for
  streaming LLM tokens. WebSockets are bidirectional and require a persistent
  handshake — overkill for token delivery.
  SSE works over plain HTTP, works through proxies, and reconnects automatically
  in the browser.

Protocol:
  1. Client opens GET /api/stream/{stream_id}
  2. Server polls Redis stream key "stream:{stream_id}" for token chunks
  3. Each token is sent as:   data: {"type": "token", "text": "..."}\n\n
  4. When a HITL proposal is ready:
                               data: {"type": "hitl_request", "hitl_id": "...",
                                      "proposal": {...}}\n\n
  5. On completion:            data: {"type": "done"}\n\n
  6. On error:                 data: {"type": "error", "detail": "..."}\n\n

Redis keys used:
  stream:{stream_id}         — Redis List, each item is a JSON token chunk
  stream:{stream_id}:done    — String key set to "1" when graph finished
  stream:{stream_id}:hitl    — Hash set by HITL gate node with proposal data

The graph writes tokens to Redis via the GroqProvider streaming callback.
This route reads and forwards them.

If streaming is not configured (no Redis / graph returns immediately), the
route falls back to a single-chunk "done" response with the full answer.
"""
import asyncio
import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from backend.api.limiter import limiter
from backend.auth.middleware import UserContext, get_current_user
from backend.core.config_loader import config
from backend.core.settings import settings as _settings

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Redis key templates and TTL ───────────────────────────────────────────────

_KEY_STREAM = "stream:{sid}"       # Redis List — pending token chunks
_KEY_DONE = "stream:{sid}:done"    # String "1" when graph is complete
_KEY_HITL = "stream:{sid}:hitl"    # Hash with hitl_id + proposal JSON
_KEY_ERROR = "stream:{sid}:error"  # String error message if graph failed
_STREAM_TTL: int = config.get_redis_config().get("ttl", {}).get("stream", 300)


def _k_stream(sid: str) -> str:
    return _KEY_STREAM.format(sid=sid)


def _k_done(sid: str) -> str:
    return _KEY_DONE.format(sid=sid)


def _k_hitl(sid: str) -> str:
    return _KEY_HITL.format(sid=sid)


def _k_error(sid: str) -> str:
    return _KEY_ERROR.format(sid=sid)


async def _get_redis():
    """
    Return a verified async Redis client, or None if Redis is unreachable.

    Uses socket_connect_timeout=2 + an explicit ping() so we fail fast when
    Redis is not running rather than hanging for 30+ seconds on the first command.
    """
    try:
        from redis.asyncio import Redis
        redis_url = _settings.REDIS_URL
        client = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,   # fail fast if Redis is not listening
        )
        await client.ping()             # actually test the connection
        return client
    except Exception:
        logger.warning("stream: Redis not available — falling back to immediate done response")
        return None


# ── SSE event helpers ─────────────────────────────────────────────────────────

def _sse(payload: dict) -> str:
    """Format a dict as a single SSE data frame."""
    return f"data: {json.dumps(payload)}\n\n"


def _sse_token(text: str) -> str:
    return _sse({"type": "token", "text": text})


def _sse_hitl(hitl_id: str, proposal: dict) -> str:
    return _sse({"type": "hitl_request", "hitl_id": hitl_id, "proposal": proposal})


def _sse_done(response: str = "") -> str:
    return _sse({"type": "done", "response": response})


def _sse_error(detail: str) -> str:
    return _sse({"type": "error", "detail": detail})


def _sse_keepalive() -> str:
    """SSE comment — keeps the connection alive through proxies. Not parsed by client."""
    return ": keepalive\n\n"


# ── Main generator ────────────────────────────────────────────────────────────

async def _event_generator(stream_id: str):
    """
    Async generator that yields SSE frames for one stream_id.

    Two modes:

    Redis mode (Redis is running):
      Polls Redis for token chunks written by the graph's streaming callback.
      Sends each token chunk immediately as it arrives.
      Detects done / hitl / error sentinel keys and terminates cleanly.

    Fallback mode (Redis unavailable):
      Returns a single "done" frame immediately.
      The Chainlit UI handles both modes — in fallback mode it shows the
      full response at once (no streaming animation).

    Poll interval: 50ms — low enough to feel real-time, high enough to not
    hammer Redis (50ms × 200 tokens = 10s for a typical response, which is
    well within Groq's average 3-5s latency).
    """
    redis = await _get_redis()

    if redis is None:
        # Fallback: no streaming — client will get a single done event
        yield _sse_done()
        return

    poll_interval   = 0.05   # 50ms between polls
    max_wait_cycles = 600    # 600 × 50ms = 30s timeout
    keepalive_every = 40     # send a keepalive comment every 2 seconds

    cycles       = 0
    ka_countdown = keepalive_every

    try:
        while cycles < max_wait_cycles:
            cycles      += 1
            ka_countdown -= 1

            # ── Check for error ───────────────────────────────────────────────
            error_msg = await redis.get(_k_error(stream_id))
            if error_msg:
                yield _sse_error(error_msg)
                await redis.delete(_k_error(stream_id))
                return

            # ── Drain all pending token chunks ────────────────────────────────
            # LPOP with count drains the list efficiently without a loop
            chunks = await redis.lrange(_k_stream(stream_id), 0, -1)
            if chunks:
                # Delete the popped range atomically
                await redis.ltrim(_k_stream(stream_id), len(chunks), -1)
                for chunk in chunks:
                    try:
                        item = json.loads(chunk)
                        text = item.get("text", "")
                        if text:
                            yield _sse_token(text)
                    except (json.JSONDecodeError, TypeError):
                        yield _sse_token(str(chunk))

            # ── Check for HITL proposal ───────────────────────────────────────
            hitl_data = await redis.hgetall(_k_hitl(stream_id))
            if hitl_data:
                hitl_id  = hitl_data.get("hitl_id", "")
                proposal_raw = hitl_data.get("proposal", "{}")
                try:
                    proposal = json.loads(proposal_raw)
                except json.JSONDecodeError:
                    proposal = {"raw": proposal_raw}
                yield _sse_hitl(hitl_id, proposal)
                await redis.delete(_k_hitl(stream_id))
                # Don't return yet — the done sentinel still arrives after HITL

            # ── Check for completion ──────────────────────────────────────────
            done_val = await redis.get(_k_done(stream_id))
            if done_val:
                final_response = done_val if done_val != "1" else ""
                yield _sse_done(final_response)
                # Clean up all stream keys
                await redis.delete(
                    _k_stream(stream_id),
                    _k_done(stream_id),
                    _k_hitl(stream_id),
                    _k_error(stream_id),
                )
                return

            # ── Keepalive ─────────────────────────────────────────────────────
            if ka_countdown <= 0:
                yield _sse_keepalive()
                ka_countdown = keepalive_every

            await asyncio.sleep(poll_interval)

        # Timeout — graph took too long
        logger.warning("stream: timeout waiting for stream_id='%s'", stream_id)
        yield _sse_error("Response timeout. The server took too long to respond. Please try again.")

    except asyncio.CancelledError:
        # Client disconnected — normal, not an error
        logger.debug("stream: client disconnected for stream_id='%s'", stream_id)
    except Exception:
        logger.exception("stream: unexpected error for stream_id='%s'", stream_id)
        yield _sse_error("An unexpected server error occurred.")
    finally:
        try:
            await redis.aclose()
        except Exception:
            pass


# ── Route ─────────────────────────────────────────────────────────────────────

@limiter.limit("30/minute")
@router.get("/stream/{stream_id}")
async def stream_response(
    request:   Request,
    stream_id: str,
    user: UserContext = Depends(get_current_user),
):
    """
    SSE streaming endpoint.

    Open this endpoint immediately after calling POST /api/chat.
    Use the stream_id returned in the ChatResponse.

    Example (JavaScript EventSource):
        const source = new EventSource('/api/stream/abc123', {
            headers: { 'x-token': 'dev_token_alice' }
        });
        source.onmessage = (e) => {
            const data = JSON.parse(e.data);
            if (data.type === 'token') appendToken(data.text);
            if (data.type === 'hitl_request') showHITLButtons(data);
            if (data.type === 'done') source.close();
        };

    The Chainlit frontend (frontend/app.py) uses httpx AsyncClient.stream()
    which handles SSE natively.
    """
    logger.info(
        "stream: opening for stream_id='%s' user='%s'",
        stream_id, user.name,
    )

    return StreamingResponse(
        _event_generator(stream_id),
        media_type="text/event-stream",
        headers={
            # Prevent any proxy or CDN from buffering the stream
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
            # Allow the client to reconnect after a disconnect
            "Connection":       "keep-alive",
        },
    )
