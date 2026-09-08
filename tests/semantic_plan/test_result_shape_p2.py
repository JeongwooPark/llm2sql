"""P2: stages/bins/ratios 결과 형상과 AskResult 호환."""

from __future__ import annotations

from txt2sql.semantic_plan.answer import format_semantic_answer
from txt2sql.semantic_plan.models import (
    AggregationSpec,
    BinSpec,
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
from txt2sql.semantic_plan.result_shape import (
    build_semantic_result_table,
    diagnose_result_shape,
    infer_plan_result_kind,
    verify_result,
)
from txt2sql.types import AskResult


def _ratio_pred(field: str, value: str) -> PredicateSpec:
    return PredicateSpec(
        op="cmp",
        operator="eq",
        left=OperandSpec(kind="field", field=field),
        right=OperandSpec(kind="literal", value=value),
    )


def test_infer_kind_stages_is_scalar() -> None:
    plan = SemanticQueryPlan(
        query_kind="aggregate",
        entity="building",
        aggregations=[
            AggregationSpec(function="avg", field="height_m", alias="avg_height_m")
        ],
        stages=[
            StageSpec(
                id="rank0",
                kind="rank",
                select=["height_m"],
                order_by=[OrderSpec(field="gross_floor_area_m2", direction="desc")],
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
    assert infer_plan_result_kind(plan) == "scalar"
    assert diagnose_result_shape(plan, [{"avg_height_m": 42.5}]) == []
    assert diagnose_result_shape(plan, [{"avg_height_m": 1}, {"avg_height_m": 2}]) == [
        "Q03"
    ]


def test_infer_kind_bins_is_group() -> None:
    plan = SemanticQueryPlan(
        query_kind="aggregate",
        entity="building",
        bins=[BinSpec(field="gross_floor_area_m2", edges=[0, 100, 200])],
        group_by=["gross_floor_area_m2"],
        aggregations=[AggregationSpec(function="count", alias="n")],
    )
    assert infer_plan_result_kind(plan) == "group"
    rows = [
        {"gross_floor_area_m2": "0_100", "n": 3},
        {"gross_floor_area_m2": "100_200", "n": 7},
    ]
    assert diagnose_result_shape(plan, rows) == []
    table = build_semantic_result_table(plan, rows, question="연면적 구간")
    assert table is not None
    assert table["total"] == 10
    assert len(table["rows"]) == 2


def test_infer_kind_ratios() -> None:
    plan = SemanticQueryPlan(
        query_kind="aggregate",
        entity="building",
        ratios=[
            RatioSpec(
                numerator_predicate=_ratio_pred("usage", "공동주택"),
                alias="ratio_pct",
            )
        ],
    )
    assert infer_plan_result_kind(plan) == "ratio"
    assert verify_result(None, [{"ratio_pct": 12.5}], plan=plan).ok is True
    assert verify_result(None, [{"ratio_pct": 250.0}], plan=plan).ok is False


def test_verify_keeps_stages_despite_stale_count_contract() -> None:
    from txt2sql.query_understanding.contract import extract_contract

    contract = extract_contract("해운대구 연면적 상위 10개 평균 높이")
    # force count-like stale contract if extracted otherwise
    plan = SemanticQueryPlan(
        query_kind="aggregate",
        entity="building",
        aggregations=[
            AggregationSpec(function="avg", field="height_m", alias="avg_height_m")
        ],
        stages=[
            StageSpec(id="r0", kind="rank", limit=10, select=["height_m"]),
            StageSpec(
                id="a1",
                kind="aggregate",
                aggregations=[
                    AggregationSpec(
                        function="avg", field="height_m", alias="avg_height_m"
                    )
                ],
            ),
        ],
    )
    rows = [{"avg_height_m": 55.0}]
    assert verify_result(contract, rows, plan=plan).ok is True


def test_format_answer_stages_bins_ratio() -> None:
    stages_plan = SemanticQueryPlan(
        query_kind="aggregate",
        entity="building",
        scope=ScopeSpec(place=PlaceSpec(name="해운대구", kind="gu")),
        aggregations=[
            AggregationSpec(function="avg", field="height_m", alias="avg_height_m")
        ],
        stages=[
            StageSpec(id="r0", kind="rank", limit=10, select=["height_m"]),
            StageSpec(
                id="a1",
                kind="aggregate",
                aggregations=[
                    AggregationSpec(
                        function="avg", field="height_m", alias="avg_height_m"
                    )
                ],
            ),
        ],
    )
    text = format_semantic_answer(
        "상위 10 평균",
        plan=stages_plan,
        rows=[{"avg_height_m": 40}],
        row_count=1,
    )
    assert "상위 10" in text
    assert "평균 높이" in text

    bins_plan = SemanticQueryPlan(
        query_kind="aggregate",
        entity="building",
        bins=[BinSpec(field="height_m", width=10)],
        group_by=["height_m"],
        aggregations=[AggregationSpec(function="count", alias="n")],
    )
    text2 = format_semantic_answer(
        "높이 구간",
        plan=bins_plan,
        rows=[{"height_m": 0, "n": 2}, {"height_m": 10, "n": 5}],
        row_count=2,
    )
    assert "구간" in text2
    assert "2건" in text2

    ratio_plan = SemanticQueryPlan(
        query_kind="aggregate",
        entity="building",
        ratios=[
            RatioSpec(
                numerator_predicate=_ratio_pred("usage", "공동주택"),
                alias="ratio_pct",
            )
        ],
    )
    text3 = format_semantic_answer(
        "비율",
        plan=ratio_plan,
        rows=[{"ratio_pct": 33.3}],
        row_count=1,
    )
    assert "%" in text3


def test_ask_result_roundtrip_keeps_table_chart_fields() -> None:
    payload = {
        "ok": True,
        "answer": "구간별 분포",
        "sql": "SELECT 1",
        "tables": ["AL_D010"],
        "rows": [{"range": "0_100", "n": 3}],
        "row_count": 1,
        "route": "semantic_plan_aggregate",
        "table": {
            "caption": "x",
            "range_header": "구간",
            "count_header": "동 수",
            "share_header": "비율",
            "rows": [{"range": "0_100", "n": 3, "pct": 100.0}],
            "total": 3,
            "peak": {"range": "0_100", "n": 3, "pct": 100.0},
        },
        "chart_offer": True,
        "chart_spec": {"type": "bar", "labels": ["0_100"], "datasets": []},
        "semantic_plan": {"version": "1.0", "query_kind": "aggregate", "entity": "building"},
    }
    result = AskResult.from_dict(payload)
    back = result.to_dict()
    assert back["table"]["total"] == 3
    assert back["chart_offer"] is True
    assert back["route"] == "semantic_plan_aggregate"


def test_chart_spec_for_semantic_bins() -> None:
    from txt2sql.chart_qa import build_chart_spec

    rows = [
        {"gross_floor_area_m2": "0_100", "n": 3},
        {"gross_floor_area_m2": "100_200", "n": 7},
    ]
    spec = build_chart_spec(
        route="semantic_plan_aggregate",
        rows=rows,
        question="연면적 구간별",
    )
    assert spec is not None
    assert len(spec["labels"]) == 2


def test_legacy_count_diagnose_unchanged() -> None:
    plan = SemanticQueryPlan(query_kind="count", entity="building")
    assert diagnose_result_shape(plan, []) == ["Q03"]
    assert diagnose_result_shape(plan, [{"count": 0}]) == []
