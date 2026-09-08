from txt2sql.query_understanding.contract import extract_contract
from txt2sql.query_understanding.gate import accept_heuristic_plan
from txt2sql.semantic_plan.catalog import get_field
from txt2sql.semantic_plan.generator import try_heuristic_plan
from txt2sql.semantic_plan.models import (
    AggregationSpec,
    OperandSpec,
    PredicateSpec,
    PlaceSpec,
    ScopeSpec,
    SemanticQueryPlan,
)
from txt2sql.semantic_plan.contract_verifier import verify_contract
from txt2sql.route_capability import SIMPLE_COUNT, missing_slots


def test_outputs_are_actually_bound() -> None:
    c = extract_contract("해운대구 건물 이름과 높이를 보여줘")
    assert c.outputs
    assert c.all_requested_outputs_bound is True
    assert {item.value for item in c.outputs} >= {"name", "height_m"}


def test_or_operands_are_recorded() -> None:
    c = extract_contract("수영구 숙박시설 또는 위락시설 중 연면적 1000㎡ 이상")
    ors = [item for item in c.boolean_ops if item.kind == "or"]
    assert ors
    assert ors[0].meta.get("left")
    assert ors[0].meta.get("right")


def test_greedy_numeric_binding_does_not_reuse_metric() -> None:
    c = extract_contract("높이 40m 이상 층수 12층 이상 연면적 4000㎡ 이상")
    fields = [item.meta.get("field") for item in c.numbers]
    assert "height_m" in fields
    assert "ground_floors" in fields
    assert "gross_floor_area_m2" in fields


def test_nested_or_passes_gate_and_verifier() -> None:
    q = "연제구 공동주택 또는 단독주택이면서 높이 30m 이상"
    pred = PredicateSpec(
        op="and",
        args=[
            PredicateSpec(
                op="or",
                args=[
                    PredicateSpec(
                        op="cmp",
                        operator="eq",
                        left=OperandSpec(kind="field", field="usage"),
                        right=OperandSpec(kind="literal", value="공동주택"),
                    ),
                    PredicateSpec(
                        op="cmp",
                        operator="eq",
                        left=OperandSpec(kind="field", field="usage"),
                        right=OperandSpec(kind="literal", value="단독주택"),
                    ),
                ],
            ),
            PredicateSpec(
                op="cmp",
                operator="gte",
                left=OperandSpec(kind="field", field="height_m"),
                right=OperandSpec(kind="literal", value=30),
            ),
        ],
    )
    plan = SemanticQueryPlan(
        query_kind="count",
        entity="building",
        scope=ScopeSpec(place=PlaceSpec(name="연제구", kind="gu")),
        predicate=pred,
    )
    contract = extract_contract(q)
    assert accept_heuristic_plan(contract, plan) is True
    result = verify_contract(q, plan)
    assert result.ok is True


def test_multi_aggregate_set_is_checked() -> None:
    q = "해운대구 건물 높이 합계와 평균"
    plan = SemanticQueryPlan(
        query_kind="aggregate",
        entity="building",
        scope=ScopeSpec(place=PlaceSpec(name="해운대구", kind="gu")),
        aggregations=[
            AggregationSpec(function="sum", field="height_m", alias="sum_height_m"),
            AggregationSpec(function="avg", field="height_m", alias="avg_height_m"),
        ],
    )
    assert accept_heuristic_plan(extract_contract(q), plan) is True
    assert verify_contract(q, plan).ok is True


def test_catalog_coverage_and_industrial() -> None:
    assert get_field("building", "building_coverage_ratio").column == "A17"
    assert get_field("building", "floor_area_ratio").column == "A18"
    assert get_field("building", "violation_status").column == "A20"
    assert get_field("industrial_complex", "name").column == "A8"


def test_legacy_route_blocked_when_or_present() -> None:
    c = extract_contract("수영구 숙박시설 또는 위락시설 채수")
    assert "or" in missing_slots(SIMPLE_COUNT, c)


def test_heuristic_does_not_drop_or_when_compare_present() -> None:
    q = "남구 공동주택 또는 단독주택 중 건축면적이 연면적보다 큰 건물"
    plan = try_heuristic_plan(q)
    assert plan is not None
    assert plan.predicate is not None
    assert plan.predicate.op == "or"
    assert any(item.value_field for item in plan.filters)


def test_try_route_yields_compound_or_to_plan() -> None:
    from txt2sql.route_capability import select_execution_path

    assert select_execution_path("수영구 숙박시설 또는 위락시설 채수") == "semantic_plan"


def test_decade_compiles_to_year_between() -> None:
    from txt2sql.semantic_plan.compiler import compile_semantic_plan

    plan = try_heuristic_plan("해운대구 1990년대 사용승인 건물 수")
    assert plan is not None
    assert any(item.field == "approval_date" for item in plan.filters)
    sql = compile_semantic_plan(plan).sql.upper()
    assert "BETWEEN" in sql
    assert "1990" in sql
    assert "1999" in sql


def test_age_filter_uses_calendar_year() -> None:
    from txt2sql.semantic_plan.compiler import compile_semantic_plan

    plan = try_heuristic_plan(
        "동구 공동주택 중 지어진지 20년 넘고 지상 10층 이상인 채수",
        reference_date="2025-07-04",
    )
    assert plan is not None
    sql = compile_semantic_plan(plan).sql
    # rel_years는 컴파일 시 frozen reference_date 앵커로 INTERVAL 전개된다.
    assert "INTERVAL '20 years'" in sql
    assert "DATE '" in sql
    assert "CURRENT_DATE" not in sql


def test_approval_decade_group_compiles() -> None:
    from txt2sql.semantic_plan.compiler import compile_semantic_plan

    q = "금정구 사용승인 연도 구간별 공동주택 수(1970s~2010s)"
    plan = try_heuristic_plan(q)
    assert plan is not None
    assert "approval_decade" in (plan.assumptions or [])
    sql = compile_semantic_plan(plan).sql
    assert "/ 10 * 10" in sql
    assert "1970" in sql
    assert "2019" in sql
    from txt2sql.query_understanding.temporal import parse_temporal_filters
    from txt2sql.semantic_plan.compiler import compile_semantic_plan

    filters = parse_temporal_filters("남구 공동주택 사용승인이 1980년 이상 1999년 이하")
    assert len(filters) == 1
    assert filters[0].operator == "between"
    assert filters[0].value == 1980
    assert filters[0].value2 == 1999
    plan = try_heuristic_plan("남구 공동주택 사용승인이 1980년 이상 1999년 이하이면서 10층 이상인 채수")
    assert plan is not None
    sql = compile_semantic_plan(plan).sql
    assert "1980" in sql and "1999" in sql


def test_year_tilde_and_area_range_compile() -> None:
    from txt2sql.semantic_plan.compiler import compile_semantic_plan

    q = "금정구 단독주택 중 사용승인 1970~1989년이고 건축면적 60~150㎡인 채수"
    plan = try_heuristic_plan(q)
    assert plan is not None
    fields = {item.field for item in plan.filters}
    assert "approval_date" in fields
    assert "building_area_m2" in fields
    sql = compile_semantic_plan(plan).sql
    assert "1970" in sql and "1989" in sql
    assert "60" in sql and "150" in sql


def test_building_age_uses_reference_date() -> None:
    plan = try_heuristic_plan(
        "금정구에서 30년 넘은 건물 몇 채",
        reference_date="2025-07-04",
    )
    assert plan is not None
    assert any(item.field == "approval_date" for item in plan.filters)


def test_unknown_terms_skip_count_tails() -> None:
    from txt2sql.clarify_qa import _unknown_terms
    from txt2sql.domain import extract_gu, extract_place

    q = "강서구 건물은 얼마나 되나요?"
    unknown = _unknown_terms(q, place=extract_place(q), gu=extract_gu(q))
    assert unknown == []


def test_count_how_many_routes_not_clarify() -> None:
    from txt2sql.intent_router import try_route

    routed = try_route("강서구 건물은 얼마나 되나요?")
    assert routed is not None
    assert "COUNT" in routed.sql.upper()
    assert routed.intent != "building_name_lookup"


def test_jungangdong_still_place_ambiguous() -> None:
    from txt2sql.domain import extract_place, extract_gu

    q = "중앙동 건물 몇 채"
    assert extract_place(q) == "중앙동"
    assert extract_gu(q) is None


def test_multi_area_defers_and_heuristic_keeps_both() -> None:
    from txt2sql.intent_router import should_defer_compound_to_plan, try_route
    from txt2sql.semantic_plan.compiler import compile_semantic_plan

    q = "해운대구 연면적 1000㎡ 이상 건축면적 200㎡ 이상인 건물 수"
    assert should_defer_compound_to_plan(q)
    from txt2sql.route_capability import select_execution_path

    assert select_execution_path(q) == "semantic_plan"
    plan = try_heuristic_plan(q)
    assert plan is not None
    fields = {item.field for item in plan.filters}
    assert "gross_floor_area_m2" in fields
    assert "building_area_m2" in fields
    sql = compile_semantic_plan(plan).sql
    assert '"A14"' in sql
    assert '"A12"' in sql


def test_named_industrial_sql_has_complex_name() -> None:
    from txt2sql.intent_router import try_route

    routed = try_route("명지국가산업단지 안 건물 수")
    assert routed is not None
    assert "명지국가산업단지" in routed.sql
    assert "ST_Intersects" in routed.sql


def test_usage_count_not_name_lookup() -> None:
    from txt2sql.domain import extract_usage, looks_like_building_name_lookup
    from txt2sql.intent_router import try_route

    q = "자동차관련시설 건물은 몇 채인가요?"
    assert extract_usage(q) == "자동차관련시설"
    assert looks_like_building_name_lookup(q) is False
    routed = try_route(q)
    if routed is not None:
        assert routed.intent != "building_name_lookup"


def test_or_ileona_and_not_exclude_compile() -> None:
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.query_understanding.gate import accept_heuristic_plan
    from txt2sql.query_understanding.contract import extract_contract

    q_or = "수영구 숙박시설 또는 위락시설 중 연면적 1000㎡ 이상"
    plan = try_heuristic_plan(q_or)
    assert plan is not None
    assert plan.predicate is not None
    assert plan.predicate.op == "or"
    assert accept_heuristic_plan(extract_contract(q_or), plan) is True
    sql = compile_semantic_plan(plan).sql
    assert " OR " in sql.upper()
    # Covered gu + usage → D198 A19; uncovered → D010 A14.
    assert '"A14"' in sql or '"A19"' in sql

    q_not = "해운대구에서 공장·창고를 제외한 건물 수"
    plan_not = try_heuristic_plan(q_not)
    assert plan_not is not None
    sql_not = compile_semantic_plan(plan_not).sql.upper()
    assert "NOT" in sql_not or "NOT IN" in sql_not or "<>" in sql_not


def test_basic_zone_rank_not_building() -> None:
    from txt2sql.intent_router import try_route

    routed = try_route("북구 기초구역 면적 최대")
    assert routed is not None
    assert routed.intent.startswith("bas_area")
    assert "TL_KODIS_BAS" in routed.sql


def test_citywide_building_count_not_meta() -> None:
    from txt2sql.intent_router import try_route
    from txt2sql.meta_qa import is_metadata_question

    q = "부산시 전체 건물 수"
    assert is_metadata_question(q) is False
    routed = try_route(q)
    assert routed is not None
    assert "COUNT" in routed.sql.upper()
    assert "AL_D010" in routed.sql


def test_meta_profile_force_blocked_for_citywide_count() -> None:
    from txt2sql.pipeline import _blocks_meta_profile_force
    from txt2sql.profile_qa import is_profile_question

    assert _blocks_meta_profile_force("부산시 전체 건물 수는?") is True
    assert is_profile_question("그 건물들의 평균 높이") is False


def test_medical_usage_is_not_out_of_scope() -> None:
    from txt2sql.guide_qa import try_guide
    from txt2sql.domain import extract_usage

    q = "의료시설 또는 노유자시설 건물 수"
    assert extract_usage(q) == "의료시설"
    guide = try_guide(q)
    assert guide is None or guide.intent != "guide_out_of_scope"


def test_weather_and_flight_are_out_of_scope() -> None:
    from txt2sql.guide_qa import try_guide

    rain = try_guide("내일 부산 강수량 예보 알려줘")
    assert rain is not None and rain.intent == "guide_out_of_scope"
    flight = try_guide("김해공항 항공편 지연 현황은?")
    assert flight is not None and flight.intent == "guide_out_of_scope"


def test_temporal_plus_floors_yields_to_plan() -> None:
    from txt2sql.intent_router import should_defer_compound_to_plan, try_route
    from txt2sql.semantic_plan.compiler import compile_semantic_plan

    q = "금정구에서 지어진지 20년 넘고 지상 10층 이상인 건물 수"
    assert should_defer_compound_to_plan(q)
    from txt2sql.route_capability import select_execution_path

    assert select_execution_path(q) == "semantic_plan"
    plan = try_heuristic_plan(q)
    assert plan is not None
    sql = compile_semantic_plan(plan).sql
    # Age cue → D198 (A31/A34) or legacy D010 (A26/A13).
    assert '"A26"' in sql or '"A31"' in sql
    fields = {item.field for item in plan.filters}
    assert "approval_date" in fields or "building_age_years" in fields
    assert "ground_floors" in fields


def test_decade_plus_floors_not_year_stats() -> None:
    from txt2sql.intent_router import try_route
    from txt2sql.semantic_plan.compiler import compile_semantic_plan

    q = "1990년대에 지어진 10층 이상 건물 수"
    routed = try_route(q)
    assert routed is None or routed.intent != "d198_year_stats"
    plan = try_heuristic_plan(q)
    assert plan is not None
    sql = compile_semantic_plan(plan).sql
    assert "1990" in sql
    assert '"A26"' in sql


def test_count_kind_from_trailing_su() -> None:
    plan = try_heuristic_plan(
        "금정구에서 연면적 5000㎡ 이상이고 15층 이상인 철근콘크리트 공동주택 수"
    )
    assert plan is not None
    assert plan.query_kind == "count"


def test_structure_or_binds_both() -> None:
    from txt2sql.semantic_plan.compiler import compile_semantic_plan

    q = "해운대구 철근콘크리트 또는 철골철근콘크리트 건물 수"
    plan = try_heuristic_plan(q)
    assert plan is not None
    assert plan.predicate is not None
    assert plan.predicate.op == "or"
    sql = compile_semantic_plan(plan).sql.upper()
    assert "철근콘크리트" in sql
    assert "철골철근콘크리트" in sql
    assert " OR " in sql


def test_bunyo_usage_is_known() -> None:
    from txt2sql.domain import extract_usage
    from txt2sql.clarify_qa import _unknown_terms
    from txt2sql.domain import extract_gu, extract_place

    q = "분뇨쓰레기처리시설 건물 수"
    assert extract_usage(q) == "분뇨쓰레기처리시설"
    assert _unknown_terms(q, place=extract_place(q), gu=extract_gu(q)) == []


def test_jungangdong_requires_gu() -> None:
    from txt2sql.domain import dong_requires_gu, extract_place, extract_gu
    from txt2sql.intent_router import try_route
    from txt2sql.spatial_router import try_spatial_route

    q = "중앙동 건물 몇 채야?"
    assert extract_place(q) == "중앙동"
    assert extract_gu(q) is None
    assert dong_requires_gu("중앙동")
    assert try_spatial_route(q) is None
    routed = try_route(q)
    assert routed is None or "중앙동" not in (routed.sql or "")


def test_bas_area_identity_not_value_only() -> None:
    from txt2sql.intent_router import try_route

    routed = try_route("북구 기초구역 면적이 가장 큰 것은?")
    assert routed is not None
    assert routed.intent == "bas_area_topn"
    assert "BAS_ID" in routed.sql


def test_university_name_compare_route() -> None:
    from txt2sql.intent_router import try_route
    from txt2sql.profile_qa import is_profile_question

    q = "부산대학교와 부경대학교 건물 수를 비교해줘"
    assert is_profile_question(q) is False
    routed = try_route(q)
    assert routed is not None
    assert routed.intent == "building_name_set_compare"
    assert "부산대학교" in routed.sql
    assert "부경대학교" in routed.sql


def test_filter_only_followup_forces_count() -> None:
    from txt2sql.semantic_plan.followup import apply_plan_delta, parse_followup_delta
    from txt2sql.semantic_plan.models import FilterSpec, PlaceSpec, ScopeSpec, SemanticQueryPlan
    from txt2sql.session import SessionContext

    dumped = {
        "query_kind": "list",
        "limit": 100,
        "select": ["name"],
        "entity": "building",
    }
    SessionContext._coerce_count_display_plan(
        dumped,
        'SELECT "A24", COUNT(*) OVER() AS total_n FROM t',
        "해운대구 공동주택 높이 50m 이상은 모두 675동입니다.",
    )
    assert dumped["query_kind"] == "count"
    assert dumped["limit"] is None

    base = SemanticQueryPlan(
        query_kind="count",
        entity="building",
        scope=ScopeSpec(place=PlaceSpec(name="해운대구", kind="gu")),
        filters=[
            FilterSpec(field="usage", operator="eq", value="공동주택"),
            FilterSpec(field="height_m", operator="gte", value=50),
        ],
    )
    delta = parse_followup_delta("그중 연면적 8000㎡ 이상만")
    assert delta is not None
    # 부모 count면 필터만 추가하고 kind는 유지(명시 개수 단서 없으면 change_kind 없음)
    assert delta.change_kind in {None, "count"}
    merged = apply_plan_delta(base, delta)
    assert merged.query_kind == "count"
    assert merged.limit is None
    assert any(item.field == "gross_floor_area_m2" for item in merged.filters)


def test_followup_rank_from_aggregate_clears_aggs() -> None:
    from txt2sql.semantic_plan.followup import apply_plan_delta, parse_followup_delta
    from txt2sql.semantic_plan.models import AggregationSpec, FilterSpec, PlaceSpec, ScopeSpec, SemanticQueryPlan

    base = SemanticQueryPlan(
        query_kind="aggregate",
        entity="building",
        scope=ScopeSpec(place=PlaceSpec(name="해운대구", kind="gu")),
        filters=[FilterSpec(field="usage", operator="eq", value="공동주택")],
        aggregations=[
            AggregationSpec(function="count", alias="n"),
            AggregationSpec(function="avg", field="height_m", alias="avg_h"),
        ],
    )
    delta = parse_followup_delta("그중 가장 높은 건물의 이름과 지번")
    assert delta is not None
    assert delta.change_kind == "rank"
    merged = apply_plan_delta(base, delta)
    assert merged.query_kind == "rank"
    assert merged.aggregations == []
    assert "name" in merged.select
    assert "lot_address" in merged.select


def test_followup_buildings_plural_is_not_anchor() -> None:
    from txt2sql.semantic_plan.followup import parse_followup_delta

    delta = parse_followup_delta("그 건물들 연면적 합계")
    assert delta is not None
    assert delta.change_kind == "aggregate"
    assert delta.change_limit is None
    fields = {item.field for item in (delta.change_aggregations or [])}
    assert "gross_floor_area_m2" in fields
    assert "height_m" not in fields


def test_followup_industrial_spatial_counts() -> None:
    from txt2sql.semantic_plan.followup import apply_plan_delta, parse_followup_delta
    from txt2sql.semantic_plan.models import PlaceSpec, ScopeSpec, SemanticQueryPlan

    delta = parse_followup_delta("그중 산업단지 안에 있는 것만")
    assert delta is not None
    assert delta.add_spatial
    base = SemanticQueryPlan(
        query_kind="count",
        entity="building",
        scope=ScopeSpec(place=PlaceSpec(name="해운대구", kind="gu")),
    )
    merged = apply_plan_delta(base, delta)
    assert merged.query_kind == "count"
    assert merged.spatial_relations


def test_rank_followup_selects_name() -> None:
    from txt2sql.semantic_plan.followup import parse_followup_delta

    delta = parse_followup_delta("연면적이 가장 큰 것의 법정동과 지번")
    assert delta is not None
    assert delta.change_kind == "rank"
    assert "name" in delta.add_select
    assert "lot_address" in delta.add_select
    assert "legal_dong" in delta.add_select


def test_height_particle_binds() -> None:
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import extract_plan_hints, try_heuristic_plan

    q = "광안동에서 높이가 20미터를 넘는 건물은?"
    hints = extract_plan_hints(q)
    assert any(item["field"] == "height_m" for item in hints["numeric_expressions"])
    plan = try_heuristic_plan(q)
    assert plan is not None
    sql = compile_semantic_plan(plan).sql
    assert '"A16"' in sql
    assert ">=" in sql or ">" in sql


def test_recorded_coverage_avg_skips_clarify() -> None:
    from txt2sql.config import Settings
    from txt2sql.semantic_plan.generator import generate_semantic_plan

    q = "연제구 건폐율이 기록된 공동주택의 평균 건폐율과 건수"
    plan = generate_semantic_plan(
        q,
        Settings(database_url="postgresql://x:x@localhost/x"),
        allow_llm=False,
    )
    assert plan.requires_clarification is False
    assert plan.aggregations


def test_poi_does_not_kill_citywide_count() -> None:
    from txt2sql.config import Settings
    from txt2sql.semantic_plan.generator import generate_semantic_plan
    from txt2sql.semantic_plan.validator import validate_semantic_plan

    q = "부산광역시 공동주택은 몇 채야?"
    plan = generate_semantic_plan(
        q,
        Settings(database_url="postgresql://x:x@localhost/x"),
        allow_llm=False,
    )
    assert plan.requires_clarification is False
    assert plan.query_kind == "count"
    assert plan.scope is not None and plan.scope.place is not None
    assert plan.scope.place.kind == "sido"
    checked = validate_semantic_plan(plan, q)
    assert "ambiguous_poi" not in checked.errors
    assert checked.status != "clarify"


def test_jungdong_does_not_require_gu() -> None:
    from txt2sql.domain import dong_requires_gu

    assert dong_requires_gu("중동") is False
    assert dong_requires_gu("중앙동") is True


def test_industrial_base_date_is_stats() -> None:
    from txt2sql.intent_router import try_route

    routed = try_route("산업단지 자료의 기준일은?")
    assert routed is not None
    assert "min_base" in routed.sql
    assert "AL_D060" in routed.sql


def test_lightweight_steel_not_all_steel() -> None:
    from txt2sql.domain import extract_structure
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    st = extract_structure("경량철골구조")
    assert st is not None
    assert "경량철골" in st[1]
    plan = try_heuristic_plan("사상구 공장 중 경량철골구조이고 연면적 1500㎡ 이상인 채수")
    assert plan is not None
    sql = compile_semantic_plan(plan).sql
    assert "경량철골" in sql
    assert sql.count("철골") >= 1


def test_far_gt_zero_and_lt_percent() -> None:
    from txt2sql.semantic_plan.compiler import compile_semantic_plan

    q = "기장군 용적율이 0보다 크고 80% 미만인 공장 채수"
    plan = try_heuristic_plan(q)
    assert plan is not None
    sql = compile_semantic_plan(plan).sql
    assert "80" in sql
    assert "< 0" not in sql.replace("< 0.", "")


def test_coverage_tilde_range_and_far() -> None:
    from txt2sql.semantic_plan.compiler import compile_semantic_plan

    q = "남구 공동주택 중 건폐율 20~50%이고 용적율 150% 이상인 채수"
    plan = try_heuristic_plan(q)
    assert plan is not None
    fields = {item.field for item in plan.filters}
    assert "building_coverage_ratio" in fields
    assert "floor_area_ratio" in fields
    sql = compile_semantic_plan(plan).sql
    # D010: A17/A18 · D198 (usage+coverage): A21/A20
    assert ("A17" in sql and "A18" in sql) or ("A21" in sql and "A20" in sql)


def test_multi_agg_keeps_avg_and_count() -> None:
    q = "연제구 건폐율이 기록된 공동주택의 평균 건폐율과 건수"
    plan = try_heuristic_plan(q)
    assert plan is not None
    fns = {item.function for item in plan.aggregations}
    assert "avg" in fns
    assert "count" in fns


def test_approval_year_filter_validates() -> None:
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import try_heuristic_plan
    from txt2sql.semantic_plan.validator import validate_semantic_plan

    q = "남구 공동주택 중 2000년 이후 사용승인이고 지상 15층 이상인 채수"
    plan = try_heuristic_plan(q)
    assert plan is not None
    assert any(item.field == "approval_date" for item in plan.filters)
    checked = validate_semantic_plan(plan, q)
    assert "numeric operator on text field: approval_date" not in checked.errors
    sql = compile_semantic_plan(plan).sql
    assert "2000" in sql
    # D010 A13/A26 or D198 A34/A31
    assert ("A13" in sql or "A34" in sql)
    assert ("A26" in sql or "A31" in sql)


def test_exclude_near_usage_not_legacy_height() -> None:
    from txt2sql.intent_router import try_route
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    q = "부산진구 제1·2종근린생활시설을 뺀 건물 중 높이 25m 이상인 채수"
    from txt2sql.route_capability import select_execution_path

    assert select_execution_path(q) == "semantic_plan"
    plan = try_heuristic_plan(q)
    assert plan is not None
    sql = compile_semantic_plan(plan).sql.upper()
    assert "NOT" in sql or "<>" in sql or "NOT IN" in sql


def test_basement_floors_bind() -> None:
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    q = "남구 아파트 또는 업무시설 중 지하 1층 이상이면서 지상 10층 이상인 채수"
    plan = try_heuristic_plan(q)
    assert plan is not None
    fields = {item.field for item in plan.filters}
    assert "basement_floors" in fields
    assert "ground_floors" in fields
    sql = compile_semantic_plan(plan).sql
    # D010 A27/A26 or D198 A32/A31 (detail/usage → ledger)
    assert '"A27"' in sql or '"A32"' in sql
    assert '"A26"' in sql or '"A31"' in sql


def test_d198_detail_usage_or_compiles() -> None:
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    q = "동래구 명륜동 오피스텔 또는 사무소(세부용도) 채수"
    plan = try_heuristic_plan(q)
    assert plan is not None
    assert plan.requires_clarification is False
    assert plan.predicate is not None and plan.predicate.op == "or"
    sql = compile_semantic_plan(plan).sql
    assert "AL_D198" in sql
    assert '"A27"' in sql
    assert "오피스텔" in sql
    assert "사무소" in sql
    assert "AL_D010" not in sql


def test_permit_decade_is_count_not_year_stats() -> None:
    from txt2sql.d198_attrs import looks_like_year_stats_question, parse_year_stats
    from txt2sql.intent_router import try_route

    q = "동래구 집합건축물 중 허가일자가 1990년대인 것은 몇 채야?"
    assert looks_like_year_stats_question(q) is False
    assert parse_year_stats(q) is None
    routed = try_route(q)
    assert routed is not None
    assert routed.intent != "d198_year_stats"
    assert "COUNT" in routed.sql.upper()
    assert "1990" in routed.sql
    assert "1999" in routed.sql
    assert "A33" in routed.sql


def test_d198_or_and_far_use_d198_route() -> None:
    from txt2sql.intent_router import try_route

    q_or = "동래구 명륜동 오피스텔 또는 사무소(세부용도) 채수"
    routed = try_route(q_or)
    assert routed is not None
    assert "AL_D198" in routed.sql
    assert "오피스텔" in routed.sql

    q_far = "동래구 표제부 중 용적율 200% 이상인 공동주택(주요용도) 채수"
    routed_far = try_route(q_far)
    assert routed_far is not None
    assert "AL_D198" in routed_far.sql
    assert "A25" in routed_far.sql or "A20" in routed_far.sql


def test_d198_ground_floor_threshold_binds_a31() -> None:
    from txt2sql.d198_attrs import parse_d198_question

    parsed = parse_d198_question(
        "금정구 집합건축물 중 세부용도가 아파트이고 지상 15층 이상인 채수"
    )
    assert parsed is not None
    joined = " ".join(parsed.filters)
    assert "A31" in joined
    assert "15" in joined


def test_d010_guard_rewrites_a34() -> None:
    from txt2sql.sql_d010_guard import rewrite_d198_columns_on_d010

    sql = (
        'SELECT COUNT(*) FROM "AL_D010_26_20250704" '
        "WHERE \"A34\" BETWEEN '1990-01-01' AND '1990-12-31'"
    )
    out = rewrite_d198_columns_on_d010(sql, "영도구 1990년대 사용승인")
    assert '"A34"' not in out
    assert '"A13"' in out
    assert "1999" in out


def test_defer_temporal_floors_skips_llm() -> None:
    from txt2sql.config import Settings
    from txt2sql.semantic_plan.generator import generate_semantic_plan

    q = "남구 공동주택 중 2000년 이후 사용승인이고 지상 15층 이상인 채수"
    plan = generate_semantic_plan(
        q,
        Settings(database_url="postgresql://x:x@localhost/x"),
        allow_llm=False,
    )
    assert plan.requires_clarification is False
    fields = {item.field for item in plan.filters}
    assert "approval_date" in fields
    assert "ground_floors" in fields


def test_dual_count_keeps_both_metrics() -> None:
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    q = "중구 위반건축물 전체 채수와 그 중 제2종근린생활시설 채수"
    plan = try_heuristic_plan(q)
    assert plan is not None
    assert plan.query_kind == "aggregate"
    assert len(plan.aggregations) >= 2
    sql = compile_semantic_plan(plan).sql.upper()
    assert "COUNT(*)" in sql
    assert "FILTER" in sql


def test_bas_intersect_count_and_max() -> None:
    from txt2sql.intent_router import try_route

    q = "우동과 교차하는 기초구역 개수와 그 중 면적 최대값"
    routed = try_route(q)
    assert routed is not None
    assert "COUNT" in routed.sql.upper()
    assert "MAX" in routed.sql.upper()
    assert "BAS_AR" in routed.sql


def test_basement_not_stolen_as_ground_floors() -> None:
    from txt2sql.intent_router import try_route
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    q = "해운대구 지하 2층 이상 건물 채수"
    from txt2sql.route_capability import select_execution_path

    assert select_execution_path(q) == "semantic_plan"
    plan = try_heuristic_plan(q)
    assert plan is not None
    assert plan.query_kind == "count"
    assert any(item.field == "basement_floors" for item in plan.filters)
    sql = compile_semantic_plan(plan).sql
    assert '"A27"' in sql
    assert '"A26"' not in sql or "A27" in sql


def test_basement_and_ground_defers_legacy() -> None:
    from txt2sql.intent_router import try_route
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    q = "해운대구 지하 2층 이상이면서 지상 15층 이상인 공동주택 수"
    from txt2sql.route_capability import select_execution_path

    assert select_execution_path(q) == "semantic_plan"
    plan = try_heuristic_plan(q)
    assert plan is not None
    fields = {item.field for item in plan.filters}
    assert "basement_floors" in fields
    assert "ground_floors" in fields
    sql = compile_semantic_plan(plan).sql
    # D010 A27/A26 or D198 A32/A31 (usage/detail → ledger)
    assert '"A27"' in sql or '"A32"' in sql
    assert '"A26"' in sql or '"A31"' in sql


def test_coverage_ratio_threshold_is_count() -> None:
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    plan = try_heuristic_plan("연제구 공동주택 중 건폐율 30% 이상")
    assert plan is not None
    assert plan.query_kind == "count"
    assert any(item.field == "building_coverage_ratio" for item in plan.filters)


def test_area_threshold_without_countish_defers_to_list() -> None:
    from txt2sql.intent_router import try_route
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    q = "북구 교육연구시설 중 대지면적 1500㎡ 이상"
    from txt2sql.route_capability import select_execution_path

    assert select_execution_path(q) == "semantic_plan"
    plan = try_heuristic_plan(q)
    assert plan is not None
    assert plan.query_kind == "list"
    assert any(item.field == "site_area_m2" for item in plan.filters)


def test_floor_range_not_bound_as_height() -> None:
    from txt2sql.query_understanding.contract import extract_contract
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    q = "금정구 건물 중 지상 8층 이상 20층 이하이면서 높이 25m 이상인 채수"
    contract = extract_contract(q)
    assert any(span.meta.get("field") == "ground_floors" for span in contract.ranges)
    plan = try_heuristic_plan(q)
    assert plan is not None
    sql = compile_semantic_plan(plan).sql
    assert '"A26"' in sql
    assert "BETWEEN" in sql.upper()
    assert "25" in sql


def test_unnamed_industrial_park_adds_d060_intersect() -> None:
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import try_heuristic_plan
    from txt2sql.semantic_plan.validator import validate_semantic_plan

    q = "장림동 산업단지 안 공장 중 연면적 3000㎡ 이상인 채수"
    plan = try_heuristic_plan(q)
    assert plan is not None
    assert plan.spatial_relations
    assert plan.spatial_relations[0].target.entity == "industrial_complex"
    checked = validate_semantic_plan(plan, q)
    assert checked.status == "ready"
    sql = compile_semantic_plan(plan).sql.upper()
    assert "AL_D060" in sql
    assert "ST_INTERSECTS" in sql
    assert "EXISTS" in sql


def test_grouped_avg_rank_emits_order_by() -> None:
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import try_heuristic_plan
    from txt2sql.semantic_plan.validator import validate_semantic_plan

    q = "수영구 건물 용도별 평균 연면적 상위 10개 용도"
    plan = try_heuristic_plan(q)
    assert plan is not None
    sql = compile_semantic_plan(plan).sql.upper()
    assert "ORDER BY" in sql
    assert "AVG(" in sql
    assert "LIMIT" in sql
    checked = validate_semantic_plan(plan, q)
    assert checked.status != "fallback"


def test_named_industrial_without_gu_still_plans() -> None:
    from txt2sql.domain import extract_industrial_names
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    q = "모라도시첨단산업단지 안 건물 수와 평균 연면적"
    assert extract_industrial_names(q)
    plan = try_heuristic_plan(q)
    assert plan is not None
    assert plan.spatial_relations


def test_basic_zone_intersect_park_is_count_not_area_rank() -> None:
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    q = "사하구 산업단지와 교차하는 기초구역은 몇 개야?"
    plan = try_heuristic_plan(q)
    assert plan is not None
    assert plan.entity == "basic_zone"
    assert plan.query_kind == "count"


def test_d060_sigungu_code_not_stolen_by_building_plan() -> None:
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    q = "부산 일반산업단지 도형 중 시군구코드가 강서구(26440)인 개수"
    assert try_heuristic_plan(q) is None
    from txt2sql.domain import extract_industrial_names

    names = extract_industrial_names("센텀2지구 도시첨단산업단지와 교차하는 건물")
    assert any("센텀2지구" in n for n in names)


def test_main_usage_plans_on_d198() -> None:
    from txt2sql.intent_router import try_route
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    q = "동래구 주요용도별 건수 상위 8"
    from txt2sql.route_capability import select_execution_path

    assert select_execution_path(q) == "semantic_plan"
    plan = try_heuristic_plan(q)
    assert plan is not None
    sql = compile_semantic_plan(plan).sql
    assert "AL_D198" in sql
    assert '"A25"' in sql


def test_detail_usage_not_name_lookup() -> None:
    from txt2sql.domain import looks_like_building_name_lookup
    from txt2sql.intent_router import try_route

    q = "금정구 오피스텔(세부용도) 이름과 지상층·건물높이"
    assert looks_like_building_name_lookup(q) is False
    routed = try_route(q)
    if routed is not None:
        assert routed.intent != "building_name_lookup"
        assert "AL_D198" in routed.sql


def test_not_applies_to_marked_operand_only() -> None:
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    q = "장림동 공장 중 경량철골이 아닌 연면적 2500㎡ 이상 채수"
    plan = try_heuristic_plan(q)
    assert plan is not None
    sql = compile_semantic_plan(plan).sql
    assert "공장" in sql
    assert "NOT " in sql.upper()
    assert "경량철골" in sql
    assert sql.upper().count("NOT") >= 1


def test_violate_negation_is_not_y() -> None:
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    q = "해운대구 위반건축물이 아니면서 높이 80m 이상인 공동주택 채수"
    plan = try_heuristic_plan(q)
    assert plan is not None
    sql = compile_semantic_plan(plan).sql
    assert '"A20"' in sql
    # Negation may be NOT, <>, or IS DISTINCT FROM (NULL-safe)
    upper = sql.upper()
    assert "<>" in sql or "NOT" in upper or "IS DISTINCT FROM" in upper
    assert "'Y'" in sql
    assert "공동주택" in sql


def test_short_gu_name_uses_exact_admin_code_prefix() -> None:
    from txt2sql.intent_router import try_route

    routed = try_route("서구에 있는 건물은 모두 몇 채야?")
    assert routed is not None
    assert routed.intent == "building_place_count"
    assert '"A3" LIKE \'26140%\'' in routed.sql
    assert "%서구%" not in routed.sql

    gangseo = try_route("강서구에 있는 건물은 모두 몇 채야?")
    assert gangseo is not None
    assert '"A3" LIKE \'26440%\'' in gangseo.sql
    assert "26140%" not in gangseo.sql


def test_special_land_not_일반_keeps_factory() -> None:
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    q = "사하구 일반지번이 아닌 건물 중 공장인 채수"
    plan = try_heuristic_plan(q)
    assert plan is not None
    sql = compile_semantic_plan(plan).sql
    assert "공장" in sql
    upper = sql.upper()
    assert "NOT" in upper or "IS DISTINCT FROM" in upper or "<>" in upper
    assert "일반" in sql or "'1'" in sql


def test_miman_is_not_chart_series_filter() -> None:
    from txt2sql.chart_qa import is_chart_series_filter_question

    assert is_chart_series_filter_question("수영구 광안동 숙박시설 연면적 80평 이상과 미만 채수") is False
    assert is_chart_series_filter_question("금정구 단독주택 경과 40년 이상 vs 10년 미만 채수") is False


def test_gu_place_uses_pnu_prefix_not_substring() -> None:
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    q = "서구 위반건축물 중 높이 20m 이상인 채수"
    plan = try_heuristic_plan(q)
    assert plan is not None
    sql = compile_semantic_plan(plan).sql
    assert '"A3"' in sql
    assert "강서구" not in sql


def test_ilban_cheolgol_is_not_all_steel() -> None:
    from txt2sql.domain import extract_structure

    alias, pattern = extract_structure("사하구 공장 일반철골인 채수")
    assert alias == "일반철골"
    assert "일반철골" in pattern
    assert pattern != "%철골%"


def test_basement_present_is_gt_zero() -> None:
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    q = "북구 지하층이 있고 지상 5층 이상인 제2종근린생활시설 채수"
    plan = try_heuristic_plan(q)
    assert plan is not None
    sql = compile_semantic_plan(plan).sql
    # Covered + usage → D198 basement A32; else D010 A27.
    assert '"A27"' in sql or '"A32"' in sql
    assert any(tok in sql for tok in ("> 0", ">0"))


def test_bas_area_metric_is_area_m2() -> None:
    from txt2sql.query_understanding.contract import extract_contract
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import try_heuristic_plan
    from txt2sql.semantic_plan.contract_verifier import verify_contract

    q = "남구 기초구역 중 면적(BAS_AR) 0.3 이상인 개수"
    plan = try_heuristic_plan(q)
    assert plan is not None
    assert plan.entity == "basic_zone"
    assert any(item.field == "area_m2" for item in plan.filters)
    result = verify_contract(q, plan)
    assert result.ok is True
    sql = compile_semantic_plan(plan).sql
    assert "BAS_AR" in sql
    assert "0.3" in sql


def test_centroid_distance_uses_st_centroid() -> None:
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    q = "우1동 중심에서 300m 이내 공동주택 중 높이 40m 이상 채수"
    plan = try_heuristic_plan(q)
    assert plan is not None
    sql = compile_semantic_plan(plan).sql.upper()
    assert "ST_CENTROID" in sql


def test_special_land_gaji_and_block_compile() -> None:
    from txt2sql.domain import extract_structures
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    assert extract_structures("서구 블럭지번 건물 수를 세어줘") == []
    for q, token in (
        ("중구 가지번 건물은 몇 채야?", "가지"),
        ("서구 블럭지번 건물 수를 세어줘", "블럭"),
    ):
        plan = try_heuristic_plan(q)
        assert plan is not None
        assert any(item.field == "special_land" for item in plan.filters)
        sql = compile_semantic_plan(plan).sql
        assert "A6" in sql
        assert token in sql


def test_decade_list_orders_by_approval_date() -> None:
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    q = "구서동에서 1990년대 준공된 건물을 보여줘"
    plan = try_heuristic_plan(q)
    assert plan is not None
    assert any(
        item.field == "approval_date" and item.operator == "between"
        for item in plan.filters
    )
    assert plan.order_by and plan.order_by[0].field == "approval_date"
    sql = compile_semantic_plan(plan).sql
    assert "1990" in sql and "1999" in sql
    assert "A34" in sql
    assert "LIMIT" in sql.upper()


def test_permit_approval_lag_operators() -> None:
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.contract_verifier import verify_contract
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    list_q = "금사동에서 허가일부터 사용승인일까지 1년 이상 걸린 건물을 보여줘"
    plan = try_heuristic_plan(list_q)
    assert plan is not None
    assert any(a.startswith("permit_day_gap_gte:") for a in (plan.assumptions or []))
    assert verify_contract(list_q, plan).ok is True
    sql = compile_semantic_plan(plan).sql
    assert "A33" in sql and "A34" in sql
    assert ">= 365" in sql

    ratio_q = "복천동에서 허가 후 2년 이내 준공된 건물 비율을 알려줘"
    plan = try_heuristic_plan(ratio_q)
    assert plan is not None
    assert any(a.startswith("permit_day_gap_lte:") for a in (plan.assumptions or []))
    sql = compile_semantic_plan(plan).sql
    assert "<= 730" in sql
    assert "within_2y_ratio" in sql

    year_q = "금정구에서 허가연도와 사용승인연도가 다른 건물 비율을 구해줘"
    plan = try_heuristic_plan(year_q)
    assert plan is not None
    assert "permit_approval_year_neq" in (plan.assumptions or [])
    sql = compile_semantic_plan(plan).sql
    assert "<>" in sql
    assert "diff_year_ratio" in sql
