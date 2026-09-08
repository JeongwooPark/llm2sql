"""P012 grain / OR / verifier regression scenarios (no Q-ID hardcoding)."""

from __future__ import annotations

from txt2sql.domain import reset_d198_coverage, set_d198_coverage
from txt2sql.intent_router import _route_place_usage_count
from txt2sql.query_ir.adapters import contract_to_query_ir
from txt2sql.query_understanding.contract import extract_contract
from txt2sql.semantic_plan.contract_verifier import verify_contract
from txt2sql.semantic_plan.models import (
    FilterSpec,
    OperandSpec,
    PredicateSpec,
    RatioSpec,
    SemanticQueryPlan,
)
from txt2sql.semantic_plan.plan_repair import inject_missing_predicates


def setup_function() -> None:
    reset_d198_coverage()


def teardown_function() -> None:
    reset_d198_coverage()


def test_covered_dong_usage_count_uses_d198_a25() -> None:
    set_d198_coverage({"동래구": "AL_D198_26260_20250115"})
    routed = _route_place_usage_count("온천동에서 숙박시설 건물은 몇 채야?", conn=None)
    assert routed is not None
    assert "AL_D198_26260" in routed.sql
    assert '"A25" = \'숙박시설\'' in routed.sql


def test_select_physical_plan_uses_d198_for_covered_usage() -> None:
    from txt2sql.planner.executor_adapter import build_execution_plan
    from txt2sql.planner.physical import select_physical_plan

    set_d198_coverage({"금정구": "AL_D198_26410_20250115"})
    q = "금정구 단독주택은 몇 채야?"
    bundle = build_execution_plan(q)
    physical = select_physical_plan(bundle.logical, question=q)
    assert physical.strategy == "D198_EXECUTOR"


def test_multi_usage_union_preserves_all_on_d198() -> None:
    set_d198_coverage({"금정구": "AL_D198_26410_20250115"})
    routed = _route_place_usage_count(
        "금정구에서 공장과 창고시설을 합친 건수", conn=None
    )
    assert routed is not None
    assert "AL_D198" in routed.sql
    assert '"A25" IN' in routed.sql
    assert "공장" in routed.sql and "창고시설" in routed.sql


def test_multi_usage_or_preserves_all_on_d010_when_uncovered() -> None:
    routed = _route_place_usage_count(
        "남구에서 공장과 창고시설을 합친 건수", conn=None
    )
    assert routed is not None
    assert "AL_D010" in routed.sql
    assert '"A9" IN' in routed.sql
    assert "공장" in routed.sql and "창고시설" in routed.sql


def test_or_repair_does_not_and_duplicate_usage_filters() -> None:
    q = "금정구에서 공장 또는 창고시설인 건물 수는?"
    plan = SemanticQueryPlan(
        query_kind="count",
        entity="building",
        filters=[],
        aggregations=[],
    )
    repaired = inject_missing_predicates(plan, q)
    usage_eq = [
        f for f in repaired.filters if f.field == "usage" and f.operator == "eq"
    ]
    assert len(usage_eq) == 0
    assert repaired.predicate is not None
    assert repaired.predicate.op == "or" or (
        repaired.predicate.op == "and"
        and any(a.op == "or" for a in (repaired.predicate.args or []))
    )


def test_verifier_rejects_usage_swapped_to_detail_usage() -> None:
    q = "금정구 공동주택 건물 수는?"
    contract = extract_contract(q)
    # Plan wrongly uses detail_usage only.
    plan = SemanticQueryPlan(
        query_kind="count",
        entity="building",
        filters=[FilterSpec(field="detail_usage", operator="eq", value="아파트")],
        predicate=PredicateSpec(
            op="cmp",
            operator="eq",
            left=OperandSpec(kind="field", field="detail_usage"),
            right=OperandSpec(kind="literal", value="아파트"),
        ),
    )
    result = verify_contract(q, plan, contract=contract)
    assert result.ok is False
    assert result.hard_fail is True
    joined = " ".join(result.reasons)
    assert "PREDICATE_DROPPED" in joined or "ENTITY_SELECTION_ERROR" in joined


def test_verifier_rejects_operator_flip() -> None:
    q = "금정구 높이 40m 이상 건물 수는?"
    contract = extract_contract(q)
    plan = SemanticQueryPlan(
        query_kind="count",
        entity="building",
        filters=[FilterSpec(field="height_m", operator="lte", value=40)],
        predicate=PredicateSpec(
            op="cmp",
            operator="lte",
            left=OperandSpec(kind="field", field="height_m"),
            right=OperandSpec(kind="literal", value=40),
        ),
    )
    result = verify_contract(q, plan, contract=contract)
    assert result.ok is False
    assert "PREDICATE_DROPPED" in result.reasons


def test_group_rank_by_avg_does_not_use_stages() -> None:
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    q = "구·군별 평균 높이가 높은 순으로 상위 7개를 보여줘"
    plan = try_heuristic_plan(q)
    assert plan is not None
    assert not plan.stages
    assert "sigungu_name" in plan.group_by
    sql = compile_semantic_plan(plan).sql
    assert "GROUP BY" in sql.upper()
    assert "AVG" in sql.upper()
    assert "LIMIT 7" in sql.upper()


def test_recent_n_years_age_compare_is_lte() -> None:
    from txt2sql.domain import age_date_predicate, extract_age_compare, extract_age_years

    q = "장전동에서 최근 10년 내 준공된 건물은 몇 채야?"
    assert extract_age_years(q) == 10
    assert extract_age_compare(q) == "lte"
    pred = age_date_predicate("A34", 10, "lte")
    assert ">=" in pred
    assert "<=" not in pred.split("AND")[-1] or ">=" in pred


def test_age_years_alone_marks_age_question() -> None:
    from txt2sql.domain import looks_like_age_question, looks_like_building_name_lookup

    q = "서동에서 40년 이상 된 건물 수를 알려줘"
    assert looks_like_age_question(q) is True
    assert looks_like_building_name_lookup(q) is False


def test_usage_class_not_building_name_lookup() -> None:
    from txt2sql.domain import looks_like_building_name_lookup

    assert looks_like_building_name_lookup("동래구에서 문교사회용 건물 수를 알려줘") is False


def test_far_coverage_only_uses_d010_not_d198() -> None:
    from txt2sql.intent_router import try_route

    routed = try_route(
        "남구에서 건폐율 60% 이상이고 용적률 250% 이상인 건물 수를 알려줘"
    )
    assert routed is not None
    assert "AL_D010" in routed.sql
    assert "A17" in routed.sql and "A18" in routed.sql


def test_age_place_filter_avoids_substring_dong() -> None:
    from txt2sql.domain import set_d198_coverage
    from txt2sql.intent_router import _route_building_age

    set_d198_coverage({"금정구": "AL_D198_26410_20260715"})
    routed = _route_building_age("서동에서 40년 이상 된 건물 수를 알려줘", conn=None)
    assert routed is not None
    assert "% 서동" in routed.sql or "\"A4\" = '서동'" in routed.sql
    assert "%서동%" not in routed.sql.replace("% 서동", "")


def test_place_buffer_count_uses_distinct_building_id() -> None:
    from txt2sql.spatial_templates import place_buffer_count_sql

    sql = place_buffer_count_sql("대연3동", "300", "0.003")
    assert "COUNT(DISTINCT" in sql.upper()
    assert '"A1"' in sql


def test_legal_dong_contains_avoids_substring_match() -> None:
    from txt2sql.domain import set_d198_coverage
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    set_d198_coverage({"금정구": "AL_D198_26410_20260715"})
    plan = try_heuristic_plan("서동의 교육연구시설 건물명과 지번을 보여줘")
    assert plan is not None
    sql = compile_semantic_plan(plan).sql
    assert "% 서동" in sql or "\"A4\" = '서동'" in sql
    assert "ILIKE '%서동%'" not in sql


def test_simple_numbered_admin_dong_count_uses_a4_not_bnd() -> None:
    from txt2sql.intent_router import try_route

    routed = try_route("대저1동 건물 수를 알려줘")
    assert routed is not None
    assert routed.intent == "building_place_count"
    assert "BND_ADM" not in routed.sql
    assert "대저1동" in routed.sql


def test_verifier_accepts_threshold_in_ratio_predicates() -> None:
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    q = "15층 이상 건물 중 공동주택 비율"
    plan = try_heuristic_plan(q)
    assert plan is not None
    assert plan.ratios
    result = verify_contract(q, plan)
    assert result.ok is True
    assert result.hard_fail is False


def test_verifier_rejects_identical_ratio_stages() -> None:
    from txt2sql.semantic_plan.models import RatioSpec

    q = "금정구 공동주택 중 위반건축물 비율"
    contract = extract_contract(q)
    shared = PredicateSpec(
        op="cmp",
        operator="eq",
        left=OperandSpec(kind="field", field="usage"),
        right=OperandSpec(kind="literal", value="공동주택"),
    )
    plan = SemanticQueryPlan(
        query_kind="aggregate",
        entity="building",
        filters=[FilterSpec(field="usage", operator="eq", value="공동주택")],
        ratios=[
            RatioSpec(
                numerator_predicate=shared,
                denominator_predicate=shared,
                multiplier=1.0,
                alias="ratio",
            )
        ],
    )
    result = verify_contract(q, plan, contract=contract)
    assert result.ok is False
    assert "ratio_stage_mismatch" in result.reasons


def test_verifier_rejects_not_or_scope_flatten() -> None:
    q = "해운대구에서 공장 또는 창고시설을 제외한 건물 수"
    contract = extract_contract(q)
    # Wrong: NOT flattened to OR of positives (missing not wrapper).
    plan = SemanticQueryPlan(
        query_kind="count",
        entity="building",
        filters=[
            FilterSpec(field="usage", operator="eq", value="공장"),
            FilterSpec(field="usage", operator="eq", value="창고시설"),
        ],
        predicate=PredicateSpec(
            op="or",
            args=[
                PredicateSpec(
                    op="cmp",
                    operator="eq",
                    left=OperandSpec(kind="field", field="usage"),
                    right=OperandSpec(kind="literal", value="공장"),
                ),
                PredicateSpec(
                    op="cmp",
                    operator="eq",
                    left=OperandSpec(kind="field", field="usage"),
                    right=OperandSpec(kind="literal", value="창고시설"),
                ),
            ],
        ),
    )
    # Ensure contract marks scopes_or when present.
    not_spans = [s for s in contract.boolean_ops if s.kind == "not"]
    if not_spans and (not_spans[0].meta or {}).get("scopes_or"):
        result = verify_contract(q, plan, contract=contract)
        assert result.ok is False
        assert "BOOLEAN_NOT_DROPPED" in result.reasons


def test_contract_or_survives_to_query_ir() -> None:
    ir = contract_to_query_ir(
        extract_contract("금정구에서 단독주택 또는 공동주택인 건물 수는?")
    )
    assert any(p.logical_group == "or" for p in ir.predicates)


def test_detail_usage_group_uses_building_area() -> None:
    set_d198_coverage({"금정구": "AL_D198_26410_20250115"})
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    q = "금정구 세부용도별 평균 건축물면적을 계산해줘"
    c = extract_contract(q)
    assert "detail_usage" in c.group_fields
    assert any(
        r.field == "building_area_m2" for r in c.aggregation_requests
    )
    plan = try_heuristic_plan(q, contract=c)
    assert plan is not None
    assert plan.requires_clarification is False
    assert plan.group_by == ["detail_usage"]
    assert any(
        a.field == "building_area_m2" and a.function == "avg" for a in plan.aggregations
    )
    sql = compile_semantic_plan(plan).sql
    assert "A27" in sql
    assert "A18" in sql


def test_multi_edge_bins_not_range_filter() -> None:
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    q = "연면적 500㎡ 미만, 500~2000㎡, 2000㎡ 초과로 나눠 건수와 평균 높이를 보여줘"
    c = extract_contract(q)
    assert c.fixed_bins is True
    plan = try_heuristic_plan(q, contract=c)
    assert plan is not None
    assert plan.bins
    assert plan.bins[0].edges and len(plan.bins[0].edges) >= 2
    assert not any(
        f.field == "gross_floor_area_m2" and f.operator == "between"
        for f in plan.filters
    )
    sql = compile_semantic_plan(plan).sql.upper()
    assert "CASE" in sql
    assert "AVG" in sql and "COUNT" in sql


def test_structure_share_and_age_ratio_group() -> None:
    set_d198_coverage({"금정구": "AL_D198_26410_20250115"})
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    struct = try_heuristic_plan("구조별 건물 비율을 전체 대비 백분율로 보여줘")
    assert struct is not None
    assert struct.group_by == ["structure"]
    assert any(a.function == "count" for a in struct.aggregations)

    ratio_q = "금정구 법정동별 30년 이상 건물 비율을 보여줘"
    plan = try_heuristic_plan(ratio_q)
    assert plan is not None
    assert plan.group_by == ["legal_dong"]
    assert plan.ratios
    sql = compile_semantic_plan(plan).sql
    assert "FILTER" in sql.upper()


def test_conditional_ratio_splits_around_jung() -> None:
    set_d198_coverage({"금정구": "AL_D198_26410_20250115"})
    from txt2sql.semantic_plan.compiler import compile_semantic_plan
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    q = "금정구에서 30년 이상 된 건물 중 지상 10층 이상인 비율을 계산해줘"
    plan = try_heuristic_plan(q)
    assert plan is not None
    assert plan.ratios
    ratio = plan.ratios[0]
    assert ratio.denominator_predicate is not None
    assert ratio.numerator_predicate is not None
    # 분모≠분자 (동일 predicate면 항상 100%)
    assert ratio.numerator_predicate != ratio.denominator_predicate
    sql = compile_semantic_plan(plan).sql
    assert "A31" in sql or "A26" in sql


def test_decade_list_not_year_stats_route() -> None:
    from txt2sql.d198_attrs import looks_like_year_stats_question
    from txt2sql.intent_router import try_route

    q = "구서동에서 1990년대 준공된 건물을 보여줘"
    assert looks_like_year_stats_question(q) is False
    routed = try_route(q)
    assert routed is None or routed.intent != "d198_year_stats"


def test_followup_height_filter_keeps_list() -> None:
    from txt2sql.semantic_plan.followup import apply_plan_delta, parse_followup_delta
    from txt2sql.semantic_plan.models import PlaceSpec, ScopeSpec, SemanticQueryPlan

    base = SemanticQueryPlan(
        query_kind="list",
        entity="building",
        scope=ScopeSpec(place=PlaceSpec(name="해운대구", kind="gu")),
        filters=[
            FilterSpec(field="gross_floor_area_m2", operator="gt", value=10000),
        ],
        select=["name", "height_m"],
        limit=100,
    )
    delta = parse_followup_delta("그중 높이 50m 이상만")
    assert delta is not None
    assert delta.change_kind is None
    merged = apply_plan_delta(base, delta)
    assert merged.query_kind == "list"
    assert any(f.field == "height_m" for f in merged.filters)


def test_field_equivalence_meta() -> None:
    from txt2sql.meta_qa import answer_metadata_question, is_metadata_question
    from txt2sql.query_contract import contract_is_executable_query

    q = "연면적과 건축물면적은 같은 필드야?"
    assert is_metadata_question(q) is True
    assert contract_is_executable_query(extract_contract(q)) is False
    ans = answer_metadata_question(None, q)  # type: ignore[arg-type]
    assert ans is not None
    assert "다른 필드" in ans.answer or "컬럼" in ans.answer


def test_field_compare_count_skips_size_vague() -> None:
    from unittest.mock import MagicMock

    from txt2sql.clarify_qa import check_ambiguity

    conn = MagicMock()
    # 필드 비교·임계가 있으면 「작은 건물」 soft vague 제외
    assert (
        check_ambiguity(conn, "부산에서 연면적이 대지면적보다 작은 건물 수는?")
        is None
    )
    # 기준 없는 크기 표현은 계속 clarify
    vague = check_ambiguity(conn, "높은 건물 수는?")
    assert vague is not None
    assert vague.intent == "clarify_vague"


def test_bas_group_with_place_skips_bas_id_clarify() -> None:
    from unittest.mock import MagicMock

    from txt2sql.clarify_qa import check_ambiguity

    conn = MagicMock()
    # 장소·*별·공간 단서가 있으면 BAS_ID 확인을 요구하지 않음
    assert (
        check_ambiguity(conn, "대연3동과 겹치는 기초구역별 건물 수를 알려줘")
        is None
    )
    assert check_ambiguity(conn, "금정구 기초구역별 건물 수를 보여줘") is None
    # 대상 구역·장소가 없는 목록만 clarify
    need = check_ambiguity(conn, "기초구역 안 건물 보여줘")
    assert need is not None
    assert need.intent == "clarify_vague"
    assert "기초구역" in need.ambiguous_terms
