"""
scripts/test_agents_e2e.py

End-to-end agent smoke test. Hits the SAME endpoint the Angular UI calls
(POST /api/chat) once per agent, with a query crafted to route to that agent,
and checks: HTTP 200, the graph routed to the expected agent, and the response
shape is right (a real answer, or a HITL proposal for write actions).

This exercises the full stack: classifier -> agent -> RAG + live MCP -> reflection
loop -> persona -> eval. Passing it means every agent works from the UI too,
because the UI is just a thin client over this endpoint.

Needs the full stack up:  docker compose up -d   (qdrant, redis, mcp-server, backend)
and the knowledge base ingested (so RAG-grounded agents have context).

Run:
    python scripts/test_agents_e2e.py
    BACKEND_URL=http://localhost:8000 python scripts/test_agents_e2e.py
"""
import os
import sys

import httpx

BASE = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
TOKEN = os.getenv("DEV_TOKEN", "dev_token_alice")   # developer role
PROJECT = os.getenv("PROJECT", "SDLC")

# (label, message, expected_intent, expect_hitl)
# Each message is phrased to hit that agent's trigger keywords / routing.
CASES = [
    ("cross_source",      "What is the status of the dashboard feature?",            "cross_source",      False),
    ("risk",              "What is the sprint risk right now?",                      "risk",              False),
    ("pr_review",         "Show me the open PRs that need code review",              "pr_review",         False),
    ("release_readiness", "Are we ready to release? Give me a go/no-go",             "release_readiness", False),
    # Novel issue (not a duplicate) so it exercises the real HITL creation path —
    # a duplicate would correctly return existing-ticket info with NO HITL prompt.
    # Refresh this title if a prior run actually created a matching ticket.
    ("ticket (write)",    "Create a ticket: add CSV export to the analytics dashboard", "ticket",        True),
    ("notify (write)",    "Send a slack message to the team about the sprint status", "notify",          True),
]


def run_case(client: httpx.Client, label, message, expected, expect_hitl):
    try:
        r = client.post(
            f"{BASE}/api/chat",
            headers={"x-token": TOKEN, "Content-Type": "application/json"},
            json={"message": message, "project": PROJECT},
            timeout=120.0,
        )
    except Exception as exc:
        return False, f"request failed: {exc}"

    if r.status_code != 200:
        return False, f"HTTP {r.status_code}: {r.text[:120]}"

    data = r.json()
    agent = data.get("agent", "")
    resp = (data.get("response") or "").strip()
    hitl = data.get("hitl_required", False)
    faith = data.get("faithfulness", 0.0)

    problems = []
    if agent != expected:
        problems.append(f"routed to '{agent}', expected '{expected}'")
    if not resp:
        problems.append("empty response")
    if expect_hitl and not hitl:
        problems.append("expected HITL proposal, got none")

    detail = f"agent='{agent}' hitl={hitl} faith={faith} resp='{resp[:60].replace(chr(10), ' ')}...'"
    return (not problems), (detail if not problems else f"{detail}  << {'; '.join(problems)}")


def main() -> int:
    print(f"Testing all agents via {BASE}/api/chat (token={TOKEN}, project={PROJECT})\n")
    results = []
    with httpx.Client() as client:
        # Fail fast with a clear message if the backend isn't up.
        try:
            client.get(f"{BASE}/health", timeout=5.0)
        except Exception:
            try:
                client.get(BASE, timeout=5.0)
            except Exception as exc:
                print(f"ERROR: backend not reachable at {BASE} — {exc}")
                print("Start the stack:  docker compose up -d")
                return 2

        for label, message, expected, expect_hitl in CASES:
            ok, detail = run_case(client, label, message, expected, expect_hitl)
            results.append(ok)
            line = f"  [{'PASS' if ok else 'FAIL'}] {label:<20} {detail}"
            # Agent responses can contain emoji; the Windows console (cp1252) can't
            # encode them — fold to ASCII so the test reports instead of crashing.
            print(line.encode("ascii", "replace").decode())

    passed, total = sum(results), len(results)
    print(f"\n{'='*60}\n{passed}/{total} agents working end-to-end -- "
          f"{'ALL AGENTS OK' if passed == total else 'SOME AGENTS FAILED'}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
