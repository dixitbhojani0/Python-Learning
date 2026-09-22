"""
backend/app/memory/service.py

Consent-gated extract-then-update pipeline (§14) + the context block chat
augments with. `session` is always caller-supplied and already
tenant-scoped (§J) — same discipline as rag/ingestion.py and
rag/retrieval.py; this module never opens its own session.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import UserMemory
from backend.app.memory.extraction import extract_memory_candidates


async def apply_extracted_memory(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    consent: bool,
    user_message: str,
) -> list[UserMemory]:
    """
    No-op — extraction does not even run — when consent is False. This is a
    hard gate, not a "store but don't use" compromise: the pipeline must not
    process a message it has no consent to remember from at all.
    """
    if not consent:
        return []

    candidates = extract_memory_candidates(user_message)
    if not candidates:
        return []

    updated: list[UserMemory] = []
    for key, value in candidates:
        existing = (
            await session.execute(
                select(UserMemory).where(UserMemory.user_id == user_id, UserMemory.key == key)
            )
        ).scalar_one_or_none()

        if existing is not None:
            existing.value = value  # update in place — one row per key, not an ever-growing log
            updated.append(existing)
        else:
            memory = UserMemory(id=uuid.uuid4(), tenant_id=tenant_id, user_id=user_id, key=key, value=value)
            session.add(memory)
            updated.append(memory)

    return updated


async def get_user_memory_context(session: AsyncSession, *, user_id: uuid.UUID) -> str:
    """Returns "" when there's nothing stored — chat_augmentation-style empty-in/empty-out."""
    rows = (
        await session.execute(select(UserMemory).where(UserMemory.user_id == user_id).order_by(UserMemory.key))
    ).scalars().all()

    if not rows:
        return ""

    facts = "\n".join(f"- {row.key.replace('_', ' ')}: {row.value}" for row in rows)
    return f"What you remember about this user:\n{facts}"
