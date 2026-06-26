"""
backend/persona/adapter.py

PersonaAdapter — rewrites an agent's technical answer in role-appropriate language.

Why two LLM calls instead of one?
  The CrossSourceAgent's first LLM call has to do two hard things simultaneously:
    1. Reason over 7+ RAG chunks of technical content
    2. Write the answer in the right tone for the user's role

  When those two jobs compete, the LLM tends to produce a hybrid — partially
  technical, partially plain — rather than fully committing to one style.

  The PersonaAdapter's second call has ONLY one job: rewrite in the right tone.
  It never sees the RAG chunks, only the finished answer. This produces cleaner
  role-specific language, especially for stakeholders (who need zero technical terms).

  Tradeoff: +3-5 seconds latency per request. Acceptable for this demo.
  Phase 9 optimization: cache persona-rewritten responses with a short TTL.
"""
import logging

logger = logging.getLogger(__name__)

# System prompt prefix for the rewriting step.
# The persona instruction from prompts.yaml is appended after this.
_REWRITE_PREAMBLE = (
    "You adapt the writing style of an answer for a specific reader. "
    "Rewrite the given answer to match the style below.\n\n"
    "STRICT RULES:\n"
    "- Keep ALL facts, numbers, ticket IDs, names, and decisions exactly as given.\n"
    "- Do NOT add information that is not in the original answer. Never invent "
    "completion percentages, risk levels (AT RISK/BLOCKED), blocker owners, ETAs, "
    "sprint goals, or deadlines that the original does not state.\n"
    "- If the original says information is unavailable or out of scope, keep that "
    "meaning and stay brief (1-3 sentences). Do NOT turn it into a status report.\n"
    "- Only change vocabulary, tone, and emphasis — not content.\n\n"
    "Style to apply:\n"
)


class PersonaAdapter:
    """
    Rewrites a final LLM response for a specific user role.

    Depends on:
      llm           — any BaseLLMProvider (GroqProvider in production)
      config_loader — ConfigLoader singleton (reads prompts.yaml)

    Usage in graph.py adapt_persona node:
        adapter = PersonaAdapter(llm=_get_provider(), config_loader=config)
        adapted = await adapter.adapt(original_response, persona_key="stakeholder")
    """

    def __init__(self, llm, config_loader) -> None:
        self.llm    = llm
        self.config = config_loader

    async def adapt(self, response: str, persona_key: str) -> str:
        """
        Rewrite `response` in the style of `persona_key`.

        Args:
            response:    the original answer from an agent (technical, multi-source)
            persona_key: one of "developer", "manager", "technical_leader", "stakeholder"

        Returns:
            The rewritten response. Falls back to original `response` on any error
            so a rewriting failure never breaks the user experience.
        """
        if not response.strip():
            return response

        # Load style instruction from prompts.yaml (same key used in generation step)
        persona_instruction = (
            self.config.get_prompt(f"persona_{persona_key}")
            or self.config.get_prompt("persona_developer")
        )

        system_prompt = _REWRITE_PREAMBLE + persona_instruction

        temperature = self.config.get_temperature("persona_rewriting")
        max_tokens  = (
            self.config.get_llm_config()
            .get("primary", {})
            .get("max_tokens", {})
            .get("response", 1024)
        )

        try:
            tokens: list[str] = []
            async for token in self.llm.generate(
                prompt=f"Answer to rewrite:\n\n{response}",
                system=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                tokens.append(token)

            adapted = "".join(tokens).strip()
            if not adapted:
                logger.warning("PersonaAdapter: LLM returned empty rewrite — keeping original")
                return response

            return adapted

        except Exception:
            # Never let rewriting failure propagate — the original answer is still correct
            logger.exception("PersonaAdapter: rewrite failed for persona='%s' — keeping original", persona_key)
            return response
