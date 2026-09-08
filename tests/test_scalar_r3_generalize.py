"""SCALAR R3 — day-gap stats, industrial area, oldest tail, ratios, corr, eval min."""

from __future__ import annotations

from txt2sql.config import load_settings
from txt2sql.data.coverage import refresh_dataset_coverage
from txt2sql.evaluation.evaluation_policy import (
    contexts_align,
    infer_gold_context,
    infer_pred_context,
    match_numbers_in_haystack,
    parse_numbers,
)
from txt2sql.planner.semantic_executor import _parse_percentile_tail
from txt2sql.semantic_plan.compiler import compile_semantic_plan
from txt2sql.semantic_plan.generator import try_heuristic_plan


def setup_module() -> None:
    refresh_dataset_coverage(load_settings())


def test_min_area_eval_context_aligns() -> None:
    q = "부산 건물의 최소 양수 건축물면적을 구해줘"
    gold = infer_gold_context(kind="scalar", gold="min_area=0.72", question=q)
    pred = infer_pred_context(
        kind="scalar",
        answer="부산광역시의 집계 결과입니다. min_building_area_m2 0.72, 건수 279,229.",
        sql='SELECT MIN(b."A12"::float8) AS "min_building_area_m2", COUNT(*) AS "n"',
        rows=None,
        question=q,
    )
    assert gold.metric == "min"
    assert pred is not None and pred.metric == "min"
    assert contexts_align(gold, pred)
    ok, _, reason = match_numbers_in_haystack(
        parse_numbers("min_building_area_m2 0.72, 건수 279229"),
        [0.72],
        gold_ctx=gold,
        pred_ctx=pred,
    )
    assert ok, reason


def test_max_h_rank_answer_aligns_as_scalar() -> None:
    q = "부산에서 가장 높은 건물 높이는 얼마야?"
    gold = infer_gold_context(kind="scalar", gold="max_h=411.6", question=q)
    pred = infer_pred_context(
        kind="scalar",
        answer="높이가 가장 높은 건물은 마린시티입니다. 높이 411.6m, 층수 101층.",
        sql='SELECT "A0" FROM "AL_D010" ORDER BY "A16" DESC LIMIT 1',
        rows=None,
        question=q,
    )
    assert gold.metric == "scalar_float"
    assert pred is not None
    assert contexts_align(gold, pred)


def test_industrial_area_avg_uses_st_area_geography() -> None:
    plan = try_heuristic_plan("부산 산업단지 면적의 평균을 구해줘")
    assert plan is not None
    assert plan.entity == "industrial_complex"
    assert not plan.requires_clarification
    from txt2sql.semantic_plan.validator import validate_semantic_plan

    validated = validate_semantic_plan(plan, "부산 산업단지 면적의 평균을 구해줘")
    assert validated.status == "ready", validated.errors
    sql = compile_semantic_plan(validated.plan).sql
    assert "ST_Area" in sql
    assert "geography" in sql
    assert "AL_D060" in sql
    assert "26%" in sql or "LIKE '26" in sql


def test_corr_n_matches_filter_population() -> None:
    plan = try_heuristic_plan("금정구 건축연령과 연면적의 상관계수를 계산해줘")
    assert plan is not None
    sql = compile_semantic_plan(plan).sql
    assert "CORR(" in sql
    assert "COUNT(*) FILTER" in sql


def test_permit_approval_day_gap_median() -> None:
    plan = try_heuristic_plan(
        "동래구에서 허가일부터 사용승인일까지의 일수 중앙값을 구해줘"
    )
    assert plan is not None
    assert any(a.alias == "median_days" for a in plan.aggregations)
    sql = compile_semantic_plan(plan).sql
    assert "PERCENTILE_CONT(0.5)" in sql
    assert "A34" in sql and "A33" in sql


def test_corr_age_gfa() -> None:
    plan = try_heuristic_plan("금정구 건축연령과 연면적의 상관계수를 계산해줘")
    assert plan is not None
    assert any(a.function == "corr" for a in plan.aggregations)
    sql = compile_semantic_plan(plan).sql
    assert "CORR(" in sql


def test_oldest_percentile_tail_parse() -> None:
    assert _parse_percentile_tail(
        "동래구에서 가장 오래된 건물 1%의 평균 지상층수를 알려줘"
    ) == (0.01, "approval_date", "ground_floors", "low")


def test_violation_ratio_uses_d010() -> None:
    plan = try_heuristic_plan("금정구 공동주택 중 위반건축물 비율을 알려줘")
    assert plan is not None
    assert plan.ratios
    sql = compile_semantic_plan(plan).sql
    assert "A20" in sql
    assert "AL_D010" in sql
    assert "d198" not in sql.lower() or "AL_D198" not in sql


def test_jibhap_ratio_among_young_buildings() -> None:
    plan = try_heuristic_plan(
        "동래구에서 20년 미만 건물 중 집합건축물 비율을 알려줘"
    )
    assert plan is not None
    assert plan.ratios
    sql = compile_semantic_plan(plan).sql
    assert "집합건축물" in sql
    assert '"A10"' in sql or "A10" in sql


def test_corr_eval_context_not_overridden_by_area() -> None:
    q = "금정구 건축연령과 연면적의 상관계수를 계산해줘"
    gold = infer_gold_context(
        kind="scalar", gold="corr_age_gfa=-0.1178; n=20966", question=q
    )
    pred = infer_pred_context(
        kind="scalar",
        answer="금정구의 집계 결과입니다. corr_age_gfa -0.1182, 건수 20966.",
        sql='SELECT CORR(...) AS "corr_age_gfa", COUNT(*) AS "n"',
        rows=None,
        question=q,
    )
    assert gold.metric == "corr"
    assert pred is not None and pred.metric == "corr"
    assert contexts_align(gold, pred)


def test_day_gap_plan_not_clarified_for_age_coverage() -> None:
    from txt2sql.semantic_plan.validator import validate_semantic_plan

    q = "동래구에서 허가일부터 사용승인일까지의 일수 중앙값을 구해줘"
    plan = try_heuristic_plan(q)
    assert plan is not None
    validated = validate_semantic_plan(plan, q)
    assert validated.status == "ready", validated.errors
    assert any(a.alias == "median_days" for a in validated.plan.aggregations)


def test_basic_zone_overlap_admin_uses_intersects() -> None:
    plan = try_heuristic_plan(
        "광안2동과 일부라도 겹치는 기초구역 면적의 합계를 구해줘"
    )
    assert plan is not None
    assert plan.entity == "basic_zone"
    assert plan.spatial_relations
    sql = compile_semantic_plan(plan).sql
    assert "ST_Intersects" in sql
    assert "SUM" in sql
