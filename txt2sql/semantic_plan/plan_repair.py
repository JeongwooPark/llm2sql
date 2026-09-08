"""Contract-driven plan repair — no Q-ID rules.

All repairs derive from QueryContract spans / verification error codes.
"""

from __future__ import annotations

from typing import Any

from txt2sql.query_understanding.contract import QueryContract, extract_contract
from txt2sql.semantic_plan.catalog import get_field
from txt2sql.semantic_plan.migrate import filters_to_and
from txt2sql.semantic_plan.models import (
    AggregationSpec,
    ExpressionSpec,
    FilterSpec,
    OrderSpec,
    SemanticQueryPlan,
    UnknownSemanticFieldError,
)
from txt2sql.semantic_plan.predicate_utils import (
    and_predicates,
    effective_predicate,
    predicate_fields,
)

# Verification / contract-verifier codes that deterministic repair may fix.
REPAIRABLE_CODES = frozenset(
    {
        "PREDICATE_DROPPED",
        "RANGE_BOUND_DROPPED",
        "BOOLEAN_OR_DROPPED",
        "BOOLEAN_NOT_DROPPED",
        "TASK_OUTPUT_MISMATCH",
        "GROUP_BY_DROPPED",
        "OUTPUT_SHAPE_MISMATCH",
        "P03",
        "P04",
        "P05",
        "P06",
        "missing_predicate",
        "missing_aggregation",
        "missing_group",
        "missing_order",
        "missing_order_field",
        "missing_limit",
        "missing_output",
        "aggregation_shape_mismatch",
    }
)

_KIND_FROM_CONTRACT = {
    "count": "count",
    "list": "list",
    "rank": "rank",
    "scalar": "aggregate",
    "group": "aggregate",
    "ratio": "aggregate",
}


def _error_codes(errors: list[str]) -> set[str]:
    out: set[str] = set()
    for item in errors:
        code = item.split(":", 1)[0].strip()
        if code:
            out.add(code)
    return out


def is_repairable(errors: list[str]) -> bool:
    return bool(_error_codes(errors) & REPAIRABLE_CODES)


def _groupable_field(entity: str, field: str) -> bool:
    try:
        meta = get_field(entity, field)
        return meta.data_type not in {"geometry"}
    except UnknownSemanticFieldError:
        return True


def apply_contract_operators(
    plan: SemanticQueryPlan,
    contract: QueryContract | None,
) -> SemanticQueryPlan:
    """Bind contract aggregation / group / bin / order slots onto the plan."""
    from txt2sql.query_understanding.contract import _is_grouped_count_question

    if plan is None or contract is None:
        return plan
    aggregations = list(plan.aggregations)
    assumptions = list(plan.assumptions or [])
    filters = list(plan.filters)
    group_by = list(plan.group_by)
    query_kind = plan.query_kind
    if contract.group_fields:
        for field in contract.group_fields:
            if field not in group_by and _groupable_field(plan.entity, field):
                group_by.append(field)
        if not aggregations:
            for req in contract.aggregation_requests:
                aggregations.append(
                    AggregationSpec(
                        function=req.function,
                        field=req.field,
                        alias="n" if req.function == "count" else f"{req.function}_{req.field}",
                    )
                )
            if not aggregations and (
                contract.wants_count
                or _is_grouped_count_question(
                    contract.question or "", contract.group_fields
                )
            ):
                aggregations.append(AggregationSpec(function="count", alias="n"))
        if aggregations:
            query_kind = "aggregate"
        if "violation_status" in contract.group_fields:
            filters = [item for item in filters if item.field != "violation_status"]
    for req in contract.percentile_requests:
        if not any(
            item.function == "percentile"
            and abs(float(item.percentile or 0) - float(req.percentile)) < 1e-9
            for item in aggregations
        ):
            aggregations.append(
                AggregationSpec(
                    function="percentile",
                    field=req.field or "height_m",
                    percentile=req.percentile,
                    alias="pctl",
                )
            )
            query_kind = "aggregate"
    for req in contract.derived_metrics:
        if not any(
            item.expression is not None and item.expression.kind == "divide"
            for item in aggregations
        ):
            aggregations.append(
                AggregationSpec(
                    function="avg",
                    expression=ExpressionSpec(
                        kind="divide",
                        left=ExpressionSpec(kind="field", field=req.left),
                        right=ExpressionSpec(kind="field", field=req.right),
                    ),
                    alias="avg_ratio",
                )
            )
            query_kind = "aggregate"
    if contract.fixed_bins and not any(
        item.startswith("width_bucket:")
        or item.startswith("edge_bins:")
        or item == "approval_decade"
        for item in assumptions
    ) and not any(spec.edges for spec in (plan.bins or [])):
        bin_field = None
        bin_width = None
        for span in contract.numbers:
            field = span.meta.get("field")
            if field and span.value:
                bin_field = str(field)
                bin_width = float(span.value)
                break
        if bin_field and bin_width and bin_width > 0:
            if bin_field not in group_by:
                group_by.append(bin_field)
            assumptions.append(f"width_bucket:{bin_field}:{bin_width:g}")
            if not aggregations:
                aggregations.append(AggregationSpec(function="count", alias="n"))
            query_kind = "aggregate"
    if group_by and query_kind == "count":
        if not aggregations:
            aggregations.append(AggregationSpec(function="count", alias="n"))
        query_kind = "aggregate"
    order_by = list(plan.order_by)
    if (
        query_kind == "aggregate"
        and group_by
        and aggregations
        and not order_by
        and any(item.function == "count" for item in aggregations)
    ):
        alias = next(
            (
                item.alias
                for item in aggregations
                if item.function == "count" and item.alias
            ),
            "n",
        )
        order_by = [OrderSpec(field=alias, direction="desc", nulls="last")]
    if contract.order_requests and not order_by:
        req = contract.order_requests[0]
        field = req.field
        if not field and contract.metrics:
            field = str(contract.metrics[0].value or "")
        if not field:
            field = "gross_floor_area_m2"
        order_by = [OrderSpec(field=field, direction=req.direction, nulls="last")]
    limit = plan.limit
    if contract.limit is not None:
        limit = contract.limit
    select = list(plan.select)
    if contract.output_fields:
        for field in contract.output_fields:
            if field not in select:
                select.append(field)
    return plan.model_copy(
        update={
            "aggregations": aggregations,
            "assumptions": assumptions,
            "filters": filters,
            "group_by": group_by,
            "query_kind": query_kind,
            "order_by": order_by,
            "limit": limit,
            "select": select,
        }
    )


def inject_missing_predicates(
    plan: SemanticQueryPlan,
    question: str,
    *,
    contract: QueryContract | None = None,
) -> SemanticQueryPlan:
    """Add threshold / range filters from contract extraction when absent on plan."""
    from txt2sql.semantic_plan.generator import extract_plan_hints
    from txt2sql.semantic_plan.models import OperandSpec, PredicateSpec
    from txt2sql.semantic_plan.predicate_utils import has_op

    contract = contract or extract_contract(question)
    hints = extract_plan_hints(question)
    existing_fields = {item.field for item in plan.filters}
    existing_fields |= predicate_fields(effective_predicate(plan))
    # BinSpec으로 이미 구간화된 필드는 threshold 재주입 금지
    existing_fields |= {
        str(spec.field)
        for spec in (plan.bins or [])
        if spec.field and (spec.edges or spec.width is not None)
    }
    new_filters: list[FilterSpec] = []
    for item in hints.get("numeric_expressions") or []:
        field = str(item.get("field") or "")
        if not field or field in existing_fields:
            continue
        new_filters.append(
            FilterSpec(
                field=field,
                operator=item.get("operator", "gte"),
                value=item.get("value"),
                value2=item.get("value2"),
                unit=item.get("unit"),
            )
        )
        existing_fields.add(field)
    for span in contract.numbers:
        if span.meta.get("role", "threshold") != "threshold":
            continue
        field = span.meta.get("field")
        if not field or str(field) in existing_fields:
            continue
        new_filters.append(
            FilterSpec(
                field=str(field),
                operator=span.meta.get("operator") or "gte",
                value=span.value,
                unit=span.meta.get("unit"),
            )
        )
        existing_fields.add(str(field))
    for span in contract.ranges:
        field = span.meta.get("field")
        if not field or str(field) in existing_fields:
            continue
        low, high = span.meta.get("low"), span.meta.get("high")
        if low is None or high is None:
            continue
        new_filters.append(
            FilterSpec(
                field=str(field),
                operator="between",
                value=low,
                value2=high,
            )
        )
        existing_fields.add(str(field))

    predicate = plan.predicate
    filters = list(plan.filters)
    if new_filters:
        filters = filters + new_filters
        predicate = and_predicates([predicate, filters_to_and(new_filters)])

    # Deterministic categorical OR / NOT repair from contract spans.
    has_or = any(span.kind == "or" for span in contract.boolean_ops)
    has_not = any(span.kind == "not" for span in contract.boolean_ops)
    pred = effective_predicate(plan.model_copy(update={"filters": filters, "predicate": predicate}))
    if has_or and not has_op(pred, "or"):
        usage_vals: list[str] = []
        for span in contract.boolean_ops:
            if span.kind != "or":
                continue
            for key in ("left", "right"):
                tok = str((span.meta or {}).get(key) or "").strip()
                if tok:
                    usage_vals.append(tok)
        for m in contract.metrics:
            if m.value == "usage" and m.text not in {"용도"}:
                usage_vals.append(str(m.meta.get("canonical") or m.text))
        usage_vals = list(dict.fromkeys(usage_vals))
        if len(usage_vals) >= 2:
            children = [
                PredicateSpec(
                    op="cmp",
                    operator="eq",
                    left=OperandSpec(kind="field", field="usage"),
                    right=OperandSpec(kind="literal", value=val),
                )
                for val in usage_vals[:4]
            ]
            or_pred = PredicateSpec(op="or", args=children)
            # Do NOT also flatten OR values into AND filters (contradicts OR).
            filters = [
                f
                for f in filters
                if not (f.field == "usage" and f.operator == "eq" and f.value in usage_vals)
            ]
            predicate = and_predicates([predicate, or_pred])
            pred = or_pred
    if has_not and not (
        has_op(pred, "not") or any(item.operator == "neq" for item in filters)
    ):
        not_span = next(span for span in contract.boolean_ops if span.kind == "not")
        operands = list((not_span.meta or {}).get("operands") or [])
        if (not_span.meta or {}).get("scopes_or") and has_op(pred, "or"):
            # Wrap only the OR subtree, not the entire AND plan.
            predicate = PredicateSpec(op="not", args=[pred])
            # Drop flat usage eq filters that duplicate OR children.
            filters = [f for f in filters if f.field != "usage"]
        elif len(operands) >= 2:
            # NOT(A OR B) when OR was not yet built: build OR then negate once.
            children = [
                PredicateSpec(
                    op="cmp",
                    operator="eq",
                    left=OperandSpec(kind="field", field="usage"),
                    right=OperandSpec(kind="literal", value=val),
                )
                for val in operands[:4]
            ]
            or_pred = PredicateSpec(op="or", args=children)
            filters = [
                f
                for f in filters
                if not (f.field == "usage" and f.value in operands)
            ]
            predicate = and_predicates(
                [predicate, PredicateSpec(op="not", args=[or_pred])]
            )
        elif operands:
            val = operands[0]
            # Single exclusion: one neq leaf (no duplicate not(eq)).
            filters = [
                f
                for f in filters
                if not (f.field == "usage" and f.operator == "eq" and f.value == val)
            ]
            filters.append(FilterSpec(field="usage", operator="neq", value=val))
            predicate = and_predicates(
                [
                    predicate,
                    PredicateSpec(
                        op="cmp",
                        operator="neq",
                        left=OperandSpec(kind="field", field="usage"),
                        right=OperandSpec(kind="literal", value=val),
                    ),
                ]
            )

    if filters == list(plan.filters) and predicate is plan.predicate:
        return plan
    return plan.model_copy(update={"filters": filters, "predicate": predicate})


def align_plan_kind(
    plan: SemanticQueryPlan,
    contract: QueryContract,
) -> SemanticQueryPlan:
    """Align query_kind / aggregations with contract.operation when shape mismatches."""
    target = _KIND_FROM_CONTRACT.get(contract.query_kind)
    if target is None:
        return plan
    updates: dict[str, Any] = {}
    if target == "aggregate" and plan.query_kind in {"count", "list", "rank"}:
        aggs = list(plan.aggregations)
        if contract.aggregation_requests:
            for req in contract.aggregation_requests:
                if not any(item.function == req.function for item in aggs):
                    aggs.append(
                        AggregationSpec(
                            function=req.function,
                            field=req.field,
                            alias=(
                                "n"
                                if req.function == "count"
                                else f"{req.function}_{req.field or 'val'}"
                            ),
                        )
                    )
        elif contract.wants_count and not aggs:
            aggs.append(AggregationSpec(function="count", alias="n"))
        updates["query_kind"] = "aggregate"
        updates["aggregations"] = aggs
        updates["select"] = []
    elif target in {"list", "rank", "count"} and plan.query_kind != target:
        updates["query_kind"] = target
        if target == "count":
            updates["select"] = []
            updates["limit"] = None
            updates["aggregations"] = []
    return plan.model_copy(update=updates) if updates else plan


def repair_plan_from_contract(
    plan: SemanticQueryPlan,
    contract: QueryContract,
    errors: list[str],
    question: str,
) -> SemanticQueryPlan:
    """One deterministic repair pass from contract + verification errors."""
    codes = _error_codes(errors)
    repaired = plan
    if codes & {
        "PREDICATE_DROPPED",
        "RANGE_BOUND_DROPPED",
        "BOOLEAN_OR_DROPPED",
        "BOOLEAN_NOT_DROPPED",
        "P03",
        "P04",
        "missing_predicate",
    }:
        repaired = inject_missing_predicates(repaired, question, contract=contract)
    repaired = apply_contract_operators(repaired, contract)
    if codes & {
        "P05",
        "missing_aggregation",
        "missing_group",
        "GROUP_BY_DROPPED",
        "OUTPUT_SHAPE_MISMATCH",
        "TASK_OUTPUT_MISMATCH",
        "aggregation_shape_mismatch",
    }:
        repaired = apply_contract_operators(repaired, contract)
        repaired = align_plan_kind(repaired, contract)
    if codes & {"P06", "missing_order", "missing_order_field", "missing_limit"}:
        repaired = apply_contract_operators(repaired, contract)
    if codes & {"missing_output"} and contract.output_fields:
        select = list(repaired.select)
        for field in contract.output_fields:
            if field not in select:
                select.append(field)
        repaired = repaired.model_copy(update={"select": select})
    return repaired


def compile_with_contract_gate(
    plan: SemanticQueryPlan,
    question: str,
    *,
    contract: QueryContract | None = None,
    max_repairs: int = 1,
):
    """Compile plan; run unified contract gate; repair up to max_repairs times."""
    from txt2sql.query_contract import verify_query_contract
    from txt2sql.semantic_plan.compiler import compile_semantic_plan

    contract = contract or extract_contract(question)
    current = plan
    last_errors: list[str] = []
    compiled = None
    for attempt in range(max_repairs + 1):
        compiled = compile_semantic_plan(current)
        last_errors = verify_query_contract(current, compiled, question=question)
        if not last_errors:
            return current, compiled, []
        if attempt < max_repairs and is_repairable(last_errors):
            current = repair_plan_from_contract(
                current, contract, last_errors, question
            )
            continue
        break
    return current, compiled, last_errors
