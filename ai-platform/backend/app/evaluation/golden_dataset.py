"""
backend/app/evaluation/golden_dataset.py

Three documents, one query each — deliberately small and deliberately
exact-text queries (see retrieval_eval.py's docstring on why: the mock
embedding provider is hash-based, not semantic, so this dataset proves
retrieval *ranking* correctness, not retrieval *quality*). Growing this
into a real quality benchmark needs a real embedding provider and
paraphrased queries — swap the provider name in retrieval_eval.py's call
site, this dataset's shape doesn't change.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GoldenCase:
    document_title: str
    document_content: str
    query: str


GOLDEN_RETRIEVAL_CASES: list[GoldenCase] = [
    GoldenCase(
        document_title="Office wifi",
        document_content="the office wifi password is sunflower123",
        query="the office wifi password is sunflower123",
    ),
    GoldenCase(
        document_title="Meeting schedule",
        document_content="the quarterly planning meeting is scheduled for next tuesday at 10am",
        query="the quarterly planning meeting is scheduled for next tuesday at 10am",
    ),
    GoldenCase(
        document_title="PTO policy",
        document_content="employees receive twenty days of paid time off per calendar year",
        query="employees receive twenty days of paid time off per calendar year",
    ),
]
