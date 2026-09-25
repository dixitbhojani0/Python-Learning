"""
backend/app/rag/parsers/registry.py

Same self-registering plugin registry pattern as adapters/llm/registry.py
and adapters/embedding/registry.py — a fourth ~30-line registry, not a
generic one, for the same reason those two aren't merged (§30: a shared
"any-plugin registry" abstraction is exactly the speculative generalization
that warns against). Keyed by lowercase file extension without the dot
("pdf", "docx", "txt", "md", "csv"), not MIME type — the upload endpoint
only ever has a filename to go on.
"""
from __future__ import annotations

import logging

from backend.app.rag.parsers.base import BaseDocumentParser

logger = logging.getLogger(__name__)


class ParserAlreadyRegisteredError(Exception):
    pass


class UnsupportedFileTypeError(ValueError):
    pass


class DocumentParserRegistry:
    _registry: dict[str, type[BaseDocumentParser]] = {}

    @classmethod
    def register(cls, extension: str, parser_class: type[BaseDocumentParser]) -> None:
        if extension in cls._registry and cls._registry[extension] is not parser_class:
            raise ParserAlreadyRegisteredError(
                f"Parser for '.{extension}' is already registered to "
                f"{cls._registry[extension].__name__}; refusing to shadow it with {parser_class.__name__}."
            )
        cls._registry[extension] = parser_class
        logger.debug("DocumentParserRegistry: registered '.%s' -> %s", extension, parser_class.__name__)

    @classmethod
    def create(cls, extension: str) -> BaseDocumentParser:
        parser_class = cls._registry.get(extension.lower().lstrip("."))
        if parser_class is None:
            available = sorted(cls._registry.keys())
            raise UnsupportedFileTypeError(f"Unsupported file type '.{extension}'. Supported: {available}.")
        return parser_class()

    @classmethod
    def available(cls) -> list[str]:
        return sorted(cls._registry.keys())

    @classmethod
    def reset(cls) -> None:
        cls._registry = {}
