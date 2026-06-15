import logging
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document as LCDocument
from core.interfaces import BaseChunker
from core.models import RawDocument, Chunk
from core.exceptions import ChunkError
from config.settings import Settings

logger = logging.getLogger(__name__)


class RecursiveChunker(BaseChunker):
    """Step 2 — Splits documents using LangChain RecursiveCharacterTextSplitter.

    Splits in order: paragraph → sentence → word — preserving semantic boundaries
    instead of cutting blindly by word count.
    """

    def __init__(self, settings: Settings) -> None:
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""],
        )

    def chunk(self, docs: list[RawDocument]) -> list[Chunk]:
        try:
            chunks: list[Chunk] = []
            source_index: dict[str, int] = {}

            for doc in docs:
                lc_doc = LCDocument(
                    page_content=doc.text,
                    metadata={"source": doc.source},
                )
                for split in self._splitter.split_documents([lc_doc]):
                    source = split.metadata["source"]
                    idx = source_index.get(source, 0)
                    source_index[source] = idx + 1
                    chunks.append(Chunk(
                        id=f"{source}__{idx}",
                        text=split.page_content,
                        source=source,
                        chunk_index=idx,
                    ))

            logger.info("Created %d chunks from %d documents", len(chunks), len(docs))
            return chunks
        except Exception as exc:
            raise ChunkError("Failed to chunk documents") from exc
