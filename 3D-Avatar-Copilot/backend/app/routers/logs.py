"""POST /api/v1/log — client-side error reporting (appends backend/demo.log)."""
from fastapi import APIRouter, Request

from ..config import BACKEND_DIR

router = APIRouter()


@router.post("/log")
async def log_client_message(request: Request) -> dict:
    msg = (await request.body()).decode(errors="replace")
    print("CLIENT:", msg)
    with open(BACKEND_DIR / "demo.log", "a", encoding="utf-8") as f:
        f.write(msg + "\n")
    return {"ok": True}
