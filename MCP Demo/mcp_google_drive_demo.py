"""
MCP Demo — Google Drive via Anthropic API
==========================================
This script shows how MCP works:
  - The LLM decides which tool to call
  - MCP Host routes it to Google Drive MCP server
  - Result comes back and LLM uses it to answer

Setup:
  pip install anthropic python-dotenv

Your .env file needs:
  ANTHROPIC_API_KEY=sk-ant-...

Run:
  python mcp_google_drive_demo.py
"""

import os
import json
from dotenv import load_dotenv
import anthropic

load_dotenv()

# ── Config ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL = "claude-sonnet-4-20250514"

# Google Drive MCP server (standard Anthropic-hosted endpoint)
MCP_SERVERS = [
    {
        "type": "url",
        "url": "https://drivemcp.googleapis.com/mcp/v1",
        "name": "google-drive",
    }
]

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# ── Helper: pretty-print content blocks ─────────────────────────────────────
def print_response(label: str, response):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")

    for block in response.content:
        if block.type == "text":
            print(f"\n[LLM Answer]\n{block.text}")

        elif block.type == "tool_use":
            print(f"\n[MCP Tool Called] {block.name}")
            print(f"  Input: {json.dumps(block.input, indent=4)}")

        elif block.type == "tool_result":
            print(f"\n[MCP Tool Result]")
            if isinstance(block.content, list):
                for item in block.content:
                    if hasattr(item, "text"):
                        print(f"  {item.text[:500]}")  # trim long results
            else:
                print(f"  {str(block.content)[:500]}")


# ── Demo 1: List files ───────────────────────────────────────────────────────
def demo_list_files():
    print("\n🔵 DEMO 1: List files in Google Drive")

    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": "List the 5 most recent files in my Google Drive. Show file name, type, and last modified date.",
            }
        ],
        mcp_servers=MCP_SERVERS,
        betas=["mcp-client-2025-04-04"],
    )

    print_response("List Files", response)
    return response


# ── Demo 2: Search for a file ────────────────────────────────────────────────
def demo_search_file(search_term: str = "report"):
    print(f"\n🔵 DEMO 2: Search for '{search_term}' in Google Drive")

    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": f"Search my Google Drive for files containing '{search_term}' in the name. List what you find.",
            }
        ],
        mcp_servers=MCP_SERVERS,
        betas=["mcp-client-2025-04-04"],
    )

    print_response(f"Search: '{search_term}'", response)
    return response


# ── Demo 3: Read file contents ───────────────────────────────────────────────
def demo_read_file():
    print("\n🔵 DEMO 3: Read contents of a file from Google Drive")

    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=2048,
        messages=[
            {
                "role": "user",
                "content": (
                    "Find any text document or Google Doc in my Drive "
                    "and read its contents. Summarize what it contains in 3 bullet points."
                ),
            }
        ],
        mcp_servers=MCP_SERVERS,
        betas=["mcp-client-2025-04-04"],
    )

    print_response("Read File Contents", response)
    return response


# ── Demo 4: Multi-turn (conversation with Drive) ─────────────────────────────
def demo_multi_turn():
    print("\n🔵 DEMO 4: Multi-turn conversation with Drive context")

    conversation = [
        {
            "role": "user",
            "content": "How many files do I have in my Google Drive? Also tell me the breakdown by file type.",
        }
    ]

    # Turn 1
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=conversation,
        mcp_servers=MCP_SERVERS,
        betas=["mcp-client-2025-04-04"],
    )

    print_response("Turn 1 — File count by type", response)

    # Add assistant reply to history
    conversation.append({"role": "assistant", "content": response.content})

    # Turn 2 — follow-up
    conversation.append(
        {
            "role": "user",
            "content": "Which of those files was modified most recently?",
        }
    )

    response2 = client.beta.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=conversation,
        mcp_servers=MCP_SERVERS,
        betas=["mcp-client-2025-04-04"],
    )

    print_response("Turn 2 — Most recently modified", response2)


# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY not found in .env")
        exit(1)

    print("╔══════════════════════════════════════════════╗")
    print("║     MCP Demo — Google Drive Integration      ║")
    print("╚══════════════════════════════════════════════╝")
    print("\nHow MCP works here:")
    print("  1. You send a natural language question")
    print("  2. Claude decides which Drive tool to call (list/search/read)")
    print("  3. MCP Host calls Google Drive API on your behalf")
    print("  4. Result comes back → Claude gives you a clean answer\n")

    # Run all demos
    demo_list_files()
    demo_search_file("report")   # change to any keyword you like
    demo_read_file()
    demo_multi_turn()

    print("\n\n✅ All demos complete!")
    print("Try changing the search term or the questions above to explore further.")