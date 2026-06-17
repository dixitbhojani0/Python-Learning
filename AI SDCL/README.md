# 🤖 AI SDLC Assistant

> An intelligent, multi-agent AI assistant for software delivery teams — powered by LangGraph, Groq, and a hybrid RAG pipeline.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green.svg)](https://fastapi.tiangolo.com/)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.4-orange.svg)](https://langchain-ai.github.io/langgraph/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 📌 Overview

The **AI SDLC Assistant** bridges the gap between siloed development tools (Jira, GitHub, Slack) and the people who need answers from them — developers, project managers, and stakeholders.

Instead of manually searching across 5 different tools to answer _"What is happening with the dashboard feature?"_, the assistant:

1. **Queries all tools simultaneously** via MCP (Model Context Protocol) connectors
2. **Retrieves historical context** using a hybrid RAG pipeline (BM25 + Vector + Reranker)
3. **Routes to the right specialized agent** using a LangGraph state machine
4. **Adapts its response** to the user's role (technical for devs, business language for stakeholders)
5. **Requires human approval** before taking any external action (creating tickets, assigning reviewers)

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Chainlit Chat UI                             │
│                  (Role switcher: Dev / PM / Stakeholder)               │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │ POST /api/chat
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          FastAPI Backend                                │
│                                                                         │
│  ┌────────────┐    ┌──────────────────────────────────────────────┐     │
│  │  Auth      │    │         LangGraph Orchestrator               │     │
│  │ Middleware │───▶│                                              │     │
│  └────────────┘    │  classify_intent ──▶ route_to_agent ──▶ ...  │     │
│                    │       │                    │                  │     │
│                    │       ▼                    ▼                  │     │
│                    │  ┌──────────┐    ┌──────────────────┐        │     │
│                    │  │  Cross   │    │   Ticket Agent   │        │     │
│                    │  │  Source  │    │   (HITL gate)    │        │     │
│                    │  │  Agent   │    └──────────────────┘        │     │
│                    │  └──────────┘    ┌──────────────────┐        │     │
│                    │  ┌──────────┐    │    PR Agent      │        │     │
│                    │  │   Risk   │    │   (HITL gate)    │        │     │
│                    │  │  Agent   │    └──────────────────┘        │     │
│                    │  └──────────┘                                │     │
│                    │       │                                      │     │
│                    │       ▼                                      │     │
│                    │  persona_adapter ──▶ context_builder ──▶ LLM │     │
│                    └──────────────────────────────────────────────┘     │
│                                                                         │
│  ┌──────────────────┐   ┌────────────────┐   ┌─────────────────────┐   │
│  │  MCP Connectors  │   │  Hybrid RAG    │   │   Redis Cache       │   │
│  │  GitHub│Jira│Slack│   │  BM25+Vec+Rnk  │   │  HITL State+Session │   │
│  └──────────────────┘   └────────────────┘   └─────────────────────┘   │
│           │                     │                                       │
└───────────┼─────────────────────┼───────────────────────────────────────┘
            │                     │
            ▼                     ▼
     External APIs          Qdrant Vector DB
   (GitHub, Jira, Slack)     (Docker, local)
```

---

## ⚡ Tech Stack

| Component | Technology | Why |
|---|---|---|
| **Orchestration** | LangGraph | Stateful agent graph with HITL interrupt/resume |
| **LLM** | Groq (Llama 3.3 70B) | Free tier, ultra-fast inference |
| **Embeddings** | `all-MiniLM-L6-v2` (local) | Free, no API key, 384-dim vectors |
| **Vector DB** | Qdrant (Docker) | Local, free, metadata pre-filtering |
| **Sparse Search** | BM25 via `rank_bm25` | Exact keyword matching |
| **Reranker** | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Free, production-grade cross-encoder |
| **API** | FastAPI | Async, SSE streaming, OpenAPI docs |
| **Chat UI** | Chainlit | Chat interface with HITL action buttons |
| **Admin** | Streamlit | Config editor, RAG chunk browser |
| **Cache** | Redis (Docker) | HITL state persistence, session cache |
| **MCP** | Custom connectors | GitHub, Jira, Slack (mock for demo) |
| **Observability** | LangSmith | Full trace of every LLM + agent call |
| **Config** | YAML (hot-reload) | Zero hardcoded prompts or routing rules |

---

## 📁 Project Structure

```
ai-sdlc-assistant/
├── backend/
│   ├── agents/                    # Specialized SDLC agents
│   │   ├── base_agent.py         #   Abstract base class + AgentPayload
│   │   ├── cross_source_agent.py #   Unified cross-tool correlation
│   │   ├── risk_agent.py         #   Sprint risk detection
│   │   ├── ticket_agent.py       #   HITL ticket creation
│   │   └── pr_agent.py           #   PR review + reviewer assignment
│   ├── api/
│   │   ├── main.py               # FastAPI entry point
│   │   └── routes/
│   │       ├── chat.py           #   POST /api/chat
│   │       ├── stream.py         #   GET /api/stream/{id} (SSE)
│   │       ├── hitl.py           #   POST /api/hitl/approve|reject
│   │       └── admin.py          #   /admin/* routes
│   ├── auth/
│   │   └── middleware.py         # Role-based demo auth (no OAuth)
│   ├── core/
│   │   ├── config_loader.py      # YAML loader with hot-reload (watchdog)
│   │   ├── context_builder.py    # 7-slot prompt assembly + token budget
│   │   └── settings.py           # Pydantic Settings (.env loader)
│   ├── mcp/                       # MCP connector layer
│   │   ├── registry.py           #   Load connectors from YAML
│   │   ├── base_connector.py     #   Abstract interface
│   │   ├── github_connector.py   #   GitHub REST API
│   │   ├── jira_connector.py     #   Jira REST API
│   │   └── slack_connector.py    #   Slack mock (local JSON)
│   ├── memory/
│   │   ├── redis_cache.py        # Hot cache + HITL state
│   │   └── session_store.py      # SQLite session history
│   ├── orchestrator/
│   │   ├── state.py              # SDLCState TypedDict (shared graph state)
│   │   ├── graph.py              # LangGraph state machine
│   │   └── hitl.py               # HITL interrupt/resume logic
│   ├── persona/
│   │   ├── detector.py           # Role → persona mapping
│   │   └── adapter.py            # Rewrite response by user role
│   └── rag/                       # Retrieval-Augmented Generation
│       ├── pipeline.py           #   Full ingestion (parse → chunk → embed → store)
│       ├── chunker.py            #   Contextual parent-child chunking
│       ├── retriever.py          #   Hybrid BM25 + Vector + Reranker
│       └── vector_store.py       #   Qdrant wrapper
├── frontend/
│   └── app.py                    # Chainlit chat UI
├── admin/
│   └── app.py                    # Streamlit admin panel
├── config/                        # All configuration in YAML (hot-reloadable)
│   ├── agents.yaml               #   Agent routing rules & triggers
│   ├── llm.yaml                  #   LLM provider & temperature settings
│   ├── mcp_registry.yaml         #   MCP connector definitions
│   ├── prompts.yaml              #   ALL prompts (zero hardcoded strings)
│   ├── rag_sources.yaml          #   RAG pipeline settings
│   └── redis.yaml                #   Redis connection & TTL config
├── data/                          # Mock data for demo scenarios
│   ├── sprint_docs/              #   Sprint planning documents
│   ├── adr_documents/            #   Architecture Decision Records
│   └── mock_slack/               #   Mock Slack channel history (JSON)
├── scripts/
│   └── ingest.py                 # RAG ingestion script
├── docker-compose.yml             # Qdrant + Redis infrastructure
├── requirements.txt               # Pinned Python dependencies
├── .env.example                   # Environment variable template
└── .gitignore
```

---

## 🚀 Getting Started

### Prerequisites

- **Python 3.11+**
- **Docker Desktop** (for Qdrant + Redis)
- **Groq API Key** — free at [console.groq.com](https://console.groq.com)
- _(Optional)_ **LangSmith API Key** — free at [smith.langchain.com](https://smith.langchain.com)

### 1. Clone & Setup Environment

```bash
# Clone the repository
git clone https://github.com/your-org/ai-sdlc-assistant.git
cd ai-sdlc-assistant

# Create virtual environment
python -m venv .venv

# Activate (Windows)
.venv\Scripts\activate

# Activate (macOS/Linux)
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment Variables

```bash
# Copy the template
cp .env.example .env

# Edit .env with your actual keys
# At minimum, set GROQ_API_KEY
```

| Variable | Required | Description |
|---|---|---|
| `GROQ_API_KEY` | ✅ Yes | Free LLM API key from Groq |
| `LANGCHAIN_API_KEY` | Optional | LangSmith tracing (recommended) |
| `GITHUB_TOKEN` | Optional | GitHub API access (or use mock) |
| `JIRA_TOKEN` | Optional | Jira API access (or use mock) |
| `SLACK_USE_MOCK` | Default: `true` | Set `true` to use local JSON mock data |

### 3. Start Infrastructure (Docker)

```bash
# Start Qdrant (vector DB) + Redis (cache)
docker compose up -d

# Verify services are healthy
docker compose ps

# Check Qdrant health
curl http://localhost:6333/health
# Expected: {"title":"qdrant - vectorass engine","version":"...","status":"ok"}
```

**Qdrant Dashboard:** [http://localhost:6333/dashboard](http://localhost:6333/dashboard)

### 4. Ingest Knowledge Base

```bash
# Run RAG ingestion (without LLM context — fast mode)
python scripts/ingest.py --no-llm

# Or with LLM contextual prefixes (requires GROQ_API_KEY)
python scripts/ingest.py
```

### 5. Start the Application

```bash
# Start FastAPI backend
uvicorn backend.api.main:app --reload --port 8000

# In a separate terminal — start Chainlit UI
chainlit run frontend/app.py --port 8001

# (Optional) Start Streamlit admin panel
streamlit run admin/app.py --server.port 8502
```

| Service | URL |
|---|---|
| FastAPI (API + docs) | [http://localhost:8000/docs](http://localhost:8000/docs) |
| Chainlit (Chat UI) | [http://localhost:8001](http://localhost:8001) |
| Streamlit (Admin) | [http://localhost:8502](http://localhost:8502) |
| Qdrant Dashboard | [http://localhost:6333/dashboard](http://localhost:6333/dashboard) |

---

## 🎯 Demo Scenarios

The assistant is designed to demonstrate 4 real-world SDLC scenarios:

### Scenario 1 — Cross-Source Feature Status (Manager)
```
👤 "What is happening with the dashboard feature?"
🤖 Cross-source agent queries Jira + Slack + GitHub + RAG simultaneously.
   → "Dashboard API is IN PROGRESS (Alice). Payment gateway is BLOCKED
      (vendor cert issue). Sprint deadline in 2 days — AT RISK."
```

### Scenario 2 — Issue Resolution with Ticket Creation (Developer)
```
👤 "CORS error on /api/v2/auth after nginx config change"
🤖 RAG retrieves ADR-001 (nginx CORS config) + past ticket.
   → Proposes Jira ticket with title, description, assignee.
   → [✅ Approve] [❌ Reject] — HITL gate activated.
   → On approve: ticket SDLC-1042 created in Jira.
```

### Scenario 3 — Stakeholder Status Update (Stakeholder)
```
👤 "Will the payment feature be ready by Friday?"
🤖 Same data as Scenario 1, but rewritten in plain business language:
   → "The payment feature is currently delayed due to an external
      vendor issue. The engineering team is actively working on it
      and expects to resolve it before Friday."
```

### Scenario 4 — Proactive Risk Alert (Automated)
```
⏰ 5pm daily (APScheduler) — no user query needed.
🤖 Risk agent scans sprint board + blockers.
   → If risk score > 70: posts to Slack #engineering-manager channel.
   → "⚠️ Sprint 12 at risk. 2 blockers. Dashboard deadline in 2 days."
```

---

## 🧩 Key Design Decisions

### RAG Pipeline — Hybrid Retrieval
The retrieval pipeline uses a **3-stage approach** for maximum quality:

1. **Vector search** (semantic similarity via Qdrant) — finds conceptually similar chunks
2. **BM25** (sparse keyword matching) — finds exact terms like ticket IDs, endpoints
3. **Reciprocal Rank Fusion** — merges both ranked lists scale-independently
4. **Cross-encoder reranker** — scores all candidates for TRUE relevance to the query

**Confidence tiers** determine the response strategy:
| Score | Strategy |
|---|---|
| ≥ 0.75 | Answer confidently |
| 0.45 – 0.74 | Answer with caveat ("Based on available data…") |
| < 0.45 | Corrective RAG (reformulate query + retry) |
| No results | Graceful degradation message |

### MCP (Model Context Protocol) Connectors
All external tool integrations follow the **MCP pattern**:
- **Registry-based**: connectors are defined in `config/mcp_registry.yaml`
- **Abstract interface**: every connector implements `BaseMCPConnector`
- **Normalized output**: raw API responses are compressed into structured JSON
- **Mock-friendly**: Slack uses local JSON files — identical architecture to real API

### Human-in-the-Loop (HITL)
The system **never takes external actions without human approval**:
- Ticket creation → requires `[Approve]` click
- Reviewer assignment → requires `[Approve]` click
- HITL state is persisted in Redis (survives server restarts)
- LangGraph checkpointer enables graph pause/resume at the HITL gate

### Persona Adaptation
The same underlying data produces **role-appropriate responses**:
- **Developer** → technical precision (error codes, endpoints, stack traces)
- **Manager** → delivery language (completion %, blockers, risk flags)
- **Stakeholder** → plain business English (no jargon, timeline focus)

### Zero Hardcoded Prompts
Every LLM prompt lives in `config/prompts.yaml` and is hot-reloaded via watchdog. You can edit prompts while the app is running — changes take effect within ~1 second.

---

## ⚙️ Configuration

All configuration is externalized into 6 YAML files under `config/`:

| File | Purpose |
|---|---|
| `prompts.yaml` | All LLM prompts and templates |
| `agents.yaml` | Agent routing rules, trigger keywords, MCP tools |
| `llm.yaml` | LLM provider, model, temperature per task |
| `mcp_registry.yaml` | MCP connector definitions and credentials |
| `rag_sources.yaml` | RAG pipeline settings (chunk sizes, thresholds) |
| `redis.yaml` | Redis connection, TTL, and pool settings |

**Hot-reload**: All YAML files are watched by `watchdog`. Edit any file → the running app picks up changes automatically.

---

## 🔍 Observability (LangSmith)

When `LANGCHAIN_TRACING_V2=true` is set in `.env`, every operation is traced in LangSmith:

- Full agent execution trace (which agent was selected, why)
- RAG retrieval scores and chunk contents
- MCP tool calls and response times
- LLM token usage per step
- HITL approval/rejection events

Open [smith.langchain.com](https://smith.langchain.com) during the demo to show the complete execution trace.

---

## 🧪 Testing

```bash
# Run the end-to-end test checklist
# (after docker compose up + ingest.py)

# 1. Health check
curl http://localhost:8000/health
# Expected: {"status": "ok"}

# 2. RAG retrieval test
python -c "
from backend.rag.retriever import HybridRetriever
retriever = HybridRetriever()
chunks, score = retriever.retrieve('CORS error nginx auth', 'antlog')
print(f'Top score: {score:.3f}, Chunks: {len(chunks)}')
print(chunks[0].parent_text[:200] if chunks else 'No results')
"

# 3. Chat API test (developer role)
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -H "x-token: dev_token_alice" \
  -d '{"message": "CORS error on /api/v2/auth", "session_id": "test-1"}'
```

---

## 📋 Development Roadmap

- [x] Project skeleton & dependency management
- [x] Docker Compose (Qdrant + Redis)
- [x] YAML configuration system with hot-reload
- [x] Pydantic Settings (`.env` → typed config)
- [x] Role-based auth middleware (demo tokens)
- [x] Mock data (sprint docs, ADR, Slack messages)
- [x] RAG ingestion pipeline (parse → chunk → embed → store)
- [x] Qdrant vector store wrapper
- [x] Hybrid retriever (BM25 + Vector + Reranker)
- [x] Document chunker (parent-child, Slack-aware)
- [x] Ingestion script (`scripts/ingest.py`)
- [x] LangGraph state schema (`SDLCState`)
- [x] Base agent class + `AgentPayload`
- [ ] Core agents (CrossSource, Risk, Ticket, PR)
- [ ] LangGraph orchestrator graph + intent classifier
- [ ] HITL interrupt/resume logic
- [ ] MCP connectors (GitHub, Jira, Slack mock)
- [ ] FastAPI routes (chat, stream, HITL)
- [ ] Persona adapter (role-based response rewriting)
- [ ] Context builder (7-slot prompt assembly)
- [ ] Chainlit chat UI
- [ ] Streamlit admin panel
- [ ] APScheduler (proactive risk scan)
- [ ] LangSmith observability integration
- [ ] End-to-end demo scenarios validated

---

## 📄 License

This project is for educational and demonstration purposes as part of a GenAI assignment.

---

## 🙏 Acknowledgements

- [LangChain](https://python.langchain.com/) & [LangGraph](https://langchain-ai.github.io/langgraph/) for the orchestration framework
- [Groq](https://console.groq.com) for free, blazing-fast LLM inference
- [Qdrant](https://qdrant.tech/) for the local vector database
- [Sentence-Transformers](https://www.sbert.net/) for free, local embeddings and reranking
- [Chainlit](https://docs.chainlit.io/) for the chat UI with HITL support
