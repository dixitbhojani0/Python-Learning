"""POST /api/v1/session — provider-agnostic session bootstrap.

The response's `provider` field tells the frontend which media adapter to use.
Adding Azure or Unreal pixel-streaming later = new branch here + new settings;
the frontend contract stays identical.
"""

import httpx
from fastapi import APIRouter, HTTPException

from ..config import settings

router = APIRouter()

ANAM_TOKEN_URL = "https://api.anam.ai/v1/auth/session-token"  # noqa: S105 — public URL, not a secret


@router.post("/session")
async def create_session() -> dict:
    if settings.avatar_provider == "anam":
        return await _anam_session()
    raise HTTPException(500, f"Unknown avatar provider configured: {settings.avatar_provider}")


async def _anam_session() -> dict:
    if not settings.anam_api_key:
        raise HTTPException(503, "ANAM_API_KEY missing — set it in backend/.env")

    persona = {
        "name": settings.persona_name,
        "avatarId": settings.anam_avatar_id,
        "avatarModel": settings.anam_avatar_model,
        "llmId": "CUSTOMER_CLIENT_V1",  # disable Anam's brain; our script drives all speech
    }
    if settings.anam_voice_id:
        persona["voiceId"] = settings.anam_voice_id

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            ANAM_TOKEN_URL,
            json={"personaConfig": persona},
            headers={"Authorization": f"Bearer {settings.anam_api_key}"},
        )
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, f"Anam token request failed: {resp.text}")

    return {
        "provider": "anam",
        "personaName": settings.persona_name,
        "sessionToken": resp.json()["sessionToken"],
    }
