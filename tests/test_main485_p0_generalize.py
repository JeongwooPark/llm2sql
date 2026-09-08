"""MAIN485 P0 — day-gap, GROUP BY safety, percentile low-tail, industrial rank."""

from __future__ import annotations

from txt2sql.config import load_settings
from txt2sql.data.coverage import refresh_dataset_coverage
from txt2sql.domain import (
    extract_age_years,
    is_permit_approval_lag_question,
    looks_like_age_question,
)
from txt2sql.planner.semantic_executor import _parse_percentile_tail
from txt2sql.semantic_plan.compiler import compile_semantic_plan
from txt2sql.semantic_plan.generator import try_heuristic_plan


def setup_module() -> None:
    refresh_dataset_coverage(load_settings())


def test_permit_lag_not_building_age() -> None:
    q = "동래구에서 허가 후 사용승인까지 3년 이상 걸린 건물 수를 알려줘"
    assert is_permit_approval_lag_question(q)
    assert not looks_like_age_question(q)
    assert extract_age_years(q) is None
    plan = try_heuristic_plan(q)
    assert plan is not None
    assert any(a.startswith("permit_day_gap_gte:") for a in plan.assumptions)
    sql = compile_semantic_plan(plan).sql
    assert "A34" in sql and "A33" in sql
    assert "1095" in sql or "3 years" in sql.lower() or ">= 1095" in sql


def test_permit_lag_avg_days() -> None:
    plan = try_heuristic_plan(
        "수안동에서 허가 후 사용승인까지 걸린 평균 일수를 계산해줘"
    )
    assert plan is not None
    assert any(a.alias == "avg_days" for a in plan.aggregations)
    sql = compile_semantic_plan(plan).sql
    assert "AVG" in sql
    assert "A34" in sql and "A33" in sql


def test_count_without_column_order_by() -> None:
    plan = try_heuristic_plan("부산에서 건축물면적이 연면적보다 큰 건물을 찾아줘")
    assert plan is not None
    assert plan.query_kind == "list"
    sql = compile_semantic_plan(plan).sql
    assert "COUNT(*)" not in sql.upper() or "GROUP BY" in sql.upper()
    assert "A12" in sql and "A14" in sql


def test_industrial_park_building_count_rank() -> None:
    plan = try_heuristic_plan(
        "산업단지 내부 건물 수가 많은 상위 10개 단지를 보여줘"
    )
    assert plan is not None
    assert "group_by_industrial_name" in (plan.assumptions or [])
    sql = compile_semantic_plan(plan).sql
    assert "GROUP BY" in sql
    assert "park" in sql.lower() or "A8" in sql
    assert "ORDER BY" in sql
    assert "LIMIT 10" in sql or "LIMIT 10" in sql.replace("\n", " ")


def test_low_percentile_tail_parse() -> None:
    assert _parse_percentile_tail(
        "금정구에서 준공연도가 빠른 하위 10% 건물의 평균 연면적을 구해줘"
    ) == (0.1, "approval_date", "gross_floor_area_m2", "low")


def test_legal_dong_ratio_rank() -> None:
    plan = try_heuristic_plan(
        "금정구에서 공동주택 비율이 가장 높은 법정동 5곳을 보여줘"
    )
    assert plan is not None
    assert "legal_dong" in plan.group_by
    assert plan.limit == 5
    sql = compile_semantic_plan(plan).sql
    assert "GROUP BY" in sql
    assert "ORDER BY" in sql
    assert "LIMIT 5" in sql


def test_day_gap_list_order() -> None:
    plan = try_heuristic_plan(
        "동래구에서 허가 후 준공까지 걸린 기간이 가장 긴 건물 10개를 보여줘"
    )
    assert plan is not None
    assert any(a.startswith("order_by_day_gap:") for a in plan.assumptions)
    sql = compile_semantic_plan(plan).sql
    assert "ORDER BY" in sql
    assert "A34" in sql and "A33" in sql


def test_find_question_is_list_not_count() -> None:
    from txt2sql.query_understanding.contract import extract_contract

    c = extract_contract("부산에서 건축물면적이 연면적보다 큰 건물을 찾아줘")
    assert c.wants_count is False
    assert c.query_kind in {"list", "rank"}
    plan = try_heuristic_plan("부산에서 건축물면적이 연면적보다 큰 건물을 찾아줘")
    assert plan is not None
    assert plan.query_kind == "list"


def test_permit_lag_validator_ready() -> None:
    from txt2sql.semantic_plan.validator import validate_semantic_plan

    q = "동래구에서 허가 후 사용승인까지 3년 이상 걸린 건물 수를 알려줘"
    plan = try_heuristic_plan(q)
    assert plan is not None
    validated = validate_semantic_plan(plan, q)
    assert validated.status == "ready", validated.errors


def test_day_gap_list_quality_above_min() -> None:
    from txt2sql.config import load_settings
    from txt2sql.semantic_plan.validator import validate_semantic_plan

    q = "동래구에서 허가 후 준공까지 걸린 기간이 가장 긴 건물 10개를 보여줘"
    plan = try_heuristic_plan(q)
    assert plan is not None
    validated = validate_semantic_plan(plan, q)
    assert validated.status == "ready"
    assert validated.score >= load_settings().semantic_plan_min_quality


def test_positive_day_gap_list() -> None:
    plan = try_heuristic_plan(
        "금정구에서 허가 후 준공까지 걸린 기간이 가장 짧은 양수 기간 건물 10개를 보여줘"
    )
    assert plan is not None
    assert "permit_day_gap_gte:1" in (plan.assumptions or [])
    sql = compile_semantic_plan(plan).sql
    assert "A34" in sql and "A33" in sql
    assert ">= 1" in sql or "> 0" in sql


def test_field_compare_not_named_dataset() -> None:
    from txt2sql.named_dataset_qa import _named_dataset_ineligible

    q = "부산에서 건축물면적이 연면적보다 큰 건물을 찾아줘"
    assert _named_dataset_ineligible(q) is True


def test_usage_exclusion_list_orders_by_height() -> None:
    plan = try_heuristic_plan(
        "금정구에서 주거용인데 주요용도가 단독주택도 공동주택도 아닌 레코드를 찾아줘"
    )
    assert plan is not None
    assert plan.query_kind == "list"
    sql = compile_semantic_plan(plan).sql
    assert "A19" in sql or "height" in sql.lower()
    assert "ORDER BY" in sql.upper()


def test_rel_years_range_bound_accepted() -> None:
    from txt2sql.query_contract import verify_range_bounds
    from txt2sql.semantic_plan.compiler import compile_semantic_plan

    q = "금정구에서 최근 10년 내 준공 건물 수가 많은 법정동 5곳을 보여줘"
    plan = try_heuristic_plan(q)
    assert plan is not None
    sql = compile_semantic_plan(plan).sql
    assert verify_range_bounds(plan, sql) == []
