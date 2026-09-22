"""
Phase 10 evaluation tests: the golden-dataset retrieval regression gate
(§22, §V eval-gate thresholds) — a real, runnable CI gate, not a
description of one. A regression in retrieval ranking logic (§N) makes
this fail; a regression in unrelated code does not, since the dataset and
threshold are fixed and deterministic against the mock embedding provider.
"""
from __future__ import annotations

from backend.app.db.session import tenant_scoped_session
from backend.app.evaluation.golden_dataset import GOLDEN_RETRIEVAL_CASES, GoldenCase
from backend.app.evaluation.retrieval_eval import run_retrieval_eval
from backend.app.rag.ingestion import ingest_document


# ── Positive (the actual CI gate) ──────────────────────────────────────────

async def test_golden_retrieval_hit_rate_meets_the_gate_threshold(make_tenant):
    """
    The gate: hit_rate must be 1.0 (all three exact-match cases rank their
    own document first). Anything less means retrieval ranking broke.
    """
    tenant = await make_tenant()
    async with tenant_scoped_session(tenant.tenant_id) as session:
        result = await run_retrieval_eval(session, tenant_id=tenant.tenant_id, cases=GOLDEN_RETRIEVAL_CASES)

    assert result.hit_rate >= 1.0, f"Retrieval regression: hit_rate={result.hit_rate}, cases={result.case_results}"
    assert result.total == len(GOLDEN_RETRIEVAL_CASES)


# ── Negative (the gate actually gates — proven by making it fail) ────────

async def test_a_genuinely_wrong_expectation_is_reported_as_a_miss(make_tenant):
    """
    Proves the eval can fail, not just always pass by construction. With only
    one document in the tenant, "rank 1" is trivially that document
    regardless of query — so this needs a genuine competing decoy: a
    pre-ingested document whose content exactly matches the case's query,
    guaranteeing IT wins rank 1 instead of the case's own (mismatched) document.
    """
    tenant = await make_tenant()
    async with tenant_scoped_session(tenant.tenant_id) as session:
        await ingest_document(
            session, tenant_id=tenant.tenant_id, title="Decoy", content="the decoy content", embedding_provider="mock"
        )
        mismatched_case = GoldenCase(document_title="Real doc", document_content="the real content", query="the decoy content")
        result = await run_retrieval_eval(session, tenant_id=tenant.tenant_id, cases=[mismatched_case])

    assert result.hit_rate == 0.0
    assert result.case_results[0].hit is False


# ── Edge ──────────────────────────────────────────────────────────────────

async def test_empty_case_list_has_a_defined_zero_hit_rate_not_a_zero_division_crash(make_tenant):
    tenant = await make_tenant()
    async with tenant_scoped_session(tenant.tenant_id) as session:
        result = await run_retrieval_eval(session, tenant_id=tenant.tenant_id, cases=[])

    assert result.total == 0
    assert result.hit_rate == 0.0


# ── Side effects (tenant isolation extends to the eval harness too) ─────

async def test_eval_runs_in_one_tenant_do_not_contaminate_another_tenants_corpus(make_tenant):
    tenant_a = await make_tenant()
    tenant_b = await make_tenant()

    async with tenant_scoped_session(tenant_a.tenant_id) as session:
        await run_retrieval_eval(session, tenant_id=tenant_a.tenant_id, cases=GOLDEN_RETRIEVAL_CASES)

    async with tenant_scoped_session(tenant_b.tenant_id) as session:
        result_b = await run_retrieval_eval(session, tenant_id=tenant_b.tenant_id, cases=GOLDEN_RETRIEVAL_CASES)

    # Tenant B ingests its own fresh copies of the same documents (RLS means
    # it never sees A's) — still a clean 1.0, not inflated or degraded by A's run.
    assert result_b.hit_rate == 1.0
