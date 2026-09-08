"""Regression tests for count-mismatch fixes."""

from __future__ import annotations

from txt2sql.chart_qa import is_chart_series_filter_question
from txt2sql.intent_router import (
    _parse_floor_threshold,
    _route_building_structure,
    _route_d010_d198_pnu_match,
    _route_building_industrial_bas_overlap,
    _route_industrial_count,
    _route_place_usage_count,
    try_route,
)
from txt2sql.profile_qa import is_usage_overview_question
from txt2sql.query_ir.adapters import contract_to_query_ir, query_ir_to_semantic_plan
from txt2sql.query_ir.models import PredicateIR, QueryIR, ScopeIR
from txt2sql.semantic_plan.predicate_utils import effective_predicate


def test_or_predicate_survives_query_ir_adapter() -> None:
    ir = QueryIR(
        task="count",
        entity="building",
        scope=ScopeIR(place="금정구", place_kind="gu"),
        predicates=[
            PredicateIR(
                logical_group="or",
                children=[
                    PredicateIR(field="usage", operator="eq", value="단독주택"),
                    PredicateIR(field="usage", operator="eq", value="공동주택"),
                ],
            )
        ],
    )
    plan = query_ir_to_semantic_plan(ir)
    pred = effective_predicate(plan)
    assert pred is not None
    assert pred.op == "or"
    args = pred.args or []
    assert len(args) == 2
    assert all(node.op == "cmp" for node in args)


def test_floor_threshold_parses_hangul_yeol() -> None:
    assert _parse_floor_threshold("금정구에서 지상 열 층 넘는 건물 몇 개?") == (">", "10")


def test_structure_exact_match_for_full_name() -> None:
    routed = _route_building_structure("대연동 철근콘크리트구조 건물 수는?")
    assert routed is not None
    assert '"A11" = \'철근콘크리트구조\'' in routed.sql


def test_or_coalesce_in_contract_adapter() -> None:
    from txt2sql.query_understanding.contract import extract_contract

    ir = contract_to_query_ir(extract_contract("금정구에서 단독주택 또는 공동주택인 건물 수는?"))
    assert any(p.logical_group == "or" for p in ir.predicates)


def test_usage_count_uses_d198_for_covered_dong() -> None:
    """등록 D198 구의 단순 대분류 용도 건수는 D198+A25."""
    from txt2sql.domain import reset_d198_coverage, set_d198_coverage

    set_d198_coverage({"동래구": "AL_D198_26260_20250115"})
    try:
        routed = _route_place_usage_count("온천동에서 숙박시설 건물은 몇 채야?", conn=None)
        assert routed is not None
        assert "AL_D198" in routed.sql
        assert '"A25" = \'숙박시설\'' in routed.sql
    finally:
        reset_d198_coverage()


def test_industrial_count_uses_row_count_not_distinct_names() -> None:
    routed = _route_industrial_count("부산에 속한 산업단지는 총 몇 개인가?")
    assert routed is not None
    assert "COUNT(*)" in routed.sql
    assert "DISTINCT name" not in routed.sql


def test_building_industrial_bas_overlap_route() -> None:
    routed = _route_building_industrial_bas_overlap(
        "산업단지와 기초구역과 동시에 겹치는 건물 수를 알려줘"
    )
    assert routed is not None
    assert "COUNT(DISTINCT b.\"A1\")" in routed.sql
    assert "AL_D060" in routed.sql


def test_d010_d198_pnu_match_route() -> None:
    routed = _route_d010_d198_pnu_match(
        "금정구 D010과 D198을 PNU로 연결했을 때 매칭되는 건물 수를 알려줘"
    )
    assert routed is not None
    assert 'd."A2" = u."A2"' in routed.sql


def test_q476_routes_to_floor_count() -> None:
    routed = try_route("금정구에서 지상 열 층 넘는 건물 몇 개?")
    assert routed is not None
    assert routed.intent == "building_floor_count"
    assert '"A26" > 10' in routed.sql


def test_usage_overview_skips_explicit_count() -> None:
    q = "금정구에서 주요용도와 세부용도가 모두 있는 건물 수는?"
    assert not is_usage_overview_question(q)


def test_d198_permit_without_approval_route() -> None:
    from txt2sql.count_routes import match_priority_count_route

    hit = match_priority_count_route(
        "회동동에서 허가일은 있지만 사용승인일이 없는 건물 수는?"
    )
    assert hit is not None
    assert hit.intent == "d198_permit_without_approval_count"
    assert "A33" in hit.sql and "A34" in hit.sql


def test_wants_count_recognizes_confirm_and_find_patterns() -> None:
    from txt2sql.intent_router import _wants_count

    assert _wants_count("동래구에서 상업용인데 주요용도가 주거계열로 표시된 레코드가 있는지 확인해줘")
    assert _wants_count("금정구에서 사용승인일이 미래 날짜로 기록된 건물이 있는지 찾아줘")


def test_priority_routes_for_remaining_mismatch_cases() -> None:
    from txt2sql.count_routes import match_priority_count_route

    q137 = match_priority_count_route("부산에서 건축물면적이 0㎡ 이하인 레코드 수를 알려줘")
    assert q137 is not None
    assert q137.intent == "building_area_a12_nonpos_count"

    q290 = match_priority_count_route(
        "동래구에서 주요용도는 있는데 세부용도가 없는 건물 수를 알려줘"
    )
    assert q290 is not None
    assert q290.intent == "d198_usage_without_detail_count"

    q292 = match_priority_count_route(
        "동래구에서 상업용인데 주요용도가 주거계열로 표시된 레코드가 있는지 확인해줘"
    )
    assert q292 is not None
    assert q292.intent == "d198_usage_class_mismatch_count"

    q341 = match_priority_count_route(
        "금정구에서 사용승인일 형식이 날짜로 해석되지 않는 값이 있는지 확인해줘"
    )
    assert q341 is not None
    assert q341.intent == "d198_invalid_approval_format_count"

    q343 = match_priority_count_route(
        "금정구에서 사용승인일이 미래 날짜로 기록된 건물이 있는지 찾아줘"
    )
    assert q343 is not None
    assert q343.intent == "d198_future_approval_count"

    q438 = match_priority_count_route(
        "산업단지와 기초구역과 동시에 겹치는 건물 수를 알려줘"
    )
    assert q438 is not None
    assert q438.intent == "building_industrial_bas_overlap"

    q473 = match_priority_count_route(
        "금정구 건물명에 ' OR 1=1 -- 가 들어간 건물이 있는지 찾아줘"
    )
    assert q473 is not None
    assert q473.intent == "adversarial_building_name_count"


def test_count_only_display_followup_from_list_sql() -> None:
    from txt2sql.followup_qa import try_count_only_display_followup
    from txt2sql.session import SessionContext

    session = SessionContext()
    session.last_sql = (
        'SELECT "A24","A4" FROM "AL_D010_26_20250704" '
        'WHERE "A3" LIKE \'26260%\' AND NULLIF(TRIM("A16"::text), \'\')::float8 >= 50 '
        'ORDER BY "A16" ASC LIMIT 10'
    )
    routed = try_count_only_display_followup("표 말고 개수만 알려줘", session)
    assert routed is not None
    assert routed.intent == "followup_count_display"
    assert "COUNT(*)" in routed.sql
    assert '"A3" LIKE \'26260%\'' in routed.sql


def test_regression_guards_on_priority_count_routes() -> None:
    from txt2sql.count_routes import match_priority_count_route
    from txt2sql.intent_router import _wants_count

    assert match_priority_count_route(
        "동래구 주거용과 상업용 건물의 평균 높이 차이를 알려줘"
    ) is None
    assert match_priority_count_route(
        "산업단지별 겹치는 기초구역 수를 보여줘"
    ) is None
    hit = match_priority_count_route(
        "동래구에서 허가일이 미래 날짜인 레코드가 있는지 확인해줘"
    )
    assert hit is not None
    assert hit.intent == "d198_future_permit_count"
    assert '"A33"' in hit.sql
    assert not _wants_count(
        "금정구에서 GIS건물통합식별번호가 D198에 중복된 사례를 찾아줘"
    )


def test_grouped_count_not_scalar_route() -> None:
    from txt2sql.query_understanding.contract import extract_contract
    from txt2sql.semantic_plan.generator import _guess_kind, try_heuristic_plan

    qs = [
        "법정동코드별 건물 수를 집계해줘",
        "구·군별 건물 수를 보여줘",
        "구조별 건물 수를 전체 부산 기준으로 보여줘",
    ]
    for q in qs:
        c = extract_contract(q)
        assert c.query_kind == "group", q
        assert not c.wants_count, q
        assert _guess_kind(q) == "aggregate", q
        plan = try_heuristic_plan(q, contract=c)
        assert plan is not None, q
        assert plan.query_kind == "aggregate", q
        assert plan.group_by, q


def test_followup_scope_and_sort_delta() -> None:
    from txt2sql.semantic_plan.followup import (
        is_semantic_plan_followup,
        parse_followup_delta,
    )
    from txt2sql.session import SessionContext

    delta = parse_followup_delta("이번에는 금정구만")
    assert delta is not None
    assert delta.change_scope is not None
    assert delta.change_scope.place is not None
    assert delta.change_scope.place.name == "금정구"

    sort_delta = parse_followup_delta("높이 낮은 순으로 바꿔줘")
    assert sort_delta is not None
    assert sort_delta.change_sort is not None
    assert sort_delta.change_sort[0].direction == "asc"

    session = SessionContext()
    session.last_semantic_plan = {"query_kind": "list", "entity": "building"}
    session.last_sql = 'SELECT "A24" FROM "AL_D010_26_20250704" WHERE "A3" LIKE \'26410%\''
    assert is_semantic_plan_followup("이번에는 금정구만", session)


def test_building_in_dong_count_uses_distinct_a1() -> None:
    from txt2sql.spatial_templates import building_in_dong_count_sql

    sql = building_in_dong_count_sql("광안2동")
    assert 'COUNT(DISTINCT b."A1")' in sql


def test_union_usage_coalesce() -> None:
    from txt2sql.query_understanding.contract import extract_contract

    ir = contract_to_query_ir(
        extract_contract("동래구에서 공장과 창고시설을 합친 건수는?")
    )
    assert any(p.logical_group == "or" for p in ir.predicates)
