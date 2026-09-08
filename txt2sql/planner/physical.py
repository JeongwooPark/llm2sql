"""Physical plan selection from LogicalPlan (no NL re-interpretation)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from txt2sql.planner.logical import LogicalPlan

PhysicalStrategy = Literal[
    "FAST_SIMPLE_COUNT",
    "FAST_THRESHOLD",
    "D010_EXECUTOR",
    "D198_EXECUTOR",
    "BASIC_ZONE_EXECUTOR",
    "SPATIAL_EXECUTOR",
    "GENERIC_SQL_EXECUTOR",
]


@dataclass(frozen=True)
class PhysicalPlan:
    strategy: PhysicalStrategy
    logical: LogicalPlan
    cost: float
    reasons: tuple[str, ...] = ()
    covered_ops: tuple[str, ...] = ()
    partial: bool = False


def _collect_ops(node) -> list[str]:
    out = [node.op]
    for child in node.children:
        out.extend(_collect_ops(child))
    return out


def _has_op(ops: list[str], name: str) -> bool:
    return name in ops


_D198_STRONG_SLOTS = frozenset(
    {"detail_usage", "permit_date", "approval_date", "building_age_years"}
)


def _has_d198_strong_slot(ir) -> bool:
    if any(p.field in _D198_STRONG_SLOTS for p in ir.predicates):
        return True
    if ir.temporal is not None and (
        ir.temporal.field in _D198_STRONG_SLOTS or ir.temporal.age_years is not None
    ):
        return True
    return False


def _prefer_d010_for_main_usage(ir, *, question: str = "") -> bool:
    """Main usage without D198 need (no coverage / busan-wide) → D010."""
    from txt2sql.dataset_grain import query_ir_needs_d198

    if ir.task not in {"list", "rank", "count", "aggregate", "scalar"}:
        return False
    if not any(p.field == "usage" for p in ir.predicates):
        # also check nested OR children
        def _has_usage(preds) -> bool:
            for p in preds:
                if getattr(p, "field", None) == "usage":
                    return True
                kids = getattr(p, "children", None) or []
                if kids and _has_usage(kids):
                    return True
            return False

        if not _has_usage(ir.predicates or []):
            return False
    return not query_ir_needs_d198(ir, question)


def _ir_prefers_d198(ir, *, question: str = "") -> bool:
    from txt2sql.dataset_grain import query_ir_needs_d198

    return query_ir_needs_d198(ir, question)


def select_physical_plan(
    logical: LogicalPlan,
    *,
    question: str = "",
) -> PhysicalPlan:
    """Select an executor strategy from LogicalPlan only.

    Partial coverage is never returned as executable: if a candidate cannot
    cover all ops, fall through to GENERIC_SQL_EXECUTOR or mark unsupported.
    """
    if logical.status != "READY":
        return PhysicalPlan(
            strategy="GENERIC_SQL_EXECUTOR",
            logical=logical,
            cost=10_000,
            reasons=(f"not_ready:{logical.status}", *logical.reason_codes),
            covered_ops=(),
            partial=False,
        )

    ops = _collect_ops(logical.root)
    ir = logical.query_ir
    datasets = {b.dataset for b in logical.bindings}

    if _prefer_d010_for_main_usage(ir, question=question):
        return PhysicalPlan(
            strategy="D010_EXECUTOR",
            logical=logical,
            cost=2.0,
            reasons=("dataset_grain_d010_usage",),
            covered_ops=tuple(ops),
            partial=False,
        )

    # D198 binding wins for temporal/detail/permit — and covered main-usage.
    if "building_attr_d198" in datasets and not _prefer_d010_for_main_usage(
        ir, question=question
    ):
        return PhysicalPlan(
            strategy="D198_EXECUTOR",
            logical=logical,
            cost=3.0,
            reasons=("d198_binding",),
            covered_ops=tuple(ops),
            partial=False,
        )

    if _ir_prefers_d198(ir, question=question):
        return PhysicalPlan(
            strategy="D198_EXECUTOR",
            logical=logical,
            cost=3.0,
            reasons=("dataset_grain_d198",),
            covered_ops=tuple(ops),
            partial=False,
        )

    # Fast simple count: Scan + optional scope Filter + Aggregate(count) only
    simple_count = (
        ir.task == "count"
        and not ir.spatial
        and ir.temporal is None
        and len([p for p in ir.predicates if p.field and p.field not in {None}]) <= 1
        and not ir.dimensions
        and not any(a.function not in {"count"} for a in ir.aggregations)
    )
    if simple_count and not _has_op(ops, "SpatialFilter") and not _has_op(ops, "TemporalFilter"):
        # threshold vs place-only
        has_threshold = any(
            p.operator in {"gt", "gte", "lt", "lte", "between"} and p.field
            for p in ir.predicates
        )
        if has_threshold and len(ir.predicates) <= 2:
            return PhysicalPlan(
                strategy="FAST_THRESHOLD",
                logical=logical,
                cost=1.0,
                reasons=("simple_threshold_count",),
                covered_ops=tuple(ops),
                partial=False,
            )
        if not has_threshold:
            return PhysicalPlan(
                strategy="FAST_SIMPLE_COUNT",
                logical=logical,
                cost=0.5,
                reasons=("simple_count",),
                covered_ops=tuple(ops),
                partial=False,
            )

    if _has_op(ops, "SpatialFilter") or ir.spatial:
        return PhysicalPlan(
            strategy="SPATIAL_EXECUTOR",
            logical=logical,
            cost=5.0,
            reasons=("spatial_ops_present",),
            covered_ops=tuple(ops),
            partial=False,
        )

    if ir.entity == "basic_zone" or "basic_zone" in datasets:
        return PhysicalPlan(
            strategy="BASIC_ZONE_EXECUTOR",
            logical=logical,
            cost=4.0,
            reasons=("basic_zone_entity",),
            covered_ops=tuple(ops),
            partial=False,
        )

    # Ledger-oriented slots prefer D198; bare main usage stays on D010
    # unless central grain policy (coverage + usage) requires D198.
    if (
        _has_d198_strong_slot(ir)
        or (
            "building_attr_d198" in datasets
            and not _prefer_d010_for_main_usage(ir, question=question)
        )
        or _ir_prefers_d198(ir, question=question)
    ):
        return PhysicalPlan(
            strategy="D198_EXECUTOR",
            logical=logical,
            cost=3.0,
            reasons=("d198_slot_or_binding",),
            covered_ops=tuple(ops),
            partial=False,
        )

    if "building_gis_d010" in datasets or ir.entity == "building":
        return PhysicalPlan(
            strategy="D010_EXECUTOR",
            logical=logical,
            cost=2.0,
            reasons=("d010_binding_or_building",),
            covered_ops=tuple(ops),
            partial=False,
        )

    return PhysicalPlan(
        strategy="GENERIC_SQL_EXECUTOR",
        logical=logical,
        cost=8.0,
        reasons=("fallback_generic",),
        covered_ops=tuple(ops),
        partial=False,
    )


def reject_partial_execution(plan: PhysicalPlan) -> PhysicalPlan:
    """Hard guard: never execute a partial plan."""
    if plan.partial:
        raise RuntimeError("partial semantic coverage execution is forbidden")
    ops = set(_collect_ops(plan.logical.root))
    covered = set(plan.covered_ops)
    if covered and ops - covered:
        raise RuntimeError(f"partial coverage detected: missing={ops - covered}")
    return plan
