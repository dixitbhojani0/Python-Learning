"""
connectors/jira_connector.py

Real Jira connector — Jira REST API v3 via httpx.
Auth: Basic auth with base64(email:api_token) — standard Jira Cloud auth.
"""
import base64
import logging
from typing import Any

import httpx

from core.settings import settings
from connectors.base_connector import BaseMCPConnector

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(connect=5.0, read=25.0, write=5.0, pool=5.0)


def _basic_auth_header(email: str, token: str) -> str:
    encoded = base64.b64encode(f"{email}:{token}".encode()).decode()
    return f"Basic {encoded}"


def _extract_adf_text(node: object) -> str:
    """Recursively extract plain text from an Atlassian Document Format (ADF) node."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        parts = []
        for child in node.get("content", []):
            parts.append(_extract_adf_text(child))
        return " ".join(p for p in parts if p)
    if isinstance(node, list):
        return " ".join(_extract_adf_text(n) for n in node)
    return ""


def _normalize_issue(issue: dict) -> dict:
    fields = issue.get("fields", {})
    assignee = fields.get("assignee") or {}
    status   = fields.get("status", {})
    priority = fields.get("priority", {})
    labels   = fields.get("labels", [])
    blockers = [
        link["outwardIssue"]["key"]
        for link in fields.get("issuelinks", [])
        if link.get("type", {}).get("name") == "Blocks" and "outwardIssue" in link
    ]
    raw_desc = fields.get("description")
    description = _extract_adf_text(raw_desc) if isinstance(raw_desc, dict) else str(raw_desc or "")
    raw_comments = (fields.get("comment") or {}).get("comments", [])
    comments = [
        {
            "author":  (c.get("author") or {}).get("displayName", "unknown"),
            "created": (c.get("created") or "")[:10],
            "body":    _extract_adf_text(c.get("body")),
        }
        for c in raw_comments
    ][-5:]
    return {
        "id":          issue.get("key", ""),
        "title":       fields.get("summary", ""),
        "status":      status.get("name", "UNKNOWN").upper().replace(" ", "_"),
        "priority":    priority.get("name", "MEDIUM").upper(),
        "assignee":    assignee.get("displayName", "unassigned"),
        "description": description,
        "labels":      labels,
        "blockers":    blockers,
        "comments":    comments,
        "created":     (fields.get("created") or "")[:10],
        "updated":     (fields.get("updated") or "")[:10],
        "sprint":      _extract_sprint_name(fields),
    }


def _extract_sprint_name(fields: dict) -> str:
    sprints = fields.get("customfield_10020") or []
    if sprints and isinstance(sprints, list):
        active = [s for s in sprints if isinstance(s, dict) and s.get("state") == "active"]
        if active:
            return active[0].get("name", "")
        if sprints:
            return sprints[-1].get("name", "")
    return ""


class JiraConnector(BaseMCPConnector):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._base_url = settings.JIRA_BASE_URL.rstrip("/")
        self._project  = settings.JIRA_PROJECT_KEY
        self._headers  = {
            "Authorization": _basic_auth_header(settings.JIRA_EMAIL, settings.JIRA_TOKEN),
            "Accept":        "application/json",
            "Content-Type":  "application/json",
        }

    def is_available(self) -> bool:
        return bool(
            settings.JIRA_TOKEN
            and settings.JIRA_TOKEN != "placeholder"
            and settings.JIRA_BASE_URL
            and "your-org" not in settings.JIRA_BASE_URL
        )

    async def get_ticket(self, ticket_id: str) -> dict | None:
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                r = await client.get(
                    f"{self._base_url}/rest/api/3/issue/{ticket_id.upper()}",
                    params={"fields": "summary,status,priority,assignee,labels,description,issuelinks,customfield_10020,created,updated,comment"},
                )
                r.raise_for_status()
            logger.info("JiraConnector.get_ticket: fetched '%s'", ticket_id)
            return _normalize_issue(r.json())
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                logger.warning("JiraConnector.get_ticket: ticket '%s' not found", ticket_id)
                return None
            logger.exception("JiraConnector.get_ticket failed — HTTP %d", exc.response.status_code)
            return None
        except Exception:
            logger.exception("JiraConnector.get_ticket failed for '%s'", ticket_id)
            return None

    async def search_tickets(self, query: str, project: str = "") -> list[dict]:
        import re
        project_key = project.upper() if project else self._project

        ticket_id_match = re.search(r'\b([A-Z]+)\s*-\s*(\d+)\b', query.upper())
        if ticket_id_match:
            ticket_id = f"{ticket_id_match.group(1)}-{ticket_id_match.group(2)}"
            ticket = await self.get_ticket(ticket_id)
            if ticket:
                return [ticket]

        keywords = [kw.replace('"', '\\"') for kw in self._extract_keywords(query)]
        query_normalized = query.lower().replace("-", " ")

        if "sprint" in query_normalized:
            jql = f'project = "{project_key}" AND sprint in openSprints() ORDER BY updated DESC'
        elif assignee_match := re.search(r'(?:assigned to|owned by)\s+(\w+)', query_normalized):
            name = assignee_match.group(1).capitalize()
            jql = f'project = "{project_key}" AND assignee = "{name}" ORDER BY updated DESC'
        elif any(w in query_normalized for w in ["in progress", "blocked", "done", "to do", "open"]):
            status_map = {
                "in progress": "In Progress", "blocked": "Blocked",
                "done": "Done", "to do": "To Do", "open": "To Do",
            }
            matched_status = next((v for k, v in status_map.items() if k in query_normalized), None)
            if matched_status:
                jql = f'project = "{project_key}" AND status = "{matched_status}" ORDER BY updated DESC'
            else:
                jql = self._text_jql(project_key, keywords)
        else:
            jql = self._text_jql(project_key, keywords)

        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                r = await client.post(
                    f"{self._base_url}/rest/api/3/search/jql",
                    json={
                        "jql": jql,
                        "maxResults": 50,
                        "fields": ["summary", "status", "priority", "assignee", "labels", "description", "issuelinks", "customfield_10020", "created", "updated"],
                    },
                )
                r.raise_for_status()
            issues = r.json().get("issues", [])
            logger.info("JiraConnector.search_tickets: '%s' → %d issues", query[:50], len(issues))
            return [_normalize_issue(i) for i in issues]
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (400, 404, 410):
                logger.warning("JiraConnector.search_tickets: project '%s' returned HTTP %d", project_key, status)
                return []
            logger.exception("JiraConnector.search_tickets failed — HTTP %d", status)
            return []
        except Exception:
            logger.exception("JiraConnector.search_tickets failed for query='%s'", query[:50])
            return []

    def _extract_keywords(self, query: str) -> list[str]:
        import re as _re
        stop_words = {
            "what", "which", "who", "when", "why", "how", "where", "whose",
            "is", "are", "was", "were", "can", "will", "should", "would",
            "could", "did", "do", "does", "has", "have", "had",
            "show", "me", "list", "tell", "give", "find", "get", "check",
            "look", "see", "describe", "explain", "summarize",
            "the", "a", "an", "and", "or", "of", "on", "in", "for",
            "about", "with", "from", "to", "at", "by", "its", "our",
            "caused", "cause", "resolved", "resolve", "fixed", "fix",
            "happened", "occur", "occurred", "affect", "affecting", "affected",
            "related", "relate", "handle", "handling", "need", "needed",
            "implement", "create", "add", "build", "develop", "write", "make",
            "error", "bug", "issue", "problem", "ticket", "tickets", "story",
            "task", "defect", "feature", "change", "changes", "update", "fix", "solution",
            "thing", "stuff", "item", "case",
            "status", "current", "now", "today", "latest", "recent",
            "please", "right", "just", "also", "still",
        }
        words = _re.findall(r'\b\w+\b', query.lower())
        return [w for w in words if w not in stop_words and len(w) >= 2][:5]

    def _text_jql(self, project_key: str, keywords: list[str]) -> str:
        if not keywords:
            return f'project = "{project_key}" ORDER BY updated DESC'
        parts = [f'(summary ~ "{kw}" OR description ~ "{kw}" OR comment ~ "{kw}")' for kw in keywords]
        return f'project = "{project_key}" AND ({" OR ".join(parts)}) ORDER BY updated DESC'

    async def get_blocked_tickets(self, project: str = "") -> list[dict]:
        project_key = project.upper() if project else self._project
        jql = (
            f'project = "{project_key}" AND '
            f'(status = "Blocked" OR labels = "blocked" OR priority = "Blocker") '
            f'AND statusCategory != "Done" '
            f'ORDER BY priority DESC'
        )
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                r = await client.post(
                    f"{self._base_url}/rest/api/3/search/jql",
                    json={"jql": jql, "maxResults": 20, "fields": ["summary", "status", "priority", "assignee", "labels", "description", "issuelinks", "created", "updated"]},
                )
                r.raise_for_status()
            issues = r.json().get("issues", [])
            logger.info("JiraConnector.get_blocked_tickets: %d blocked", len(issues))
            return [_normalize_issue(i) for i in issues]
        except Exception:
            logger.exception("JiraConnector.get_blocked_tickets failed")
            return []

    async def get_sprint_board(self, project: str = "") -> dict:
        project_key = project.upper() if project else self._project
        jql_base = f'project = "{project_key}" AND sprint in openSprints()'
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                r = await client.post(
                    f"{self._base_url}/rest/api/3/search/jql",
                    json={"jql": jql_base, "maxResults": 50, "fields": ["summary", "status", "priority", "customfield_10020", "labels"]},
                )
                r.raise_for_status()
            issues = r.json().get("issues", [])
            if not issues:
                return {"sprint": "No active sprint", "project": project_key, "total_tickets": 0}

            def _status_cat(issue: dict) -> str:
                return issue["fields"].get("status", {}).get("statusCategory", {}).get("key", "new").lower()

            done    = sum(1 for i in issues if _status_cat(i) == "done")
            in_prog = sum(1 for i in issues if _status_cat(i) == "indeterminate")
            blocked = sum(
                1 for i in issues
                if "block" in " ".join(i["fields"].get("labels", [])).lower()
                and _status_cat(i) != "done"
            )
            total = len(issues)
            pct   = round((done / total) * 100) if total else 0
            sprint_name = _extract_sprint_name(issues[0]["fields"]) or "Current Sprint"
            risk  = "HIGH" if blocked >= 2 or pct < 25 else "MEDIUM" if pct < 60 else "LOW"
            logger.info("JiraConnector.get_sprint_board: sprint='%s' total=%d done=%d", sprint_name, total, done)
            return {
                "sprint": sprint_name, "project": project_key, "total_tickets": total,
                "done": done, "in_progress": in_prog, "blocked": blocked,
                "not_started": max(0, total - done - in_prog),
                "completion_pct": pct, "risk_level": risk,
            }
        except Exception:
            logger.exception("JiraConnector.get_sprint_board failed")
            return {"sprint": "unknown", "project": project_key, "total_tickets": 0, "risk_level": "UNKNOWN"}

    async def get_project_members(self, project: str = "") -> list[dict]:
        project_key = project.upper() if project else self._project
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                r = await client.get(
                    f"{self._base_url}/rest/api/3/user/assignable/search",
                    params={"project": project_key, "maxResults": 50},
                )
                r.raise_for_status()
            users = r.json()
            members = [
                {
                    "name":         u.get("name") or u.get("accountId", ""),
                    "display_name": u.get("displayName", ""),
                    "account_id":   u.get("accountId", ""),
                    "email":        u.get("emailAddress", ""),
                    "active":       u.get("active", True),
                }
                for u in users if u.get("active", True)
            ]
            logger.info("JiraConnector.get_project_members: %d members for '%s'", len(members), project_key)
            return members
        except Exception:
            logger.warning("JiraConnector.get_project_members: failed for '%s'", project_key)
            return []

    async def add_issue_to_sprint(self, ticket_id: str, sprint_id: int) -> bool:
        url = f"{self._base_url}/rest/agile/1.0/sprint/{sprint_id}/issue"
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                r = await client.post(url, json={"issues": [ticket_id]})
                if r.status_code in (200, 204):
                    logger.info("JiraConnector.add_issue_to_sprint: %s → sprint %d", ticket_id, sprint_id)
                    return True
                logger.warning("JiraConnector.add_issue_to_sprint: HTTP %d for %s → sprint %d", r.status_code, ticket_id, sprint_id)
        except Exception:
            logger.exception("JiraConnector.add_issue_to_sprint failed for %s → sprint %d", ticket_id, sprint_id)
        return False

    async def get_active_sprint_id(self, project: str = "") -> int | None:
        project_key = (project or self._project).upper()
        jql = f'project = "{project_key}" AND sprint in openSprints()'
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                r = await client.post(
                    f"{self._base_url}/rest/api/3/search",
                    json={"jql": jql, "maxResults": 1, "fields": ["customfield_10020"]},
                )
                r.raise_for_status()
            issues = r.json().get("issues", [])
            if not issues:
                return None
            sprints = issues[0]["fields"].get("customfield_10020") or []
            active = [s for s in sprints if isinstance(s, dict) and s.get("state") == "active"]
            if active:
                return int(active[0]["id"])
        except Exception:
            logger.debug("JiraConnector.get_active_sprint_id: could not fetch sprint id")
        return None

    async def create_ticket(
        self,
        title: str,
        description: str,
        priority: str = "MEDIUM",
        issue_type: str = "Story",
        assignee_account_id: str = "",
        labels: list[str] | None = None,
        sprint_id: int | None = None,
    ) -> dict:
        _JIRA_PRIORITY = {
            "P0": "Highest", "P1": "High", "P2": "Medium", "P3": "Low",
            "CRITICAL": "Highest", "HIGH": "High", "MEDIUM": "Medium", "LOW": "Low",
            "HIGHEST": "Highest", "LOWEST": "Lowest",
        }
        jira_priority = _JIRA_PRIORITY.get(priority.upper(), "Medium")
        fields: dict[str, Any] = {
            "project":     {"key": self._project},
            "summary":     title,
            "description": {
                "type": "doc", "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": description}]}],
            },
            "issuetype": {"name": issue_type},
            "priority":  {"name": jira_priority},
            "labels":    labels or [],
        }
        if assignee_account_id and assignee_account_id != "unassigned":
            fields["assignee"] = {"accountId": assignee_account_id}
        if sprint_id:
            fields["customfield_10020"] = sprint_id

        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                r = await client.post(f"{self._base_url}/rest/api/3/issue", json={"fields": fields})
                r.raise_for_status()
            data = r.json()
            ticket_id = data.get("key", "")
            logger.info("JiraConnector.create_ticket: created '%s' → %s", title[:60], ticket_id)
            return {"id": ticket_id, "url": f"{self._base_url}/browse/{ticket_id}"}
        except Exception:
            logger.exception("JiraConnector.create_ticket failed for title='%s'", title[:60])
            return {}

    async def update_ticket(self, ticket_id: str, description: str = "", summary: str = "", labels: list[str] | None = None) -> dict:
        fields: dict[str, Any] = {}
        if description:
            fields["description"] = {
                "type": "doc", "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": description}]}],
            }
        if summary:
            fields["summary"] = summary
        if labels is not None:
            fields["labels"] = labels
        if not fields:
            return {"success": False, "error": "nothing to update"}
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                r = await client.put(
                    f"{self._base_url}/rest/api/3/issue/{ticket_id.upper()}",
                    json={"fields": fields},
                )
            if r.status_code == 204:
                logger.info("JiraConnector.update_ticket: updated '%s'", ticket_id)
                return {"success": True, "ticket_id": ticket_id}
            return {"success": False, "error": f"HTTP {r.status_code}"}
        except Exception:
            logger.exception("JiraConnector.update_ticket: failed for %s", ticket_id)
            return {"success": False, "error": "request failed"}

    async def assign_ticket(self, ticket_id: str, account_id: str) -> dict:
        url = f"{self._base_url}/rest/api/3/issue/{ticket_id}/assignee"
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                r = await client.put(url, json={"accountId": account_id})
            if r.status_code == 204:
                logger.info("JiraConnector.assign_ticket: %s → accountId=%s", ticket_id, account_id)
                return {"success": True, "ticket_id": ticket_id, "account_id": account_id}
            return {"success": False, "error": f"HTTP {r.status_code}"}
        except Exception:
            logger.exception("JiraConnector.assign_ticket: failed for %s", ticket_id)
            return {"success": False, "error": "request failed"}

    async def add_comment(self, ticket_id: str, body: str) -> dict:
        payload = {
            "body": {
                "type": "doc", "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": body}]}],
            }
        }
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                r = await client.post(
                    f"{self._base_url}/rest/api/3/issue/{ticket_id.upper()}/comment",
                    json=payload,
                )
                r.raise_for_status()
            logger.info("JiraConnector.add_comment: added to %s", ticket_id)
            return {"success": True, "ticket_id": ticket_id}
        except Exception:
            logger.exception("JiraConnector.add_comment: failed for %s", ticket_id)
            return {"success": False, "error": "request failed"}


from registry import MCPRegistry  # noqa: E402
MCPRegistry.register("jira", JiraConnector)
