from dataclasses import dataclass, field


@dataclass
class Chunk:
    id: str           # unique key: "<source>__<chunk_index>"
    text: str
    source: str
    chunk_index: int


@dataclass
class DocStats:
    file: str
    chunks: int
    chars: int


@dataclass
class RetrievedChunk:
    id: str
    text: str
    source: str
    chunk_index: int
    score: float      # cosine similarity [0, 1]
    rank: int
    embedding: list[float] = field(default_factory=list)


@dataclass
class QueryResult:
    question: str
    retrieved_chunks: list[RetrievedChunk]
    answer: str
    prompt: str
    query_embedding: list[float]
