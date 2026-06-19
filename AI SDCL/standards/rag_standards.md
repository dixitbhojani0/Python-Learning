# RAG Pipeline Standards — AI SDLC Assistant

Rules for the retrieval-augmented generation pipeline: ingestion, chunking, retrieval, and confidence handling.

---

## Chunking Rules

### Parent Chunks
- Target size: **1500 tokens**, paragraph-boundary-aware
- Overlap: **0%** (parents are non-overlapping context units)
- Purpose: returned to the LLM for full reading context

### Child Chunks
- Target size: **350 tokens**
- Overlap: **40 tokens** with adjacent siblings
- Purpose: embedded and searched (small = precise retrieval)

### One-line rule
> Embed the child. Return the parent. The child finds the location; the parent gives the context.

```python
# Correct — embed child, store parent alongside it
text_to_embed = f"{context_prefix}\n\n{chunk.text}"    # child text (350 tokens)
self.vector_store.upsert(
    text=chunk.text,            # child stored separately
    embedding=embed_text(text_to_embed),
    parent_text=chunk.parent_text,   # full parent stored for retrieval
    ...
)
```

---

## Contextual Prefix (Anthropic Contextual Retrieval Pattern)

Every child chunk gets a 1-2 sentence LLM-generated context prefix **prepended before embedding**.

- The prefix answers: "What document is this from, and what section does this belong to?"
- Example: `"This chunk is from Sprint 12 plan, describing blockers on the Dashboard API feature."`
- Prefix is generated at ingestion time with `temperature=0.0` — deterministic
- In fast mode (`--no-llm`), skip the prefix. Retrieval quality drops but ingestion is instant.
- The prefix is stored in Qdrant metadata under `context_prefix` for inspection.

---

## Required Metadata on Every Chunk

Every call to `vector_store.upsert()` must include these metadata fields:

```python
metadata = {
    "source":    "local_sprint_docs",   # jira | github | slack | teams | drive | local_*
    "project":   "antlog",              # project identifier — used for pre-filter
    "type":      "doc",                 # doc | adr | chat | ticket | code | pr
    "author":    "system",              # who created/owns this document
    "timestamp": "2026-05-28",          # ISO date string
    "stale":     False,                 # set True on re-ingestion before deleting
}
```

Missing `project` or `stale` fields will break the retrieval pre-filter. Always include both.

---

## Document Type → Chunking Strategy

| Document type | `type` field | Chunking approach | Data source |
|--------------|-------------|------------------|-------------|
| Sprint planning docs | `doc` | Semantic paragraph splitter | `data/sprint_docs/` |
| Jira tickets | `ticket` | Title+desc as one chunk, each comment as child | Jira MCP export |
| GitHub PR diffs | `code` | File-level parent, function-level child | GitHub MCP |
| Slack/Teams history | `chat` | Thread as parent, individual messages as children | `data/mock_slack/` |
| ADRs | `adr` | Section-level: Context, Decision, Consequences as separate chunks | `data/adr_documents/` |
| Policies / standards | `doc` | Rule-level chunking (one distinct rule per chunk) | `.claude/standards/` |
| **Version policies** | `version_policy` | Rule-level: each versioning rule as one chunk | `data/version_policies/` |
| **Release notes** | `release_note` | Section-level: version, changes, breaking changes | `data/release_notes/` |
| **Incident reports** | `incident_report` | Section-level: timeline, root cause, resolution | `data/incidents/` |

### New data folders to create (Phase 0 extension)
- `data/version_policies/` — semantic versioning rules, API change policy, deprecation guidelines (used by PR Review Agent for version policy validation)
- `data/release_notes/` — past release notes (used by Release Readiness Agent)
- `data/incidents/` — past incident reports (used by Cross-Source Agent for similar incident lookup)

---

## Retrieval Pipeline — Do Not Skip Steps

The full pipeline (from `HybridRetriever.retrieve()`):

```
1. embed_text(query)                        → query vector
2. vector_store.search(project=, stale=false) → top 30 candidate child chunks (pre-filtered)
3. BM25Okapi(corpus).get_scores(query)      → keyword scores on same 30 candidates
4. _reciprocal_rank_fusion(vector, bm25)    → merged ranking (RRF k=60)
5. CrossEncoder.predict([(query, doc), ...]) → reranker scores
6. sorted by reranker score → top 7         → final set
7. fetch parent_text for each              → return RetrievedChunk with parent_text
```

Never skip step 7 (parent fetch). The LLM must receive `parent_text`, not `text` (child).

---

## Confidence Tiers — Read from Config

```python
thresholds = config.get_confidence_thresholds()
# returns: {"high_threshold": 0.75, "medium_threshold": 0.45, "no_evidence_threshold": 0.20}
```

| Tier | Reranker top score | Agent behaviour |
|------|--------------------|----------------|
| HIGH | ≥ 0.75 | Answer confidently. Full RAG context used. |
| MEDIUM | 0.45 – 0.74 | Answer with caveat: "Based on available data..." |
| LOW | < 0.45 | Trigger corrective RAG before answering |
| NO_EVIDENCE | < 0.20 | Graceful degradation — no hallucination |

---

## Corrective RAG — When and How

Triggered when first retrieval confidence < `medium_threshold` (0.45).

```python
chunks, confidence, strategy = retriever.retrieve_with_corrective_rag(
    query=query,
    project=project,
    llm_rewrite_fn=lambda q: provider.complete(config.get_prompt("query_reformulation", query=q))
)
# strategy: "first_pass" | "corrective" | "degraded"
```

- If second retrieval also fails (< 0.45), return the best of both attempts with `strategy="degraded"`.
- Degraded does NOT mean empty — return whatever chunks exist, flag `strategy` in the payload.

---

## Graceful Degradation — No Hallucination Policy

When `NO_EVIDENCE` (score < 0.20) or corrective RAG still fails:
- Use the `graceful_degradation` prompt template from `config/prompts.yaml`
- Format it with: what was searched, what sources were checked, related topics available
- Return this structured message — never fabricate information

```python
degradation_msg = config.get_prompt(
    "graceful_degradation",
    topic=query,
    sources_checked="Jira, Slack, sprint docs, ADRs",
    related_topics="dashboard feature, nginx config, auth service"
)
```

---

## Re-ingestion — Always Mark Stale First

Before re-ingesting a document (e.g. sprint doc updated):

```python
vector_store.mark_stale(project="antlog", source="local_sprint_docs")
# then re-ingest
pipeline.ingest_directory(directory, metadata)
```

Stale chunks are excluded from search by the `stale=false` pre-filter. They remain in Qdrant for 24 hours before cleanup (future: implement purge job).

---

## Embedding Model Consistency

- **Same model for ingestion and query time**: `all-MiniLM-L6-v2`
- If the embedding model ever changes, the entire collection must be re-ingested (different vector space)
- Model name read from `config/rag_sources.yaml` under `embeddings.model`
- Model loaded once at module level (expensive ~90MB download on first run, cached in `~/.cache/`)

---

## What RAG Does NOT Do

- RAG does not replace MCP live data. MCP = current state. RAG = historical context.
- RAG does not search Slack in real time. The mock_slack JSON is ingested as historical context.
- RAG does not create tickets, assign PRs, or send notifications. Those are MCP write operations.
- RAG does not run on every LLM call — only when an agent specifically triggers it.
