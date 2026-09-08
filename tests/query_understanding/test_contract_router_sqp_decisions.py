"""Contract → Capability → Router/SQP 결정 케이스 A–F."""

from txt2sql.intent_router import try_route
from txt2sql.profile_qa import is_profile_question
from txt2sql.query_understanding.contract import extract_contract, merge_contract
from txt2sql.route_capability import (
    PROFILE,
    fully_supports,
    legacy_route_eligible,
    select_execution_path,
)
from txt2sql.semantic_plan.contract_verifier import verify_contract
from txt2sql.semantic_plan.generator import try_heuristic_plan


def test_case_a_simple_count_uses_router() -> None:
    q = "금정구 건축물은 몇 채야?"
    path = select_execution_path(q)
    assert path != "semantic_plan"
    routed = try_route(q)
    assert routed is not None
    assert legacy_route_eligible(routed.intent, extract_contract(q))


def test_case_b_group_multi_agg_uses_sqp() -> None:
    q = (
        "사상구 공장을 구조별로 나누고 "
        "건수와 평균 연면적을 구해서 "
        "평균 연면적 상위 6개를 보여줘"
    )
    contract = extract_contract(q)
    assert "structure" in contract.group_fields
    assert any(item.function == "count" for item in contract.aggregation_requests)
    assert any(item.function == "avg" for item in contract.aggregation_requests)
    assert contract.limit == 6
    assert select_execution_path(q) == "semantic_plan"
    plan = try_heuristic_plan(q, contract=contract)
    assert plan is not None
    assert "structure" in plan.group_by
    plan_fns = {item.function for item in plan.aggregations}
    assert "count" in plan_fns
    assert "avg" in plan_fns
    assert plan.limit == 6
    assert verify_contract(q, plan, contract=contract).ok is True


def test_case_c_conditional_ratio_uses_sqp() -> None:
    q = "15층 이상 건물 중 공동주택 비율"
    contract = extract_contract(q)
    assert contract.ratios
    assert contract.ratios[0].has_denominator
    assert select_execution_path(q) == "semantic_plan"
    plan = try_heuristic_plan(q, contract=contract)
    assert plan is not None
    assert plan.ratios
    assert plan.ratios[0].denominator_predicate is not None
    assert verify_contract(q, plan, contract=contract).ok is True


def test_case_d_percentile_uses_sqp_not_rank() -> None:
    q = "건물 높이 상위 10% 경계값"
    contract = extract_contract(q)
    assert contract.percentile_requests
    assert abs(contract.percentile_requests[0].percentile - 0.9) < 1e-9
    assert select_execution_path(q) == "semantic_plan"
    routed = try_route(q)
    if routed is not None:
        assert routed.intent.startswith("building_rank_")
        assert not legacy_route_eligible(routed.intent, contract)
    plan = try_heuristic_plan(q, contract=contract)
    assert plan is not None
    assert any(
        item.function == "percentile" and item.percentile is not None
        and abs(item.percentile - 0.9) < 1e-9
        for item in plan.aggregations
    )


def test_case_e_derived_metric_uses_typed_divide() -> None:
    q = "건축면적/연면적 비율의 평균"
    contract = extract_contract(q)
    assert contract.derived_metrics
    assert contract.derived_metrics[0].kind == "divide"
    assert select_execution_path(q) == "semantic_plan"
    plan = try_heuristic_plan(q, contract=contract)
    assert plan is not None
    divides = [
        item.expression
        for item in plan.aggregations
        if item.expression is not None and item.expression.kind == "divide"
    ]
    assert divides
    assert divides[0].left is not None and divides[0].left.field == "building_area_m2"
    assert divides[0].right is not None and divides[0].right.field == "gross_floor_area_m2"


def test_case_f_profile_does_not_execute_rank_projection() -> None:
    q = "해운대구 공동주택 용적률 상위 10개 이름과 높이"
    contract = extract_contract(q)
    assert is_profile_question(q)
    assert not fully_supports(PROFILE, contract)
    path = select_execution_path(q)
    assert path != "building_profile"
    # D198 커버 시 attr rank 라우트, 아니면 SQP.
    assert path in {"semantic_plan", "d198_attr_rank"}
    plan = try_heuristic_plan(q, contract=contract)
    assert plan is not None
    assert "name" in plan.select
    assert "height_m" in plan.select


def test_regression_simple_count_stays_router() -> None:
    assert select_execution_path("해운대구 건물 몇 채야") != "semantic_plan"


def test_regression_area_threshold_count_stays_router() -> None:
    q = "해운대구 공동주택 중 건축면적이 1000㎡ 이상인 건물 수"
    path = select_execution_path(q)
    assert path != "semantic_plan"
    routed = try_route(q)
    assert routed is not None
    assert routed.intent == "building_area_threshold_count"


def test_structure_plus_area_rejects_threshold() -> None:
    q = "사상구 공장 중 경량철골구조이고 연면적 1500㎡ 이상인 채수"
    contract = extract_contract(q)
    assert "structure" in {str(s.value) for s in contract.metrics}
    assert not legacy_route_eligible("building_area_threshold_count", contract)
    assert select_execution_path(q) == "semantic_plan"


def test_verify_result_count_rejects_multi_numeric() -> None:
    from txt2sql.semantic_plan.result_shape import verify_result

    contract = extract_contract("해운대구 건물 몇 채야")
    contract.query_kind = "count"
    bad = verify_result(contract, [{"a": 200.0, "b": 269.0, "c": 269.0}])
    assert bad.ok is False
    good = verify_result(contract, [{"cnt": 12}])
    assert good.ok is True


def test_verify_result_keeps_plan_list_despite_contract_count() -> None:
    from txt2sql.semantic_plan.models import SemanticQueryPlan
    from txt2sql.semantic_plan.result_shape import verify_result

    contract = extract_contract("해운대구 건물 몇 채야")
    contract.query_kind = "count"
    plan = SemanticQueryPlan(query_kind="list", entity="building", select=["name"])
    rows = [
        {"name": "A", "legal_dong": "해운대구"},
        {"name": "B", "legal_dong": "해운대구"},
    ]
    assert verify_result(contract, rows, plan=plan).ok is True


def test_verify_result_keeps_grouped_bins_despite_contract_count() -> None:
    from txt2sql.semantic_plan.models import AggregationSpec, SemanticQueryPlan
    from txt2sql.semantic_plan.result_shape import verify_result

    contract = extract_contract("금정구 사용승인 연도 구간별 공동주택 수")
    contract.query_kind = "count"
    plan = SemanticQueryPlan(
        query_kind="aggregate",
        entity="building",
        group_by=["decade"],
        aggregations=[AggregationSpec(function="count", alias="n")],
    )
    rows = [{"decade": 1990, "n": 10}, {"decade": 2000, "n": 20}]
    assert verify_result(contract, rows, plan=plan).ok is True


def test_infer_query_kind_bins_is_group() -> None:
    contract = extract_contract("대연동 공동주택 층수 구간별 건수")
    assert contract.fixed_bins
    assert contract.query_kind == "group"


def test_infer_query_kind_multi_agg_is_not_count() -> None:
    contract = extract_contract("해운대구 공동주택 평균 높이와 중앙값 높이, 건수")
    assert contract.query_kind != "count"


def test_infer_query_kind_simple_count_stays_count() -> None:
    contract = extract_contract("해운대구 건물 몇 채야")
    assert contract.query_kind == "count"


def test_merge_contract_keeps_place_adds_filter() -> None:
    prev = extract_contract("해운대구 공동주택 목록")
    delta = extract_contract("2000년 이후")
    merged = merge_contract(prev, delta)
    assert any("해운대" in str(item.text) for item in merged.places) or "해운대" in merged.question
    assert merged.wants_temporal or "2000" in merged.question


def test_bind_d198_vs_d010() -> None:
    from txt2sql.query_understanding.bind import bind_catalog, lookup_spatial_path

    d198 = bind_catalog("동래구 사용승인 2010년 이후 건물 수")
    assert any(item.id == "d198" for item in d198.datasets)
    spatial = bind_catalog("대연3동과 교차하는 기초구역은 몇 개야")
    assert spatial.spatial_path is not None
    assert lookup_spatial_path("building", "intersects", "basic_zone") is not None
