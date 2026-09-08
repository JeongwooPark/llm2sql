"""Plan 노드가 SQL compile_trace에 모두 남았는지 검사한다. 누락이면 실행 금지."""

from __future__ import annotations

from collections import Counter

from txt2sql.semantic_plan.compiler import CompiledSemanticQuery
from txt2sql.semantic_plan.models import SemanticQueryPlan
from txt2sql.semantic_plan.predicate_utils import (
    effective_predicate,
    has_op,
    predicate_atoms,
)


def verify_plan_to_sql(
    plan: SemanticQueryPlan,
    compiled: CompiledSemanticQuery,
) -> list[str]:
    errors: list[str] = []
    extra = compiled.extra or {}
    trace = extra.get("compile_trace") or {}
    pred = effective_predicate(plan)
    atoms = predicate_atoms(pred)
    compiled_nodes = list(trace.get("predicate_nodes") or [])
    if atoms and len(compiled_nodes) < len(atoms):
        errors.append("PREDICATE_DROPPED")
    sql = compiled.sql.upper()
    if pred and has_op(pred, "or") and " OR " not in sql:
        errors.append("BOOLEAN_OR_DROPPED")
    if pred and has_op(pred, "not") and "NOT " not in sql and "NOT IN" not in sql and "<>" not in sql:
        errors.append("BOOLEAN_NOT_DROPPED")
    wanted_aggs = Counter(item.function.upper() for item in plan.aggregations)
    got_aggs = Counter(str(fn).upper() for fn in (trace.get("aggregations") or []))
    if wanted_aggs and any(got_aggs[fn] < count for fn, count in wanted_aggs.items()):
        missing = False
        for fn in wanted_aggs:
            if fn == "PERCENTILE":
                token_ok = "PERCENTILE" in sql or "PERCENTILE_CONT" in sql
            elif fn == "VARIANCE":
                token_ok = "VAR_POP(" in sql or "VARIANCE(" in sql or "VAR_SAMP(" in sql
            elif fn == "COUNT":
                token_ok = "COUNT(" in sql
            else:
                token_ok = f"{fn}(" in sql
            if not token_ok:
                missing = True
                break
        if missing:
            errors.append("OUTPUT_SHAPE_MISMATCH")
    wanted_groups = list(plan.group_by)
    got_groups = list(trace.get("group_fields") or [])
    if wanted_groups and (
        not set(wanted_groups) <= set(got_groups) or "GROUP BY" not in sql
    ):
        errors.append("GROUP_BY_DROPPED")
    if plan.spatial_relations:
        if "ST_" not in sql:
            errors.append("SPATIAL_TARGET_DROPPED")
    return list(dict.fromkeys(errors))
