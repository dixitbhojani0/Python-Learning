"""
tests/unit/test_mcp_client_as_list.py
Unit tests for as_list() — normalizes a call_mcp_tool() result for a tool whose
return type is a list. Root-cause fix for a real bug: langchain_mcp_adapters
collapses a single MCP content block down to its bare value, so a list-returning
tool with exactly one result comes back as a bare dict, not a one-item list.
Nine call sites across five files were silently discarding real data this way
(e.g. "assign SDLC-26 to dixit" reported 0 project members when there was 1).
"""
from backend.mcp_client.client import as_list


def test_passes_through_a_real_list():
    assert as_list([{"id": "1"}, {"id": "2"}]) == [{"id": "1"}, {"id": "2"}]


def test_wraps_a_single_collapsed_dict():
    """The exact bug: a lone result arrives as a bare dict, not a one-item list."""
    assert as_list({"id": "1", "display_name": "Bhojani Dixit"}) == [{"id": "1", "display_name": "Bhojani Dixit"}]


def test_empty_list_stays_empty():
    assert as_list([]) == []


def test_none_becomes_empty_list():
    assert as_list(None) == []


def test_exception_from_return_exceptions_gather_becomes_empty_list():
    """asyncio.gather(..., return_exceptions=True) can hand this an Exception object."""
    assert as_list(ValueError("mcp call failed")) == []


def test_empty_string_becomes_empty_list():
    assert as_list("") == []
