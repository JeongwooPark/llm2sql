"""행정·법정동·시·구 등 경계 면적 질의 (건물 연면적/건축면적과 구분)."""

from __future__ import annotations

import re
from typing import Any

import psycopg

from txt2sql.domain import extract_gu, extract_place
from txt2sql.gazetteer import resolve_place_kind
from txt2sql.named_dataset_qa import (
    NamedDatasetQuery,
    _BUILDING_ENTITY_CUES,
    _STATS_OP_CUES,
    _area_denominator_sql,
    _load_columns,
    _place_where,
    _preferred_name_col,
    _question_wants_busan,
    _resolve_area_unit,
)

_BUILDING_AREA_CUES = (
    "연면적",
    "건축면적",
    "건물면적",
    "건축물면적",
    "대지면적",
    "건폐",
    "용적",
)
# 행정 경계가 아닌 면적 엔티티 — place_area shallow path 제외
_NON_BOUNDARY_AREA_ENTITIES = (
    "기초구역",
    "산업단지",
    "필지",
    "필지면적",
)
# place_area는 「장소의 면적은?」만 — 집계·통계는 SQP/router
_BOUNDARY_AREA_AGG_CUES = (
    "평균",
    "합계",
    "총합",
    "비율",
) + tuple(_STATS_OP_CUES)
_AREA_ASK_CUES = (
    "면적",
    "넓이",
)


def is_place_boundary_area_question(question: str) -> bool:
    """「구서동의 면적은?」「금정구 면적」「부산시 면적」류."""
    q = question.strip()
    if not q:
        return False
    if not any(k in q for k in _AREA_ASK_CUES):
        return False
    if any(k in q for k in _BUILDING_AREA_CUES):
        return False
    if any(k in q for k in _BUILDING_ENTITY_CUES):
        return False
    if any(k in q for k in _NON_BOUNDARY_AREA_ENTITIES):
        return False
    if any(k in q for k in _BOUNDARY_AREA_AGG_CUES):
        return False
    if "시가화" in q:
        return False
    if any(
        k in q
        for k in (
            "면적대비",
            "면적 대비",
            "면적당",
            "면적 당",
            "밀도",
            "비율로 바꿔",
            "비율로바꿔",
        )
    ):
        return False
    place = extract_place(q)
    gu = extract_gu(q)
    busan = _question_wants_busan(q)
    if not place and not gu and not busan:
        return False
    if re.search(r"\d+\s*(㎡|m2|평|ha|헥타)", q, flags=re.I):
        return False
    if any(k in q for k in ("이상", "이하", "초과", "미만", "상위", "가장 큰", "가장큰")):
        return False
    return True


def try_place_boundary_area_query(
    conn: psycopg.Connection,
    question: str,
) -> NamedDatasetQuery | None:
    if not is_place_boundary_area_question(question):
        return None

    table = _urban_area_table(conn)
    if not table:
        return None
    cols = _load_columns(conn, table)
    if not cols:
        return None
    area_physical = _pick_admin_area_col(cols)
    if not area_physical:
        return None
    name_col = _preferred_name_col(cols)
    if not name_col:
        return None

    q = question.strip()
    gu = extract_gu(q)
    place = extract_place(q)
    busan = _question_wants_busan(q)

    if place and place.endswith(("구", "군", "시")):
        gu = gu or place
        place = None
    if place in {"부산", "부산시", "부산광역시"}:
        place = None
        busan = True

    kind = (
        resolve_place_kind(place or gu, q)
        if (place or gu)
        else ("sido" if busan else "unknown")
    )

    where: list[str] = []
    if place and name_col:
        where = ["(" + " OR ".join(_admin_dong_name_predicates(name_col, place)) + ")"]
    else:
        where = _place_where(
            conn,
            table,
            cols,
            gu=gu,
            place=None,
            busan_wide=busan and not gu,
        )

    if not where:
        return None

    denom_sql, _, area_alias = _area_denominator_sql(area_physical, cols)
    where_sql = " AND ".join(where)

    aggregate = kind == "sido" or (busan and not place and not gu)
    if aggregate:
        sql = (
            f'SELECT COUNT(*)::int AS "adm_dong_n", '
            f'SUM({denom_sql}) AS "{area_alias}"\n'
            f'FROM "{table}"\n'
            f"WHERE {where_sql};"
        )
        select_cols: tuple[str, ...] = ("adm_dong_n", area_alias)
    else:
        sql = (
            f'SELECT "{name_col}", {denom_sql} AS "{area_alias}", '
            f'"{area_physical}"\n'
            f'FROM "{table}"\n'
            f"WHERE {where_sql}\n"
            f'ORDER BY "{name_col}" ASC NULLS LAST\n'
            f"LIMIT 100;"
        )
        select_cols = (name_col, area_alias, area_physical)

    display = "행정구역 면적"
    try:
        row = conn.execute(
            """
            SELECT display_name FROM table_metadata
            WHERE schema_name = 'public' AND table_name = %s
            """,
            (table,),
        ).fetchone()
        if row and row.get("display_name"):
            display = str(row["display_name"])
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass

    return NamedDatasetQuery(
        intent="named_dataset_place_area",
        sql=sql,
        table=table,
        display_name=display,
        select_cols=select_cols,
        matched_columns=((area_physical, "행정동 면적"),),
    )


def _urban_area_table(conn: psycopg.Connection) -> str | None:
    try:
        row = conn.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = 'adm_urban_area_per_capita'
            """
        ).fetchone()
        if row:
            return str(row["table_name"])
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    return None


def _pick_admin_area_col(cols: list[dict[str, Any]]) -> str | None:
    by = {str(c["column_name"]).lower(): c for c in cols}
    for key in ("ha", "sqkm"):
        if key in by:
            return str(by[key]["column_name"])
    for col in cols:
        name = str(col["column_name"])
        disp = str(col.get("display_name") or "")
        if name.lower() == "urban_area":
            continue
        if "면적" in disp and _resolve_area_unit(col) in {"ha", "sqkm", "m2"}:
            return name
    return None


def _admin_dong_name_predicates(name_col: str, place: str) -> list[str]:
    """법정동 구서동 → 구서1동·구서2동, 또는 동일 행정동명."""
    place = place.strip()
    if not place:
        return []
    esc = place.replace("'", "''")
    preds = [f'"{name_col}" = \'{esc}\'']
    if re.search(r"\d+동$", place):
        return preds
    stem = place[:-1] if place.endswith(("동", "읍", "면", "가", "리")) else place
    if len(stem) < 2:
        return preds
    # PostgreSQL POSIX: 구서 + 선택적 숫자 + 동
    preds.append(f"\"{name_col}\" ~ '^{re.escape(stem)}[0-9]*동$'")
    return preds
