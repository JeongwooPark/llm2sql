"""Hybrid SQP fallback → RAG control-flow tests."""

from __future__ import annotations

from typing import Any

from txt2sql.pipeline import _semantic_blocks_rag_fallback


def test_readonly_failure_blocks_rag() -> None:
    assert _semantic_blocks_rag_fallback(
        {"fallback": True, "fallback_reason": "readonly_failed", "error": "DDL"}
    )


def test_sql_validation_failure_blocks_rag() -> None:
    assert _semantic_blocks_rag_fallback(
        {"fallback": True, "fallback_reason": "sql_validation_failed"}
    )


def test_contract_mismatch_allows_rag() -> None:
    assert not _semantic_blocks_rag_fallback(
        {"fallback": True, "fallback_reason": "sql_semantic_mismatch"}
    )


def test_validation_failed_allows_rag() -> None:
    assert not _semantic_blocks_rag_fallback(
        {"fallback": True, "fallback_reason": "validation_failed"}
    )


def test_clarify_blocks_rag() -> None:
    assert _semantic_blocks_rag_fallback(
        {"fallback": False, "needs_clarification": True, "fallback_reason": None}
    )
