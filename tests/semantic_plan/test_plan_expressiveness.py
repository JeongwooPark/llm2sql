from txt2sql.db import assert_readonly_sql
from txt2sql.semantic_plan.compiler import compile_semantic_plan
from txt2sql.semantic_plan.generator import try_heuristic_plan
from txt2sql.semantic_plan.models import (
    AggregationSpec,
    BinSpec,
    ExpressionSpec,
    FilterSpec,
    OperandSpec,
    OrderSpec,
    PlaceSpec,
    PredicateSpec,
    RatioSpec,
    ScopeSpec,
    SemanticQueryPlan,
    StageSpec,
)
from txt2sql.semantic_plan.validator import validate_semantic_plan


def test_semantic_plan_supports_stddev() -> None:
    plan = try_heuristic_plan("해운대구 건물 높이 평균과 표준편차")
    assert plan is not None
    functions = [item.function for item in plan.aggregations]
    assert "stddev" in functions
    assert "avg" in functions


def test_semantic_plan_supports_percentile() -> None:
    plan = try_heuristic_plan("사하구 공장 연면적 상위 10% 경계값(90백분위)")
    assert plan is not None
    aggs = [item for item in plan.aggregations if item.function == "percentile"]
    assert aggs
    agg = aggs[0]
    assert agg.function == "percentile"
    assert abs((agg.percentile or 0) - 0.9) < 1e-9


def test_semantic_plan_supports_field_ratio_expression() -> None:
    plan = try_heuristic_plan("수영구 숙박시설 중 연면적 대비 건축면적 비(평균 A12/A14)")
    assert plan is not None
    exprs = [item.expression for item in plan.aggregations if item.expression is not None]
    assert exprs
    assert exprs[0].kind == "divide"


def test_semantic_plan_preserves_ratio_denominator() -> None:
    plan = try_heuristic_plan("영도구 15층 이상 건물 중 공동주택 비율 %")
    assert plan is not None
    assert plan.ratios
    ratio = plan.ratios[0]
    assert ratio.denominator_predicate is not None
    assert ratio.numerator_predicate is not None


def test_semantic_plan_supports_multiple_ratios() -> None:
    plan = try_heuristic_plan("해운대구 건물 중 높이 50m 이상 비율과 20층 이상 비율")
    assert plan is not None
    assert len(plan.ratios) == 2


def test_validator_allows_order_by_aggregation_alias() -> None:
    plan = SemanticQueryPlan(
        query_kind="aggregate",
        entity="building",
        aggregations=[AggregationSpec(function="count", alias="n")],
        group_by=["structure"],
        order_by=[OrderSpec(field="n", direction="desc")],
        limit=6,
    )
    result = validate_semantic_plan(plan, "구조별 건수 상위 6")
    assert result.status != "fallback"
    assert not any("unknown field: n" in e for e in result.errors)


def test_validator_blocks_aggregate_when_contract_group_is_dropped() -> None:
    plan = SemanticQueryPlan(
        query_kind="aggregate",
        entity="building",
        aggregations=[AggregationSpec(function="count", alias="n")],
    )
    result = validate_semantic_plan(plan, "용도별 건수")
    assert result.status == "fallback"
    assert "missing_group" in result.errors


def test_validator_requires_percentile_value() -> None:
    plan = SemanticQueryPlan(
        query_kind="aggregate",
        entity="building",
        aggregations=[
            AggregationSpec(function="percentile", field="height_m", percentile=None)
        ],
    )
    result = validate_semantic_plan(plan, "높이 백분위")
    assert result.status == "fallback"


def test_validator_rejects_divide_without_denominator() -> None:
    plan = SemanticQueryPlan(
        query_kind="aggregate",
        entity="building",
        aggregations=[
            AggregationSpec(
                function="avg",
                expression=ExpressionSpec(
                    kind="divide",
                    left=ExpressionSpec(kind="field", field="building_area_m2"),
                    right=None,
                ),
                alias="avg_ratio",
            )
        ],
    )
    result = validate_semantic_plan(plan, "건축면적 대비")
    assert result.status == "fallback"


def test_place_spec_roundtrip_keeps_parent_context() -> None:
    plan = SemanticQueryPlan(
        query_kind="count",
        entity="building",
        scope=ScopeSpec(
            place=PlaceSpec(
                name="연산동",
                kind="legal_dong",
                sido="부산광역시",
                sigungu="연제구",
                code="26470",
            )
        ),
    )
    restored = SemanticQueryPlan.model_validate(plan.model_dump())
    assert restored.scope is not None
    assert restored.scope.place is not None
    assert restored.scope.place.sigungu == "연제구"
    assert restored.scope.place.sido == "부산광역시"
    assert restored.scope.place.code == "26470"


def test_legacy_place_json_deserializes_without_parent_fields() -> None:
    plan = SemanticQueryPlan.model_validate(
        {
            "version": "1.0",
            "query_kind": "count",
            "entity": "building",
            "scope": {
                "place": {"name": "해운대구", "kind": "gu"},
                "spatial_mode": "auto",
            },
        }
    )
    assert plan.scope and plan.scope.place
    assert plan.scope.place.sido is None
    assert plan.bins == []
    assert plan.stages == []


def test_bins_edges_compile_to_case() -> None:
    plan = SemanticQueryPlan(
        query_kind="aggregate",
        entity="building",
        scope=ScopeSpec(place=PlaceSpec(name="금정구", kind="gu")),
        bins=[
            BinSpec(
                field="gross_floor_area_m2",
                edges=[0, 100, 200],
                labels=["0_100", "100_200", "200_inf"],
            )
        ],
        group_by=["gross_floor_area_m2"],
        aggregations=[AggregationSpec(function="count", alias="n")],
    )
    compiled = compile_semantic_plan(plan)
    sql_u = compiled.sql.upper()
    assert "CASE" in sql_u
    assert "0_100" in compiled.sql
    assert_readonly_sql(compiled.sql)


def test_empty_stages_matches_flat_sql() -> None:
    base = SemanticQueryPlan(
        query_kind="count",
        entity="building",
        scope=ScopeSpec(place=PlaceSpec(name="해운대구", kind="gu")),
        filters=[FilterSpec(field="usage", operator="eq", value="공동주택")],
    )
    with_empty = base.model_copy(update={"stages": [], "bins": []})
    assert compile_semantic_plan(base).sql == compile_semantic_plan(with_empty).sql


def test_stages_topn_then_avg_compiles_cte() -> None:
    plan = SemanticQueryPlan(
        query_kind="aggregate",
        entity="building",
        scope=ScopeSpec(place=PlaceSpec(name="해운대구", kind="gu")),
        aggregations=[
            AggregationSpec(function="avg", field="height_m", alias="avg_height_m")
        ],
        stages=[
            StageSpec(
                id="rank0",
                kind="rank",
                select=[
                    "name",
                    "legal_dong",
                    "lot_address",
                    "gross_floor_area_m2",
                    "height_m",
                ],
                order_by=[
                    OrderSpec(field="gross_floor_area_m2", direction="desc")
                ],
                limit=10,
            ),
            StageSpec(
                id="agg1",
                kind="aggregate",
                aggregations=[
                    AggregationSpec(
                        function="avg", field="height_m", alias="avg_height_m"
                    )
                ],
            ),
        ],
    )
    compiled = compile_semantic_plan(plan)
    sql_u = compiled.sql.upper()
    assert sql_u.startswith("WITH")
    assert "STAGE_0" in sql_u or '"STAGE_0"' in compiled.sql.upper()
    assert "LIMIT 10" in sql_u
    assert "AVG(" in sql_u
    assert "SELECT *" not in sql_u
    assert_readonly_sql(compiled.sql)


def test_ratio_with_stage0_population_keeps_filter_on_outer() -> None:
    usage_eq = PredicateSpec(
        op="cmp",
        operator="eq",
        left=OperandSpec(kind="field", field="usage"),
        right=OperandSpec(kind="literal", value="공동주택"),
    )
    plan = SemanticQueryPlan(
        query_kind="aggregate",
        entity="building",
        scope=ScopeSpec(place=PlaceSpec(name="영도구", kind="gu")),
        filters=[FilterSpec(field="ground_floors", operator="gte", value=15)],
        ratios=[
            RatioSpec(
                numerator_predicate=usage_eq,
                denominator_predicate=None,
                alias="ratio_pct",
            )
        ],
        stages=[
            StageSpec(
                id="pop0",
                kind="filter",
                select=["usage", "ground_floors"],
            )
        ],
    )
    compiled = compile_semantic_plan(plan)
    sql_u = compiled.sql.upper()
    assert sql_u.startswith("WITH")
    assert "FILTER" in sql_u
    assert "A26" in compiled.sql or "GROUND_FLOORS" in sql_u
    assert_readonly_sql(compiled.sql)


def test_heuristic_topn_average_emits_stages() -> None:
    plan = try_heuristic_plan("해운대구 연면적 상위 10개 건물의 평균 높이")
    assert plan is not None
    assert plan.stages
    assert plan.stages[0].kind == "rank"
    assert plan.stages[0].limit == 10
    assert plan.stages[1].kind == "aggregate"
    sql = compile_semantic_plan(plan).sql.upper()
    assert sql.startswith("WITH")


def test_gu_plus_dong_fills_place_sigungu() -> None:
    plan = try_heuristic_plan("연제구 연산동 건물 수는?")
    assert plan is not None
    assert plan.scope and plan.scope.place
    assert plan.scope.place.sigungu == "연제구" or plan.assumptions
