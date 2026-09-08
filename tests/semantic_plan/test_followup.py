from txt2sql.semantic_plan.followup import (
    apply_followup_history,
    apply_plan_delta,
    is_semantic_plan_followup,
    parse_followup_delta,
)
from txt2sql.semantic_plan.models import (
    FilterSpec,
    PlaceSpec,
    ScopeSpec,
    SemanticQueryPlan,
)
from txt2sql.session import SessionContext


def _base_plan() -> SemanticQueryPlan:
    return SemanticQueryPlan(
        query_kind="rank",
        entity="building",
        scope=ScopeSpec(place=PlaceSpec(name="해운대구", kind="gu")),
        filters=[FilterSpec(field="usage", operator="eq", value="공동주택")],
        select=["name", "gross_floor_area_m2"],
        order_by=[],
        limit=20,
    )


def test_add_filter_height() -> None:
    delta = parse_followup_delta("그중 100m 이상만")
    assert delta is not None
    assert any(item.field == "height_m" for item in delta.add_filters)
    merged = apply_plan_delta(_base_plan(), delta)
    fields = {item.field for item in merged.filters}
    assert "usage" in fields
    assert "height_m" in fields


def test_change_sort_and_limit() -> None:
    sort_delta = parse_followup_delta("높이 순으로 바꿔")
    assert sort_delta is not None and sort_delta.change_sort
    assert sort_delta.change_sort[0].field == "height_m"
    merged = apply_plan_delta(_base_plan(), sort_delta)
    assert merged.query_kind == "rank"
    assert merged.order_by[0].field == "height_m"

    limit_delta = parse_followup_delta("10개만 보여줘")
    assert limit_delta is not None
    assert limit_delta.change_limit == 10
    merged2 = apply_plan_delta(merged, limit_delta)
    assert merged2.limit == 10


def test_followup_merge_keeps_ready_score() -> None:
    from txt2sql.semantic_plan.validator import validate_semantic_plan

    delta = parse_followup_delta("건물명과 지번도 같이")
    assert delta is not None
    merged = apply_plan_delta(_base_plan(), delta)
    assert "name" in merged.select
    assert "lot_address" in merged.select
    checked = validate_semantic_plan(merged, "건물명과 지번도 같이")
    assert checked.status == "ready"
    assert checked.score >= 0.85


def test_followup_gate_keeps_new_place_independent() -> None:
    session = SessionContext()
    session.last_semantic_plan = _base_plan().model_dump()
    session.last_semantic_plan_route = "semantic_plan_rank"
    session.last_route = "semantic_plan_rank"
    assert is_semantic_plan_followup("그중 높이 순으로", session)
    assert not is_semantic_plan_followup("금정구 아파트가 몇 채야?", session)


def test_followup_from_router_d010_sql() -> None:
    session = SessionContext()
    session.last_sql = (
        'SELECT "A0" FROM "AL_D010_26_20250704" '
        "WHERE \"A4\" LIKE '%해운대구%' AND \"A9\" = '공동주택' "
        'ORDER BY "A14" DESC NULLS LAST LIMIT 20;'
    )
    session.last_route = "building_rank_연면적"
    session.last_full_question = "해운대구 아파트 중 연면적이 큰 20개 보여줘"
    assert is_semantic_plan_followup("그중 높이 80m 이상만", session)
    assert is_semantic_plan_followup("10개만 보여줘", session)


def test_event_log_four_turn_and_undo() -> None:
    base = _base_plan()
    step1 = apply_followup_history("그중 100m 이상만", base, [])
    assert step1 is not None
    plan1, events1 = step1
    assert any(item.field == "height_m" for item in plan1.filters)

    step2 = apply_followup_history("높이 순으로 바꿔", base, events1)
    assert step2 is not None
    plan2, events2 = step2
    assert plan2.order_by and plan2.order_by[0].field == "height_m"

    step3 = apply_followup_history("10개만 보여줘", base, events2)
    assert step3 is not None
    plan3, events3 = step3
    assert plan3.limit == 10

    undone = apply_followup_history("직전 취소", base, events3)
    assert undone is not None
    plan4, events4 = undone
    assert plan4.query_kind == "rank"
    assert plan4.limit == 20
    assert len(events4) == len(events3) - 1

    reset = apply_followup_history("처음부터", base, events4)
    assert reset is not None
    assert reset[0].filters == base.filters
    assert reset[0].limit == base.limit


def test_remove_filter_rebuilds_predicate() -> None:
    from txt2sql.semantic_plan.followup import apply_plan_events, parse_followup_events
    from txt2sql.semantic_plan.models import FilterSpec, SemanticQueryPlan

    base = SemanticQueryPlan.model_validate(
        {
            "query_kind": "list",
            "entity": "building",
            "filters": [
                FilterSpec(
                    field="gross_floor_area_m2", operator="gte", value=10000
                ).model_dump(),
                FilterSpec(field="height_m", operator="gte", value=50).model_dump(),
            ],
        }
    )
    events = parse_followup_events("연면적 조건은 빼줘")
    assert any(ev.op == "remove_filter" for ev in events)
    plan = apply_plan_events(base, events)
    fields = {item.field for item in plan.filters}
    assert "gross_floor_area_m2" not in fields
    assert "height_m" in fields
    assert plan.predicate is not None
