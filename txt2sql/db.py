from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from txt2sql.security.sql_ast import assert_readonly_sql_ast

# 읽기 전용: 데이터 변경/DDL 키워드 차단 (AST 보조)
_FORBIDDEN = (
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "truncate",
    "create",
    "grant",
    "revoke",
    "copy",
    "call",
    "execute",
    "do",
)

_GEOM_TYPE_NAMES = {"geometry", "geography"}


@contextmanager
def connect(
    database_url: str,
    *,
    read_only: bool = False,
) -> Iterator[psycopg.Connection]:
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        if read_only:
            with conn.cursor() as cur:
                cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            conn.commit()
        yield conn


def assert_readonly_sql(sql: str) -> None:
    """AST allowlist first; keyword scan remains as defense in depth inside AST helper."""
    assert_readonly_sql_ast(sql)


def ensure_limit(sql: str, default_limit: int = 100) -> str:
    """집계가 아닌 조회에 LIMIT이 없으면 강제 부여."""
    body = sql.rstrip().rstrip(";")
    lower = body.lower()
    if re.search(r"\blimit\b", lower):
        return body + ";"
    # COUNT/순수 스칼라 집계만 있는 단순 쿼리는 LIMIT 생략 허용
    if re.search(r"\bcount\s*\(", lower) and not re.search(
        r"\bgroup\s+by\b", lower
    ):
        return body + ";"
    # 연도·면적 구간 등 GROUP BY 집계는 전체 버킷이 필요함
    if re.search(r"\bgroup\s+by\b", lower):
        return body + ";"
    return f"{body}\nLIMIT {default_limit};"


def _sanitize_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        type_name = type(value).__name__.lower()
        module = type(value).__class__.__module__
        if value is None:
            out[key] = None
        elif "Geometry" in type(value).__name__ or type_name in _GEOM_TYPE_NAMES:
            out[key] = "<geometry omitted>"
        elif module.startswith("shapely") or "WKB" in type(value).__name__.upper():
            out[key] = "<geometry omitted>"
        elif isinstance(value, (memoryview, bytes, bytearray)):
            # WKB 등 바이너리 geometry
            out[key] = "<binary omitted>"
        else:
            out[key] = value
    return out


def execute_query(
    conn: psycopg.Connection,
    sql: str,
    *,
    default_limit: int = 100,
    statement_timeout_ms: int | None = None,
) -> list[dict[str, Any]]:
    assert_readonly_sql(sql)
    sql = ensure_limit(sql, default_limit=default_limit)
    try:
        with conn.cursor() as cur:
            if statement_timeout_ms and statement_timeout_ms > 0:
                cur.execute(
                    f"SET LOCAL statement_timeout = {int(statement_timeout_ms)}"
                )
            # Prefer transaction-level read-only when connection supports it.
            try:
                cur.execute("SET LOCAL transaction_read_only = on")
            except Exception:
                pass
            cur.execute(sql)
            if cur.description is None:
                return []
            rows = list(cur.fetchall())
            return [_sanitize_row(dict(r)) for r in rows]
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
