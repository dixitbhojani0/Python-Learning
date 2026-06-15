from fastembed import TextEmbedding
from config import EMBEDDING_MODEL

_model = TextEmbedding(EMBEDDING_MODEL)


def embed(texts: list[str]) -> list[list[float]]:
    return [vec.tolist() for vec in _model.embed(texts)]
