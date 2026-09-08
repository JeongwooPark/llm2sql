from txt2sql.chart_qa import (
    build_chart_spec,
    filter_chart_series,
    is_chart_metric_draw_question,
    is_chart_series_filter_question,
)
from txt2sql.pipeline import _try_chart_turn
from txt2sql.progress import ProgressTracker
from txt2sql.session import SessionContext

COMPARE_ROWS = [
    {
        "label": "금정구",
        "cnt": 38794,
        "avg_area": 354.8,
        "avg_height": 12.7,
        "avg_far": 173.3,
        "avg_floors": 1.7,
    },
    {
        "label": "사하구",
        "cnt": 41271,
        "avg_area": 445.0,
        "avg_height": 11.8,
        "avg_far": 145.3,
        "avg_floors": 1.7,
    },
]


def _compare_spec(question: str = "금정구와 사하구 건물을 비교") -> dict:
    spec = build_chart_spec(
        route="building_profile_compare",
        rows=COMPARE_ROWS,
        question=question,
    )
    assert spec is not None
    return spec


def test_metric_draw_detects_far_without_man() -> None:
    q = "평균용적율로 그려라"
    assert is_chart_metric_draw_question(q)
    assert not is_chart_series_filter_question(q)
    assert is_chart_series_filter_question("용적율만으로 그려라")


def test_compare_all_datasets_includes_far_when_default_is_height() -> None:
    spec = _compare_spec()
    all_labels = [d["label"] for d in spec["all_datasets"]]
    shown = [d["label"] for d in spec["datasets"]]
    assert "평균 용적율(%)" in all_labels
    assert "평균 높이(m)" in shown
    assert "평균 용적율(%)" not in shown


def test_filter_keeps_only_far() -> None:
    spec = _compare_spec()
    chart, answer = filter_chart_series(spec, "평균용적율로 그려라")
    assert chart is not None
    assert len(chart["datasets"]) == 1
    assert "용적율" in chart["datasets"][0]["label"]
    assert chart["datasets"][0]["data"] == [173.3, 145.3]
    assert chart["unit"] == "%"
    assert "용적율" in answer


def test_try_chart_turn_draws_far_from_pending() -> None:
    spec = _compare_spec()
    session = SessionContext()
    session.pending_chart = spec
    session.last_chart = spec
    session.last_route = "building_profile_compare"
    session.last_rows = list(COMPARE_ROWS)
    session.last_sql = 'SELECT 1 FROM "AL_D010_26_20250704"'
    result = _try_chart_turn(
        "평균용적율로 그려라", session, ProgressTracker(), None
    )
    assert result is not None
    assert result["route"] == "chart_render"
    assert "확인이 필요" not in (result.get("answer") or "")
    datasets = result["chart"]["datasets"]
    assert len(datasets) == 1
    assert datasets[0]["data"] == [173.3, 145.3]


def test_rebuild_far_when_old_spec_omits_it() -> None:
    """이전에 높이만 all_datasets에 넣었던 세션도 last_rows로 복구한다."""
    session = SessionContext()
    session.pending_chart = {
        "type": "bar",
        "title": "지역 비교",
        "labels": ["금정구", "사하구"],
        "datasets": [
            {"label": "건물 수(동)", "data": [38794.0, 41271.0]},
            {"label": "평균 높이(m)", "data": [12.7, 11.8]},
        ],
        "all_datasets": [
            {"label": "건물 수(동)", "data": [38794.0, 41271.0]},
            {"label": "평균 높이(m)", "data": [12.7, 11.8]},
        ],
        "unit": "동·m",
    }
    session.last_chart = dict(session.pending_chart)
    session.last_route = "building_profile_compare"
    session.last_rows = list(COMPARE_ROWS)
    result = _try_chart_turn(
        "평균용적율로 그려라", session, ProgressTracker(), None
    )
    assert result is not None
    assert result["route"] == "chart_render"
    assert result["chart"]["datasets"][0]["data"] == [173.3, 145.3]


def test_accept_chart_display_from_named_dataset_rows() -> None:
    session = SessionContext()
    session.last_route = "named_dataset_attr"
    session.last_rows = [
        {"ADM_NM": "구서1동", "n10": 1903.45},
        {"ADM_NM": "남산동", "n10": 3387.61},
    ]
    session.last_full_question = "금정구 10세 유동인구"
    result = _try_chart_turn("차트로 표시하라", session, ProgressTracker(), None)
    assert result is not None
    assert result["route"] == "chart_render"
    assert "확인이 필요" not in (result.get("answer") or "")
    assert result["chart"]["labels"] == ["구서1동", "남산동"]
