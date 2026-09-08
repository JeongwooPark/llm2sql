"""PhysicalPlan → compiler dataset bridge + typed v2 failure."""

from __future__ import annotations

from txt2sql.planner.executor_adapter import build_execution_plan
from txt2sql.planner.logical import build_logical_plan
from txt2sql.planner.physical import PhysicalPlan, select_physical_plan
from txt2sql.planner.semantic_executor import (
    build_sqp,
    compile_sql_from_bundle,
    physical_to_dataset_assumptions,
    should_try_semantic_v2,
)
from txt2sql.query_ir.models import AggregationIR, QueryIR
from txt2sql.semantic_catalog.binding import SemanticBinding


def test_d198_binding_selects_d198_even_with_d010() -> None:
    ir = QueryIR(task="group", aggregations=[AggregationIR(function="count")])
    logical = build_logical_plan(ir)
    logical.status = "READY"
    logical.bindings = [
        SemanticBinding(
            concept="building.usage",
            dataset="building_attr_d198",
            physical_field="usage",
            grain="building_attr",
            confidence=0.8,
            reason="test",
        ),
        SemanticBinding(
            concept="building.height",
            dataset="building_gis_d010",
            physical_field="height_m",
            grain="building_unit",
            confidence=0.5,
            reason="test",
        ),
    ]
    physical = select_physical_plan(logical)
    assert physical.strategy == "D198_EXECUTOR"
    assert "d198_ledger" in physical_to_dataset_assumptions(physical, logical)


def test_physical_d198_forces_d198_table_in_sql() -> None:
    q = "금정구에서 2000년 이전 사용승인된 건물 수는?"
    bundle = build_execution_plan(q)
    assert should_try_semantic_v2(bundle) is True
    assert bundle.physical.strategy == "D198_EXECUTOR"
    plan = build_sqp(
        bundle.query_ir,
        question=q,
        physical=bundle.physical,
        logical=bundle.logical,
    )
    assert "d198_ledger" in (plan.assumptions or [])
    sql, meta = compile_sql_from_bundle(bundle, question=q)
    assert "AL_D198" in sql
    assert meta.get("physical_strategy") == "D198_EXECUTOR"


def test_physical_d010_strips_d198_ledger() -> None:
    q = "동래구 평균 높이는?"
    bundle = build_execution_plan(q)
    # Force D010 strategy regardless of heuristics.
    forced = PhysicalPlan(
        strategy="D010_EXECUTOR",
        logical=bundle.logical,
        cost=1.0,
        reasons=("forced_test",),
        covered_ops=bundle.physical.covered_ops,
        partial=False,
    )
    plan = build_sqp(
        bundle.query_ir,
        question=q,
        physical=forced,
        logical=bundle.logical,
    )
    assert "d198_ledger" not in (plan.assumptions or [])
    assert "d010_gis" in (plan.assumptions or [])


def test_central_grain_overrides_forced_d010_for_covered_usage() -> None:
    """Coverage+usage → D198 even if PhysicalPlan wrongly says D010 (P012 −15 root)."""
    from txt2sql.domain import reset_d198_coverage, set_d198_coverage

    set_d198_coverage({"금정구": "AL_D198_26410_20250115"})
    try:
        q = "금정구 단독주택은 몇 채야?"
        bundle = build_execution_plan(q)
        forced = PhysicalPlan(
            strategy="D010_EXECUTOR",
            logical=bundle.logical,
            cost=1.0,
            reasons=("forced_wrong_d010",),
            covered_ops=bundle.physical.covered_ops,
            partial=False,
        )
        plan = build_sqp(
            bundle.query_ir,
            question=q,
            physical=forced,
            logical=bundle.logical,
        )
        assert "d198_ledger" in (plan.assumptions or [])
        assert "d010_gis" not in (plan.assumptions or [])
    finally:
        reset_d198_coverage()


def test_typed_failure_gate_returns_none_path() -> None:
    # meta/list gated — should_try false
    ir = QueryIR(task="meta")
    logical = build_logical_plan(ir)
    logical.status = "READY"
    physical = select_physical_plan(logical)
    from txt2sql.planner.executor_adapter import ExecutionPlanBundle

    bundle = ExecutionPlanBundle(query_ir=ir, logical=logical, physical=physical)
    assert should_try_semantic_v2(bundle) is False
