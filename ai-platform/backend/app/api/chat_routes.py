"""
backend/app/api/chat_routes.py

Phase 3 conversation service + streaming chat (§Q), now with Phase 6's
retrieval wired in: before generating a reply, the tenant's corpus is
searched (§N) and any hits become both a system-prompt context block and a
"citations" SSE event — RAG when there's something to retrieve, plain chat
otherwise. No memory beyond raw session history, no agents yet (§C).

Streaming + DB dependency gotcha this file deliberately avoids: FastAPI
tears down a `Depends(...)` context-manager dependency as soon as the route
handler function RETURNS — but a StreamingResponse's generator body runs
AFTER that return, while the response is being sent. A DB session obtained
via `Depends(get_scoped_session)` would already be closed by the time the
streaming generator tried to use it. So `POST /v1/chat` opens its own
`tenant_scoped_session(...)` blocks explicitly, scoped to exactly the work
each one does, instead of relying on the dependency for the streaming part.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.adapters.embedding import providers as _embedding_providers  # noqa: F401  (triggers registration)
from backend.app.adapters.llm import providers as _providers  # noqa: F401  (triggers registration)
from backend.app.adapters.llm.registry import LLMRegistry
from backend.app.api.deps import Principal, get_current_principal, get_scoped_session
from backend.app.core.config import resolve_config
from backend.app.core.i18n import DEFAULT_LOCALE, t
from backend.app.db.models import Conversation, Message, PendingToolApproval, User
from backend.app.db.session import tenant_scoped_session
from backend.app.memory.service import apply_extracted_memory, get_user_memory_context
from backend.app.observability.telemetry import record_event
from backend.app.rag.chat_augmentation import build_rag_augmentation
from backend.app.rag.retrieval import search_chunks
from backend.app.tools import builtin as _tool_builtins  # noqa: F401  (triggers registration)
from backend.app.tools.base import ToolExecutionError
from backend.app.tools.intent import detect_tool_intent
from backend.app.tools.registry import ToolRegistry

router = APIRouter(prefix="/v1", tags=["chat"])

_PLATFORM_DEFAULTS = {
    "default_locale": DEFAULT_LOCALE,
    "llm_provider": "mock",
    "embedding_provider": "mock",
    "feature_flags": {"rag_enabled": True},
}
_RAG_TOP_K = 3
# Bounded autonomy (§15): a tool call that hangs must not hang the whole
# chat turn — this is the actual enforced bound, not a documented intention.
_TOOL_EXECUTION_TIMEOUT_SECONDS = 5.0


class ChatRequest(BaseModel):
    conversation_id: uuid.UUID | None = None
    content: str = Field(min_length=1)


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str

    model_config = {"from_attributes": True}


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str

    model_config = {"from_attributes": True}


async def _get_owned_conversation(
    session: AsyncSession, principal: Principal, conversation_id: uuid.UUID
) -> Conversation | None:
    """
    RLS (§J) already confines this query to the caller's tenant. It knows
    nothing about which USER within that tenant owns the conversation, so
    that check is explicit application code — the same discipline as §Z's
    "app-layer authz bug" risk entry, just one level down from tenant to user.
    """
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != principal.user_id:
        return None
    return conversation


@router.post("/chat")
async def chat(
    body: ChatRequest,
    principal: Principal = Depends(get_current_principal),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> StreamingResponse:
    locale = (accept_language or DEFAULT_LOCALE).split(",")[0].split("-")[0]

    async with tenant_scoped_session(principal.tenant_id) as session:
        if body.conversation_id is None:
            conversation = Conversation(
                id=uuid.uuid4(),
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                title=body.content[:60],
            )
            session.add(conversation)
            await session.flush()
        else:
            conversation = await _get_owned_conversation(session, principal, body.conversation_id)
            if conversation is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=t("chat.conversation_not_found", locale=locale),
                )

        conversation_id = conversation.id
        session.add(
            Message(
                id=uuid.uuid4(),
                tenant_id=principal.tenant_id,
                conversation_id=conversation_id,
                role="user",
                content=body.content,
            )
        )

        # Extraction runs on this exact raw message, here — the one point in
        # the whole request where the text is unambiguously "typed directly
        # by this user," never retrieved RAG content or generated text (§17
        # memory poisoning; see memory/extraction.py's docstring).
        user = await session.get(User, principal.user_id)
        memory_consent = user.memory_consent if user is not None else False
        await apply_extracted_memory(
            session,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            consent=memory_consent,
            user_message=body.content,
        )

    async def event_stream():
        start_time = time.perf_counter()
        config = resolve_config(_PLATFORM_DEFAULTS)
        provider = LLMRegistry.create(config.llm_provider)

        # ── Tool agent loop (§15) — at most one tool call per turn, enforced
        # by detect_tool_intent() returning at most one match, not by a loop
        # counter here. A high-risk tool call stops the turn entirely: no
        # LLM call, no token stream, just the approval-required event.
        tool_intent = detect_tool_intent(body.content)
        tool_result_text = ""
        if tool_intent is not None:
            tool_name, tool_kwargs = tool_intent
            tool = ToolRegistry.create(tool_name)

            if tool.risk_level == "high":
                async with tenant_scoped_session(principal.tenant_id) as approval_session:
                    approval = PendingToolApproval(
                        id=uuid.uuid4(),
                        tenant_id=principal.tenant_id,
                        user_id=principal.user_id,
                        conversation_id=conversation_id,
                        tool_name=tool_name,
                        tool_args=tool_kwargs,
                    )
                    approval_session.add(approval)
                    await approval_session.flush()
                    approval_id = approval.id

                    approval_session.add(
                        Message(
                            id=uuid.uuid4(),
                            tenant_id=principal.tenant_id,
                            conversation_id=conversation_id,
                            role="assistant",
                            content=t("tools.approval_required_message", locale=locale),
                        )
                    )
                    await record_event(
                        approval_session,
                        tenant_id=principal.tenant_id,
                        event_type="tool_call_pending_approval",
                        payload={"tool_name": tool_name, "approval_id": str(approval_id)},
                    )

                yield f"data: {json.dumps({'type': 'approval_required', 'approval_id': str(approval_id), 'tool': tool_name, 'args': tool_kwargs})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'conversation_id': str(conversation_id)})}\n\n"
                return  # nothing left to do this turn — no LLM call, no token stream

            # Low-risk: execute inline, bounded by a hard timeout.
            async with tenant_scoped_session(principal.tenant_id) as tool_session:
                try:
                    result = await asyncio.wait_for(
                        tool.execute(
                            session=tool_session, tenant_id=principal.tenant_id, user_id=principal.user_id, **tool_kwargs
                        ),
                        timeout=_TOOL_EXECUTION_TIMEOUT_SECONDS,
                    )
                    tool_result_text = f"Tool '{tool_name}' result: {json.dumps(result)}"
                    tool_success = True
                except (ToolExecutionError, asyncio.TimeoutError) as exc:
                    tool_result_text = f"Tool '{tool_name}' could not complete: {exc}"
                    tool_success = False

                await record_event(
                    tool_session,
                    tenant_id=principal.tenant_id,
                    event_type="tool_call",
                    payload={"tool_name": tool_name, "risk_level": tool.risk_level, "success": tool_success},
                )

            yield f"data: {json.dumps({'type': 'tool_result', 'tool': tool_name, 'text': tool_result_text})}\n\n"

        rag_results = []
        memory_context = ""
        async with tenant_scoped_session(principal.tenant_id) as context_session:
            if config.feature_flags.get("rag_enabled", True):
                rag_results = await search_chunks(
                    context_session, query=body.content, top_k=_RAG_TOP_K, embedding_provider=config.embedding_provider
                )
            if memory_consent:
                # Independent of rag_enabled — memory (§14) and document
                # retrieval (§N) are separate capabilities that happen to
                # both feed the same system prompt, not one feature.
                memory_context = await get_user_memory_context(context_session, user_id=principal.user_id)

        augmentation = build_rag_augmentation(rag_results)
        system_prompt = "\n\n".join(
            part for part in (memory_context, augmentation.system_prompt, tool_result_text) if part
        )

        if augmentation.citations:
            yield f"data: {json.dumps({'type': 'citations', 'chunks': augmentation.citations})}\n\n"

        full_text = ""
        async for chunk in provider.stream_text(body.content, system=system_prompt):
            full_text += chunk
            yield f"data: {json.dumps({'type': 'token', 'text': chunk})}\n\n"

        latency_ms = round((time.perf_counter() - start_time) * 1000, 1)

        async with tenant_scoped_session(principal.tenant_id) as session:
            session.add(
                Message(
                    id=uuid.uuid4(),
                    tenant_id=principal.tenant_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=full_text,
                )
            )
            # Word count, not a real tokenizer — an honest proxy metric
            # (§20 "AI/LLM telemetry"), not a claim of exact token accounting;
            # swapping in a real tokenizer later changes only this one line.
            await record_event(
                session,
                tenant_id=principal.tenant_id,
                event_type="chat_turn",
                payload={
                    "conversation_id": str(conversation_id),
                    "llm_provider": config.llm_provider,
                    "rag_used": bool(rag_results),
                    "citation_count": len(augmentation.citations),
                    "memory_used": bool(memory_context),
                    "latency_ms": latency_ms,
                    "response_word_count": len(full_text.split()),
                },
            )

        yield f"data: {json.dumps({'type': 'done', 'conversation_id': str(conversation_id)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_scoped_session),
) -> list[ConversationOut]:
    rows = (
        await session.execute(
            select(Conversation).where(Conversation.user_id == principal.user_id).order_by(Conversation.created_at.desc())
        )
    ).scalars().all()
    return [ConversationOut.model_validate(c) for c in rows]


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def get_conversation_messages(
    conversation_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_scoped_session),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> list[MessageOut]:
    locale = (accept_language or DEFAULT_LOCALE).split(",")[0].split("-")[0]

    conversation = await _get_owned_conversation(session, principal, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=t("chat.conversation_not_found", locale=locale))

    rows = (
        await session.execute(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at))
    ).scalars().all()
    return [MessageOut.model_validate(m) for m in rows]
