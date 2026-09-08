"""Place-scope policy: legal=A4, admin=BND, gu=sigungu_a3_prefix."""

from __future__ import annotations

from txt2sql.gazetteer import resolve_place_kind, sigungu_a3_prefix, uses_admin_boundary
from txt2sql.semantic_plan.compiler import compile_semantic_plan
from txt2sql.semantic_plan.generator import extract_plan_hints, try_heuristic_plan


def test_admin_dong_preferred_over_gu_in_correction() -> None:
    q = "연제구 건물 반경? 아니, 행정동 경계 안에 있는 건물 수를 묻는 거야: 연산1동"
    hints = extract_plan_hints(q)
    assert hints["place"] == "연산1동"
    assert hints["place_kind"] == "admin_dong"
    plan = try_heuristic_plan(q)
    assert plan is not None
    sql = compile_semantic_plan(plan).sql
    assert "BND_ADM_DONG_PG" in sql
    assert "연산1동" in sql
    assert 'ADM_NM" = ' in sql or "ADM_NM\" =" in sql


def test_gu_uses_a3_not_admin_boundary_name() -> None:
    plan = try_heuristic_plan("영도구 건물 수는?")
    assert plan is not None
    assert plan.scope and plan.scope.place and plan.scope.place.kind == "gu"
    sql = compile_semantic_plan(plan).sql
    assert "A3" in sql
    assert "BND_ADM_DONG_PG" not in sql


def test_legal_dong_uses_a4() -> None:
    plan = try_heuristic_plan("구서동 건물 수는?")
    assert plan is not None
    sql = compile_semantic_plan(plan).sql
    assert "A4" in sql
    assert "BND_ADM_DONG_PG" not in sql


def test_outside_distance_beyond_buffer() -> None:
    q = "연산5동 경계 250m 밖에 있는 연제구 건물 수를 알려줘"
    hints = extract_plan_hints(q)
    assert hints["distance_m"] == 250.0
    assert hints["distance_outside"] is True
    assert hints["place"] == "연산5동"
    plan = try_heuristic_plan(q)
    assert plan is not None
    sql = compile_semantic_plan(plan).sql.upper()
    assert "NOT ST_DWITHIN" in sql
    assert "26470" in sql


def test_sigungu_a3_prefix_busan_fallback() -> None:
    assert sigungu_a3_prefix("연제구") == "26470"
    assert resolve_place_kind("우1동", "우1동 행정동 내부") == "admin_dong"
    assert resolve_place_kind("우1동") == "admin_dong"
    assert uses_admin_boundary("우1동", question="우1동 행정동") is True
    assert uses_admin_boundary("우1동") is False
    assert uses_admin_boundary("구서동") is False


def test_numbered_admin_dong_simple_count_uses_a4() -> None:
    """단순 동 이름 count → A4 (P012 §8); BND는 공간 cue 있을 때만."""
    from txt2sql.intent_router import try_route

    routed = try_route("대저1동 건물 수를 알려줘")
    assert routed is not None
    assert "BND_ADM" not in routed.sql
    assert '"A4"' in routed.sql or "A4" in routed.sql
    plan = try_heuristic_plan("대저1동 건물 수를 알려줘")
    assert plan is not None
    sql = compile_semantic_plan(plan).sql
    assert "BND_ADM_DONG_PG" not in sql


def test_numbered_dong_inside_uses_bnd() -> None:
    plan = try_heuristic_plan("광안2동 안에 있는 건물은 몇 채야?")
    assert plan is not None
    sql = compile_semantic_plan(plan).sql
    assert "BND_ADM_DONG_PG" in sql


def test_numbered_dong_inside_short_cue_uses_bnd() -> None:
    """Q366/Q367: 「연산1동 안」「괴정1동 안의」→ BND (not A4)."""
    for q in (
        "연산1동 안 건물의 평균 지상층수를 알려줘",
        "괴정1동 안의 위반건축물 수를 알려줘",
    ):
        plan = try_heuristic_plan(q)
        assert plan is not None
        sql = compile_semantic_plan(plan).sql
        assert "BND_ADM_DONG_PG" in sql, q


def test_ambiguous_gu_pnu_uses_sido_context() -> None:
    from txt2sql.gazetteer import choose_sigungu_pnu_code

    codes = ["11140", "26110"]
    assert choose_sigungu_pnu_code(codes, default_sido="부산광역시") == "26110"
    assert choose_sigungu_pnu_code(codes, default_sido="서울특별시") == "11140"
    assert choose_sigungu_pnu_code(codes, question_sido="서울특별시", default_sido="부산광역시") == "11140"
    assert sigungu_a3_prefix("중구", sido="서울특별시") == "11140"
    assert sigungu_a3_prefix("중구", sido="부산광역시") == "26110"


def test_resolve_building_table_default() -> None:
    from txt2sql.dataset_tables import (
        DEFAULT_BASIC_ZONE_TABLE,
        DEFAULT_BUILDING_TABLE,
        resolve_basic_zone_table,
        resolve_building_table,
    )

    assert resolve_building_table() == DEFAULT_BUILDING_TABLE
    assert resolve_basic_zone_table() == DEFAULT_BASIC_ZONE_TABLE


def test_building_place_predicate_gu_uses_a3() -> None:
    from txt2sql.place_scope import building_place_predicate

    pred = building_place_predicate("금정구")
    assert "A3" in pred
    assert "26410" in pred


def test_resolve_place_scope_homonym_uses_entity_sigungu() -> None:
    from txt2sql.semantic_catalog.place_scope import PlaceEntity, resolve_place_scope

    binding = resolve_place_scope(
        PlaceEntity(name="중동", place_type="legal_dong", sigungu="해운대구", sido="부산광역시")
    )
    assert binding.sigungu == "해운대구"
    assert binding.sido == "부산광역시"
    assert binding.code is not None
    assert binding.code.startswith("26")


def test_compiler_uses_place_sigungu_for_dong_a3() -> None:
    from txt2sql.semantic_plan.models import PlaceSpec, ScopeSpec, SemanticQueryPlan

    plan = SemanticQueryPlan(
        query_kind="count",
        entity="building",
        scope=ScopeSpec(
            place=PlaceSpec(
                name="연산동",
                kind="legal_dong",
                sigungu="연제구",
                sido="부산광역시",
                code="26470",
            )
        ),
    )
    sql = compile_semantic_plan(plan).sql
    assert "A4" in sql
    assert "26470" in sql
    assert "A3" in sql
