from txt2sql.semantic_plan.generator import parse_plan_json, try_heuristic_plan
from txt2sql.semantic_plan.models import SemanticPlanGenerationError


def test_parse_rejects_physical_column() -> None:
    raw = '{"query_kind":"list","entity":"building","select":["A16"]}'
    try:
        parse_plan_json(raw)
    except SemanticPlanGenerationError:
        return
    raise AssertionError("physical column should be rejected")


def test_parse_rejects_sql() -> None:
    raw = '{"query_kind":"list","entity":"building","select":["name"],"assumptions":["SELECT * FROM t"]}'
    try:
        parse_plan_json(raw)
    except SemanticPlanGenerationError:
        return
    raise AssertionError("SQL leak should be rejected")


def test_heuristic_mvp_height_list() -> None:
    plan = try_heuristic_plan(
        "해운대구 아파트 중 높이 70m 이상인 건물 이름과 높이"
    )
    assert plan is not None
    assert plan.entity == "building"
    assert plan.scope is not None and plan.scope.place is not None
    assert plan.scope.place.name == "해운대구"
    fields = {item.field for item in plan.filters}
    assert "usage" in fields or "detail_usage" in fields
    assert "height_m" in fields
    assert "name" in plan.select
    assert "height_m" in plan.select


def test_heuristic_ambiguous_area() -> None:
    plan = try_heuristic_plan("면적이 가장 큰 건물")
    assert plan is not None
    assert plan.requires_clarification is True


def test_heuristic_rank_building_area_top1() -> None:
    plan = try_heuristic_plan("구서동에서 건물면적이 가장 큰 아파트는?")
    assert plan is not None
    assert plan.query_kind == "rank"
    assert plan.limit == 1
    assert plan.order_by
    assert plan.order_by[0].field == "building_area_m2"


def test_heuristic_boundary_and_distance() -> None:
    inside = try_heuristic_plan("연산동 안에 있는 공동주택")
    assert inside is not None
    assert inside.scope is not None
    assert inside.scope.spatial_mode == "boundary"

    near = try_heuristic_plan("구서동 주변 500m 이내에 있는 공동주택")
    assert near is not None
    assert near.spatial_relations
    assert near.spatial_relations[0].relation == "within_distance"
    assert near.spatial_relations[0].distance_m == 500

    station = try_heuristic_plan("구서역 주변 500m 이내 공동주택")
    assert station is not None
    assert station.requires_clarification is True
