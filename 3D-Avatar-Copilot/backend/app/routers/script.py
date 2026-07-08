"""GET /api/v1/script — the demo script as structured JSON.

ponytail: hardcoded scenes — a real product replaces this router's data source
with a RAG + LLM pipeline; the response shape (and the frontend) stay the same.
Cards are structured data, never HTML, so the frontend renders them safely.
"""
from fastapi import APIRouter

from ..config import settings

router = APIRouter()

SCENES = [
    {
        "id": 1,
        "label": "Greeting",
        "advisorLine": "Help me prepare for my meeting with John Davies.",
        "triggers": ["john", "davies", "prepare", "meeting"],
        "spokenText": (
            "Good morning. I found John Davies' annual review meeting scheduled for tomorrow. "
            "I can help you review client context, identify key planning areas, and prepare follow-up notes."
        ),
        "card": {
            "title": "📅 Meeting Found",
            "badge": "Tomorrow",
            "keyValues": [
                {"label": "Client", "value": "John Davies"},
                {"label": "Type", "value": "Annual financial planning review"},
                {"label": "Prepared by", "value": "FinAdvisor AI Copilot"},
            ],
        },
    },
    {
        "id": 2,
        "label": "Client summary",
        "advisorLine": "Give me a quick overview.",
        "triggers": ["overview", "summary", "quick", "snapshot"],
        "spokenText": (
            "John Davies is 58 and planning to retire at 62. His key review areas are pension contributions, "
            "retirement income readiness, ISA utilisation, investment risk alignment, and inheritance planning."
        ),
        "card": {
            "title": "👤 Client Snapshot",
            "keyValues": [
                {"label": "Age", "value": "58"},
                {"label": "Retirement target", "value": "62"},
                {"label": "Review type", "value": "Annual financial planning review"},
            ],
            "bullets": [
                "Pension contributions",
                "ISA utilisation",
                "Retirement income readiness",
                "Inheritance (IHT) planning",
            ],
        },
    },
    {
        "id": 3,
        "label": "Knowledge retrieval",
        "advisorLine": "What should I consider before discussing pension contributions and retirement income?",
        "triggers": ["pension", "contribution", "consider", "income", "discussing"],
        "searching": True,
        "spokenText": (
            "Based on approved planning guidance, consider current pension contribution levels, annual allowance "
            "position, retirement income target, tax position, and portfolio risk alignment. Also confirm whether "
            "there have been any changes in employment income, family circumstances, or retirement timeline."
        ),
        "card": {
            "title": "📚 Retrieved from Approved Knowledge Base",
            "badge": "Grounded",
            "chips": [
                "Retirement Planning Guidance",
                "Pension Contribution Checklist",
                "Annual Review Framework",
                "Suitability Review Notes",
            ],
        },
    },
    {
        "id": 4,
        "label": "Compliance",
        "advisorLine": "Are there any compliance points I should keep in mind?",
        "triggers": ["compliance", "keep in mind", "regulation", "points"],
        "spokenText": (
            "Yes. Please confirm updated client objectives, risk profile, capacity for loss, tax position, and "
            "retirement timeline. I can support meeting preparation — but the final recommendation must be "
            "reviewed and confirmed by you, the qualified advisor."
        ),
        "card": {
            "title": "⚖️ Compliance Checklist",
            "badge": "Advisor confirms",
            "badgeTone": "amber",
            "bullets": [
                "Updated client objectives",
                "Risk profile & capacity for loss",
                "Tax position",
                "Retirement timeline",
                "Final recommendation reviewed by the qualified advisor",
            ],
        },
    },
    {
        "id": 5,
        "label": "Follow-up note",
        "advisorLine": "Draft a short follow-up note I can refine after the meeting.",
        "triggers": ["draft", "note", "follow", "refine"],
        "spokenText": "Of course. I have prepared a draft follow-up note for your review.",
        "card": {
            "title": "✉️ Draft Follow-up Note",
            "badge": "Editable",
            "note": (
                "Hi John, thank you for meeting today. We reviewed your retirement timeline, pension position, "
                "investment portfolio, and key planning considerations. The next step is to validate your updated "
                "income assumptions and review the suitability of your current contribution and investment strategy. "
                "I will prepare the required documentation and follow up with next steps shortly."
            ),
        },
    },
    {
        "id": 6,
        "label": "Closing",
        "advisorLine": "Thanks, that's all for now.",
        "triggers": ["thank", "thanks", "done", "that's all", "finish"],
        "spokenText": "You're welcome. Good luck with the meeting tomorrow — I'll be here when you need me.",
        "closing": True,
    },
]


@router.get("/script")
def get_script() -> dict:
    return {"scenes": SCENES[: settings.active_scenes]}
