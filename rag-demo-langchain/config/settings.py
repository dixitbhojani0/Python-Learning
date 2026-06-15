import os
from pydantic_settings import BaseSettings, SettingsConfigDict

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Settings(BaseSettings):
    # API keys
    groq_api_key: str
    cohere_api_key: str = ""          # optional — needed only when reranker_enabled=True

    # Embedding
    embedding_provider: str = "fastembed"           # registry key → ComponentFactory
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    # LLM
    llm_provider: str = "groq"                      # registry key → ComponentFactory
    llm_model: str = "llama-3.3-70b-versatile"
    llm_temperature: float = 0.1
    llm_max_tokens: int = 1000

    # Retrieval strategy — "basic" | "multi_query" | "hyde"
    retriever_strategy: str = "basic"

    # Reranking (disabled by default; set reranker_enabled=true in .env to activate)
    reranker_enabled: bool = False
    reranker_provider: str = "cohere"               # registry key → ComponentFactory
    reranker_top_n: int = 3

    # Conversation memory
    memory_enabled: bool = False
    memory_max_turns: int = 5                       # sliding window size

    # Chunking
    chunk_size: int = 500       # tighter = one topic per chunk, better precision
    chunk_overlap: int = 100    # proportional to chunk_size

    # Retrieval
    top_k_default: int = 5

    # Storage — absolute paths resolved from project root
    qdrant_db_path: str = os.path.join(_BASE_DIR, "qdrant_db")
    qdrant_collection: str = "rag_lc_demo"
    vector_size: int = 384  # BAAI/bge-small-en-v1.5 output dimensions
    docs_dir: str = os.path.join(_BASE_DIR, "documents")

    model_config = SettingsConfigDict(
        env_file=os.path.join(_BASE_DIR, "..", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
