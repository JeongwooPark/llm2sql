"""sqlglot AST 기반 읽기 전용 SQL allowlist."""

from __future__ import annotations

import re
from typing import Any

import sqlglot
from sqlglot import exp

# Dangerous PostgreSQL functions (file/net/session/admin side effects).
_DENIED_FUNCS = frozenset(
    {
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "lo_import",
        "lo_export",
        "lo_get",
        "lo_put",
        "lo_unlink",
        "pg_sleep",
        "set_config",
        "current_setting",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "dblink",
        "dblink_exec",
        "dblink_connect",
        "http",
        "http_get",
        "http_post",
        "file_fdw",
        "copy_from",
        "copy_to",
    }
)

_DENIED_NODE_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Command,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Set,
    exp.Use,
    exp.Grant,
    exp.Revoke,
    exp.TruncateTable,
    exp.Copy,
)

# Keyword fallback (defense in depth).
_FORBIDDEN_KW = (
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


def assert_readonly_sql_ast(sql: str) -> None:
    """Reject non-read-only SQL via AST walk + keyword fallback."""
    text = (sql or "").strip()
    if not text:
        raise ValueError("SQL이 비어 있습니다.")

    # Single statement: disallow extra semicolons (except trailing).
    body = text.rstrip().rstrip(";")
    if ";" in body:
        raise ValueError("한 번에 하나의 SQL문만 허용됩니다.")

    try:
        trees = sqlglot.parse(text, read="postgres")
    except Exception as exc:
        raise ValueError(f"SQL 구문 오류: {exc}") from exc

    trees = [t for t in trees if t is not None]
    if len(trees) != 1:
        raise ValueError("한 번에 하나의 SQL문만 허용됩니다.")

    root = trees[0]
    if not _is_select_like(root):
        raise ValueError("SELECT/WITH 쿼리만 허용됩니다.")

    for node in root.walk():
        if isinstance(node, _DENIED_NODE_TYPES):
            raise ValueError(f"금지된 SQL 구문입니다: {type(node).__name__}")
        if isinstance(node, exp.Select) and node.args.get("into"):
            raise ValueError("SELECT INTO는 허용되지 않습니다.")
        # Locking clauses
        locks = node.args.get("locks") if isinstance(node, exp.Select) else None
        if locks:
            raise ValueError("행 잠금 절(FOR UPDATE/SHARE)은 허용되지 않습니다.")
        locking_hint = getattr(exp, "LockingTableHint", None)
        if locking_hint is not None and isinstance(node, locking_hint):
            raise ValueError("행 잠금 힌트는 허용되지 않습니다.")
        # Writable CTE: WITH t AS (INSERT ...)
        if isinstance(node, exp.CTE):
            this = node.this
            if this is not None and not _is_select_like(this):
                raise ValueError("쓰기 가능한 CTE는 허용되지 않습니다.")
        # Table/schema denylist for chat queries
        if isinstance(node, exp.Table):
            catalog = (node.args.get("catalog") or "")
            db = (node.args.get("db") or "")
            # sqlglot: db often holds schema
            schema_name = str(getattr(db, "name", db) or "").lower()
            table_name = str(getattr(node, "name", "") or "").lower()
            if schema_name in {"pg_catalog", "information_schema", "pg_toast"}:
                raise ValueError(f"허용되지 않은 schema입니다: {schema_name}")
            if table_name.startswith("pg_") and schema_name in {"", "public"}:
                # bare pg_* system relations
                if table_name in {
                    "pg_sleep",
                    "pg_user",
                    "pg_roles",
                    "pg_shadow",
                    "pg_authid",
                }:
                    raise ValueError(f"허용되지 않은 테이블입니다: {table_name}")
            if table_name.startswith("temp_"):
                raise ValueError("임시 분석 테이블은 질의에서 직접 참조할 수 없습니다.")
            _ = catalog  # reserved for future catalog allowlist
        if isinstance(node, exp.Anonymous):
            name = (node.name or "").lower()
            if name in _DENIED_FUNCS:
                raise ValueError(f"금지된 함수입니다: {name}")
        if isinstance(node, exp.Func):
            raw_name = getattr(node, "sql_name", None)
            if callable(raw_name):
                try:
                    name = str(raw_name()).lower()
                except Exception:
                    name = type(node).__name__.lower()
            elif raw_name:
                name = str(raw_name).lower()
            else:
                name = type(node).__name__.lower()
            simple = name.split(".")[-1]
            if simple in _DENIED_FUNCS:
                raise ValueError(f"금지된 함수입니다: {simple}")

    # Keyword fallback on normalized text (comments stripped by sqlglot already
    # for structure; still scan raw lower for defense).
    normalized = " ".join(text.lower().split())
    for word in _FORBIDDEN_KW:
        if f" {word} " in f" {normalized} " or normalized.startswith(f"{word} "):
            # Allow words inside identifiers/strings poorly — AST is primary.
            # Only trip if AST somehow missed and keyword appears as statement.
            if word in {"select", "with"}:
                continue
            # Heuristic: keyword as token after comment strip via simple regex
            if re.search(rf"(?:^|[^a-z0-9_]){word}(?:[^a-z0-9_]|$)", normalized):
                # If AST said select-like, skip soft keywords that appear in names
                if word in {"do", "call", "execute"} and _is_select_like(root):
                    # still deny if present as statement word at start of clause
                    if re.search(rf"\b{word}\s*\(", normalized):
                        raise ValueError(
                            f"금지된 키워드가 포함되어 있습니다: {word.upper()}"
                        )
                    continue
                if not _keyword_inside_select_only(normalized, word):
                    raise ValueError(
                        f"금지된 키워드가 포함되어 있습니다: {word.upper()}"
                    )


def _is_select_like(node: Any) -> bool:
    if isinstance(node, (exp.Select, exp.Union, exp.Except, exp.Intersect)):
        return True
    if isinstance(node, exp.With):
        return node.this is not None and _is_select_like(node.this)
    if isinstance(node, exp.Subquery):
        return node.this is not None and _is_select_like(node.this)
    return False


def _keyword_inside_select_only(normalized: str, word: str) -> bool:
    """Soft allow when forbidden word only appears as column/table fragment."""
    # If the statement clearly starts with select/with and word isn't a clause head
    if not (normalized.startswith("select") or normalized.startswith("with")):
        return False
    return not re.search(rf"\b{word}\b\s+(?:into|table|from|set|database)\b", normalized)
