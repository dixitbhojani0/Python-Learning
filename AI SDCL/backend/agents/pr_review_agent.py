"""
backend/agents/pr_review_agent.py

PR Review Agent — reviews open PRs against coding standards and version policy.

Always requires HITL approval (reviewer assignment is a team action).
Flow:
  1. GitHub MCP — search for relevant open PRs
  2. RAG — retrieve from data/version_policies/ (semver, API change policy)
  3. RAG — retrieve from data/adr_documents/ (coding standards)
  4. CoT reasoning → LLM produces structured review JSON
  5. Format HITL proposal card (reviewer assignment)
  6. Return AgentPayload(hitl_required=True)
"""
import asyncio
import logging
import re

try:
    from langsmith import traceable
except ImportError:
    def traceable(fn=None, **_kw):
        return fn if fn is not None else (lambda f: f)

# Code PR signals — titles/labels that indicate real code work
_CODE_PR_PAT = re.compile(
    r'\b(fix|feat|feature|patch|minor|major|refactor|test|ci|config|api|auth|bug|hotfix|chore|docs|perf)\b',
    re.IGNORECASE,
)
# Documentation-only PR signals — these should be deprioritized
_DOC_PR_PAT = re.compile(
    r'\b(information report|solution document|added document|readme|changelog|release notes?|wiki)\b',
    re.IGNORECASE,
)


def _is_code_pr(pr: dict) -> bool:
    """Return True if the PR looks like a code change (not a documentation upload)."""
    title  = pr.get("title", "")
    files  = pr.get("files_changed", [])
    labels = pr.get("labels", [])

    # If we have file data: code file = non-markdown, non-doc extension
    if files:
        code_exts = {".py", ".js", ".ts", ".java", ".go", ".yaml", ".yml", ".json", ".conf", ".sh"}
        if any(any(f.endswith(ext) for ext in code_exts) for f in files):
            return True
    # Title or label signals
    if _CODE_PR_PAT.search(title) or any(_CODE_PR_PAT.search(lb) for lb in labels):
        return True
    if _DOC_PR_PAT.search(title):
        return False
    return True  # default: include

from backend.agents.base_agent import AgentPayload, BaseAgent
from backend.core.config_loader import config as _default_config
from backend.orchestrator.state import SDLCState
from backend.rag.retriever import HybridRetriever, RetrievedChunk

logger = logging.getLogger(__name__)


# ── Context formatters ─────────────────────────────────────────────────────────

def _format_pr_context(prs: list[dict]) -> str:
    if not prs:
        return "No relevant PRs found."
    lines = []
    for pr in prs:
        files = ", ".join(pr.get("files_changed", []))
        reviewers = ", ".join(pr.get("reviewers", []))
        lines.append(
            f"PR #{pr['id']}: {pr['title']}\n"
            f"  Author: {pr.get('author', 'unknown')} | Status: {pr.get('status', 'UNKNOWN')}\n"
            f"  CI: {pr.get('ci_status', 'unknown')} | Branch: {pr.get('branch', '')} → {pr.get('base_branch', 'main')}\n"
            f"  Files: {files or 'N/A'}\n"
            f"  Current reviewers: {reviewers or 'none assigned'}\n"
            f"  Description: {pr.get('description', '')[:300]}"
        )
    return "\n\n".join(lines)


def _format_rag_context(chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return "No relevant documentation found."
    lines = []
    for i, chunk in enumerate(chunks, 1):
        content = chunk.parent_text or chunk.text
        lines.append(f"[Source {i}: {chunk.source} ({chunk.doc_type})]")
        lines.append(content[:700])
    return "\n\n".join(lines)


# ── Proposal card formatter ────────────────────────────────────────────────────

def _role_summary(review_data: dict, user_role: str, total_prs: int) -> str:
    """
    Generate a one-sentence role-appropriate intro above the review table.
    The table itself is identical for all roles — only this line changes.
    """
    pr_num    = review_data.get("pr_number", "")
    risk      = review_data.get("risk_level", "MEDIUM")
    ci        = review_data.get("ci_status", "unknown")
    concerns  = review_data.get("concerns", "").strip()
    pending   = sum(1 for p in ([] if total_prs <= 1 else []) if p.get("ci_status") != "passed")

    if user_role == "developer":
        action = "can be merged with caution" if risk == "MEDIUM" else ("is safe to merge" if risk == "LOW" else "needs rework before merge")
        return f"**Code review — {pr_num}:** {action}. {'Concerns: ' + concerns[:120] if concerns else 'No blocking concerns found.'}"

    if user_role in ("manager", "admin"):
        reviewer_note = "No reviewer assigned — assign manually in GitHub." if review_data.get("suggested_reviewer", "unassigned") == "unassigned" else f"Suggested reviewer: `{review_data.get('suggested_reviewer')}`."
        return f"**Sprint impact — {total_prs} open PR(s):** {pr_num} is {risk} risk. {reviewer_note}"

    # stakeholder / default
    release_signal = "does not block release" if risk in ("LOW", "MEDIUM") else "blocks release — rework required"
    return f"**Release signal — {pr_num}:** This change {release_signal}. CI: {ci}."


def _format_pr_proposal(review_data: dict, all_prs: list[dict] | None = None, user_role: str = "developer", action_mode: str = "assign") -> str:
    """
    Build the human-readable PR review card.
    Role-specific summary line on top; structured review table is identical for all roles.
    """
    risk_emoji = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}.get(
        review_data.get("risk_level", "MEDIUM"), "🟡"
    )
    standards_emoji  = "✅" if review_data.get("standards_result") == "PASS" else "⚠️"
    policy_emoji     = "✅" if review_data.get("version_policy_result") == "COMPLIANT" else "⚠️"
    ci_emoji         = "✅" if review_data.get("ci_status") == "passed" else "⏳" if review_data.get("ci_status") == "running" else "❌"

    total_prs = len(all_prs) if all_prs else 1
    lines     = [_role_summary(review_data, user_role, total_prs), ""]

    # Show all PRs that were reviewed (not just the chosen one)
    if all_prs and len(all_prs) > 1:
        lines += [f"**{len(all_prs)} open PRs reviewed:**", ""]
        for pr in all_prs:
            ci = pr.get("ci_status", "unknown")
            ci_icon = "✅" if ci == "passed" else "⏳" if ci == "running" else "❌" if ci == "failed" else "⚪"
            lines.append(f"- `{pr['id']}` {pr['title']} — CI: {ci_icon} {ci}")
        lines += ["", "---", ""]

    lines += [
        f"## PR Review: {review_data.get('pr_number', '')} — {review_data.get('pr_title', 'N/A')}",
        "",
        f"| Check | Result |",
        f"|-------|--------|",
        f"| Coding Standards | {standards_emoji} {review_data.get('standards_result', 'N/A')} |",
        f"| Version Policy   | {policy_emoji} {review_data.get('version_policy_result', 'N/A')} |",
        f"| CI Status        | {ci_emoji} {review_data.get('ci_status', 'unknown')} |",
        f"| Overall Risk     | {risk_emoji} {review_data.get('risk_level', 'MEDIUM')} |",
        "",
        f"📁 **Files changed:** {review_data.get('files_changed', 'N/A')}",
    ]

    concerns = review_data.get("concerns", "").strip()
    if concerns:
        lines += ["", f"⚠️ **Concerns:** {concerns}"]

    summary = review_data.get("summary", "").strip()
    if summary:
        lines += ["", summary]

    # Approve mode: ask to approve the PR itself (not assign a reviewer).
    if action_mode == "approve":
        pr_num = review_data.get("pr_number", "this PR")
        lines += [
            "",
            "---",
            f"_Shall I approve **{pr_num}**? (This approves the PR — it does not merge it.)_",
            "_Click **Approve** to approve, or **Reject** to cancel._",
        ]
        return "\n".join(lines)

    reviewer = review_data.get("suggested_reviewer", "unassigned")
    if reviewer and reviewer != "unassigned":
        lines += [
            "",
            f"**Suggested reviewer:** `{reviewer}` (based on file ownership)",
            "",
            "---",
            f"_Shall I assign `{reviewer}` as reviewer for this PR?_",
            "_Click **Approve** to assign, or **Reject** to cancel._",
        ]
    else:
        lines += [
            "",
            "📌 **Action needed:** No reviewer could be automatically identified.",
            "Please assign a reviewer manually in GitHub.",
        ]
    return "\n".join(lines)


# ── Agent class ────────────────────────────────────────────────────────────────

class PRReviewAgent(BaseAgent):
    """
    PR Review Agent — reviews PRs against coding standards and version policy.

    1. GitHub MCP — fetch open/relevant PRs
    2. RAG — version_policies/ (semver, API change policy)
    3. RAG — adr_documents/ (coding standards)
    4. LLM CoT → structured JSON review
    5. Format HITL reviewer assignment proposal
    6. Return AgentPayload(hitl_required=True)
    """

    def __init__(self, retriever: HybridRetriever, llm, config_loader=None, mcp_registry=None):
        super().__init__(
            mcp_registry=mcp_registry,
            retriever=retriever,
            llm=llm,
            config_loader=config_loader or _default_config,
        )

    async def _fetch_prs(self, query: str) -> list[dict]:
        """Fetch relevant open PRs from GitHub MCP."""
        if self.mcp is None:
            return []
        try:
            results = await asyncio.gather(
                self.mcp.get("github").search_prs(query),
                self.mcp.get("github").list_open_prs(),
                return_exceptions=True,
            )
            searched = results[0] if not isinstance(results[0], Exception) else []
            all_open = results[1] if not isinstance(results[1], Exception) else []

            # Merge: searched first, then any open PRs not already in results
            seen_ids = {pr["id"] for pr in searched}
            merged   = list(searched)
            for pr in all_open:
                if pr["id"] not in seen_ids:
                    merged.append(pr)
                    seen_ids.add(pr["id"])

            # Sort: code PRs first, documentation PRs last
            merged.sort(key=lambda p: (0 if _is_code_pr(p) else 1))
            logger.info("PRReviewAgent: %d PRs fetched (%d from search, %d open total)", len(merged), len(searched), len(all_open))
            return merged[:5]  # cap at 5 to avoid prompt bloat
        except Exception:
            logger.exception("PRReviewAgent: GitHub fetch failed — proceeding without PR data")
            return []

    @traceable(name="pr_review_agent", run_type="chain")
    async def run(self, state: SDLCState) -> AgentPayload:
        """Review relevant PRs and propose a reviewer assignment (HITL)."""
        query   = state["query"]
        project = state["project_id"]

        logger.info("PRReviewAgent.run: project='%s' query='%s...'", project, query[:60])

        # ── Step 1: GitHub PRs (async) ────────────────────────────────────────
        prs = await self._fetch_prs(query)

        # ── Step 2: RAG — version policy + coding standards (sync) ───────────
        version_chunks, version_conf = self.retriever.retrieve(
            f"version policy semver api breaking change {query}", project,
        )
        standards_chunks, _ = self.retriever.retrieve(
            f"coding standards code review adr {query}", project,
        )

        confidence = version_conf
        logger.info(
            "PRReviewAgent: %d PRs, %d version-policy chunks, %d standards chunks",
            len(prs), len(version_chunks), len(standards_chunks),
        )

        # ── Step 3: Build CoT prompt ─────────────────────────────────────────
        system_prompt    = self.config.get_prompt("system_prompt")
        reasoning_prompt = self.config.get_prompt(
            "pr_review_reasoning",
            pr_context=_format_pr_context(prs),
            version_policy_context=_format_rag_context(version_chunks[:4]),
            standards_context=_format_rag_context(standards_chunks[:3]),
        )

        # ── Step 4: LLM call via generate_structured (provider handles JSON extraction) ──
        temperature = self.config.get_temperature("agent_reasoning")   # 0.1
        resp        = await self.llm.generate_structured(reasoning_prompt, system_prompt, temperature, 1000)
        review_data = resp.structured if not resp.parse_error else {}

        # ── Fallback if JSON parse fails or LLM rate limited ────────────────────
        if not review_data:
            logger.warning("PRReviewAgent: JSON parse failed — using fallback review")
            primary_pr = prs[0] if prs else {}
            # Only mark checks as PASS/COMPLIANT when the PR has actual file data.
            # Without a diff, we cannot assess standards — use REVIEW_NEEDED.
            has_files = bool(primary_pr.get("files_changed"))
            review_data = {
                "pr_number":             primary_pr.get("id", "N/A"),
                "pr_title":              primary_pr.get("title", "Unknown PR"),
                "files_changed":         ", ".join(primary_pr.get("files_changed", [])) or "N/A — diff not fetched",
                "ci_status":             primary_pr.get("ci_status", "unknown"),
                "standards_result":      "REVIEW_NEEDED" if not has_files else "REVIEW_NEEDED",
                "version_policy_result": "REVIEW_NEEDED",
                "concerns":              "Could not complete automated review — please review manually.",
                "suggested_reviewer":    "unassigned",
                "risk_level":            "MEDIUM",
                "summary":               "Manual review required — file diff not available.",
            }

        # ── Guard: downgrade PASS result to REVIEW_NEEDED when no files were fetched.
        # The LLM cannot assess coding standards without seeing the actual changed files.
        chosen_pr_id = review_data.get("pr_number", "")
        chosen_pr    = next((p for p in prs if p["id"] == chosen_pr_id), prs[0] if prs else None)
        if chosen_pr and not chosen_pr.get("files_changed"):
            if review_data.get("standards_result") == "PASS":
                review_data["standards_result"] = "REVIEW_NEEDED"
                review_data.setdefault("concerns", "")
                review_data["concerns"] = (
                    "⚠️ File diff not available — coding standards check requires manual review. "
                    + review_data["concerns"]
                ).strip()

        # ── Populate CI status directly from GitHub data (not from LLM guess) ──
        if chosen_pr:
            real_ci = chosen_pr.get("ci_status", "unknown")
            if real_ci != "unknown":
                review_data["ci_status"] = real_ci

        # ── If pr_number not set by LLM, use the first PR ───────────────────
        if not review_data.get("pr_number") and prs:
            review_data["pr_number"] = prs[0]["id"]
            review_data["pr_title"]  = review_data.get("pr_title") or prs[0]["title"]

        # ── If LLM left reviewer as "unassigned", pull from the PR's own reviewers.
        # If still unassigned, leave it — hitl.py will refuse to call GitHub with "unassigned".
        if review_data.get("suggested_reviewer", "unassigned") == "unassigned":
            chosen_pr = next(
                (p for p in prs if p["id"] == review_data.get("pr_number")),
                prs[0] if prs else None,
            )
            if chosen_pr and chosen_pr.get("reviewers"):
                review_data["suggested_reviewer"] = chosen_pr["reviewers"][0]

        # ── Step 5: Format proposal card + decide if HITL is needed ─────────
        # HITL only makes sense when a real reviewer name was identified.
        # "unassigned" means no reviewer could be determined — show a read-only
        # review card instead so the user isn't asked to approve a no-op.
        pr_number          = review_data.get("pr_number", "")
        suggested_reviewer = review_data.get("suggested_reviewer", "unassigned")
        # Did the user ask to approve/merge the PR, or just to review/assign a reviewer?
        wants_approval = bool(re.search(r'\b(approve|merge|sign[\s-]?off|lgtm)\b', query, re.IGNORECASE))

        if wants_approval and pr_number:
            action_type   = "approve_pr"
            hitl_required = True
        else:
            action_type   = "assign_reviewer"
            hitl_required = bool(suggested_reviewer and suggested_reviewer != "unassigned")

        final_response = _format_pr_proposal(
            review_data, all_prs=prs,
            user_role=state.get("user_role", "developer"),
            action_mode="approve" if action_type == "approve_pr" else "assign",
        )

        proposal = {
            "action":             action_type,
            "pr_number":          pr_number,
            "pr_title":           review_data.get("pr_title", ""),
            "suggested_reviewer": suggested_reviewer,
            "project":            project,
            "review_data":        review_data,
        }

        all_sources = list({c.source for c in version_chunks + standards_chunks})
        if prs:
            all_sources.append("github_live")

        return AgentPayload(
            agent_name="pr_review_agent",
            confidence=confidence,
            summary=f"PR review: {review_data.get('pr_number', '')} — {review_data.get('risk_level', 'MEDIUM')} risk",
            structured={
                "final_response": final_response,
                "review_data":    review_data,
                "skip_persona":   True,   # review cards must not be rewritten into sprint-status language
                "rag_chunks": [
                    {"text": c.text, "source": c.source, "score": c.score}
                    for c in version_chunks + standards_chunks
                ],
            },
            sources=all_sources,
            hitl_required=hitl_required,
            hitl_proposal=proposal if hitl_required else {},
        )
