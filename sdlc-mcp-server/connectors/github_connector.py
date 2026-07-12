"""
connectors/github_connector.py

Real GitHub connector — GitHub REST API v3 via httpx.
Auth: Bearer token (fine-grained PAT or classic token with repo + read:org scopes).
"""
import asyncio
import logging

import httpx

from core.settings import settings
from connectors.base_connector import BaseMCPConnector

logger = logging.getLogger(__name__)

_API_BASE = "https://api.github.com"
_TIMEOUT  = httpx.Timeout(connect=5.0, read=25.0, write=5.0, pool=5.0)


def _error_message(exc: httpx.HTTPStatusError) -> str:
    """
    Pull GitHub's own `message` field out of an error response body.

    Surfacing GitHub's real message (e.g. "Resource not accessible by personal
    access token") beats guessing the reason from the status code alone — a 403
    on a PR review endpoint can mean an under-scoped fine-grained PAT just as
    easily as a self-review restriction, and a wrong guess is worse than none.
    """
    try:
        return exc.response.json().get("message", "")
    except Exception:
        return ""


def _normalize_pr(pr: dict) -> dict:
    labels    = [lb["name"] for lb in pr.get("labels", [])]
    reviewers = [r["login"] for r in pr.get("requested_reviewers", [])]
    files     = pr.get("_files_changed", [])
    return {
        "id":           f"PR-{pr.get('number', '')}",
        "title":        pr.get("title", ""),
        "author":       (pr.get("user") or {}).get("login", "unknown"),
        "status":       "MERGED" if pr.get("merged_at") else pr.get("state", "open").upper(),
        "repo":         (pr.get("base", {}).get("repo") or {}).get("name", ""),
        "branch":       (pr.get("head") or {}).get("ref", ""),
        "base_branch":  (pr.get("base") or {}).get("ref", "main"),
        "created":      (pr.get("created_at") or "")[:10],
        "merged":       (pr.get("merged_at") or "")[:10] or None,
        "description":  (pr.get("body") or "")[:300],
        "reviewers":    reviewers,
        "files_changed": files,
        "labels":       labels,
        "ci_status":    pr.get("_ci_status", "unknown"),
    }


class GitHubConnector(BaseMCPConnector):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._headers = {
            "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
            "Accept":        "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        parts       = settings.GITHUB_REPO.split("/", 1)
        self._owner = parts[0] if len(parts) == 2 else ""
        self._repo  = parts[1] if len(parts) == 2 else settings.GITHUB_REPO

    def is_available(self) -> bool:
        return bool(
            settings.GITHUB_TOKEN
            and settings.GITHUB_TOKEN not in ("placeholder", "ghp_placeholder_replace_with_your_token")
            and self._owner
            and self._repo
            and "your-org" not in settings.GITHUB_REPO
        )

    async def _get_pr_ci_status(self, client: httpx.AsyncClient, pr_number: int) -> str:
        try:
            r = await client.get(
                f"{_API_BASE}/repos/{self._owner}/{self._repo}/pulls/{pr_number}/commits",
                timeout=_TIMEOUT,
            )
            commits = r.json()
            if not commits:
                return "unknown"
            latest_sha = commits[-1]["sha"]
            r2 = await client.get(
                f"{_API_BASE}/repos/{self._owner}/{self._repo}/commits/{latest_sha}/check-runs",
                timeout=_TIMEOUT,
            )
            runs = r2.json().get("check_runs", [])
            if not runs:
                return "unknown"
            conclusions = [run.get("conclusion") for run in runs if run.get("conclusion")]
            if all(c == "success" for c in conclusions):
                return "passed"
            if any(c in ("failure", "timed_out") for c in conclusions):
                return "failed"
            return "running"
        except Exception:
            return "unknown"

    async def search_prs(self, query: str, repo: str = "") -> list[dict]:
        target_repo = repo or self._repo
        q = f"repo:{self._owner}/{target_repo} is:pr {query}"
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                r = await client.get(
                    f"{_API_BASE}/search/issues",
                    params={"q": q, "per_page": 5, "sort": "updated"},
                )
                r.raise_for_status()
            items = r.json().get("items", [])
            results = []
            for item in items:
                results.append({
                    "id":          f"PR-{item.get('number', '')}",
                    "title":       item.get("title", ""),
                    "author":      (item.get("user") or {}).get("login", "unknown"),
                    "status":      "OPEN" if item.get("state") == "open" else "CLOSED",
                    "repo":        target_repo,
                    "branch":      "",
                    "base_branch": "main",
                    "created":     (item.get("created_at") or "")[:10],
                    "merged":      None,
                    "description": (item.get("body") or "")[:300],
                    "reviewers":   [],
                    "files_changed": [],
                    "labels":      [lb["name"] for lb in item.get("labels", [])],
                    "ci_status":   "unknown",
                })
            logger.info("GitHubConnector.search_prs: '%s' → %d PRs", query[:50], len(results))
            return results
        except Exception:
            logger.exception("GitHubConnector.search_prs failed for query='%s'", query[:50])
            return []

    async def list_open_prs(self, repo: str = "") -> list[dict]:
        target_repo = repo or self._repo
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                r = await client.get(
                    f"{_API_BASE}/repos/{self._owner}/{target_repo}/pulls",
                    params={"state": "open", "per_page": 10, "sort": "updated"},
                )
                r.raise_for_status()
                prs = r.json()
                results = []
                for pr in prs:
                    ci = await self._get_pr_ci_status(client, pr["number"])
                    pr["_ci_status"] = ci
                    try:
                        r_files = await client.get(
                            f"{_API_BASE}/repos/{self._owner}/{target_repo}/pulls/{pr['number']}/files",
                            timeout=_TIMEOUT,
                        )
                        pr["_files_changed"] = [f["filename"] for f in (r_files.json() if r_files.is_success else [])]
                    except Exception:
                        pr["_files_changed"] = []
                    results.append(_normalize_pr(pr))
            logger.info("GitHubConnector.list_open_prs: %d open PRs in '%s'", len(results), target_repo)
            return results
        except Exception:
            logger.exception("GitHubConnector.list_open_prs failed for repo='%s'", target_repo)
            return []

    async def get_pr_details(self, pr_id: str) -> dict | None:
        number = pr_id.replace("PR-", "").strip()
        base = f"{_API_BASE}/repos/{self._owner}/{self._repo}"
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                r = await client.get(f"{base}/pulls/{number}")
                if r.status_code == 404:
                    return None
                r.raise_for_status()
                pr = r.json()

                files_r, reviews_r, review_cmts_r, issue_cmts_r, ci = await asyncio.gather(
                    client.get(f"{base}/pulls/{number}/files"),
                    client.get(f"{base}/pulls/{number}/reviews"),
                    client.get(f"{base}/pulls/{number}/comments"),
                    client.get(f"{base}/issues/{number}/comments"),
                    self._get_pr_ci_status(client, int(number)),
                    return_exceptions=True,
                )

                def _safe(resp):
                    return resp.json() if hasattr(resp, "is_success") and resp.is_success else []

                pr["_files_changed"] = [f["filename"] for f in _safe(files_r)]
                pr["_ci_status"] = ci if isinstance(ci, str) else "unknown"
                pr["_reviews"] = [
                    {"state": rv.get("state"), "user": (rv.get("user") or {}).get("login", ""), "body": rv.get("body") or "", "submitted_at": rv.get("submitted_at", "")}
                    for rv in _safe(reviews_r)
                ]
                pr["_review_comments"] = [
                    {"user": (c.get("user") or {}).get("login", ""), "body": c.get("body") or "", "path": c.get("path", ""), "line": c.get("line"), "created_at": c.get("created_at", "")}
                    for c in _safe(review_cmts_r)
                ]
                pr["_issue_comments"] = [
                    {"user": (c.get("user") or {}).get("login", ""), "body": c.get("body") or "", "created_at": c.get("created_at", "")}
                    for c in _safe(issue_cmts_r)
                ]

            normalized = _normalize_pr(pr)
            normalized["reviews"]         = pr["_reviews"]
            normalized["review_comments"] = pr["_review_comments"]
            normalized["issue_comments"]  = pr["_issue_comments"]
            logger.info("GitHubConnector.get_pr_details: PR-%s with %d reviews", number, len(normalized["reviews"]))
            return normalized
        except Exception:
            logger.exception("GitHubConnector.get_pr_details failed for pr_id='%s'", pr_id)
            return None

    async def is_collaborator(self, username: str) -> bool:
        """
        True if `username` is a collaborator on this repo (GET .../collaborators/{username}
        — 204 if yes, 404 if not, per GitHub's docs). Lets callers validate a reviewer
        BEFORE proposing to assign them, instead of only discovering at write-time that
        GitHub silently ignores a nonexistent/unreachable username (see assign_reviewer).
        """
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                r = await client.get(f"{_API_BASE}/repos/{self._owner}/{self._repo}/collaborators/{username}")
            return r.status_code == 204
        except Exception:
            # Fail OPEN: this check only exists to skip a proposal we already know will
            # fail. A network hiccup here isn't evidence the user is invalid — treat it
            # as "unknown, don't block" rather than turning our own transient error into
            # a false rejection of a possibly-legitimate reviewer.
            logger.exception("GitHubConnector.is_collaborator failed for '%s' — assuming valid (fail-open)", username)
            return True

    async def assign_reviewer(self, pr_id: str, reviewer: str) -> dict:
        number = pr_id.replace("PR-", "").strip()
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                r = await client.post(
                    f"{_API_BASE}/repos/{self._owner}/{self._repo}/pulls/{number}/requested_reviewers",
                    json={"reviewers": [reviewer]},
                )
                r.raise_for_status()
            # GitHub returns 201 even when `reviewer` doesn't exist or can't be reached
            # (e.g. a nonexistent username) — it silently omits them from the response's
            # requested_reviewers instead of erroring. A 2xx status alone does NOT mean
            # the reviewer was actually added; verify against the response body.
            actual = [rv["login"].lower() for rv in r.json().get("requested_reviewers", [])]
            if reviewer.lower() not in actual:
                logger.warning(
                    "GitHubConnector.assign_reviewer: GitHub returned 2xx but did not add '%s' to PR-%s "
                    "(user likely doesn't exist or isn't reachable for this repo)", reviewer, number,
                )
                return {
                    "pr": pr_id, "reviewer": reviewer, "status": "error", "http_code": r.status_code,
                    "message": f"GitHub accepted the request but did not add '{reviewer}' as a reviewer "
                               f"— the user likely doesn't exist or can't be reached for this repo.",
                }
            logger.info("GitHubConnector.assign_reviewer: %s → PR-%s", reviewer, number)
            return {"pr": pr_id, "reviewer": reviewer, "status": "assigned"}
        except httpx.HTTPStatusError as exc:
            code, message = exc.response.status_code, _error_message(exc)
            logger.exception("GitHubConnector.assign_reviewer failed for %s (HTTP %s): %s", pr_id, code, message)
            return {"pr": pr_id, "reviewer": reviewer, "status": "error", "http_code": code, "message": message}
        except Exception:
            logger.exception("GitHubConnector.assign_reviewer failed for %s", pr_id)
            return {"pr": pr_id, "reviewer": reviewer, "status": "error", "http_code": 0, "message": ""}

    async def approve_pr(self, pr_id: str, approver: str = "") -> dict:
        number = pr_id.replace("PR-", "").strip()
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                r = await client.post(
                    f"{_API_BASE}/repos/{self._owner}/{self._repo}/pulls/{number}/reviews",
                    json={"event": "APPROVE"},
                )
                r.raise_for_status()
            logger.info("GitHubConnector.approve_pr: PR-%s APPROVED", number)
            return {"pr": pr_id, "status": "APPROVED", "approved_by": approver}
        except httpx.HTTPStatusError as exc:
            code, message = exc.response.status_code, _error_message(exc)
            logger.exception("GitHubConnector.approve_pr failed for %s (HTTP %s): %s", pr_id, code, message)
            return {"pr": pr_id, "status": "error", "http_code": code, "message": message}
        except Exception:
            logger.exception("GitHubConnector.approve_pr failed for %s", pr_id)
            return {"pr": pr_id, "status": "error", "http_code": 0, "message": ""}

    async def request_changes_pr(self, pr_id: str, body: str = "", reviewer: str = "") -> dict:
        number = pr_id.replace("PR-", "").strip()
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                r = await client.post(
                    f"{_API_BASE}/repos/{self._owner}/{self._repo}/pulls/{number}/reviews",
                    json={"event": "REQUEST_CHANGES", "body": body or "Changes requested."},
                )
                r.raise_for_status()
            logger.info("GitHubConnector.request_changes_pr: PR-%s changes requested", number)
            return {"pr": pr_id, "status": "CHANGES_REQUESTED", "reviewer": reviewer, "body": body}
        except httpx.HTTPStatusError as exc:
            code, message = exc.response.status_code, _error_message(exc)
            logger.exception("GitHubConnector.request_changes_pr failed for %s (HTTP %s): %s", pr_id, code, message)
            return {"pr": pr_id, "status": "error", "http_code": code, "message": message}
        except Exception:
            logger.exception("GitHubConnector.request_changes_pr failed for %s", pr_id)
            return {"pr": pr_id, "status": "error", "http_code": 0, "message": ""}


from registry import MCPRegistry  # noqa: E402
MCPRegistry.register("github", GitHubConnector)
