"""MAIN485 P1 — list rank ASC, usage list, industrial early, field-scale compare."""

from __future__ import annotations

from txt2sql.config import load_settings
from txt2sql.data.coverage import refresh_dataset_coverage
from txt2sql.domain import looks_like_building_name_lookup
from txt2sql.intent_router import (
    _route_building_rank,
    _route_industrial_area_rank,
    _route_industrial_names,
)
from txt2sql.meta_qa import is_metadata_question
from txt2sql.semantic_plan.compiler import compile_semantic_plan
from txt2sql.semantic_plan.generator import try_heuristic_plan, _guess_kind


def setup_module() -> None:
    refresh_dataset_coverage(load_settings())


def test_find_threshold_is_list_not_count() -> None:
    q = "북구에서 높이 20m 이상이지만 지상층수는 5층 이하인 건물을 찾아줘"
    assert _guess_kind(q) == "list"
    plan = try_heuristic_plan(q)
    assert plan is not None
    assert plan.query_kind == "list"


def test_building_rank_asc_positive_area() -> None:
    routed = _route_building_rank(
        "사하구에서 건축물면적이 작은 양수값 기준 10개를 보여줘"
    )
    assert routed is not None
    assert "ASC" in routed.sql.upper()
    assert '"A12" > 0' in routed.sql or "A12 > 0" in routed.sql.replace('"', "")


def test_far_rank_not_vague_and_routed() -> None:
    from txt2sql.clarify_qa import check_ambiguity
    from txt2sql.config import load_settings
    from txt2sql.db import connect

    q = "수영구에서 용적률이 높은 건물 10개를 보여줘"
    with connect(load_settings().database_url) as conn:
        assert check_ambiguity(conn, q) is None
    routed = _route_building_rank(q)
    assert routed is not None
    assert "A18" in routed.sql


def test_detail_usage_not_name_lookup() -> None:
    assert looks_like_building_name_lookup(
        "부곡동의 일반음식점 건물명과 지번을 보여줘"
    ) is False
    assert looks_like_building_name_lookup(
        "온천동의 다세대주택 건물명과 지번을 보여줘"
    ) is False


def test_industrial_names_not_meta() -> None:
    q = "부산 산업단지 이름 목록을 보여줘"
    assert is_metadata_question(q) is False
    routed = _route_industrial_names(q)
    assert routed is not None
    assert "ILIKE '%산업단지%'" not in routed.sql


def test_industrial_area_rank() -> None:
    routed = _route_industrial_area_rank(
        "부산 산업단지 중 면적이 큰 순으로 10개를 보여줘"
    )
    assert routed is not None
    assert "ORDER BY" in routed.sql.upper()
    assert "LIMIT 10" in routed.sql


def test_height_vs_floors_scale() -> None:
    plan = try_heuristic_plan(
        "부산에서 높이가 지상층수의 10배보다 큰 레코드를 보여줘"
    )
    assert plan is not None
    assert any(
        f.value_field == "ground_floors" and f.value_scale == 10
        for f in plan.filters
    )
    sql = compile_semantic_plan(plan).sql
    assert "* 10" in sql or "*10" in sql
    assert "A26 >= 10" not in sql.replace(" ", "")


def test_null_presence_floors() -> None:
    plan = try_heuristic_plan(
        "부산에서 높이는 있는데 지상층수가 없는 건물을 보여줘"
    )
    assert plan is not None
    ops = {(f.field, f.operator) for f in plan.filters}
    assert ("height_m", "is_not_null") in ops
    assert ("ground_floors", "is_null") in ops


def test_detail_vs_usage_mismatch() -> None:
    plan = try_heuristic_plan(
        "금정구에서 세부용도가 아파트인데 주요용도가 공동주택이 아닌 레코드를 찾아줘"
    )
    assert plan is not None
    assert plan.query_kind == "list"
    sql = compile_semantic_plan(plan).sql
    assert "아파트" in sql
    assert "공동주택" in sql
    assert "NOT" in sql.upper()


def test_structure_share_pct_assumption() -> None:
    plan = try_heuristic_plan("구조별 건물 비율을 전체 대비 백분율로 보여줘")
    assert plan is not None
    assert "structure" in plan.group_by
    assert "group_share_pct" in (plan.assumptions or [])
