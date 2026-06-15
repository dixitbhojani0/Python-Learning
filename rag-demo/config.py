import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")

EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"  # local ONNX model, ~50MB, no API key needed
LLM_MODEL: str = "llama-3.3-70b-versatile"
LLM_TEMPERATURE: float = 0.1
LLM_MAX_TOKENS: int = 500

CHUNK_SIZE: int = 300   # words per chunk
CHUNK_OVERLAP: int = 50  # word overlap between consecutive chunks
TOP_K_DEFAULT: int = 3

CHROMA_DB_PATH: str = os.path.join(os.path.dirname(__file__), "chroma_db")
CHROMA_COLLECTION: str = "rag_demo"
DOCS_DIR: str = os.path.join(os.path.dirname(__file__), "documents")
