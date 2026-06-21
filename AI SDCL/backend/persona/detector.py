"""
backend/persona/detector.py

Maps authenticated user roles to prompt keys used for persona adaptation.

Why a separate class instead of a dict lookup inline?
  If the mapping gets complex (role + seniority + preference), this class
  can grow to handle it without changing callers. For now: simple lookup.
"""

# Maps user_role (from auth token) → persona key (suffix of persona_* in prompts.yaml)
# e.g. "stakeholder" → looks up "persona_stakeholder" in prompts.yaml
_ROLE_TO_PERSONA: dict[str, str] = {
    "developer":       "developer",
    "tech_lead":       "technical_leader",
    "manager":         "manager",
    "stakeholder":     "stakeholder",
    "admin":           "developer",   # admins see the same view as developers
}


class PersonaDetector:
    """Determines which persona style to apply based on the user's role."""

    def detect(self, user_role: str) -> str:
        """
        Return the persona key for the given role.

        The returned key is appended to "persona_" to form the prompts.yaml key:
            detect("manager") → "manager" → prompts.yaml["persona_manager"]

        Falls back to "developer" for unknown roles so we never get an empty prompt.
        """
        return _ROLE_TO_PERSONA.get(user_role.lower(), "developer")
