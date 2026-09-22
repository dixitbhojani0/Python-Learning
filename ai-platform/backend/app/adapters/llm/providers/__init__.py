"""
Importing this package triggers every provider module's registration (each
calls LLMRegistry.register(...) at its own bottom). Add a new provider by
adding one import line here — never by editing the registry itself.
"""
from backend.app.adapters.llm.providers import gemini_provider  # noqa: F401
from backend.app.adapters.llm.providers import groq_provider  # noqa: F401
from backend.app.adapters.llm.providers import mock_provider  # noqa: F401

__all__: list[str] = []
