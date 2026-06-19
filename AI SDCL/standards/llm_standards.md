# LLM Standards — AI SDLC Assistant

Rules for every interaction with an LLM in this project.

---

## Provider Layer — Never Import ChatGroq Directly in Agents

Agents and routes **never** import `ChatGroq`, `ChatGoogleGenerativeAI`, or any concrete LLM class.
They always use `BaseLLMProvider` (defined in `backend/providers/base_llm.py`).

```python
# CORRECT — agents receive provider via constructor injection
class CrossSourceAgent(BaseAgent):
    def __init__(self, mcp_registry, retriever, llm: BaseLLMProvider, config_loader):
        super().__init__(mcp_registry, retriever, llm, config_loader)

# WRONG — direct concrete import in agent
from langchain_groq import ChatGroq
llm = ChatGroq(api_key="...")   # ← never do this in an agent
```

**Why**: Swapping Groq for Gemini means changing one line in `config/llm.yaml` and one line in `backend/api/main.py` — zero agent code changes.

---

## Temperature Rules (from config/llm.yaml — do not hardcode)

| Task | Temperature | Why |
|------|-------------|-----|
| `chunk_contextualization` | 0.0 | Deterministic — same chunk must get same prefix every time |
| `summarization` | 0.0 | Deterministic — reproducible summaries |
| `agent_reasoning` | 0.1 | Grounded — minimal creativity for factual analysis |
| `persona_rewriting` | 0.3 | Slight natural variation in tone |
| `response_generation` | 0.4 | Natural conversational variety |

Read temperatures from config, never hardcode:
```python
# CORRECT
temperature = config.get_temperature("response_generation")  # → 0.4

# WRONG
temperature = 0.4  # hardcoded
```

---

## Prompt Rules — Zero Hardcoded Strings in Python

Every prompt template lives in `config/prompts.yaml`. Python code only loads and formats them.

```python
# CORRECT
prompt = config.get_prompt(
    "ticket_proposal",
    title="CORS bug on /api/v2/auth",
    assignee="DevOps",
)

# WRONG — hardcoded prompt in Python
prompt = f"Create a Jira ticket for: {title}. Assign to {assignee}."
```

**Prompt key naming convention** in `prompts.yaml`:
- `system_prompt` — main assistant identity
- `persona_{role}` — persona instructions (persona_developer, persona_manager, etc.)
- `chunk_context_generator` — used during RAG ingestion
- `{agent_name}_reasoning` — agent-specific reasoning prompt
- `graceful_degradation` — no-evidence fallback response
- `query_reformulation` — corrective RAG query rewrite prompt

---

## Token Counting — Always Measure Before Sending

Never estimate token counts. Always use `count_tokens()` from `backend/core/context_builder.py`.

```python
from backend.core.context_builder import count_tokens

total = sum(count_tokens(slot) for slot in assembled_slots.values())
if total > TOTAL_INPUT_BUDGET:
    # compress before sending
```

**Budget**: 8000 input tokens (safe for llama-3.3-70b on Groq). Reserve 1024 for output.

---

## Max Tokens — Always Set, Read from Config

```python
# CORRECT
max_tokens = config.get_llm_config()["primary"]["max_tokens"]["response"]  # → 1024

# WRONG
max_tokens = 2000  # arbitrary hardcode
```

Per-task max_tokens from `config/llm.yaml`:
- `response`: 1024 (user-facing answers)
- `chunk_context`: 100 (contextual prefix generation — keep short)
- `summary`: 300 (conversation summarization)

---

## Error Handling for LLM Calls

Every LLM call must:
1. Be wrapped in try/except
2. Log with `logger.exception()` on failure (preserves traceback)
3. Return a safe fallback (empty string, or trigger graceful degradation)
4. Try the configured fallback model before giving up (see `config/llm.yaml` fallback section)

```python
try:
    tokens = []
    async for chunk in self.llm.generate(prompt, system=system, temperature=temp, max_tokens=max_t):
        tokens.append(chunk)
    return "".join(tokens)
except Exception as e:
    logger.exception("LLM call failed for task '%s'", task_name)
    try:
        tokens = []
        async for chunk in self.llm_fallback.generate(prompt, system=system, temperature=temp, max_tokens=max_t):
            tokens.append(chunk)
        return "".join(tokens)
    except Exception:
        logger.exception("Fallback LLM also failed")
        return ""  # caller handles empty string → graceful degradation
```

---

## BaseLLMProvider Interface (v3 updated)

The provider interface uses `generate()` which always returns an `AsyncGenerator` (always streams). There is no separate `complete()` method.

```python
class BaseLLMProvider(ABC):
    @abstractmethod
    async def generate(
        self,
        prompt:      str,
        system:      str,
        temperature: float,
        max_tokens:  int,
    ) -> AsyncGenerator[str, None]:
        """Yields token chunks. Consume with: async for token in provider.generate(...)"""
        ...

    @abstractmethod
    async def get_model_window(self) -> int:
        """Returns the context window size for the active model."""
        ...
```

**For internal reasoning (agents)** — collect all tokens into a string:
```python
tokens = []
async for chunk in self.llm.generate(prompt, system=system_prompt, temperature=0.1, max_tokens=500):
    tokens.append(chunk)
result = "".join(tokens)
```

**For user-facing streaming** — pass tokens directly to FastAPI SSE or Chainlit:
```python
async for chunk in self.llm.generate(prompt, system=system_prompt, temperature=0.4, max_tokens=1024):
    await redis.lpush(f"stream:response:{stream_id}", chunk)
```

## Per-Model Token Window Scaling

Different models have different context windows. The context builder reads the active model's window and scales the RAG slot proportionally. Configure in `config/llm.yaml`:

```yaml
models:
  groq_llama33_70b:
    window:         8192
    output_reserve: 4096   # guaranteed space for LLM response
    rag_cap:        2000   # max RAG tokens even if window allows more
  llama31_8b_instant:      # fallback
    window:         8192
    output_reserve: 2048
    rag_cap:        1500
```

`rag_cap` prevents cost explosion on large-window fallback models. Even if a model supports 100k tokens, we cap RAG at `rag_cap` — more context rarely improves answers and significantly increases latency.

## Streaming — `generate()` Always Streams

| Use case | How to consume |
|----------|---------------|
| User-facing response | Forward each chunk to SSE/Chainlit stream |
| Agent internal reasoning | Collect: `"".join([c async for c in llm.generate(...)])` |
| Chunk context generation | Collect all chunks (short, TTL cached in Redis) |
| Conversation summarization | Collect all chunks |

---

## Fallback LLM

Primary: `llama-3.3-70b-versatile` (Groq)
Fallback: `llama-3.1-8b-instant` (Groq — smaller, faster, lower quality)

The `GroqProvider` should automatically try the fallback model if the primary fails with a rate limit or timeout. Do not fall back on logic errors (wrong prompt format, invalid API key) — those need to be fixed, not retried.

---

## LangSmith Tracing (Phase 12)

When observability is wired in:
- Decorate provider `complete()` and `stream()` with `@traceable`
- Pass `run_name=task_name` to trace calls by purpose
- Never log full prompt content at INFO level (could contain sensitive data) — LangSmith traces handle that securely

---

## Cost Awareness

Groq free tier: 6000 requests/day, 500k tokens/min on llama-3.3-70b.

- Keep `chunk_contextualization` calls minimal — only run with LLM when `use_llm_context=True`
- Semantic cache threshold 0.92 means repeated similar queries never hit the LLM
- Conversation summarization after every 10 messages keeps context tokens low
- Never send raw MCP output to LLM — always normalize first (95% token reduction)
