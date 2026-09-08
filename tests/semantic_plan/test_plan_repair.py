"""Plan repair from QueryContract (no Q-ID rules)."""

from __future__ import annotations

from txt2sql.query_understanding.contract import extract_contract
from txt2sql.semantic_plan.models import FilterSpec, SemanticQueryPlan
from txt2sql.semantic_plan.plan_repair import (
    apply_contract_operators,
    inject_missing_predicates,
    is_repairable,
    repair_plan_from_contract,
)


def _base_plan() -> SemanticQueryPlan:
    return SemanticQueryPlan.model_validate(
        {
            "version": "1.0",
            "query_kind": "count",
            "entity": "building",
            "scope": {"place": {"name": "금정구", "kind": "gu"}},
        }
    )


def test_is_repairable_predicate_codes() -> None:
    assert is_repairable(["RANGE_BOUND_DROPPED"])
    assert is_repairable(["P03", "missing_predicate"])
    assert not is_repairable(["compile_failed"])


def test_inject_missing_predicates_from_contract() -> None:
    q = "금정구 연면적 3000㎡ 이상 몇 채"
    contract = extract_contract(q)
    plan = _base_plan()
    repaired = inject_missing_predicates(plan, q, contract=contract)
    fields = {item.field for item in repaired.filters}
    assert "gross_floor_area_m2" in fields


def test_apply_contract_operators_group_and_count() -> None:
    q = "금정구 용도별 건물 수"
    contract = extract_contract(q)
    plan = _base_plan()
    repaired = apply_contract_operators(plan, contract)
    assert repaired.query_kind == "aggregate"
    assert repaired.group_by
    assert any(item.function == "count" for item in repaired.aggregations)


def test_repair_plan_from_contract_missing_predicate() -> None:
    q = "남구 창고시설 높이 40m 이상 몇 채"
    contract = extract_contract(q)
    plan = SemanticQueryPlan.model_validate(
        {
            "version": "1.0",
            "query_kind": "count",
            "entity": "building",
            "scope": {"place": {"name": "남구", "kind": "gu"}},
            "filters": [
                FilterSpec(field="usage", operator="eq", value="창고시설").model_dump()
            ],
        }
    )
    repaired = repair_plan_from_contract(
        plan, contract, ["missing_predicate", "P03"], q
    )
    fields = {item.field for item in repaired.filters}
    assert "height_m" in fields


def test_repair_boolean_not_dropped() -> None:
    q = "동래구 공장 또는 창고를 제외한 건물 수"
    contract = extract_contract(q)
    plan = SemanticQueryPlan.model_validate(
        {
            "version": "1.0",
            "query_kind": "count",
            "entity": "building",
            "scope": {"place": {"name": "동래구", "kind": "gu"}},
            "filters": [
                FilterSpec(field="usage", operator="eq", value="공장").model_dump()
            ],
        }
    )
    repaired = repair_plan_from_contract(
        plan, contract, ["BOOLEAN_NOT_DROPPED", "P04"], q
    )
    from txt2sql.semantic_plan.predicate_utils import effective_predicate, has_op

    pred = effective_predicate(repaired)
    assert has_op(pred, "not") or any(f.operator == "neq" for f in repaired.filters)


def test_contract_is_executable_query() -> None:
    from txt2sql.query_contract import contract_is_executable_query

    list_q = "광안동 숙박시설 목록(연면적 큰 순 15개)"
    assert contract_is_executable_query(extract_contract(list_q))
    meta_q = "사용 가능한 데이터셋 목록을 알려줘"
    assert not contract_is_executable_query(extract_contract(meta_q))
