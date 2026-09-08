"""MAIN485 P2: structure exact, non_violation AND, ROUND numeric, gu avg floors."""

from __future__ import annotations

from txt2sql.count_routes import match_priority_count_route
from txt2sql.domain import exact_structure_label
from txt2sql.intent_router import (
    fix_common_sql_mistakes,
    try_route,
    _route_building_structure,
    _route_sigungu_avg_ground_floors,
)


def test_exact_structure_label_full_suffix() -> None:
    assert exact_structure_label("부산 전체 철근콘크리트구조 건물 수는?") == "철근콘크리트구조"
    assert exact_structure_label("벽돌구조인데 위반건축물이 아닌 건물 수는?") == "벽돌구조"
    assert exact_structure_label("철근콘크리트 건물 수") is None
    assert exact_structure_label("남구 일반목구조 건물 수는?") is None


def test_citywide_structure_exact_count() -> None:
    routed = _route_building_structure("부산 전체 철근콘크리트구조 건물 수는?")
    assert routed is not None
    assert routed.intent == "building_structure_count"
    assert "\"A11\" = '철근콘크리트구조'" in routed.sql
    assert "ILIKE" not in routed.sql


def test_structure_and_violation_count() -> None:
    routed = try_route("철근콘크리트구조이면서 위반건축물인 건물 수를 알려줘")
    assert routed is not None
    assert "\"A11\" = '철근콘크리트구조'" in routed.sql
    assert "\"A20\" = 'Y'" in routed.sql


def test_non_violation_keeps_structure_and() -> None:
    hit = match_priority_count_route("벽돌구조인데 위반건축물이 아닌 건물 수는?")
    assert hit is not None
    assert hit.intent == "non_violation_building_count"
    assert "\"A11\" = '벽돌구조'" in hit.sql
    assert "IS DISTINCT FROM" in hit.sql


def test_sigungu_avg_ground_floors_route() -> None:
    routed = _route_sigungu_avg_ground_floors("구·군별 평균 지상층수를 보여줘")
    assert routed is not None
    assert "AVG(" in routed.sql
    assert "A26" in routed.sql
    assert "GROUP BY" in routed.sql


def test_round_double_rewritten_to_numeric() -> None:
    sql = 'SELECT ROUND(AVG("A26"), 2) AS x FROM t'
    out = fix_common_sql_mistakes(sql, question="구·군별 평균 지상층수를 보여줘")
    assert "::numeric" in out
    assert "ROUND((" in out or "ROUND ((" in out.replace(" ", "")


def test_dual_gu_median_route_needs_d198_tables(monkeypatch) -> None:
    from txt2sql import domain
    from txt2sql.intent_router import _route_dual_gu_approval_year_median

    monkeypatch.setitem(
        domain.D198_BY_GU,
        "금정구",
        "AL_D198_26410_20260715",
    )
    monkeypatch.setitem(
        domain.D198_BY_GU,
        "동래구",
        "AL_D198_26260_20260715",
    )
    routed = _route_dual_gu_approval_year_median(
        "금정구와 동래구의 준공연도 중앙값을 비교해줘"
    )
    assert routed is not None
    assert "PERCENTILE_CONT" in routed.sql
    assert "AL_D198_26410" in routed.sql
    assert "AL_D198_26260" in routed.sql
