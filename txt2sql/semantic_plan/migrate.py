"""Plan 1.0 → 1.1 migration. filters[] 는 AND predicate 로 변환한다."""

from __future__ import annotations

from txt2sql.semantic_plan.models import (
    FilterSpec,
    OperandSpec,
    PredicateSpec,
    ProjectionSpec,
    SemanticCompileError,
    SemanticQueryPlan,
)

MAX_PRED_DEPTH = 6
MAX_PRED_NODES = 32


def filter_to_predicate(spec: FilterSpec) -> PredicateSpec:
    left = OperandSpec(kind="field", field=spec.field)
    if spec.value_field:
        right = OperandSpec(
            kind="field",
            field=spec.value_field,
            scale=spec.value_scale,
        )
        return PredicateSpec(op="cmp", operator=spec.operator, left=left, right=right)
    if spec.operator == "between":
        return PredicateSpec(
            op="cmp",
            operator="between",
            left=left,
            right=OperandSpec(
                kind="literal",
                value=[spec.value, spec.value2],
                unit=spec.unit,
            ),
        )
    if spec.operator == "not_in":
        return PredicateSpec(
            op="cmp",
            operator="not_in",
            left=left,
            right=OperandSpec(kind="literal", value=spec.value, unit=spec.unit),
        )
    return PredicateSpec(
        op="cmp",
        operator=spec.operator,
        left=left,
        right=OperandSpec(kind="literal", value=spec.value, unit=spec.unit),
    )


def filters_to_and(filters: list[FilterSpec]) -> PredicateSpec | None:
    if not filters:
        return None
    nodes = [filter_to_predicate(item) for item in filters]
    if len(nodes) == 1:
        return nodes[0]
    return PredicateSpec(op="and", args=nodes)


def dict_predicate_to_spec(raw: dict) -> PredicateSpec:
    op = raw.get("op")
    if op in {"and", "or"}:
        args = [dict_predicate_to_spec(item) if isinstance(item, dict) else item for item in raw.get("args") or []]
        return PredicateSpec(op=op, args=args)
    if op == "not":
        args = raw.get("args") or []
        converted = [dict_predicate_to_spec(args[0])] if args else []
        return PredicateSpec(op="not", args=converted)
    field = raw.get("field")
    operator = raw.get("operator") or "eq"
    if raw.get("value_field"):
        return PredicateSpec(
            op="cmp",
            operator=operator,
            left=OperandSpec(kind="field", field=field),
            right=OperandSpec(kind="field", field=raw["value_field"]),
        )
    return PredicateSpec(
        op="cmp",
        operator=operator,
        left=OperandSpec(kind="field", field=field),
        right=OperandSpec(kind="literal", value=raw.get("value"), unit=raw.get("unit")),
    )


def count_nodes(pred: PredicateSpec | None) -> int:
    if pred is None:
        return 0
    total = 1
    for child in pred.args or []:
        total += count_nodes(child)
    return total


def depth_of(pred: PredicateSpec | None) -> int:
    if pred is None:
        return 0
    if not pred.args:
        return 1
    return 1 + max(depth_of(child) for child in pred.args)


def validate_predicate(pred: PredicateSpec | None) -> None:
    if pred is None:
        return
    if depth_of(pred) > MAX_PRED_DEPTH:
        raise SemanticCompileError("predicate depth exceeds limit")
    if count_nodes(pred) > MAX_PRED_NODES:
        raise SemanticCompileError("predicate node count exceeds limit")
    if pred.op == "cmp":
        if pred.left is None or pred.operator is None:
            raise SemanticCompileError("cmp predicate missing operator/left")
        if pred.left.kind == "field" and not pred.left.field:
            raise SemanticCompileError("field operand missing field")
    elif pred.op in {"and", "or"}:
        if not pred.args or len(pred.args) < 2:
            raise SemanticCompileError(f"{pred.op} needs at least 2 args")
        for child in pred.args:
            validate_predicate(child)
    elif pred.op == "not":
        if not pred.args or len(pred.args) != 1:
            raise SemanticCompileError("not needs 1 arg")
        validate_predicate(pred.args[0])


def migrate_plan_v11(plan: SemanticQueryPlan) -> SemanticQueryPlan:
    data = plan.model_dump()
    extra = getattr(plan, "model_extra", None)
    predicate = plan.predicate
    if predicate is None and plan.filters:
        predicate = filters_to_and(plan.filters)
    raw_pred = data.get("predicate")
    if predicate is None and isinstance(raw_pred, dict):
        predicate = dict_predicate_to_spec(raw_pred)
    validate_predicate(predicate)
    projections = plan.projections or [ProjectionSpec(field=name) for name in plan.select]
    migrated = plan.model_copy(
        update={
            "version": "1.1",
            "predicate": predicate,
            "projections": projections,
        }
    )
    _ = extra
    return migrated
