# Prompt Engineering Standards — AI SDLC Assistant

Read this file before writing or editing any prompt in `config/prompts.yaml`.
Every LLM prompt in this project must map to one of the techniques defined here.

---

## Section A — Technique Quick Reference

| Technique | When to use | When NOT to use |
|-----------|------------|-----------------|
| Zero-shot | Simple, unambiguous tasks (summarize, rewrite in tone X) | When the LLM keeps giving wrong format or wrong routing |
| Few-shot (3–6 examples) | Classification tasks, format-sensitive output, routing decisions | When examples would take >300 tokens — too expensive |
| Chain of Thought (CoT) | Multi-step reasoning, risk scores, go/no-go decisions | Simple lookups — CoT wastes tokens on tasks that don't need reasoning |
| Structured output (JSON) | Any agent that produces data another component reads | Free-form prose responses to users (Persona layer) |
| Role prompting | Every system prompt — always define who the LLM is | Never use role prompting alone without also specifying output format |
| Scratchpad + structured output | All specialist agents (reason privately, output structured) | Never use raw scratchpad output as the final user response |

---

## Section B — Prompt Key Mapping

Which technique applies to each key in `config/prompts.yaml`:

| `prompts.yaml` key | Technique | Reason |
|-------------------|-----------|--------|
| `system_prompt` | Role prompting | Defines assistant identity — runs on every call |
| `persona_developer` | Zero-shot + Role | Task is clear: "respond technically" — no examples needed |
| `persona_manager` | Zero-shot + Role | Same — tone is simple to describe |
| `persona_stakeholder` | Zero-shot + Role | Same |
| `persona_technical_leader` | Zero-shot + Role | Same |
| `intent_classifier` | **Few-shot (6 examples)** | Classification is error-prone zero-shot; examples dramatically reduce misrouting |
| `chunk_context_generator` | Zero-shot | Task is deterministic — describe chunk origin in 1-2 sentences |
| `query_reformulation` | Zero-shot + instruction | Reformulate query; task is clear enough without examples |
| `cross_source_reasoning` | Scratchpad CoT + **Structured JSON output** | Must reason across 4 sources, then compress to AgentPayload |
| `risk_agent_reasoning` | **Chain of Thought (6 steps)** + **Structured JSON output** | Multi-step numeric reasoning: ticket counts, velocity, risk score |
| `ticket_agent_reasoning` | Few-shot (1 example ticket) + **Structured JSON output** | Show what a well-formed ticket proposal looks like |
| `pr_review_reasoning` | CoT (check standards → check version policy → verdict) + **Structured JSON** | Multi-check checklist pattern |
| `release_readiness_reasoning` | **Chain of Thought (5 steps)** + **Structured JSON output** | Go/no-go needs auditable reasoning — HITL approval requires explanation |
| `mcp_normalizer` | Zero-shot + **Structured JSON output** | Compress MCP dump to JSON: deterministic, no reasoning needed |
| `conversation_summarizer` | Zero-shot | Compress last N messages — straightforward |
| `graceful_degradation` | Zero-shot + constrained output | Fixed structure: what was searched + next steps + related topics |
| `semantic_fact_extractor` | Zero-shot + **Structured JSON output** | Extract key facts from conversation for semantic memory store |

---

## Section C — CoT Step Templates

When writing a Chain of Thought prompt, always number the steps explicitly.
Never write "think step by step" — always specify WHAT the steps are.

**Risk Agent CoT (6 steps):**
```
Step 1: Count total open tickets for this sprint.
Step 2: Count blocked tickets (status = BLOCKED or IN REVIEW > 3 days).
Step 3: Calculate block ratio = blocked / total.
Step 4: Check sprint velocity — compare planned points vs completed so far.
Step 5: Identify the single highest-risk item (most dependencies, most blocked).
Step 6: Assign risk score 0-100. Output structured JSON.
```

**Release Readiness CoT (5 steps):**
```
Step 1: Check for open P0/P1 tickets — any = automatic BLOCK.
Step 2: Check for failing CI on main branch — any = automatic BLOCK.
Step 3: Check for open critical PRs not yet merged.
Step 4: Verify version bump follows semantic versioning policy.
Step 5: Produce go/no-go verdict with specific blocker list. Output structured JSON.
```

**PR Review CoT (3 steps):**
```
Step 1: Check if PR follows coding standards (python_coding.md rules).
Step 2: Check if version bump type is correct per semantic_versioning_policy.
Step 3: Produce verdict (APPROVE / REQUEST_CHANGES) with line-level comments. Output structured JSON.
```

---

## Section D — Structured JSON Output Schema Rules

Every prompt that uses structured output must end with the exact JSON schema the agent expects.
Never leave the format implicit.

**Example — correct:**
```
Respond with ONLY valid JSON in this exact format:
{
  "summary": "string — 2-3 sentence answer",
  "confidence": "HIGH | MEDIUM | LOW",
  "sources_used": ["string"],
  "hitl_required": false,
  "structured": {}
}
```

**Example — wrong (never do this):**
```
Respond with a JSON object containing your analysis.
```

---

## Section E — Few-Shot Example Rules

1. Always include exactly 3–6 examples per few-shot prompt
2. At least one example must be an edge case (ambiguous query, multiple valid intents)
3. Examples must be representative of real SDLC queries — not generic text
4. Format: each example = `Input: ...\nOutput: ...` separated by `---`
5. Place examples BEFORE the actual input to classify

**Intent classifier example set (6 examples):**
```
Input: "What is blocking the dashboard feature?"
Output: cross_source

Input: "Create a Jira ticket for the CORS bug"
Output: ticket

Input: "What is our sprint risk this week?"
Output: risk

Input: "Review this PR for the auth service changes"
Output: pr_review

Input: "Are we ready to release v2.2.0?"
Output: release_readiness

Input: "Can you summarize what happened in the last standup?"
Output: cross_source
---
Input: {query}
Output:
```

---

## Section F — Golden Rules (Never Violate)

1. Every prompt ends with explicit output format — either JSON schema or "respond with only..."
2. CoT steps are numbered — never rely on the LLM to choose its own reasoning steps
3. Few-shot examples always include one edge case — not just the happy path
4. Temperature is always specified in `llm.yaml` per task, never in the prompt text itself
5. System prompt and task prompt are always separate keys in `prompts.yaml`
6. Never write a prompt longer than 800 tokens — if it's longer, split into two keys
7. Role prompting is used in every system prompt — always define who the LLM is before what it should do
8. Scratchpad reasoning is NEVER shown to the user — it stays inside the agent, only `AgentPayload.summary` is shown
