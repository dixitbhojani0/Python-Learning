"""GET /api/v1/script — the demo script, loaded fresh from backend/scenes.json on every
request, so anyone can edit questions/answers in that file and just refresh the browser.
See backend/HOW_TO_ADD_QUESTIONS.md for the plain-language editing guide.

ponytail: file-based script — a real product replaces the data source with a
RAG + LLM pipeline; the response shape (and the frontend) stay the same.
"""
import json

from fastapi import APIRouter, HTTPException

from ..config import BACKEND_DIR

router = APIRouter()

SCENES_FILE = BACKEND_DIR / "scenes.json"


@router.get("/script")
def get_script() -> dict:
    try:
        scenes = json.loads(SCENES_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HTTPException(503, "scenes.json is missing from the backend folder")
    except json.JSONDecodeError as e:
        raise HTTPException(503, f"scenes.json has a mistake on line {e.lineno}: {e.msg}")

    return {"scenes": [s for s in scenes if s.get("enabled", True)]}
