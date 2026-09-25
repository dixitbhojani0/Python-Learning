"""
Unit tests for the self-registering DocumentParserRegistry (rag/parsers/) —
mirrors test_llm_registry.py / test_embedding_registry.py / test_tool_registry.py
exactly, plus one test per real parser's actual extraction behavior.
"""
from __future__ import annotations

import io

import docx
import pytest

from backend.app.rag.parsers import providers as _providers  # noqa: F401  (triggers registration)
from backend.app.rag.parsers.base import BaseDocumentParser, DocumentParseError
from backend.app.rag.parsers.registry import DocumentParserRegistry, ParserAlreadyRegisteredError, UnsupportedFileTypeError


@pytest.fixture(autouse=True)
def _isolated_registry():
    """Snapshot/restore the registry so tests never see each other's registrations."""
    original = dict(DocumentParserRegistry._registry)
    yield
    DocumentParserRegistry._registry = original


# ── Positive ──────────────────────────────────────────────────────────────

def test_all_real_parsers_are_registered_on_import():
    assert set(DocumentParserRegistry.available()) == {"txt", "md", "csv", "pdf", "docx"}


def test_txt_parser_decodes_utf8():
    assert DocumentParserRegistry.create("txt").parse("hello, world".encode("utf-8")) == "hello, world"


def test_md_extension_uses_the_same_parser_as_txt():
    assert type(DocumentParserRegistry.create("md")) is type(DocumentParserRegistry.create("txt"))


def test_csv_parser_renders_rows_as_labeled_pairs():
    result = DocumentParserRegistry.create("csv").parse(b"name,age\nAlice,30\nBob,25")
    assert result == "name: Alice; age: 30\nname: Bob; age: 25"


def test_docx_parser_extracts_paragraph_text():
    document = docx.Document()
    document.add_paragraph("first paragraph")
    document.add_paragraph("second paragraph")
    buf = io.BytesIO()
    document.save(buf)

    result = DocumentParserRegistry.create("docx").parse(buf.getvalue())
    assert "first paragraph" in result
    assert "second paragraph" in result


def test_extension_lookup_is_case_insensitive_and_tolerates_a_leading_dot():
    assert DocumentParserRegistry.create("TXT") is not None
    assert DocumentParserRegistry.create(".txt") is not None


def test_reregistering_the_identical_class_is_a_harmless_no_op():
    from backend.app.rag.parsers.providers.text_parser import TextParser

    DocumentParserRegistry.register("txt", TextParser)  # must not raise
    assert DocumentParserRegistry.create("txt") is not None


# ── Negative ──────────────────────────────────────────────────────────────

def test_unsupported_extension_raises_with_the_available_list():
    with pytest.raises(UnsupportedFileTypeError) as exc_info:
        DocumentParserRegistry.create("exe")
    assert "csv" in str(exc_info.value)  # the real available list, not a generic message


def test_registering_a_different_class_to_the_same_extension_raises():
    class _FakeParser(BaseDocumentParser):
        def parse(self, raw_bytes: bytes) -> str:
            return ""

    with pytest.raises(ParserAlreadyRegisteredError):
        DocumentParserRegistry.register("txt", _FakeParser)


def test_txt_parser_rejects_invalid_utf8():
    with pytest.raises(DocumentParseError):
        DocumentParserRegistry.create("txt").parse(b"\xff\xfe\x00\x01invalid")


def test_docx_parser_rejects_a_file_that_is_not_a_zip_at_all():
    with pytest.raises(DocumentParseError):
        DocumentParserRegistry.create("docx").parse(b"not a docx file at all")


def test_docx_parser_rejects_a_zip_that_is_not_a_docx_package():
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", "just a plain zip, not a docx")

    with pytest.raises(DocumentParseError):
        DocumentParserRegistry.create("docx").parse(buf.getvalue())


def test_pdf_parser_rejects_a_file_that_is_not_a_pdf():
    with pytest.raises(DocumentParseError):
        DocumentParserRegistry.create("pdf").parse(b"this is not a pdf file at all")


# ── Edge ──────────────────────────────────────────────────────────────────

def test_csv_parser_on_headers_only_produces_empty_output():
    assert DocumentParserRegistry.create("csv").parse(b"name,age") == ""


def test_csv_parser_on_completely_empty_input_produces_empty_output():
    assert DocumentParserRegistry.create("csv").parse(b"") == ""


def test_reset_clears_every_registration():
    DocumentParserRegistry.reset()
    assert DocumentParserRegistry.available() == []


# ── Side effects ──────────────────────────────────────────────────────────

def test_parsing_is_pure_and_deterministic():
    parser = DocumentParserRegistry.create("txt")
    data = b"the same input twice"
    assert parser.parse(data) == parser.parse(data)
