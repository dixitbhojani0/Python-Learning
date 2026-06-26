# backend/providers/__init__.py
#
# Importing providers here triggers their LLMFactory.register() calls.
# The import order doesn't matter — each provider registers itself independently.
#
# To enable a new LLM provider:
#   1. Create backend/providers/<name>_provider.py
#   2. Add LLMFactory.register("<name>", YourProvider) at the bottom of that file
#   3. Uncomment (or add) the import below
#   4. Set 'provider: <name>' in config/llm.yaml
#
# That's all. No changes to factory.py, graph.py, agents, or any other file.

from backend.providers.groq_provider import GroqProvider          # registers "groq"
# from backend.providers.gemini_provider import GeminiProvider    # registers "gemini" (future)
# from backend.providers.openai_provider import OpenAIProvider    # registers "openai" (future)
