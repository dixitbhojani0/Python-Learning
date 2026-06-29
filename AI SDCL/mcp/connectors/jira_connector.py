"""
backend/mcp/connectors/jira_connector.py

Real Jira connector — Jira REST API v3 via httpx.

Auth: Basic auth with base64(email:api_token) — standard Jira Cloud auth.
Auto-selected by MCPRegistry when JIRA_TOKEN != "placeholder" in settings.

Jira REST API docs: https://developer.atlassian.com/cloud/jira/platform/rest/v3/
"""
import base64
import logging
from typing import Any

import httpx

from backend.core.settings import settings
from backend.mcp.base_connector import BaseMCPConnector

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(connect=5.0, read=25.0, write=5.0, pool=5.0)


def _mock_fallback():
    """Return a MockJiraConnector instance for graceful fallback when the real API fails."""
    from backend.mcp.connectors.mock_jira import MockJiraConnector  # local import avoids circular dep
    return MockJiraConnector(name="mock_fallback", connector_config={})


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
    """Map a Jira REST API issue response to the flat dict format agents expect."""
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
    # B3: surface developer comments — the source of truth for "how big / how long".
    # Only present when get_ticket fetched the `comment` field (search doesn't);
    # keep the latest few so a stakeholder sees the real story, not a guess.
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
    """Extract sprint name from the customfield_10020 array (Jira cloud sprint field)."""
    sprints = fields.get("customfield_10020") or []
    if sprints and isinstance(sprints, list):
        active = [s for s in sprints if isinstance(s, dict) and s.get("state") == "active"]
        if active:
            return active[0].get("name", "")
        if sprints:
            return sprints[-1].get("name", "")
    return ""


class JiraConnector(BaseMCPConnector):
    """
    Real Jira Cloud connector — calls Jira REST API v3.

    Requires:
        JIRA_BASE_URL  — e.g. https://your-org.atlassian.net
        JIRA_EMAIL     — account email
        JIRA_TOKEN     — API token from id.atlassian.com
        JIRA_PROJECT_KEY — e.g. SDLC
    """

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
        """
        Direct lookup of a single ticket by its key (e.g. SDLC-5).
        Uses GET /rest/api/3/issue/{key} — much faster than JQL search.
        """
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
        """
        Smart ticket search — detects query intent and builds the right JQL:

        - Ticket ID (e.g. "SDLC-5", "SDLC-123") → direct key lookup via get_ticket()
        - Assignee (e.g. "tickets assigned to alice") → assignee = "alice"
        - Status (e.g. "in progress tickets") → status = "In Progress"
        - Labels (e.g. "tickets labeled blocked") → labels = "blocked"
        - General text → summary ~ OR description ~ OR comment ~

        Uses POST /rest/api/3/search/jql (text ~ is deprecated, returns 410 on Jira Cloud).
        """
        import re
        project_key = project.upper() if project else self._project

        # ── Detect ticket ID pattern (e.g. SDLC-5, PROJ-123) ──────────────────
        # Primary: direct key lookup — exact, fast, fetches comments too.
        # Fallback: if ticket not found or API error, continue to text search below.
        # Match "SDLC-4", "sdlc-4", "SDLC -4", "sdlc - 4" — normalize spaces out
        ticket_id_match = re.search(r'\b([A-Z]+)\s*-\s*(\d+)\b', query.upper())
        if ticket_id_match:
            ticket_id = f"{ticket_id_match.group(1)}-{ticket_id_match.group(2)}"
            ticket = await self.get_ticket(ticket_id)
            if ticket:
                return [ticket]
            logger.info(
                "JiraConnector.search_tickets: direct lookup for '%s' returned nothing — falling back to text search",
                ticket_id_match.group(1),
            )

        # Extract domain keywords — used for OR-based JQL text search
        keywords = [kw.replace('"', '\\"') for kw in self._extract_keywords(query)]

        # Normalize hyphens to spaces for intent detection so "in-progress" == "in progress"
        query_normalized = query.lower().replace("-", " ")

        # ── Detect assignee intent ─────────────────────────────────────────────
        assignee_match = re.search(
            r'(?:assigned to|owned by)\s+(\w+)', query_normalized
        )
        if assignee_match:
            name = assignee_match.group(1).capitalize()
            jql = (
                f'project = "{project_key}" AND assignee = "{name}" '
                f'ORDER BY updated DESC'
            )
        # ── Detect status intent ───────────────────────────────────────────────
        elif any(w in query_normalized for w in ["in progress", "blocked", "done", "to do", "open"]):
            status_map = {
                "in progress": "In Progress", "blocked": "Blocked",
                "done": "Done", "to do": "To Do", "open": "To Do",
            }
            matched_status = next(
                (v for k, v in status_map.items() if k in query_normalized), None
            )
            if matched_status:
                jql = (
                    f'project = "{project_key}" AND status = "{matched_status}" '
                    f'ORDER BY updated DESC'
                )
            else:
                jql = self._text_jql(project_key, keywords)
        else:
            jql = self._text_jql(project_key, keywords)

        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                r = await client.post(
                    f"{self._base_url}/rest/api/3/search/jql",
                    json={
                        "jql":        jql,
                        "maxResults": 10,
                        "fields":     ["summary", "status", "priority", "assignee", "labels", "description", "issuelinks", "customfield_10020", "created", "updated"],
                    },
                )
                r.raise_for_status()
            issues = r.json().get("issues", [])
            logger.info("JiraConnector.search_tickets: '%s' → %d issues", query[:50], len(issues))
            return [_normalize_issue(i) for i in issues]
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (400, 404, 410):
                logger.warning(
                    "JiraConnector.search_tickets: project '%s' returned HTTP %d — "
                    "JIRA_PROJECT_KEY in .env may be wrong. "
                    "Check valid keys at: %s/rest/api/3/project — falling back to mock data.",
                    project_key, status, self._base_url,
                )
                return await _mock_fallback().search_tickets(query, project)
            logger.exception("JiraConnector.search_tickets failed — HTTP %d", status)
            return []
        except Exception:
            logger.exception("JiraConnector.search_tickets failed for query='%s'", query[:50])
            return []

    def _extract_keywords(self, query: str) -> list[str]:
        """
        Strip question words, generic verbs, and filler from a natural-language query.
        Returns a list of domain-specific keywords for JQL search.

        "What caused the CORS error?"            → ["cors"]
        "Tell me about the payment gateway issue" → ["payment", "gateway"]
        "How was the DB pool issue resolved?"     → ["pool"]
        "What is blocking the dashboard feature?" → ["blocking", "dashboard"]
        """
        import re as _re
        stop_words = {
            # question openers
            "what", "which", "who", "when", "why", "how", "where", "whose",
            # auxiliary verbs
            "is", "are", "was", "were", "can", "will", "should", "would",
            "could", "did", "do", "does", "has", "have", "had",
            # filler / generic action words
            "show", "me", "list", "tell", "give", "find", "get", "check",
            "look", "see", "describe", "explain", "summarize",
            # articles / prepositions
            "the", "a", "an", "and", "or", "of", "on", "in", "for",
            "about", "with", "from", "to", "at", "by", "its", "our",
            # generic verbs that describe the question, not the subject
            "caused", "cause", "resolved", "resolve", "fixed", "fix",
            "happened", "occur", "occurred", "affect", "affecting", "affected",
            "related", "relate", "handle", "handling", "need", "needed",
            "implement", "create", "add", "build", "develop", "write", "make",
            # ticket creation / type words — meaningless for JQL search
            "error", "bug", "issue", "problem", "ticket", "tickets", "story",
            "task", "defect", "feature", "change", "changes", "update", "fix", "solution",
            "thing", "stuff", "item", "case",
            # sprint / generic project terms
            "status", "current", "now", "today", "latest", "recent",
            "please", "right", "just", "also", "still",
        }
        words = _re.findall(r'\b\w+\b', query.lower())
        keywords = [w for w in words if w not in stop_words and len(w) >= 2]
        return keywords[:5]

    def _text_jql(self, project_key: str, keywords: list[str]) -> str:
        """
        Build JQL that searches each keyword independently with OR.

        "caused cors error" as a phrase → zero results (requires ALL words present).
        ["cors"] → summary ~ "cors" OR ... → finds the CORS ticket correctly.
        ["payment", "gateway"] → OR between both → broad recall, precise terms.
        """
        if not keywords:
            return f'project = "{project_key}" ORDER BY updated DESC'

        parts = [
            f'(summary ~ "{kw}" OR description ~ "{kw}" OR comment ~ "{kw}")'
            for kw in keywords
        ]
        clause = " OR ".join(parts)
        return f'project = "{project_key}" AND ({clause}) ORDER BY updated DESC'

    async def get_blocked_tickets(self, project: str = "") -> list[dict]:
        """Return all tickets currently in a BLOCKED or IMPEDIMENT status."""
        project_key = project.upper() if project else self._project
        jql = (
            f'project = "{project_key}" AND '
            f'(status = "Blocked" OR labels = "blocked" OR priority = "Blocker") '
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
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (400, 404, 410):
                logger.warning(
                    "JiraConnector.get_blocked_tickets: project '%s' returned HTTP %d — falling back to mock data.",
                    project_key, status,
                )
                return await _mock_fallback().get_blocked_tickets(project)
            logger.exception("JiraConnector.get_blocked_tickets failed — HTTP %d", status)
            return []
        except Exception:
            logger.exception("JiraConnector.get_blocked_tickets failed")
            return []

    async def get_sprint_board(self, project: str = "") -> dict:
        """Return current sprint summary statistics via JQL aggregation."""
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

            # Aggregate counts from the returned issues
            done       = sum(1 for i in issues if "done" in (i["fields"].get("status", {}).get("name", "")).lower())
            in_prog    = sum(1 for i in issues if "progress" in (i["fields"].get("status", {}).get("name", "")).lower())
            blocked    = sum(1 for i in issues if "block" in " ".join(i["fields"].get("labels", [])).lower())
            total      = len(issues)
            pct        = round((done / total) * 100) if total else 0
            sprint_name = _extract_sprint_name(issues[0]["fields"]) or "Current Sprint"
            risk        = "HIGH" if blocked >= 2 or pct < 25 else "MEDIUM" if pct < 60 else "LOW"

            logger.info("JiraConnector.get_sprint_board: sprint='%s' total=%d done=%d", sprint_name, total, done)
            return {
                "sprint":          sprint_name,
                "project":         project_key,
                "total_tickets":   total,
                "done":            done,
                "in_progress":     in_prog,
                "blocked":         blocked,
                "not_started":     total - done - in_prog - blocked,
                "completion_pct":  pct,
                "risk_level":      risk,
            }
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (400, 404, 410):
                logger.warning(
                    "JiraConnector.get_sprint_board: project '%s' returned HTTP %d — falling back to mock data.",
                    project_key, status,
                )
                return await _mock_fallback().get_sprint_board(project)
            logger.exception("JiraConnector.get_sprint_board failed — HTTP %d", status)
            return {"sprint": "unknown", "project": project_key, "total_tickets": 0, "risk_level": "UNKNOWN"}
        except Exception:
            logger.exception("JiraConnector.get_sprint_board failed")
            return {"sprint": "unknown", "project": project_key, "total_tickets": 0, "risk_level": "UNKNOWN"}

    async def get_project_members(self, project: str = "") -> list[dict]:
        """
        Return all active users who can be assigned to tickets in this project.

        Uses GET /rest/api/3/user/assignable/search?project={key}
        Returns list of {name, display_name, email, active} dicts.
        Falls back to mock on any API failure.
        """
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
                for u in users
                if u.get("active", True)
            ]
            logger.info("JiraConnector.get_project_members: %d members for project '%s'", len(members), project_key)
            return members
        except Exception:
            logger.warning("JiraConnector.get_project_members: failed for '%s' — falling back to mock", project_key)
            return await _mock_fallback().get_project_members(project)

    async def get_active_sprint_id(self, project: str = "") -> int | None:
        """
        Return the numeric ID of the current active sprint for the project.
        Used by create_ticket to auto-assign new tickets to the active sprint.
        Returns None if no active sprint found or on any error.
        """
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
        """
        Create a real Jira ticket. Called by the HITL approve endpoint.
        - assignee_account_id: Jira Cloud account UUID (required for assignment)
        - sprint_id: numeric sprint ID from get_active_sprint_id() (optional)
        """
        # Map P-notation and generic names → Jira Cloud priority names
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
                "type":    "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": description}]}],
            },
            "issuetype": {"name": issue_type},
            "priority":  {"name": jira_priority},
            "labels":    labels or [],
        }
        # Jira Cloud requires accountId — display name or username will not work
        if assignee_account_id and assignee_account_id != "unassigned":
            fields["assignee"] = {"accountId": assignee_account_id}
        # Assign to active sprint so the ticket appears on the board, not just the backlog
        if sprint_id:
            fields["customfield_10020"] = sprint_id

        payload: dict[str, Any] = {"fields": fields}
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                r = await client.post(f"{self._base_url}/rest/api/3/issue", json=payload)
                r.raise_for_status()
            data = r.json()
            ticket_id = data.get("key", "")
            logger.info("JiraConnector.create_ticket: created '%s' → %s (sprint=%s)", title[:60], ticket_id, sprint_id)
            return {"id": ticket_id, "url": f"{self._base_url}/browse/{ticket_id}"}
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (400, 404, 410):
                logger.warning(
                    "JiraConnector.create_ticket: HTTP %d for project '%s' — "
                    "verify JIRA_PROJECT_KEY in .env. "
                    "List valid keys: %s/rest/api/3/project",
                    status, self._project, self._base_url,
                )
            else:
                logger.exception("JiraConnector.create_ticket failed — HTTP %d", status)
            return {}
        except Exception:
            logger.exception("JiraConnector.create_ticket failed for title='%s'", title[:60])
            return {}

    async def update_ticket(self, ticket_id: str, description: str = "", summary: str = "", labels: list[str] | None = None) -> dict:
        """Update fields on an existing Jira ticket. PUT /rest/api/3/issue/{id}."""
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
            logger.warning("JiraConnector.update_ticket: HTTP %d for %s — %s", r.status_code, ticket_id, r.text[:200])
            return {"success": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
        except Exception:
            logger.exception("JiraConnector.update_ticket: failed for %s", ticket_id)
            return {"success": False, "error": "request failed"}

    async def assign_ticket(self, ticket_id: str, account_id: str) -> dict:
        """Assign existing ticket to a user. PUT /rest/api/3/issue/{id}/assignee → 204."""
        url = f"{self._base_url}/rest/api/3/issue/{ticket_id}/assignee"
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=_TIMEOUT) as client:
                r = await client.put(url, json={"accountId": account_id})
            if r.status_code == 204:
                logger.info("JiraConnector.assign_ticket: %s → accountId=%s", ticket_id, account_id)
                return {"success": True, "ticket_id": ticket_id, "account_id": account_id}
            logger.warning("JiraConnector.assign_ticket: HTTP %d for %s", r.status_code, ticket_id)
            return {"success": False, "error": f"HTTP {r.status_code}"}
        except Exception:
            logger.exception("JiraConnector.assign_ticket: failed for %s", ticket_id)
            return {"success": False, "error": "request failed"}

    async def add_comment(self, ticket_id: str, body: str) -> dict:
        """Add a comment to a Jira issue. POST /rest/api/3/issue/{key}/comment → 201."""
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


# Self-registration — tells MCPRegistry which classes handle "jira" connectors.
# Import this file (via backend/mcp/connectors/__init__.py) to activate.
from backend.mcp.registry import MCPRegistry  # noqa: E402
from backend.mcp.connectors.mock_jira import MockJiraConnector  # noqa: E402
MCPRegistry.register("jira", JiraConnector, MockJiraConnector)
