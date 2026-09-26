"""
tests/test_jira_connector.py

Regression guard for REDESIGN_BUGS.md B1 (cause 2): a ticket marked Done in
Jira must not keep showing up as a blocker. get_blocked_tickets() relies on
the JQL query itself to exclude done tickets (Jira does the filtering
server-side) — so the test asserts the JQL sent to the search endpoint
carries that clause, not just that the connector "runs".
"""
import asyncio
import json

from connectors.jira_connector import JiraConnector


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeHTTPClient:
    is_closed = False

    def __init__(self, payload: dict):
        self._payload = payload
        self.last_json: dict | None = None

    async def post(self, url, json):
        self.last_json = json
        return _FakeResponse(self._payload)


def _connector_with_fake_http(payload: dict) -> tuple[JiraConnector, _FakeHTTPClient]:
    connector = JiraConnector(name="jira", connector_config={})
    fake = _FakeHTTPClient(payload)
    connector._http = fake
    return connector, fake


def test_get_blocked_tickets_jql_excludes_done_status():
    connector, fake = _connector_with_fake_http({"issues": []})
    asyncio.run(connector.get_blocked_tickets())

    jql = fake.last_json["jql"]
    assert 'statusCategory != "Done"' in jql, (
        "get_blocked_tickets JQL must exclude done tickets — regression of B1 "
        "(a ticket marked Done in Jira kept showing up as a blocker)"
    )


def test_get_blocked_tickets_normalizes_returned_issues():
    done_but_matched_label = {
        "issues": [{
            "key": "SDLC-1",
            "fields": {
                "summary": "Old blocker, now done",
                "status": {"name": "Done"},
                "priority": {"name": "High"},
                "assignee": None,
                "labels": ["blocked"],
                "issuelinks": [],
            },
        }]
    }
    connector, _ = _connector_with_fake_http(done_but_matched_label)
    result = asyncio.run(connector.get_blocked_tickets())

    # The JQL clause (asserted above) is what Jira uses to exclude this in
    # production; here we just confirm normalization doesn't silently drop
    # the status field a caller would use to double-check.
    assert result[0]["status"] == "DONE"
