"""Dataset grain policy tests (P012 §4 MAIN485 contract)."""

from __future__ import annotations

from txt2sql.dataset_grain import (
    needs_d198_building_grain,
    place_has_d198_coverage,
    query_ir_needs_d198,
    resolve_dataset_grain,
    simple_building_usage_count,
)
from txt2sql.domain import reset_d198_coverage, set_d198_coverage
from txt2sql.query_ir.models import PredicateIR, QueryIR, ScopeIR


def setup_function() -> None:
    reset_d198_coverage()


def teardown_function() -> None:
    reset_d198_coverage()


def test_uncovered_gu_usage_prefers_d010() -> None:
    assert simple_building_usage_count("남구 창고시설 건물 몇 채야?")
    assert not needs_d198_building_grain("남구 창고시설 건물 몇 채야?")
    assert not place_has_d198_coverage("남구 창고시설 건물 몇 채야?")


def test_covered_gu_usage_prefers_d198() -> None:
    set_d198_coverage({"동래구": "AL_D198_26260_20250115"})
    assert place_has_d198_coverage("온천동에서 숙박시설 건물은 몇 채야?")
    assert needs_d198_building_grain("온천동에서 숙박시설 건물은 몇 채야?")
    assert not simple_building_usage_count("온천동에서 숙박시설 건물은 몇 채야?")
    ir = QueryIR(
        task="count",
        entity="building",
        scope=ScopeIR(place="온천동", place_kind="legal_dong"),
        predicates=[PredicateIR(field="usage", operator="eq", value="숙박시설")],
    )
    assert resolve_dataset_grain(ir, "온천동에서 숙박시설 건물은 몇 채야?") == "d198"


def test_covered_gu_usage_with_metric_still_d198() -> None:
    set_d198_coverage({"금정구": "AL_D198_26410_20250115"})
    ir = QueryIR(
        task="count",
        entity="building",
        scope=ScopeIR(place="금정구", place_kind="gu"),
        predicates=[
            PredicateIR(field="usage", operator="eq", value="창고시설"),
            PredicateIR(field="gross_floor_area_m2", operator="gte", value=500),
        ],
    )
    assert (
        resolve_dataset_grain(ir, "금정구 창고시설 중 연면적 500 이상 건물 몇 채야?")
        == "d198"
    )


def test_uncovered_gu_usage_with_metric_d010() -> None:
    ir = QueryIR(
        task="count",
        entity="building",
        scope=ScopeIR(place="남구"),
        predicates=[
            PredicateIR(field="usage", operator="eq", value="창고시설"),
            PredicateIR(field="gross_floor_area_m2", operator="gte", value=500),
        ],
    )
    assert resolve_dataset_grain(ir, "남구 창고시설 중 연면적 500 이상 건물 몇 채야?") == "d010"
    assert not query_ir_needs_d198(ir, "남구 창고시설 중 연면적 500 이상 건물 몇 채야?")


def test_detail_usage_needs_d198() -> None:
    assert needs_d198_building_grain("해운대구 아파트 몇 채야?")
    assert not simple_building_usage_count("해운대구 아파트 몇 채야?")


def test_detail_usage_field_needs_d198() -> None:
    ir = QueryIR(
        task="count",
        entity="building",
        predicates=[PredicateIR(field="detail_usage", operator="eq", value="아파트")],
    )
    assert resolve_dataset_grain(ir, "해운대구 아파트 몇 채야?") == "d198"


def test_busan_wide_usage_prefers_d010() -> None:
    set_d198_coverage({"금정구": "AL_D198_26410_20250115"})
    assert not place_has_d198_coverage("부산 단독주택 몇 채야?")
    assert simple_building_usage_count("부산 단독주택 몇 채야?")
