"""Round3 고레버리지 회귀: list ORDER/rank「많은」·IS DISTINCT FROM·detail_usage 평균."""

from __future__ import annotations

from txt2sql.d198_attrs import parse_d198_question
from txt2sql.domain import (
    extract_detail_usages,
    extract_usages,
    d198_gu_for_dong,
)
from txt2sql.semantic_plan.compiler import compile_semantic_plan
from txt2sql.semantic_plan.generator import try_heuristic_plan
from txt2sql.semantic_plan.models import (
    FilterSpec,
    PlaceSpec,
    ScopeSpec,
    SemanticQueryPlan,
)


def test_많은_층수_is_rank_with_order() -> None:
    q = "문현동에서 지상층수가 많은 건물 10개를 보여줘"
    plan = try_heuristic_plan(q)
    assert plan is not None
    assert plan.query_kind == "rank"
    assert plan.order_by and plan.order_by[0].field == "ground_floors"
    assert plan.order_by[0].direction == "desc"
    assert plan.limit == 10


def test_list_default_orders_by_id() -> None:
    q = "해운대구 건물명과 지번을 20개만 보여줘"
    plan = try_heuristic_plan(q)
    assert plan is not None
    assert plan.query_kind == "list"
    assert plan.order_by and plan.order_by[0].field == "id"
    assert plan.limit == 20


def test_list_numeric_filter_orders_by_metric() -> None:
    q = "부산진구에서 높이 30m 이상이고 지상 10층 이상인 건물을 보여줘"
    plan = try_heuristic_plan(q)
    assert plan is not None
    assert plan.query_kind == "list"
    assert plan.order_by
    assert plan.order_by[0].field in {"height_m", "ground_floors"}


def test_violation_neq_compiles_is_distinct_from() -> None:
    plan = SemanticQueryPlan(
        query_kind="count",
        entity="building",
        scope=ScopeSpec(place=PlaceSpec(name="부산광역시", kind="sido")),
        filters=[FilterSpec(field="violation_status", operator="neq", value="Y")],
    )
    compiled = compile_semantic_plan(plan)
    assert "IS DISTINCT FROM" in compiled.sql
    assert "<>" not in compiled.sql.split("WHERE", 1)[-1]


def test_아파트_is_detail_usage_not_공동주택() -> None:
    q = "구서동 아파트의 평균 높이를 알려줘"
    assert extract_detail_usages(q) == ["아파트"]
    plan = try_heuristic_plan(q)
    assert plan is not None
    assert plan.query_kind == "aggregate"
    assert any(f.field == "detail_usage" and f.value == "아파트" for f in plan.filters)
    assert not any(f.field == "usage" for f in plan.filters)
    assert "d198_ledger" in (plan.assumptions or [])
    assert plan.scope and plan.scope.place and plan.scope.place.name == "금정구"


def test_해운대_아파트_uses_d198_detail_when_covered() -> None:
    q = "해운대구 아파트 중 높이 70m 이상인 건물 이름과 높이"
    plan = try_heuristic_plan(q)
    assert plan is not None
    fields = {item.field for item in plan.filters}
    assert "detail_usage" in fields
    assert "height_m" in fields
    assert "d198_ledger" in (plan.assumptions or [])


def test_오피스텔_detail_and_dong_gu() -> None:
    assert extract_detail_usages("서동에서 오피스텔 건물은 몇 채야?") == ["오피스텔"]
    assert d198_gu_for_dong("서동") == "금정구"
    plan = try_heuristic_plan("서동에서 오피스텔 건물은 몇 채야?")
    assert plan is not None
    assert any(f.field == "detail_usage" and f.value == "오피스텔" for f in plan.filters)
    assert "d198_ledger" in (plan.assumptions or [])


def test_다가구주택_does_not_map_to_단독주택() -> None:
    q = "부곡동에서 다가구주택 건물은 몇 채야?"
    assert extract_detail_usages(q) == ["다가구주택"]
    assert "단독주택" not in extract_usages(q)


def test_d198_between_건폐율() -> None:
    q = "수영구에서 건폐율 40% 이상 70% 이하인 건물 수는?"
    parsed = parse_d198_question(q)
    assert parsed is not None
    joined = " ".join(parsed.filters)
    assert "BETWEEN" in joined.upper()
    assert "40" in joined and "70" in joined


def test_d198_rank_building_area_top1() -> None:
    q = "구서동에서 건물면적이 가장 큰 아파트는?"
    parsed = parse_d198_question(q)
    assert parsed is not None
    assert parsed.rank is True
    assert parsed.order_col == "A18"


def test_제2종_평균_uses_usage_on_covered_dong() -> None:
    q = "남산동 제2종근린생활시설의 평균 높이를 알려줘"
    plan = try_heuristic_plan(q)
    assert plan is not None
    assert plan.query_kind == "aggregate"
    assert any(f.field == "usage" and f.value == "제2종근린생활시설" for f in plan.filters)
    # Covered dong + main usage → D198 A25 (MAIN485 grain contract).
    assert "d198_ledger" in (plan.assumptions or [])
