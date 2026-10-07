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

    def __init__(self, payload: dict, put_status: int = 204):
        self._payload = payload
        self._put_status = put_status
        self.last_json: dict | None = None
        self.last_url: str | None = None

    async def post(self, url, json):
        self.last_json = json
        return _FakeResponse(self._payload)

    async def put(self, url, json):
        self.last_json = json
        self.last_url = url
        return _FakeStatusResponse(self._put_status)


class _FakeStatusResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


def _connector_with_fake_http(payload: dict, put_status: int = 204) -> tuple[JiraConnector, _FakeHTTPClient]:
    connector = JiraConnector(name="jira", connector_config={})
    fake = _FakeHTTPClient(payload, put_status=put_status)
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


def test_deassign_ticket_sends_null_account_id():
    """
    E7: deassign must use Jira's documented null-unassign contract — {"accountId": null} —
    on the same /assignee endpoint assign_ticket uses, not a different/omitted field.
    """
    connector, fake = _connector_with_fake_http({})
    result = asyncio.run(connector.deassign_ticket("SDLC-5"))

    assert fake.last_json == {"accountId": None}
    assert fake.last_url.endswith("/issue/SDLC-5/assignee")
    assert result == {"success": True, "ticket_id": "SDLC-5"}


def test_deassign_ticket_reports_failure_on_non_204():
    connector, _ = _connector_with_fake_http({}, put_status=404)
    result = asyncio.run(connector.deassign_ticket("SDLC-5"))

    assert result["success"] is False
    assert "404" in result["error"]
