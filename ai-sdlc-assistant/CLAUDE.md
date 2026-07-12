# AI SDLC Assistant — Claude Code Project Instructions

Auto-loaded by Claude Code on every session. Follow all rules here without being reminded.

## Project Overview

An AI-powered SDLC assistant where developers, managers, and stakeholders ask natural-language questions and get intelligent answers drawn from sprint docs, Jira history, Slack messages, GitHub PRs, and ADRs. Combines hybrid RAG over historical docs + live MCP tool data via LangGraph multi-agent orchestration.

**Assignment**: GenAI RAG Assignment — Use Case 3, Azilen Technologies.
**Execution**: Local, free-tier APIs only (Groq, local embeddings, Docker infra).

## Tech Stack (Quick Reference)

| Layer | Tool | Why |
|-------|------|-----|
| LLM | Groq / llama-3.3-70b-versatile | Free, fast |
| Embeddings | sentence-transformers all-MiniLM-L6-v2 | Local, free, 384-dim |
| Vector DB | Qdrant (Docker) | Local, metadata filtering |
| Sparse search | rank_bm25 | Exact keyword match |
| Reranker | CrossEncoder ms-marco-MiniLM-L-6-v2 | Local, free |
| Orchestration | LangGraph | Stateful graph, HITL support |
| API | FastAPI | Permanent contract, async, SSE |
| Chat UI | Chainlit | Built-in streaming + action buttons |
| Admin | Streamlit | Fast config/management UI |
| Cache | Redis (Docker) | Semantic cache, HITL state |
| DB | SQLite (demo) / PostgreSQL (prod) | Session + episodic memory |
| Observability | LangSmith | LLM + agent tracing |
| Config | YAML + watchdog | Hot-reload, zero restart |

## Non-Negotiable Architecture Rules

1. **No hardcoded strings in Python** — all prompts live in `config/prompts.yaml`, retrieved via `config.get_prompt(key)`.
2. **No direct LLM imports in agents** — agents use `BaseLLMProvider` interface. Concrete provider injected at startup.
3. **No direct API calls in agents** — MCP connectors handle all external tool calls. Agents call `self.mcp.get("jira").search_tickets(...)`.
4. **Frontend is a thin client** — Chainlit calls FastAPI only. Zero business logic, zero direct imports from `backend/`.
5. **Config is the single source of truth** — temperatures, thresholds, TTLs, model names all come from YAML.
6. **Every agent returns `AgentPayload`** — no raw text passed between agents.
7. **HITL state always lives in Redis** — graph state survives container restarts this way.

## Key Design Decisions (Why things are the way they are)

- **Parent-child chunking**: Child chunks (350 tokens) are embedded and searched. Parent chunks (1500 tokens) are returned to the LLM for full context. Small for precision, large for understanding.
- **Contextual prefix before embedding**: Each chunk gets a 1-2 sentence LLM-generated summary prepended before embedding. This embeds document context, not just the text fragment.
- **Hybrid BM25 + vector**: BM25 catches exact terms (ticket IDs, endpoint names). Vector catches semantic meaning. Together they cover what neither can alone.
- **Persona layer after agents**: Agents produce structured facts. Persona layer rewrites them in role-appropriate language. Data and presentation are always separate.
- **7-slot prompt with tiktoken budget**: Token budget is measured before every LLM call, never estimated. Compression order: RAG chunks first, oldest messages second, never system/persona/query.

## Project Structure (Key Paths)

```
backend/
  agents/         ← specialist agents (extend BaseAgent)
  api/            ← FastAPI routes and schemas
  auth/           ← demo token middleware
  core/           ← config loader, context builder, settings, scheduler
  mcp/            ← MCP registry and connectors
  memory/         ← Redis cache, session store, semantic/episodic memory
  orchestrator/   ← LangGraph graph, HITL manager, state
  persona/        ← role detector, response adapter
  providers/      ← LLM provider interface + implementations
  rag/            ← chunker, pipeline, retriever, vector store
config/           ← all YAML (hot-reloaded)
data/             ← mock sprint docs, ADRs, Slack JSON
frontend/         ← Chainlit app (thin client only)
admin/            ← Streamlit admin panel
scripts/          ← CLI tools (ingest.py)
.claude/standards/← domain-specific coding standards (see below)
```

## Standards Files

Each domain has its own standards file. Read the relevant one before writing code in that area.

| File | When to read |
|------|-------------|
| [.claude/standards/python_coding.md](.claude/standards/python_coding.md) | Every Python file |
| [.claude/standards/llm_standards.md](.claude/standards/llm_standards.md) | Any LLM call, provider code, prompt loading |
| [.claude/standards/rag_standards.md](.claude/standards/rag_standards.md) | RAG pipeline, chunker, retriever, ingestion |
| [.claude/standards/memory_standards.md](.claude/standards/memory_standards.md) | Redis cache, session store, semantic/episodic memory |
| [.claude/standards/database_standards.md](.claude/standards/database_standards.md) | SQLAlchemy models, session management, queries |
| [.claude/standards/langgraph_standards.md](.claude/standards/langgraph_standards.md) | Graph nodes, state, edges, HITL, agent wiring |
| [.claude/standards/testing_standards.md](.claude/standards/testing_standards.md) | Writing tests, test categories, what to mock |
| [.claude/standards/docker_standards.md](.claude/standards/docker_standards.md) | docker-compose, Dockerfiles, service config |
| [.claude/standards/backend_standards.md](.claude/standards/backend_standards.md) | FastAPI routes, schemas, auth, streaming |
| [.claude/standards/frontend_standards.md](.claude/standards/frontend_standards.md) | Chainlit UI, role handling, HITL buttons |
| [.claude/standards/prompt_engineering.md](.claude/standards/prompt_engineering.md) | Which technique (zero-shot/few-shot/CoT/JSON) maps to which prompt key in prompts.yaml |
| [.claude/standards/query_handling.md](.claude/standards/query_handling.md) | Full decision guide: query classification (simple/medium/complex), embedding, retrieval count, chunk strategy, context assembly |
| [.claude/standards/context_management.md](.claude/standards/context_management.md) | Token budget measurement, compression cascade (what to cut when over budget), conversation/graph/RAG context rules |
| [.claude/standards/resilience_standards.md](.claude/standards/resilience_standards.md) | Rate limit backoff (tenacity), async gather safety, MCP semaphore, httpx timeouts, scheduler misfire |

## Current Implementation Status

- ✅ RAG pipeline (chunker, pipeline, retriever, vector store)
- ✅ Config system (hot-reloading YAML, all 6 config files)
- ✅ Core (context builder, settings, LangGraph state)
- ✅ Base agent + AgentPayload
- ✅ Demo auth middleware
- ✅ Docker infrastructure (Qdrant + Redis)
- ✅ Mock data (sprint docs, ADRs, Slack JSON)
- ✅ Ingestion script
- ⬜ LLM provider layer (backend/providers/)
- ⬜ FastAPI app + routes
- ⬜ LangGraph orchestrator graph
- ⬜ Specialist agents
- ⬜ MCP connectors (mock)
- ⬜ Memory layer
- ⬜ Persona layer
- ⬜ Chainlit frontend
- ⬜ Streamlit admin
- ⬜ Scheduler
- ⬜ LangSmith observability
