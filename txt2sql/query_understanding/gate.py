"""휴리스틱 Plan을 질문 contract 완전 소비 여부로만 채택한다."""

from __future__ import annotations

from txt2sql.domain import USAGE_ALIASES, extract_usages
from txt2sql.query_understanding.contract import QueryContract
from txt2sql.semantic_plan.models import SemanticQueryPlan
from txt2sql.semantic_plan.predicate_utils import (
    effective_predicate,
    has_field_compare,
    has_op,
    has_operator,
    predicate_fields,
    range_bounds,
)


def accept_heuristic_plan(contract: QueryContract, plan: SemanticQueryPlan) -> bool:
    if plan.requires_clarification:
        return True
    if plan.unsupported_reason:
        return False
    pred = effective_predicate(plan)
    or_bound = has_op(pred, "or")
    unresolved_ok = not contract.unresolved_spans
    if or_bound:
        unresolved_ok = True
    if not (
        contract.boolean_structure_supported
        and contract.aggregation_complete
        and contract.all_numeric_expressions_bound
        and contract.all_requested_outputs_bound
        and unresolved_ok
    ):
        return False
    if any(span.kind == "or" for span in contract.boolean_ops):
        if not _or_bound(contract.question, plan, pred):
            return False
    if any(span.kind == "not" for span in contract.boolean_ops):
        has_not = has_op(pred, "not") or any(item.operator == "neq" for item in plan.filters)
        if not has_not:
            return False
    if contract.comparisons:
        if not (
            has_field_compare(pred) or any(item.value_field for item in plan.filters)
        ):
            return False
    if contract.ranges:
        if not _range_filters_bound(contract, plan, pred):
            return False
    if not _threshold_numbers_bound(contract, plan, pred):
        return False
    if contract.aggregations:
        wanted = {span.value for span in contract.aggregations}
        got = {item.function for item in plan.aggregations}
        if not got or not wanted.issubset(got):
            return False
        if contract.groups and not plan.group_by:
            return False
        # Aggregation target field must match when contract bound a numeric field.
        for req in contract.aggregation_requests:
            if req.function == "count" or not req.field:
                continue
            matched = any(
                item.function == req.function and item.field == req.field
                for item in plan.aggregations
            )
            if not matched and req.function in got:
                # Allow count-only plans without field; otherwise require field match.
                if any(
                    item.function == req.function and item.field
                    for item in plan.aggregations
                ):
                    return False
    if contract.order:
        wanted_dir = contract.order[0].value
        if not plan.order_by or plan.order_by[0].direction != wanted_dir:
            return False
    if contract.limits:
        if plan.limit != contract.limits[0].value:
            return False
    if contract.places:
        if plan.scope is None or plan.scope.place is None:
            from txt2sql.domain import is_busan_wide

            industrial_place = any(
                getattr(rel.target, "entity", None) == "industrial_complex"
                or (
                    rel.target.place is not None
                    and bool((rel.target.place.name or "").strip())
                )
                for rel in plan.spatial_relations
            )
            if not is_busan_wide(contract.question) and not industrial_place:
                return False
    return True


def _threshold_numbers_bound(contract: QueryContract, plan: SemanticQueryPlan, pred) -> bool:
    semantic_fields = predicate_fields(pred) | {item.field for item in plan.filters}
    for span in contract.numbers:
        if span.meta.get("role", "threshold") != "threshold":
            continue
        field = span.meta.get("field")
        if not field:
            return False
        if str(field) not in semantic_fields:
            return False
    return True


def _range_filters_bound(
    contract: QueryContract,
    plan: SemanticQueryPlan,
    pred,
) -> bool:
    for span in contract.ranges:
        field = span.meta.get("field")
        low, high = range_bounds(pred, field) if field else (None, None)
        if low is not None and high is not None:
            continue
        if has_operator(pred, "between"):
            continue
        items = [item for item in plan.filters if item.field == field]
        between = next((item for item in items if item.operator == "between"), None)
        if between is not None:
            if between.value is None or between.value2 is None:
                return False
            continue
        ops = {item.operator for item in items}
        if not (ops & {"gte", "gt"} and ops & {"lte", "lt"}):
            return False
        if any(item.value is None for item in items if item.operator in {"gte", "gt", "lte", "lt"}):
            return False
    return True


def _or_bound(question: str, plan: SemanticQueryPlan, pred) -> bool:
    if has_op(pred, "or"):
        return True
    aliases = [alias for alias in USAGE_ALIASES if alias in question]
    mapped = list(dict.fromkeys(USAGE_ALIASES[alias] for alias in aliases))
    usages = extract_usages(question)
    if len(aliases) >= 2 and len(mapped) == 1:
        return any(item.field == "usage" and item.value == mapped[0] for item in plan.filters) or (
            plan.predicate is not None and plan.predicate.op == "cmp"
        )
    if len(usages) >= 2:
        return False
    return False
