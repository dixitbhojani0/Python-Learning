"""
MCP-Style Tool Calling Chatbot — using Groq (free)
===================================================
Features:
  - Retry with exponential backoff on 429 (rate limit)
  - Graceful handling of 400, 401, 500+ errors
  - Timeout on every API call
  - Tool execution error isolation (one bad tool won't crash the loop)
  - Input validation before sending to API
  - Safe JSON parsing with fallback
  - Startup check for API key + connectivity

Run:
  python mcp_groq_demo.py
"""

import os
import sys
import json
import time
import requests
from dotenv import load_dotenv

# Ensure UTF-8 encoding for standard streams on Windows
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass
if hasattr(sys.stdin, 'reconfigure'):
    try:
        sys.stdin.reconfigure(encoding='utf-8')
    except Exception:
        pass

load_dotenv()

# ════════════════════════════════════════════════════════════════
# CONFIG
# ════════════════════════════════════════════════════════════════

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MODEL        = "llama-3.1-8b-instant"
API_URL      = "https://api.groq.com/openai/v1/chat/completions"
HEADERS      = {
    "Authorization": f"Bearer {GROQ_API_KEY}",
    "Content-Type": "application/json",
}

# Resilience settings
MAX_RETRIES      = 3       # retry up to 3 times on 429
BACKOFF_BASE     = 2       # seconds — doubles each retry: 2s, 4s, 8s
REQUEST_TIMEOUT  = 30      # seconds before giving up on a hung request
MIN_INPUT_LENGTH = 3       # reject inputs shorter than this
MAX_INPUT_LENGTH = 500     # reject inputs longer than this


# ════════════════════════════════════════════════════════════════
# TOOLS SCHEMA
# ════════════════════════════════════════════════════════════════

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in the user's storage. Returns file names, types, sizes and last modified dates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max number of files to return (default 7, max 7)",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search for files by name or keyword. Use only when looking for a file by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search keyword to look for in file names",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the CONTENTS of a specific file. Use when user asks what a file says, contains, or wants a summary.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "The exact file name to read (e.g. Q1_Report.pdf)",
                    }
                },
                "required": ["filename"],
            },
        },
    },
]


# ════════════════════════════════════════════════════════════════
# TOOL IMPLEMENTATIONS (simulated MCP server functions)
# ════════════════════════════════════════════════════════════════

def list_files(limit: int = 7) -> dict:
    # Clamp limit to valid range
    limit = max(1, min(int(limit), 7))
    all_files = [
        {"name": "Q1_Report.pdf",           "type": "PDF",      "modified": "2025-05-20", "size": "1.2 MB"},
        {"name": "Project_Plan.docx",        "type": "Document", "modified": "2025-05-18", "size": "340 KB"},
        {"name": "meeting_notes.txt",        "type": "Text",     "modified": "2025-05-15", "size": "12 KB"},
        {"name": "budget_2025.xlsx",         "type": "Sheet",    "modified": "2025-05-10", "size": "890 KB"},
        {"name": "architecture_diagram.png", "type": "Image",    "modified": "2025-05-08", "size": "2.1 MB"},
        {"name": "sales_data.csv",           "type": "CSV",      "modified": "2025-04-30", "size": "450 KB"},
        {"name": "readme.md",                "type": "Markdown", "modified": "2025-04-25", "size": "8 KB"},
    ]
    return {"files": all_files[:limit], "total": len(all_files)}


def search_files(query: str) -> dict:
    query = str(query) if query is not None else ""
    if not query or not query.strip():
        return {"error": "Search query cannot be empty", "results": [], "count": 0}
    all_files = list_files(limit=7)["files"]
    results = [f for f in all_files if query.lower() in f["name"].lower()]
    return {"query": query, "results": results, "count": len(results)}


def read_file(filename: str) -> dict:
    filename = str(filename) if filename is not None else ""
    if not filename or not filename.strip():
        return {"error": "Filename cannot be empty"}
    contents = {
        "Q1_Report.pdf": (
            "Q1 2025 Performance Report\n"
            "Revenue: ₹42L (↑18% vs Q4)\n"
            "Top product: AI Integration Module\n"
            "Team headcount: 24 → 31\n"
            "Key milestone: Launched 3 client pilots in March."
        ),
        "Project_Plan.docx": (
            "Project: AI SDLC Assistant\n"
            "Timeline: May–August 2025\n"
            "Phases: Requirements → Design → Build → Test → Deploy\n"
            "Owner: Niketan Jain\n"
            "Status: Phase 2 in progress."
        ),
        "meeting_notes.txt": (
            "Meeting: Sprint Planning — May 15\n"
            "Attendees: Niketan, Parth, Parthil\n"
            "Topics: AI SDLC Assistant scope, RAG integration timeline\n"
            "Action items: Finalize chunking strategy by May 22."
        ),
        "budget_2025.xlsx": (
            "Budget 2025 Summary\n"
            "Total allocated: ₹18L\n"
            "Spent to date: ₹7.2L (40%)\n"
            "Largest category: Cloud infra (₹4L)\n"
            "Next review: June 1."
        ),
        "sales_data.csv": (
            "Month, Revenue, Deals\n"
            "Jan, ₹12L, 8\n"
            "Feb, ₹14L, 11\n"
            "Mar, ₹16L, 13\n"
            "Apr, ₹18L, 15"
        ),
        "readme.md": (
            "# Python Learning — AI/GenAI Track\n"
            "Trainer: Niketan Jain\n"
            "Phases: Python basics → LLM APIs → Prompt Eng → RAG → Agents\n"
            "Current phase: LLM API Integration + MCP demo"
        ),
    }
    content = contents.get(filename)
    if content is None:
        # Try case-insensitive match
        for key in contents:
            if key.lower() == filename.lower():
                content = contents[key]
                break
    if content is None:
        available = ", ".join(contents.keys())
        return {
            "error": f"File '{filename}' not found.",
            "available_files": available,
        }
    return {"filename": filename, "content": content}


# ════════════════════════════════════════════════════════════════
# TOOL ROUTER — routes LLM tool calls to the right function
# ════════════════════════════════════════════════════════════════

def execute_tool(tool_name: str, tool_args: dict) -> str:
    """
    Executes a tool safely.
    Any exception inside a tool is caught and returned as an error dict
    so the LLM can handle it gracefully instead of crashing.
    """
    print(f"  ⚙️  [{tool_name}] args={tool_args}")
    try:
        if tool_name == "list_files":
            result = list_files(**tool_args)
        elif tool_name == "search_files":
            result = search_files(**tool_args)
        elif tool_name == "read_file":
            result = read_file(**tool_args)
        else:
            result = {"error": f"Unknown tool '{tool_name}'. Available: list_files, search_files, read_file"}
    except TypeError as e:
        # Wrong arguments passed by LLM
        result = {"error": f"Tool '{tool_name}' received invalid arguments: {e}"}
    except Exception as e:
        # Any unexpected error inside the tool
        result = {"error": f"Tool '{tool_name}' failed: {e}"}

    return json.dumps(result, ensure_ascii=False)


# ════════════════════════════════════════════════════════════════
# API CALL WITH RETRY + BACKOFF
# ════════════════════════════════════════════════════════════════

def call_api(payload: dict) -> dict:
    """
    POST to Groq API with:
      - Request timeout (30s)
      - Retry on 429 with exponential backoff (2s, 4s, 8s)
      - Clear error messages for 401, 400, 500+
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.post(
                API_URL,
                headers=HEADERS,
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )

            # ── Success ────────────────────────────────────────
            if response.status_code == 200:
                return response.json()

            # ── Rate limit → wait and retry ────────────────────
            if response.status_code == 429:
                wait = BACKOFF_BASE ** attempt
                print(f"\n  ⏳ Rate limit hit (attempt {attempt}/{MAX_RETRIES}). Retrying in {wait}s...")
                time.sleep(wait)
                continue

            # ── Auth error → no point retrying ─────────────────
            if response.status_code == 401:
                raise RuntimeError("❌ Invalid API key. Check GROQ_API_KEY in your .env file.")

            # ── Bad request → no point retrying ────────────────
            if response.status_code == 400:
                err = response.json().get("error", {}).get("message", response.text)
                raise ValueError(f"❌ Bad request: {err}")

            # ── Server error → retry ────────────────────────────
            if response.status_code >= 500:
                wait = BACKOFF_BASE ** attempt
                print(f"\n  ⚠️  Server error {response.status_code} (attempt {attempt}/{MAX_RETRIES}). Retrying in {wait}s...")
                time.sleep(wait)
                continue

            # ── Other unexpected status ─────────────────────────
            response.raise_for_status()

        except requests.exceptions.ConnectionError:
            raise RuntimeError("❌ No internet connection. Check your network and try again.")

        except requests.exceptions.Timeout:
            if attempt < MAX_RETRIES:
                wait = BACKOFF_BASE ** attempt
                print(f"\n  ⏳ Request timed out (attempt {attempt}/{MAX_RETRIES}). Retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise RuntimeError(f"❌ Request timed out after {MAX_RETRIES} attempts.")

    raise RuntimeError(f"❌ All {MAX_RETRIES} retry attempts failed.")


# ════════════════════════════════════════════════════════════════
# INPUT VALIDATION
# ════════════════════════════════════════════════════════════════

def validate_input(text: str) -> tuple[bool, str]:
    """
    Returns (is_valid, error_message).
    Catches empty, too short, too long, or non-printable inputs.
    """
    if not text or not text.strip():
        return False, "Please type a question."
    if len(text.strip()) < MIN_INPUT_LENGTH:
        return False, f"Query too short. Please be more specific (min {MIN_INPUT_LENGTH} chars)."
    if len(text) > MAX_INPUT_LENGTH:
        return False, f"Query too long (max {MAX_INPUT_LENGTH} chars). Please shorten it."
    return True, ""


# ════════════════════════════════════════════════════════════════
# SAFE JSON PARSE
# ════════════════════════════════════════════════════════════════

def safe_json_parse(text) -> dict:
    """Parse JSON safely — returns error dict instead of raising."""
    if isinstance(text, dict):
        return text
    if text is None:
        return {}
    if isinstance(text, str) and not text.strip():
        return {}
    try:
        parsed = json.loads(text)
        if parsed is None:
            return {}
        if not isinstance(parsed, dict):
            return {"error": f"JSON is not a dictionary/object: {type(parsed).__name__}", "raw": str(text)[:200]}
        return parsed
    except json.JSONDecodeError as e:
        return {"error": f"JSON parse failed: {e}", "raw": str(text)[:200]}
    except Exception as e:
        return {"error": f"Unexpected JSON parse error: {e}", "raw": str(text)[:200]}


# ════════════════════════════════════════════════════════════════
# SYSTEM PROMPT
# ════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = (
    "You are a helpful file assistant with access to exactly 3 tools:\n"
    "- list_files  : when user wants to see/show/list/return all files\n"
    "- search_files: when user searches for a file BY NAME keyword\n"
    "- read_file   : when user asks about file CONTENTS (what does X say, summarize X, revenue in X, action items)\n\n"
    "CRITICAL RULES:\n"
    "- You do NOT have access to the internet, external search engines, or any tools outside of the 3 listed above.\n"
    "- If the user's query is about general knowledge or a topic not covered by the files in your storage (e.g., explaining concepts like RAG, quantum computing, cloud computing, etc.), reply EXACTLY with: 'I can only help with questions regarding the files in your storage. Please ask a question related to your files.' Do not attempt to explain the topic or suggest searching the web.\n"
    "- DO NOT attempt to call 'brave_search', 'web_search', or any other built-in/external tools.\n"
    "- NEVER invent tool names or output raw XML/HTML-like function tags in your text response (e.g., `<function=...>` or `</function>`). All responses that do not use the official tool calling API must be pure plain-text without tags.\n"
    "- For content questions (revenue, action items, details), ALWAYS use read_file, never search_files\n"
    "- list_files should be called with no arguments unless user specifies a number\n"
    "- Always show actual file names and details in your answer, not just a count\n"
    "- If query is too vague, ask the user to clarify"
)


# ════════════════════════════════════════════════════════════════
# THINK → ACT → OBSERVE
# ════════════════════════════════════════════════════════════════

def ask(user_question: str) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": user_question},
    ]

    # ── THINK ──────────────────────────────────────────────────
    print("  🧠 Think ...", end=" ", flush=True)
    data = call_api({
        "model": MODEL,
        "messages": messages,
        "tools": TOOLS,
        "max_tokens": 1024,
    })

    assistant_msg = data["choices"][0]["message"]
    finish_reason = data["choices"][0]["finish_reason"]

    # ── ACT ────────────────────────────────────────────────────
    if finish_reason == "tool_calls" and assistant_msg.get("tool_calls"):
        tool_call = assistant_msg["tool_calls"][0]
        tool_name = tool_call["function"]["name"]
        raw_args  = tool_call["function"].get("arguments", "{}")
        tool_args = safe_json_parse(raw_args)

        # If arg parsing failed, send error back to LLM
        if "error" in tool_args:
            print(f"arg parse error")
            return f"Sorry, I had trouble parsing the tool arguments: {tool_args['error']}"

        print(f"chose tool → '{tool_name}'")
        tool_result = execute_tool(tool_name, tool_args)

        # ── OBSERVE ────────────────────────────────────────────
        print("  👁️  Observe ...")
        messages.append(assistant_msg)
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call["id"],
            "content": tool_result,
        })

        final_data = call_api({
            "model": MODEL,
            "messages": messages,
            "max_tokens": 1024,
        })
        answer = final_data["choices"][0]["message"].get("content", "")

    else:
        print("no tool needed")
        answer = assistant_msg.get("content", "")

    return answer or "Sorry, I got an empty response. Please try again."


# ════════════════════════════════════════════════════════════════
# STARTUP CHECK
# ════════════════════════════════════════════════════════════════

def startup_check() -> bool:
    """Validates API key and connectivity before starting the chat loop."""
    print("  Checking API key ...", end=" ", flush=True)
    if not GROQ_API_KEY:
        print("MISSING")
        print("❌ GROQ_API_KEY not found in .env file.")
        return False
    if not GROQ_API_KEY.startswith("gsk_"):
        print("INVALID FORMAT")
        print("❌ GROQ_API_KEY looks wrong — should start with 'gsk_'.")
        return False
    print("OK")

    print("  Checking connectivity ...", end=" ", flush=True)
    try:
        r = requests.get("https://api.groq.com", timeout=5)
        print("OK")
    except requests.exceptions.ConnectionError:
        print("FAILED")
        print("❌ Cannot reach api.groq.com — check your internet connection.")
        return False
    except requests.exceptions.Timeout:
        print("TIMEOUT")
        print("⚠️  Connection to Groq timed out. Proceeding anyway...")

    return True


# ════════════════════════════════════════════════════════════════
# MAIN CHAT LOOP
# ════════════════════════════════════════════════════════════════

def main():
    print("╔══════════════════════════════════════════════╗")
    print("║   MCP-Style File Assistant  (Groq / Free)   ║")
    print("╚══════════════════════════════════════════════╝\n")

    if not startup_check():
        exit(1)

    print("\n╔══════════════════════════════════════════════╗")
    print("║  Available files in storage:                 ║")
    print("║   Q1_Report.pdf       Project_Plan.docx      ║")
    print("║   meeting_notes.txt   budget_2025.xlsx       ║")
    print("║   sales_data.csv      readme.md              ║")
    print("╠══════════════════════════════════════════════╣")
    print("║  Try asking:                                  ║")
    print("║   > show me all my files                     ║")
    print("║   > search for budget                        ║")
    print("║   > read the meeting notes                   ║")
    print("║   > what does Q1 report say about revenue?   ║")
    print("║   > type 'exit' to quit                      ║")
    print("╚══════════════════════════════════════════════╝\n")

    while True:
        # ── Get user input ──────────────────────────────────────
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            break

        # ── Exit commands ───────────────────────────────────────
        if user_input.lower() in ("exit", "quit", "bye", "q"):
            print("Goodbye!")
            break

        # ── Validate input ──────────────────────────────────────
        is_valid, err_msg = validate_input(user_input)
        if not is_valid:
            print(f"  ⚠️  {err_msg}\n")
            continue

        # ── Call the LLM with full error handling ───────────────
        try:
            answer = ask(user_input)
            print(f"\n🤖 Assistant:\n{answer}\n")
            print("─" * 50)

        except ValueError as e:
            # 400 bad request
            print(f"\n  ⚠️  {e}")
            print("  Try rephrasing — e.g. 'show all files', 'read meeting_notes.txt'\n")

        except RuntimeError as e:
            # Connectivity, auth, timeout, all retries failed
            print(f"\n  {e}\n")

        except Exception as e:
            # Catch-all — should not reach here normally
            print(f"\n  ❌ Unexpected error: {e}")
            print("  Please try again.\n")


if __name__ == "__main__":
    main()