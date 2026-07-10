---
name: python-standards
description: Python 3 + FastAPI coding standards for backend/ — PEP 8 naming, FastAPI patterns, pydantic, security, Ruff tooling. Use when writing, reviewing, or refactoring any code under backend/.
---

# Python + FastAPI Standards (backend/)

## Naming conventions (PEP 8)

| Thing | Convention | Example in this repo |
|---|---|---|
| Modules / packages | snake_case, short | `session.py`, `routers/` |
| Functions / variables | snake_case | `get_script`, `persona` |
| Classes / pydantic models | PascalCase | `Settings`, `ScriptUpdate` |
| Constants | UPPER_SNAKE_CASE | `SCENES_FILE`, `ANAM_TOKEN_URL` |
| Private helpers | leading underscore | `_load_scenes`, `_anam_session` |
| Env vars | UPPER_SNAKE_CASE | `ANAM_API_KEY` |

## FastAPI patterns (this project's layout is the pattern)

- One router per resource in `app/routers/`; mounted in `main.py` with the `/api/v1` prefix —
  version every public route.
- Request bodies are **pydantic models** (`ScriptUpdate`), never raw dict parsing; response
  shapes stay stable — they are the frontend contract.
- Errors: raise `HTTPException(status, "human-readable reason")`; 4xx for caller mistakes,
  503 for missing config/files, never leak stack traces or secrets in `detail`.
- Outbound HTTP: `httpx.AsyncClient` with an explicit `timeout` — no requests without timeouts.
- Async endpoints for I/O-bound work; never mix blocking I/O into `async def` routes
  (small local file reads are the accepted exception here).
- Full type hints on every function signature (params + return).

## Configuration & secrets

- All settings via `pydantic-settings` in `app/config.py`, values from `backend/.env`
  (gitignored; `.env.example` documents keys with placeholders).
- **Never** a secret in code, in logs, in an HTTP response, or committed to git.
- New settings: add the typed field to `Settings`, the placeholder to `.env.example`.

## Security (OWASP API-aligned)

- Validate every write path (see `put_script`: type + emptiness checks before touching disk);
  a malformed request must never corrupt stored data.
- CORS: explicit origin allowlist only (`main.py` — localhost:5173); never `"*"` with credentials.
- File paths: always anchored to known roots (`BACKEND_DIR / "scenes.json"`); never build paths
  from user input.
- No `eval`/`exec`/`pickle` on external data; JSON only.
- Dependency hygiene: `pip install --upgrade` reviewed regularly; `pip-audit` before releases;
  pin minimums in `requirements.txt`.

## Tooling — Ruff (the 2026 standard: replaces black + flake8 + isort + bandit)

Enforced via `backend/pyproject.toml` (ruff installed; both commands must pass before commit):

```toml
[tool.ruff]
line-length = 100
target-version = "py314"

[tool.ruff.lint]
select = ["E", "W", "F", "I", "B", "C4", "UP", "S", "N"]  # incl. bandit security (S), naming (N)
```

Run: `ruff check .` (lint + security) and `ruff format .` (formatting). Both must pass before commit.

## Docstrings & comments

- Module docstring states what the module owns and any non-obvious contract (see `script.py`).
- Comments explain WHY (business rule, workaround), never restate the code.

## Review checklist

1. PEP 8 names; full type hints; pydantic for request bodies.
2. No secret/path/injection leak; write paths validated; timeouts on outbound calls.
3. Response shapes unchanged (or frontend updated in the same change); `ruff check` clean.
