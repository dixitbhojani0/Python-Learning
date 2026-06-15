import logging
from pathlib import Path
from langchain_community.document_loaders import TextLoader, PyPDFLoader, CSVLoader
from langchain_core.documents import Document as LCDocument
from core.interfaces import BaseLoader
from core.models import RawDocument, DocStats
from core.exceptions import LoadError
from config.settings import Settings

logger = logging.getLogger(__name__)

_LOADER_MAP = {
    ".pdf":  PyPDFLoader,
    ".txt":  TextLoader,
    ".md":   TextLoader,
    ".csv":  CSVLoader,
}


class DocumentLoader(BaseLoader):
    """Step 1 — Loads raw text from files using LangChain document loaders."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def load(self, docs_dir: str) -> tuple[list[RawDocument], list[DocStats]]:
        raw_docs: list[RawDocument] = []
        stats: list[DocStats] = []

        for fpath in sorted(Path(docs_dir).iterdir()):
            ext = fpath.suffix.lower()
            if ext not in _LOADER_MAP:
                logger.warning("Skipping unsupported file: %s", fpath.name)
                continue
            try:
                logger.info("Loading: %s", fpath.name)
                lc_docs: list[LCDocument] = _LOADER_MAP[ext](str(fpath)).load()
                for doc in lc_docs:
                    raw_docs.append(RawDocument(
                        text=doc.page_content,
                        source=fpath.name,
                        page=doc.metadata.get("page"),
                    ))
                total_chars = sum(len(d.page_content) for d in lc_docs)
                stats.append(DocStats(file=fpath.name, chunks=0, chars=total_chars))
            except Exception as exc:
                raise LoadError(f"Failed to load '{fpath.name}'") from exc

        logger.info("Loaded %d documents from %d files", len(raw_docs), len(stats))
        return raw_docs, stats
