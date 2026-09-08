from __future__ import annotations

from txt2sql.config import load_settings
from txt2sql.data.coverage import refresh_dataset_coverage
from txt2sql.dataset_grain import needs_d198_building_grain, place_has_d198_coverage
from txt2sql.domain import D198_BY_GU, d198_gu_for_dong
from txt2sql.gazetteer import sigungu_for_legal_dong
from txt2sql.named_dataset_qa import _named_dataset_ineligible
from txt2sql.place_area_qa import is_place_boundary_area_question
from txt2sql.semantic_plan.generator import _agg_metrics


def test_homonym_dong_prefers_d198_covered_gu() -> None:
    """동명이의 시 primary sido + D198 커버 구를 고른다 (D010 퇴화 방지)."""
    settings = load_settings()
    refresh_dataset_coverage(settings)
    assert D198_BY_GU, "D198 coverage required"
    q = "남산동 제2종근린생활시설의 평균 높이를 알려줘"
    gu = d198_gu_for_dong("남산동", question=q)
    assert gu == "금정구"
    assert place_has_d198_coverage(q) is True
    assert needs_d198_building_grain(q) is True
    assert sigungu_for_legal_dong("구서동", question="구서동 아파트 평균 높이") == "금정구"


def test_agg_metrics_prefers_building_age_over_height() -> None:
    assert _agg_metrics("금정구에서 사용승인일이 기록된 건물의 평균 건축연령은?") == [
        "building_age_years"
    ]


def test_place_area_and_stats_gates() -> None:
    assert is_place_boundary_area_question("구서동의 면적은?") is True
    assert is_place_boundary_area_question("해운대구 기초구역의 평균 면적") is False
    assert _named_dataset_ineligible("부산 건축물면적의 분산을 알려줘") is True
