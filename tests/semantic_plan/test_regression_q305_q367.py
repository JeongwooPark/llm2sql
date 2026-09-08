"""Spot-check Q305-Q313 date anchor and Q366-Q367 BND scope."""

from txt2sql.config import load_settings
from txt2sql.db import connect, execute_query
from txt2sql.planner import build_execution_plan
from txt2sql.planner.semantic_executor import compile_sql_from_bundle
from txt2sql.semantic_plan.compiler import compile_semantic_plan
from txt2sql.semantic_plan.generator import try_heuristic_plan


def test_rel_years_use_reference_date_in_sql() -> None:
    plan = try_heuristic_plan("장전동에서 최근 10년 내 준공된 건물은 몇 채야?")
    assert plan is not None
    sql = compile_semantic_plan(plan).sql
    assert "DATE '2026-08-27'" in sql
    assert "CURRENT_DATE" not in sql


def test_q305_q306_q313_gold_counts() -> None:
    settings = load_settings()
    cases = [
        # D198 커버 픽스처 + frozen reference_date(2026-08-27) 기준.
        ("장전동에서 최근 10년 내 준공된 건물은 몇 채야?", 183),
        ("사직동에서 준공된 지 30년 이상 된 건물 수를 알려줘", 2533),
        ("서동에서 40년 이상 된 건물 수를 알려줘", 2838),
    ]
    with connect(settings.database_url) as conn:
        for q, gold in cases:
            bundle = build_execution_plan(q)
            sql, _ = compile_sql_from_bundle(bundle, question=q)
            assert "DATE '2026-08-27'" in sql, q
            rows = execute_query(conn, sql)
            count = int(rows[0].get("count") or rows[0].get("n") or rows[0].get("c") or 0)
            assert count == gold, (q, count, gold)


def test_q366_q367_bnd_scope() -> None:
    for q in (
        "연산1동 안 건물의 평균 지상층수를 알려줘",
        "괴정1동 안의 위반건축물 수를 알려줘",
    ):
        bundle = build_execution_plan(q)
        sql, _ = compile_sql_from_bundle(bundle, question=q)
        assert "BND_ADM_DONG_PG" in sql, q
