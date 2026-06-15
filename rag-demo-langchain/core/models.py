from pydantic import BaseModel, Field
from typing import Optional


class RawDocument(BaseModel):
    text: str
    source: str
    page: Optional[int] = None


class Chunk(BaseModel):
    id: str
    text: str
    source: str
    chunk_index: int


class DocStats(BaseModel):
    file: str
    chunks: int
    chars: int


class RetrievedChunk(BaseModel):
    id: str
    text: str
    source: str
    chunk_index: int
    score: float
    rank: int
    embedding: list[float] = Field(default_factory=list)


class IngestionResult(BaseModel):
    total_files: int
    total_chunks: int
    doc_stats: list[DocStats]


class ConversationTurn(BaseModel):
    """A single Q&A exchange stored in memory."""
    question: str
    answer: str


class MemoryContext(BaseModel):
    """Formatted prior turns injected into the prompt when memory is enabled."""
    turns: list[ConversationTurn] = Field(default_factory=list)

    def to_messages(self) -> list[dict]:
        """Return turns as OpenAI-style message dicts for prompt building."""
        msgs: list[dict] = []
        for t in self.turns:
            msgs.append({"role": "user", "content": t.question})
            msgs.append({"role": "assistant", "content": t.answer})
        return msgs


class QueryResult(BaseModel):
    question: str
    retrieved_chunks: list[RetrievedChunk]
    answer: str
    prompt: str
    query_embedding: list[float] = Field(default_factory=list)
    conversation_history: list[ConversationTurn] = Field(default_factory=list)
