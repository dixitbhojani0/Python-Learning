"""
backend/app/observability/telemetry.py

record_event() is the interface every caller depends on — not the
telemetry_events table directly. Swapping this for a real OpenTelemetry
exporter later (once something is actually consuming spans) changes this
one function, not chat_routes.py or the admin summary query.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models import TelemetryEvent


async def record_event(
    session: AsyncSession, *, tenant_id: uuid.UUID, event_type: str, payload: dict
) -> TelemetryEvent:
    event = TelemetryEvent(id=uuid.uuid4(), tenant_id=tenant_id, event_type=event_type, payload=payload)
    session.add(event)
    return event
