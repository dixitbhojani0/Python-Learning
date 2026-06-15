from models import DocStats, QueryResult
from document_loader import load_documents
from embeddings import embed
from vector_store import VectorStore
from generator import generate
from config import TOP_K_DEFAULT, DOCS_DIR


class RAGPipeline:
    def __init__(self) -> None:
        self.vector_store = VectorStore()

    def index(self, force: bool = False) -> list[DocStats]:
        if self.vector_store.is_indexed() and not force:
            return []
        if force:
            self.vector_store.clear()

        chunks, stats = load_documents(DOCS_DIR)
        embeddings = embed([c.text for c in chunks])
        self.vector_store.add_chunks(chunks, embeddings)
        return stats

    def query(self, question: str, top_k: int = TOP_K_DEFAULT) -> QueryResult:
        query_embedding = embed([question])[0]
        retrieved = self.vector_store.query(query_embedding, top_k)
        answer, prompt = generate(question, retrieved)
        return QueryResult(
            question=question,
            retrieved_chunks=retrieved,
            answer=answer,
            prompt=prompt,
            query_embedding=query_embedding,
        )

    def get_all_chunks(self) -> dict[str, list[dict]]:
        return self.vector_store.get_all()

    def is_indexed(self) -> bool:
        return self.vector_store.is_indexed()

    def chunk_count(self) -> int:
        return self.vector_store.count()
