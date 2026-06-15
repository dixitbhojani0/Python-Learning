class RAGException(Exception):
    """Base exception for all RAG errors."""


class LoadError(RAGException):
    """Raised when a document fails to load."""


class ChunkError(RAGException):
    """Raised when chunking fails."""


class EmbedError(RAGException):
    """Raised when embedding fails."""


class StoreError(RAGException):
    """Raised when storing to vector DB fails."""


class SearchError(RAGException):
    """Raised when vector search fails."""


class GenerationError(RAGException):
    """Raised when LLM generation fails."""
