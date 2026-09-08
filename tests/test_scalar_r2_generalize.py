"""SCALAR R2 — ratio formatting, industrial sido scope, year percentile tails."""

from __future__ import annotations

from txt2sql.config import load_settings
from txt2sql.data.coverage import refresh_dataset_coverage
from txt2sql.planner.semantic_executor import _parse_percentile_tail
from txt2sql.semantic_plan.answer import format_semantic_answer
from txt2sql.semantic_plan.compiler import compile_semantic_plan
from txt2sql.semantic_plan.generator import try_heuristic_plan


def setup_module() -> None:
    refresh_dataset_coverage(load_settings())


def test_ratio_answer_fraction_includes_n_without_percent() -> None:
    plan = try_heuristic_plan("복천동에서 허가 후 2년 이내 준공된 건물 비율을 알려줘")
    assert plan is not None
    assert plan.ratios
    assert float(plan.ratios[0].multiplier) == 1.0
    answer = format_semantic_answer(
        "복천동에서 허가 후 2년 이내 준공된 건물 비율을 알려줘",
        plan=plan,
        rows=[{"within_2y_ratio": 0.9456, "n": 386}],
        row_count=1,
    )
    assert "0.9456" in answer
    assert "%" not in answer
    assert "386" in answer.replace(",", "")


def test_industrial_unnamed_adds_sido_a4_filter() -> None:
    plan = try_heuristic_plan("부산 산업단지 내부 건물의 평균 높이를 구해줘")
    assert plan is not None
    sql = compile_semantic_plan(plan).sql
    assert "A4" in sql
    assert "26%" in sql or "LIKE '26%" in sql or "LIKE \"26%" in sql


def test_industrial_list_uses_exists_not_join() -> None:
    from txt2sql.semantic_plan.models import (
        PlaceSpec,
        ScopeSpec,
        SemanticQueryPlan,
        SpatialRelationSpec,
        SpatialTargetSpec,
    )

    plan = SemanticQueryPlan(
        query_kind="list",
        entity="building",
        scope=ScopeSpec(place=PlaceSpec(name="부산", kind="sido")),
        select=["name", "legal_dong"],
        limit=20,
        spatial_relations=[
            SpatialRelationSpec(
                relation="intersects",
                target=SpatialTargetSpec(entity="industrial_complex"),
            )
        ],
        assumptions=["heuristic_plan"],
    )
    sql = compile_semantic_plan(plan).sql
    assert "EXISTS" in sql
    assert "JOIN" not in sql or sql.index("EXISTS") < sql.find("JOIN")


def test_dual_usage_class_avg_compare_filters() -> None:
    plan = try_heuristic_plan("해운대구 주거용과 상업용 건물의 평균 높이를 알려줘")
    assert plan is not None
    aliases = {a.alias for a in plan.aggregations}
    assert "resi_h" in aliases and "com_h" in aliases
    sql = compile_semantic_plan(plan).sql
    assert "FILTER" in sql
    assert "A29" in sql


def test_year_percentile_tail_parse() -> None:
    assert _parse_percentile_tail(
        "동래구에서 최근 준공 상위 10% 건물의 평균 높이를 알려줘"
    ) == (0.9, "approval_date", "height_m", "high")
    assert _parse_percentile_tail(
        "금정구에서 최근 준공 5% 건물의 평균 건축물면적을 알려줘"
    ) == (0.95, "approval_date", "building_area_m2", "high")
    assert _parse_percentile_tail(
        "동래구에서 가장 오래된 건물 1%의 평균 지상층수를 알려줘"
    ) == (0.01, "approval_date", "ground_floors", "low")
