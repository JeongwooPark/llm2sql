"""SCALAR R1 generalizations — detail/usage_class/site_area/age/stats/topN."""

from __future__ import annotations

from txt2sql.config import load_settings
from txt2sql.data.coverage import refresh_dataset_coverage
from txt2sql.dataset_grain import needs_d198_building_grain
from txt2sql.evaluation.evaluation_policy import (
    infer_gold_context,
    infer_pred_context,
    contexts_align,
    match_numbers_in_haystack,
    parse_numbers,
)
from txt2sql.query_understanding.contract import extract_contract
from txt2sql.semantic_plan.compiler import compile_semantic_plan
from txt2sql.semantic_plan.generator import try_heuristic_plan
from txt2sql.semantic_plan.plan_sql_verifier import verify_plan_to_sql


def setup_module() -> None:
    refresh_dataset_coverage(load_settings())


def test_detail_usage_only_no_main_usage_filter() -> None:
    plan = try_heuristic_plan("안락동 다가구주택의 평균 높이를 알려줘")
    assert plan is not None
    assert any(f.field == "detail_usage" and f.value == "다가구주택" for f in plan.filters)
    assert not any(f.field == "usage" for f in plan.filters)
    sql = compile_semantic_plan(plan).sql
    assert "A27" in sql
    assert "A25" not in sql


def test_usage_class_residential_needs_d198() -> None:
    q = "동래구에서 주거용 건물의 평균 연면적을 알려줘"
    assert needs_d198_building_grain(q) is True
    plan = try_heuristic_plan(q)
    assert plan is not None
    assert any(f.field == "usage_class" and f.value == "주거용" for f in plan.filters)
    sql = compile_semantic_plan(plan).sql
    assert "A29" in sql
    assert "AL_D198" in sql


def test_site_area_sum_uses_d010() -> None:
    plan = try_heuristic_plan("강서구 대지면적 합계를 알려줘")
    assert plan is not None
    assert plan.query_kind == "aggregate"
    assert any(a.function == "sum" and a.field == "site_area_m2" for a in plan.aggregations)
    assert "d198_ledger" not in (plan.assumptions or [])
    sql = compile_semantic_plan(plan).sql
    assert "A15" in sql
    assert "AL_D010" in sql


def test_building_age_avg_uses_day_fraction() -> None:
    q = "금정구에서 사용승인일이 기록된 건물의 평균 건축연령은?"
    contract = extract_contract(q)
    assert any(m.value == "building_age_years" for m in contract.metrics)
    assert not any(m.value == "approval_date" for m in contract.metrics)
    plan = try_heuristic_plan(q)
    assert plan is not None
    assert any(a.field == "building_age_years" for a in plan.aggregations)
    sql = compile_semantic_plan(plan).sql.upper()
    assert "365.25" in sql
    assert "AVG(" in sql


def test_multi_percentile_and_variance_aggregate() -> None:
    pctl = try_heuristic_plan("부산 연면적의 25%, 50%, 75% 분위수를 알려줘")
    assert pctl is not None
    assert pctl.query_kind == "aggregate"
    assert len([a for a in pctl.aggregations if a.function == "percentile"]) >= 3
    sql = compile_semantic_plan(pctl).sql.upper()
    assert "PERCENTILE_CONT" in sql

    var = try_heuristic_plan("부산 건축물면적의 분산을 알려줘")
    assert var is not None
    assert var.query_kind == "aggregate"
    assert any(a.function == "variance" for a in var.aggregations)
    vsql = compile_semantic_plan(var).sql.upper()
    assert "VAR_POP" in vsql


def test_basic_zone_sum_not_rank() -> None:
    plan = try_heuristic_plan("부산 기초구역 면적의 합계를 구해줘")
    assert plan is not None
    assert plan.entity == "basic_zone"
    assert plan.query_kind == "aggregate"
    assert any(a.function == "sum" and a.field == "area_m2" for a in plan.aggregations)
    sql = compile_semantic_plan(plan).sql
    assert "SUM(" in sql.upper()
    assert "LIMIT 1" not in sql.upper()


def test_topn_gfa_then_avg_height_stages() -> None:
    q = "금정구 아파트 중 연면적 상위 10개의 평균 높이를 구해줘"
    plan = try_heuristic_plan(q)
    assert plan is not None
    assert plan.stages
    assert plan.stages[0].order_by[0].field == "gross_floor_area_m2"
    assert plan.stages[0].limit == 10
    assert plan.stages[1].aggregations[0].field == "height_m"
    compiled = compile_semantic_plan(plan)
    assert not verify_plan_to_sql(plan, compiled)
    assert compiled.sql.upper().startswith("WITH")
    assert "A19" in compiled.sql  # D198 GFA
    assert "A30" in compiled.sql  # height


def test_basic_zone_sum_eval_context_aligns() -> None:
    q = "부산 기초구역 면적의 합계를 구해줘"
    gold = infer_gold_context(kind="scalar", gold="sum_ar=771.8849", question=q)
    pred = infer_pred_context(
        kind="scalar",
        answer="부산광역시의 집계 결과입니다. sum_area_m2 771.8849, 건수 2,302.",
        sql='SELECT SUM(z."BAS_AR"::float8) AS "sum_area_m2", COUNT(*) AS "n" FROM "TL_KODIS_BAS_26_202507" z',
        rows=None,
        query_ir=None,
    )
    assert contexts_align(gold, pred)
    ok, _, reason = match_numbers_in_haystack(
        parse_numbers("sum_area_m2 771.8849, 건수 2,302."),
        [771.8849],
        gold_ctx=gold,
        pred_ctx=pred,
    )
    assert ok, reason


def test_parse_numbers_metric_kv_not_group_labels() -> None:
    assert parse_numbers("p25=10.5; p50=20; p75=30; n=100")[:3] == [10.5, 20.0, 30.0]
    # group gold keeps rank indices via full NUM_RE
    nums = parse_numbers("1. gu=해운대구, n=1,288 / 2. gu=부산진구, n=762")
    assert 1.0 in nums and 1288.0 in nums and 2.0 in nums
