"""Query contract and dataset grain policy tests."""

from __future__ import annotations

from txt2sql.dataset_grain import query_ir_needs_d198, resolve_dataset_grain
from txt2sql.domain import reset_d198_coverage
from txt2sql.query_contract import verify_task_output_alignment
from txt2sql.query_ir.models import PredicateIR, QueryIR, ScopeIR
from txt2sql.semantic_plan.models import SemanticQueryPlan


def setup_function() -> None:
    reset_d198_coverage()


def teardown_function() -> None:
    reset_d198_coverage()


def test_simple_usage_count_prefers_d010_grain() -> None:
    ir = QueryIR(
        task="count",
        entity="building",
        scope=ScopeIR(place="남구"),
        predicates=[PredicateIR(field="usage", operator="eq", value="창고시설")],
    )
    assert resolve_dataset_grain(ir, "남구 창고시설 몇 채야?") == "d010"
    assert not query_ir_needs_d198(ir, "남구 창고시설 몇 채야?")


def test_height_predicate_prefers_d010() -> None:
    ir = QueryIR(
        task="count",
        entity="building",
        scope=ScopeIR(place="부산진구"),
        predicates=[
            PredicateIR(field="usage", operator="eq", value="업무시설"),
            PredicateIR(field="height_m", operator="gte", value=40),
        ],
    )
    assert resolve_dataset_grain(ir, "부산진구 업무시설 높이 40m 이상 몇 채") == "d010"


def test_count_task_requires_count_sql() -> None:
    plan = SemanticQueryPlan.model_validate(
        {
            "version": "1.0",
            "query_kind": "count",
            "entity": "building",
            "scope": {"place": {"name": "금정구", "kind": "gu"}},
        }
    )
    assert not verify_task_output_alignment(plan, "SELECT COUNT(*) FROM t")
    assert verify_task_output_alignment(plan, "SELECT 1")
