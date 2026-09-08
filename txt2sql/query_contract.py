"""Query contract gates — predicate coverage, task-output alignment (no Q-ID rules)."""

from __future__ import annotations

import re

from txt2sql.query_understanding.contract import QueryContract
from txt2sql.semantic_plan.compiler import CompiledSemanticQuery
from txt2sql.semantic_plan.models import SemanticQueryPlan
from txt2sql.semantic_plan.plan_sql_verifier import verify_plan_to_sql
from txt2sql.semantic_plan.predicate_utils import (
    effective_predicate,
    predicate_atoms,
)


def verify_range_bounds(plan: SemanticQueryPlan, sql: str) -> list[str]:
    """Threshold / BETWEEN from plan.filters and predicate tree must appear in SQL."""
    errors: list[str] = []
    upper = sql.upper()
    seen: set[str] = set()

    pred = effective_predicate(plan)
    for node in predicate_atoms(pred):
        op = node.operator or ""
        if op == "between":
            if "BETWEEN" not in upper and "RANGE_BOUND_DROPPED" not in seen:
                errors.append("RANGE_BOUND_DROPPED")
                seen.add("RANGE_BOUND_DROPPED")
            continue
        if op not in {"gt", "gte", "lt", "lte"}:
            continue
        val = node.right.value if node.right else None
        if not _range_value_in_sql(val, sql, upper) and "RANGE_BOUND_DROPPED" not in seen:
            errors.append("RANGE_BOUND_DROPPED")
            seen.add("RANGE_BOUND_DROPPED")

    for filt in plan.filters:
        op = filt.operator
        if op not in {"gt", "gte", "lt", "lte", "between"}:
            continue
        if op == "between":
            if "BETWEEN" not in upper and "RANGE_BOUND_DROPPED" not in seen:
                errors.append("RANGE_BOUND_DROPPED")
                seen.add("RANGE_BOUND_DROPPED")
            continue
        if (
            not _range_value_in_sql(filt.value, sql, upper)
            and "RANGE_BOUND_DROPPED" not in seen
        ):
            errors.append("RANGE_BOUND_DROPPED")
            seen.add("RANGE_BOUND_DROPPED")
    return errors


def _range_value_in_sql(val: object | None, sql: str, upper: str) -> bool:
    """Literal / rel_years / INTERVAL forms that compile from range filters."""
    if val is None:
        return True
    text = str(val)
    if text in sql or text in upper:
        return True
    if isinstance(val, str) and val.startswith("rel_years:"):
        try:
            years = int(val.split(":", 1)[1])
        except (TypeError, ValueError):
            return False
        # Compiler emits: INTERVAL 'N years' (or months) from rel_years:N
        if re.search(rf"INTERVAL\s+'{years}\s+years?'", sql, flags=re.I):
            return True
        if str(years) in sql:
            return True
        return False
    try:
        num = float(text)
    except (TypeError, ValueError):
        return False
    variants = {str(num), str(int(num)) if num == int(num) else str(num)}
    for variant in variants:
        if variant in sql:
            return True
    pat = re.escape(str(int(num)) if num == int(num) else str(num))
    return bool(re.search(rf"(?<![.\d]){pat}(?:\.0+)?(?![.\d])", sql))


_SOFT_CONTRACT_ERRORS = frozenset({"RANGE_BOUND_DROPPED", "PREDICATE_DROPPED"})


def _soft_contract_only(errors: list[str]) -> bool:
    return bool(errors) and set(errors) <= _SOFT_CONTRACT_ERRORS


def verify_task_output_alignment(plan: SemanticQueryPlan, sql: str) -> list[str]:
    """task=count must emit COUNT(*); aggregate must emit agg functions."""
    errors: list[str] = []
    upper = sql.upper()
    kind = plan.query_kind
    if kind == "count" and "COUNT(" not in upper:
        errors.append("TASK_OUTPUT_MISMATCH")
    if kind == "aggregate" and plan.aggregations:
        for agg in plan.aggregations:
            fn = (agg.function or "").upper()
            if fn == "COUNT" and "COUNT(" not in upper:
                errors.append("TASK_OUTPUT_MISMATCH")
            elif fn in {"AVG", "SUM", "MIN", "MAX"} and f"{fn}(" not in upper:
                errors.append("TASK_OUTPUT_MISMATCH")
    return errors


def contract_is_executable_query(contract: QueryContract) -> bool:
    """집계·목록·건수 등 데이터 조회 계약 — 스키마 메타가 아님."""
    q = contract.question or ""
    if re.search(r"같은\s*필드|다른\s*필드|필드야\s*\??", q):
        return False
    if contract.operation in {
        "list",
        "rank",
        "group_rank",
        "aggregate",
        "ratio",
        "percentile",
    }:
        return bool(
            contract.places
            or contract.metrics
            or contract.ranges
            or contract.aggregation_requests
        )
    if contract.wants_count:
        return bool(contract.places or contract.metrics)
    if contract.group_fields or contract.aggregation_requests:
        return True
    if contract.output_fields and contract.places:
        return True
    return False


def verify_query_contract(
    plan: SemanticQueryPlan,
    compiled: CompiledSemanticQuery,
    *,
    question: str | None = None,
) -> list[str]:
    """Unified pre-execution contract check for SQP and semantic_v2."""
    errors = list(verify_plan_to_sql(plan, compiled))
    errors.extend(verify_range_bounds(plan, compiled.sql))
    errors.extend(verify_task_output_alignment(plan, compiled.sql))
    if question:
        q = question
        if any(k in q for k in (" 또는 ", " 혹은 ", "이거나")) and " OR " not in compiled.sql.upper():
            if "BOOLEAN_OR_DROPPED" not in errors:
                errors.append("BOOLEAN_OR_DROPPED")
        if any(k in q for k in ("제외", "아닌", "빼고")) and "NOT " not in compiled.sql.upper():
            if "BOOLEAN_NOT_DROPPED" not in errors:
                errors.append("BOOLEAN_NOT_DROPPED")
    return list(dict.fromkeys(errors))
