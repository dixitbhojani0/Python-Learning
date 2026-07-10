"""GET/PUT /api/v1/script — the demo script, stored in backend/scenes.json.

Loaded fresh on every request, so edits (via the admin panel or the file itself)
appear on browser refresh. See backend/HOW_TO_ADD_QUESTIONS.md for the guide.

ponytail: file-based script — a real product replaces the data source with a
RAG + LLM pipeline; the response shape (and the frontend) stay the same.
"""

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import BACKEND_DIR

router = APIRouter()

SCENES_FILE = BACKEND_DIR / "scenes.json"


def _load_scenes() -> list:
    try:
        return json.loads(SCENES_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HTTPException(503, "scenes.json is missing from the backend folder") from None
    except json.JSONDecodeError as e:
        raise HTTPException(503, f"scenes.json has a mistake on line {e.lineno}: {e.msg}") from None


@router.get("/script")
def get_script(all: bool = False) -> dict:
    scenes = _load_scenes()
    if not all:  # the demo plays enabled scenes only; ?all=1 is for the admin panel
        scenes = [s for s in scenes if s.get("enabled", True)]
    return {"scenes": scenes}


class ScriptUpdate(BaseModel):
    scenes: list[dict]


@router.put("/script")
def put_script(update: ScriptUpdate) -> dict:
    for i, scene in enumerate(update.scenes):
        for field in ("question", "answer"):
            if not isinstance(scene.get(field), str) or not scene[field].strip():
                raise HTTPException(400, f"Scene {i + 1}: '{field}' must be a non-empty text")
        if not isinstance(scene.get("keywords", []), list):
            raise HTTPException(400, f"Scene {i + 1}: 'keywords' must be a list")
    payload = json.dumps(update.scenes, indent=2, ensure_ascii=False) + "\n"
    SCENES_FILE.write_text(payload, encoding="utf-8")
    return {"ok": True, "count": len(update.scenes)}
