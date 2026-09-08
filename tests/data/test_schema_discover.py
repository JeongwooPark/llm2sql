"""스키마 검색 목록·메타데이터 동의어 자동 반영."""

from __future__ import annotations

from txt2sql.schema_retriever import is_searchable_table_name, searchable_fqnames
from txt2sql.semantic_meta import (
    distinctive_label_tokens,
    synonyms_from_labels,
    table_synonyms,
    tables_matching_labels,
)

URBAN_DISPLAY = "활동인구 1인당 시가화용지 활용/미활용 면적_행정동"


def test_urban_display_tokens() -> None:
    tokens = distinctive_label_tokens(URBAN_DISPLAY)
    assert "시가화용지" in tokens
    assert "활동인구" in tokens
    assert "면적" not in tokens
    assert "행정동" not in tokens


def test_table_synonyms_from_metadata() -> None:
    syns = table_synonyms(
        "adm_urban_area_per_capita",
        display_name=URBAN_DISPLAY,
        category="토지",
    )
    assert "시가화용지" in syns
    assert "활동인구" in syns
    assert "토지" in syns


def test_exact_display_pins_urban_table() -> None:
    from txt2sql.semantic_meta import tables_matching_exact_display_names

    q = "활동인구 1인당 시가화용지 활용/미활용 면적_행정동의 데이터 중 금정구의 것만 출력하라."
    matched = tables_matching_exact_display_names(
        q,
        [
            {
                "table_name": "adm_urban_area_per_capita",
                "display_name": URBAN_DISPLAY,
                "description": "",
                "category": "토지",
            },
            {
                "table_name": "pnu_def",
                "display_name": "PNU정의",
                "description": "",
                "category": "코드",
            },
        ],
    )
    assert matched == ["adm_urban_area_per_capita"]


def test_label_boost_picks_urban_table() -> None:
    matched = tables_matching_labels(
        "행정동별 시가화용지 면적은?",
        [
            {
                "table_name": "adm_urban_area_per_capita",
                "display_name": URBAN_DISPLAY,
                "description": "",
                "category": "토지",
            },
            {
                "table_name": "AL_D010_26_20250704",
                "display_name": "GIS건물통합정보",
                "description": "",
                "category": "건물",
            },
        ],
    )
    assert matched == ["adm_urban_area_per_capita"]


def test_generic_area_question_does_not_boost_urban() -> None:
    matched = tables_matching_labels(
        "금정구 연면적 합계는?",
        [
            {
                "table_name": "adm_urban_area_per_capita",
                "display_name": URBAN_DISPLAY,
                "description": "",
                "category": "토지",
            }
        ],
    )
    assert matched == []


def test_busan_city_token_does_not_boost_d198() -> None:
    matched = tables_matching_labels(
        "부산광역시 시가화용지 면적",
        [
            {
                "table_name": "AL_D198_26110_20260715",
                "display_name": "용도별건물공간정보_부산광역시 중구",
                "description": "",
                "category": "건물",
            },
            {
                "table_name": "adm_urban_area_per_capita",
                "display_name": URBAN_DISPLAY,
                "description": "",
                "category": "토지",
            },
        ],
    )
    assert matched == ["adm_urban_area_per_capita"]


def test_known_building_synonyms_unchanged() -> None:
    syns = table_synonyms("AL_D010_26_20250704")
    assert "건물" in syns
    assert "GIS건물통합정보" in syns


def test_column_label_tokens() -> None:
    parts = synonyms_from_labels("시가화용지 면적(㎡)")
    assert "시가화용지" in parts


def test_searchable_name_filters() -> None:
    assert is_searchable_table_name("adm_urban_area_per_capita")
    assert is_searchable_table_name("public.pnu_def")
    assert not is_searchable_table_name("table_metadata")
    assert not is_searchable_table_name("temp_upload")
    assert not is_searchable_table_name("llm_schema_catalog")


def test_seed_searchable_includes_core() -> None:
    names = searchable_fqnames()
    assert "public.AL_D010_26_20250704" in names
    assert "public.pnu_def" in names
    assert "public.table_metadata" not in names
