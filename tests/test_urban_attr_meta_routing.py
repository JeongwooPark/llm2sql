"""업로드 표시명 데이터셋: 목록·속성·메타 라우팅 회귀."""

from __future__ import annotations

import re

import pytest

from txt2sql.catalog_attrs import is_catalog_attr_question
from txt2sql.chart_qa import (
    attach_chart_offer,
    build_chart_spec,
    is_chart_accept_question,
    is_chart_series_filter_question,
)
from txt2sql.clarify_qa import _unknown_terms
from txt2sql.domain import extract_gu, extract_place, looks_like_building_name_lookup
from txt2sql.meta_qa import (
    answer_metadata_question,
    is_metadata_question,
)
from txt2sql.named_dataset_qa import (
    _label_hits_query,
    is_named_dataset_data_question,
    try_named_dataset_query,
)
from txt2sql.pipeline import _try_chart_turn
from txt2sql.progress import ProgressTracker
from txt2sql.schema_retriever import apply_admin_boost, pin_named_dataset_schema
from txt2sql.semantic_meta import tables_matching_exact_display_names
from txt2sql.session import SessionContext

URBAN = "활동인구 1인당 시가화용지 활용/미활용 면적_행정동"
URBAN_TABLE = "adm_urban_area_per_capita"
META_ROWS = [
    {
        "table_name": URBAN_TABLE,
        "display_name": URBAN,
        "description": "",
        "category": "토지",
    },
    {
        "table_name": "pnu_def",
        "display_name": "PNU정의",
        "description": "",
        "category": "코드",
    },
]


@pytest.fixture(scope="module")
def live_db():
    """실DB가 있을 때만 통합 검증."""
    try:
        from txt2sql.config import load_settings
        from txt2sql.db import connect

        settings = load_settings()
        with connect(settings.database_url) as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM adm_urban_area_per_capita"
            ).fetchone()["n"]
            if int(n) <= 0:
                pytest.skip("adm_urban_area_per_capita empty")
            yield conn
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"db unavailable: {exc}")


def test_urban_attribute_question_is_metadata_not_catalog() -> None:
    q = f"{URBAN}의 속성데이터는?"
    assert looks_like_building_name_lookup(q) is False
    assert is_catalog_attr_question(q) is False
    assert is_metadata_question(q) is True
    assert is_named_dataset_data_question(q) is False


def test_population_topic_availability_is_metadata() -> None:
    q = "현재 인구관련 데이터가 있는가?"
    assert is_metadata_question(q) is True
    assert is_named_dataset_data_question(q) is False


def test_building_height_count_not_stolen_by_named_dataset(live_db) -> None:
    """건물 임계/건수는 테이블명과 무관하게 named_dataset 비적격."""
    from txt2sql.named_dataset_qa import _named_dataset_ineligible
    from txt2sql.place_area_qa import is_place_boundary_area_question

    q = "금정구에서 높이 65m 이상인 건물 수를 알려줘"
    assert _named_dataset_ineligible(q) is True
    assert try_named_dataset_query(live_db, q) is None

    assert (
        try_named_dataset_query(
            live_db, "금정구 단독주택 중 건축물면적 80~200㎡인 건물을 보여줘"
        )
        is None
    )
    assert try_named_dataset_query(live_db, "구·군별 평균 기초구역 면적을 보여줘") is None

    assert is_place_boundary_area_question("구서동의 면적은?") is True
    assert is_place_boundary_area_question("구서동 연면적은?") is False
    assert is_place_boundary_area_question("구서동 건물 면적은?") is False


def test_place_area_excludes_basemap_and_aggregates() -> None:
    """기초구역·산업단지·집계 면적은 place_area가 선점하지 않는다."""
    from txt2sql.named_dataset_qa import _named_dataset_ineligible
    from txt2sql.place_area_qa import is_place_boundary_area_question

    assert is_place_boundary_area_question("구서동의 면적은?") is True
    assert is_place_boundary_area_question("금정구 면적") is True
    assert is_place_boundary_area_question("해운대구 기초구역의 평균 면적") is False
    assert is_place_boundary_area_question("부산 산업단지 면적의 합계") is False
    assert is_place_boundary_area_question("부산 기초구역 면적의 합계") is False
    assert _named_dataset_ineligible("부산 건축물면적의 분산을 알려줘") is True
    assert _named_dataset_ineligible("동래구에서 허가일부터 사용승인일까지의 일수 중앙값") is True


def test_ambiguous_measure_label_does_not_bind_alone() -> None:
    """「면적」「높이」단독은 변별력 없는 라벨로 취급."""
    from txt2sql.named_dataset_qa import (
        _is_ambiguous_measure_only,
        _is_distinctive_attr_label,
        _named_dataset_ineligible,
    )

    assert _is_distinctive_attr_label("높이") is False
    assert _is_distinctive_attr_label("면적") is False
    assert _is_distinctive_attr_label("10대 활동인구") is True
    assert _is_distinctive_attr_label("시가화용지 면적") is True
    assert _is_ambiguous_measure_only([("height", "높이")]) is True
    assert _named_dataset_ineligible("면적 100㎡ 이상 건물 수") is True
    assert _named_dataset_ineligible("금정구 10세 유동인구") is False
    assert _named_dataset_ineligible("구서동의 면적은?") is False
    assert _named_dataset_ineligible("성남시 20대 유동인구") is False
    assert (
        _named_dataset_ineligible(
            "금정구 단독주택 중 건축물면적 80~200㎡인 건물을 보여줘"
        )
        is True
    )
    assert _named_dataset_ineligible("구·군별 평균 기초구역 면적을 보여줘") is True


def test_urban_place_subset_is_not_chart_series_filter() -> None:
    q = f"{URBAN}의 데이터 중 금정구의 것만"
    assert is_chart_series_filter_question(q) is False
    assert is_catalog_attr_question(q) is False
    assert _try_chart_turn(q, SessionContext(), ProgressTracker(), None) is None


def test_urban_place_list_is_not_building_name_lookup() -> None:
    q = f"{URBAN}의 데이터 중 금정구의 것만 출력하라."
    assert looks_like_building_name_lookup(q) is False
    assert is_named_dataset_data_question(q) is True
    unknown = _unknown_terms(q, place=extract_place(q), gu=extract_gu(q))
    assert "출력하라" not in unknown


def test_exact_display_name_pins_urban_not_pnu() -> None:
    q = f"{URBAN}의 데이터 중 금정구의 것만 출력하라."
    exact = tables_matching_exact_display_names(q, META_ROWS)
    assert exact == [URBAN_TABLE]
    assert apply_admin_boost(q, [URBAN_TABLE], named_dataset_pin=True) == [
        URBAN_TABLE,
        "BND_ADM_DONG_PG",
    ]
    pinned, tip = pin_named_dataset_schema(
        q, ["pnu_def", "AL_D198_26410_20260715", URBAN_TABLE], exact
    )
    assert pinned[0] == URBAN_TABLE
    assert "pnu_def" not in pinned
    assert "MUST use" in tip


def test_age_band_label_does_not_match_shorter_band() -> None:
    assert _label_hits_query("10대 활동인구", "금정구 10세 유동인구", "금정구10세유동인구")
    assert not _label_hits_query("0대 활동인구", "금정구 10세 유동인구", "금정구10세유동인구")


def test_named_dataset_place_list_sql(live_db) -> None:
    from txt2sql.clarify_qa import check_ambiguity

    q = f"{URBAN}의 데이터 중 금정구의 것만 출력하라."
    named = try_named_dataset_query(live_db, q)
    assert named is not None
    assert named.table == URBAN_TABLE
    assert "adm_urban_area_per_capita" in named.sql
    assert "pnu_def" not in named.sql
    assert check_ambiguity(live_db, q) is None
    rows = live_db.execute(named.sql).fetchall()
    assert len(rows) == 16
    names = {r["ADM_NM"] for r in rows}
    assert "구서1동" in names
    assert "장전2동" in names


def test_named_dataset_attr_fuzzy_age_population(live_db) -> None:
    q = f"{URBAN}의 금정구 10세 유동인구를 보여줘."
    named = try_named_dataset_query(live_db, q)
    assert named is not None
    assert named.intent == "named_dataset_attr"
    assert any(c == "n10" for c, _ in named.matched_columns)
    rows = live_db.execute(named.sql).fetchall()
    assert len(rows) == 16
    assert "n10" in rows[0]


def test_named_dataset_dong_urban_area(live_db) -> None:
    q = f"{URBAN}의 구서1동 시가화용지 면적은?"
    named = try_named_dataset_query(live_db, q)
    assert named is not None
    rows = live_db.execute(named.sql).fetchall()
    assert len(rows) == 1
    assert rows[0]["ADM_NM"] == "구서1동"
    assert float(rows[0]["urban_area"]) > 800000


def test_column_only_binds_urban_n20_busan(live_db) -> None:
    q = "20대 활동인구를 부산만 출력하라"
    named = try_named_dataset_query(live_db, q)
    assert named is not None
    assert named.table == URBAN_TABLE
    assert any(c == "n20" for c, _ in named.matched_columns)
    assert "AL_D010" not in named.sql
    assert "pnu_def" not in named.sql
    assert "21%" in named.sql
    rows = live_db.execute(named.sql).fetchall()
    assert len(rows) == 100
    assert "n20" in rows[0]
    assert "ADM_NM" in rows[0]


def test_place_and_age_population_without_output_cue(live_db) -> None:
    q = "금정구 10세 유동인구"
    named = try_named_dataset_query(live_db, q)
    assert named is not None
    assert named.table == URBAN_TABLE
    assert any(c == "n10" for c, _ in named.matched_columns)
    assert "AL_D010" not in named.sql
    assert "21110" in named.sql or "sigungu_cd" in named.sql
    rows = live_db.execute(named.sql).fetchall()
    assert len(rows) == 16
    assert "n10" in rows[0]


def test_seongnam_city_uses_codes_not_adm_nm_ilike(live_db) -> None:
    """성남시는 ADM_NM에 안 나오므로 법정동/센서스 코드로 필터한다."""
    from txt2sql.named_dataset_qa import _admin_match_prefixes

    assert "4113" in _admin_match_prefixes("성남시")
    q = "성남시 20대 유동인구"
    named = try_named_dataset_query(live_db, q)
    assert named is not None
    assert named.table == URBAN_TABLE
    assert "ADM_NM" not in named.sql or "%성남시%" not in named.sql
    assert any(c == "n20" for c, _ in named.matched_columns)
    rows = live_db.execute(named.sql).fetchall()
    assert len(rows) == 50
    assert "n20" in rows[0]
    names = {r["ADM_NM"] for r in rows}
    assert "야탑1동" in names or "수진1동" in names


def test_bundang_gu_age_population(live_db) -> None:
    q = "분당구 20대 유동인구"
    named = try_named_dataset_query(live_db, q)
    assert named is not None
    rows = live_db.execute(named.sql).fetchall()
    assert len(rows) == 22
    assert "n20" in rows[0]


def test_busan_age_population_area_ratio(live_db) -> None:
    from txt2sql.named_dataset_qa import (
        _resolve_area_unit,
        _wants_area_ratio,
        _load_columns,
    )

    q = "부산시 20대 유동인구를 면적대비 비율로 바꿔서 출력"
    assert _wants_area_ratio(q)
    cols = _load_columns(live_db, URBAN_TABLE)
    ha_col = next(c for c in cols if c["column_name"] == "ha")
    # unit 메타가 m2여도 설명·컬럼명상 헥타르면 ha로 해석
    assert _resolve_area_unit(ha_col) == "ha"
    named = try_named_dataset_query(live_db, q)
    assert named is not None
    assert "n20_per_m2" in named.sql
    assert "* 10000" in named.sql
    assert re.search(r'AS\s+"n20_per_m2"', named.sql)
    rows = live_db.execute(named.sql).fetchall()
    assert len(rows) == 100
    assert "n20_per_m2" in rows[0]
    assert "area_m2" in rows[0]
    assert float(rows[0]["n20_per_m2"]) >= float(rows[-1]["n20_per_m2"])
    assert float(rows[0]["n20_per_m2"]) < 1.0


def test_resolve_area_unit_respects_true_m2_metadata() -> None:
    from txt2sql.named_dataset_qa import _resolve_area_unit

    assert (
        _resolve_area_unit(
            {
                "column_name": "urban_area",
                "unit": "㎡",
                "description": "시가화용지 면적",
            }
        )
        == "m2"
    )
    assert (
        _resolve_area_unit(
            {
                "column_name": "ha",
                "unit": "m2",
                "description": "헥타르 단위로 환산한 값",
            }
        )
        == "ha"
    )
    assert (
        _resolve_area_unit(
            {
                "column_name": "admin_area",
                "unit": "m2",
                "description": "행정동 면적",
            }
        )
        == "m2"
    )


def test_population_topic_availability_finds_urban(live_db) -> None:
    q = "현재 인구관련 데이터가 있는가?"
    ans = answer_metadata_question(live_db, q)
    assert ans is not None
    assert ans.intent == "meta_topic_availability"
    assert URBAN_TABLE in (ans.tables or [])
    assert "없습니다" not in ans.answer
    assert "있" in ans.answer
    assert URBAN in ans.answer or URBAN_TABLE in ans.answer


def test_place_boundary_area_guseo_and_gu(live_db) -> None:
    from txt2sql.place_area_qa import try_place_boundary_area_query
    from txt2sql.semantic_plan.generator import try_heuristic_plan

    q = "구서동의 면적은?"
    plan = try_heuristic_plan(q)
    assert plan is None or not any(
        "건축면적·연면적·대지면적" in a for a in (plan.ambiguities or [])
    )
    named = try_place_boundary_area_query(live_db, q)
    assert named is not None
    assert named.intent == "named_dataset_place_area"
    assert "area_m2" in named.sql
    rows = live_db.execute(named.sql).fetchall()
    assert len(rows) == 2
    names = {r["ADM_NM"] for r in rows}
    assert names == {"구서1동", "구서2동"}
    total = sum(float(r["area_m2"]) for r in rows)
    assert total > 6_000_000  # ≈ (265+361)*10000

    gu_q = "금정구 면적"
    gu_named = try_place_boundary_area_query(live_db, gu_q)
    assert gu_named is not None
    gu_rows = live_db.execute(gu_named.sql).fetchall()
    assert len(gu_rows) == 16

    busan_q = "부산시 면적"
    busan = try_place_boundary_area_query(live_db, busan_q)
    assert busan is not None
    assert "SUM(" in busan.sql or "sum(" in busan.sql.lower()
    b_rows = live_db.execute(busan.sql).fetchall()
    assert len(b_rows) == 1
    assert int(b_rows[0]["adm_dong_n"]) == 205
    assert float(b_rows[0]["area_m2"]) > 0


def test_named_dataset_chart_offer_and_followup_accept() -> None:
    """named_dataset_attr 결과 → 차트 제안 → 「차트로 표시하라」 렌더."""
    q = "금정구 10세 유동인구"
    follow = "차트로 표시하라"
    rows = [
        {"ADM_NM": "구서1동", "BASE_DATE": "20230701", "n10": 1903.45},
        {"ADM_NM": "구서2동", "BASE_DATE": "20230701", "n10": 2961.53},
        {"ADM_NM": "남산동", "BASE_DATE": "20230701", "n10": 3387.61},
    ]
    assert is_chart_accept_question(follow)
    assert "표시하라" not in _unknown_terms(follow, place=None, gu=None)

    offered = attach_chart_offer(
        {
            "ok": True,
            "answer": "금정구 10세 유동인구는 …",
            "route": "named_dataset_attr",
            "rows": rows,
            "tables": [URBAN_TABLE],
        },
        question=q,
    )
    assert offered.get("chart_offer") is True
    assert offered.get("chart_spec")
    assert "차트로 보시겠어요" in str(offered.get("answer") or "")

    session = SessionContext()
    session.update_from_result(q, offered)
    assert session.pending_chart is not None
    assert len(session.last_rows) == 3

    result = _try_chart_turn(follow, session, ProgressTracker(), None)
    assert result is not None
    assert result["route"] == "chart_render"
    assert result.get("chart")
    assert result["chart"]["labels"] == ["구서1동", "구서2동", "남산동"]
    assert result["chart"]["datasets"][0]["data"] == [1903.45, 2961.53, 3387.61]


def test_named_dataset_chart_rebuild_without_pending() -> None:
    """pending_chart 없어도 last_rows로 「차트로 표시하라」 재구성."""
    rows = [
        {"ADM_NM": "부곡1동", "n10": 651.11},
        {"ADM_NM": "부곡2동", "n10": 2129.67},
    ]
    session = SessionContext()
    session.last_route = "named_dataset_attr"
    session.last_rows = list(rows)
    session.last_question = "금정구 10세 유동인구"
    session.last_full_question = "금정구 10세 유동인구"
    assert session.pending_chart is None

    spec = build_chart_spec(
        route="named_dataset_attr", rows=rows, question="금정구 10세 유동인구"
    )
    assert spec is not None
    assert spec["type"] == "bar"

    result = _try_chart_turn("차트로 표시하라", session, ProgressTracker(), None)
    assert result is not None
    assert result["route"] == "chart_render"
    assert result["chart"]["labels"] == ["부곡1동", "부곡2동"]
