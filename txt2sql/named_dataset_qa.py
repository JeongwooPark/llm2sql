"""업로드/커스텀 테이블: 표시명 정확 매칭 기반 목록·속성 조회."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import psycopg

from txt2sql.domain import (
    BUSAN_GU_CODES,
    busan_gu_code,
    extract_gu,
    extract_place,
    is_busan_wide,
)
from txt2sql.gazetteer import SIDO_CENSUS_PREFIX, load_gazetteer
from txt2sql.semantic_meta import (
    distinctive_label_tokens,
    tables_matching_exact_display_names,
)

_LIST_CUES = (
    "출력",
    "보여",
    "목록",
    "리스트",
    "조회",
    "나열",
    "데이터 중",
    "자료 중",
    "것만",
    "행만",
    "만 출력",
    "만 보여",
    "만출력",
    "만보여",
)
_ATTR_VALUE_CUES = (
    "얼마",
    "값은",
    "수치",
    "보여",
    "출력",
    "조회",
    "알려",
    "은?",
    "는?",
    "인가",
)
# 짧고 다의적인 측정 라벨 — 이것만으로는 테이블 바인딩하지 않음
_AMBIGUOUS_MEASURE_LABELS = frozenset(
    {
        "높이",
        "면적",
        "층수",
        "용도",
        "이름",
        "주소",
        "지번",
        "구조",
        "건수",
        "개수",
    }
)
_BUILDING_ENTITY_CUES = (
    "건물",
    "건축물",
    "아파트",
    "주택",
    "공동주택",
    "공장",
    "상가",
    "오피스",
    "숙박",
    "창고",
    "학교",
    "종교시설",
    "근린생활",
)
_COUNT_CUES = (
    "몇 채",
    "몇채",
    "몇 개",
    "몇개",
    "건수",
    "채수",
    "개수",
    "모두 몇",
    "총 몇",
)
_RANK_CUES = (
    "상위",
    "하위",
    "가장 큰",
    "가장큰",
    "가장 높은",
    "가장높은",
    "가장 많은",
    "랭킹",
    "순위",
)
# shallow named가 컴파일하지 못하는 통계·분포 연산
_STATS_OP_CUES = (
    "분산",
    "표준편차",
    "중앙값",
    "분위수",
    "분위",
    "상관계수",
    "상관",
    "분산을",
    "중앙값을",
)
_GEOM_UDT = frozenset({"geometry", "geography"})
_NAME_COL_HINTS = (
    "adm_nm",
    "행정동명",
    "행정동 명",
    "동명",
    "dong_nm",
    "dong_name",
    "name",
)
_CODE_COL_HINTS = (
    "sigungu_cd",
    "sigungu",
    "sgg",
    "adm_cd",
    "시군구 코드",
    "시군구코드",
    "행정동 코드",
    "행정동코드",
)
_SYNONYM_PAIRS = (
    ("유동인구", "활동인구"),
    ("활동인구", "유동인구"),
    ("세 ", "대 "),
    ("세", "대"),
)
_AREA_RATIO_CUES = (
    "면적대비",
    "면적 대비",
    "면적당",
    "면적 당",
    "면적 기준",
    "면적기준",
    "밀도",
)
_AREA_RATIO_TRANSFORM = (
    "비율로 바꿔",
    "비율로바꿔",
    "비율로 계산",
    "비율로계산",
    "비율로 출력",
    "비율로출력",
    "대비 비율",
    "대비비율",
)


@dataclass(frozen=True)
class NamedDatasetQuery:
    intent: str
    sql: str
    table: str
    display_name: str
    select_cols: tuple[str, ...]
    matched_columns: tuple[tuple[str, str], ...] = ()


def is_named_dataset_data_question(question: str) -> bool:
    """표시명 데이터셋 또는 업로드 컬럼값 조회인지(스키마 메타 제외)."""
    q = question.strip()
    if not q:
        return False
    if any(k in q for k in ("속성데이터", "속성 데이터", "스키마", "컬럼목록", "컬럼 목록")):
        return False
    if "속성" in q and any(k in q for k in ("뭐야", "무엇", "설명", "의미", "알려")):
        # 「속성데이터는?」류는 meta
        if "데이터" in q and not any(k in q for k in _LIST_CUES):
            return False
    # 「알려줘」 등 ATTR 큐만으로 건물 임계/건수를 named로 끌어오지 않음
    if _named_dataset_ineligible(q):
        return False
    if "_" in q or any(k in q for k in ("데이터 중", "자료 중")):
        return True
    if any(k in q for k in _LIST_CUES) or any(k in q for k in _ATTR_VALUE_CUES):
        return True
    # 「금정구 10세 유동인구」처럼 장소+연령/인구 표현
    if extract_gu(q) or extract_place(q) or _question_wants_busan(q):
        if re.search(r"\d+\s*(대|세)", q) and any(
            k in q for k in ("인구", "유동", "활동")
        ):
            return True
    return False


def _wants_named_data_query(q: str) -> bool:
    return any(k in q for k in _LIST_CUES) or any(k in q for k in _ATTR_VALUE_CUES)


def _has_distinctive_column_labels(matched: list[tuple[str, str]]) -> bool:
    """연령대·인구·시가화 등 업로드 특화 컬럼 표시명인지."""
    for _, lab in matched:
        if _is_distinctive_attr_label(lab):
            return True
    return False


def _is_distinctive_attr_label(lab: str) -> bool:
    text = (lab or "").strip()
    if not text:
        return False
    compact = re.sub(r"\s+", "", text)
    if compact in _AMBIGUOUS_MEASURE_LABELS:
        return False
    if re.search(r"\d+\s*(대|세)", text):
        return True
    if any(k in text for k in ("인구", "시가화", "밀도", "유동", "활동")):
        return True
    # 연면적·건축면적 등은 건물 전용이지만 변별력은 있음 → 바인딩은 가능하나
    # 질의 형태 게이트에서 임계/건수면 양보한다.
    if len(compact) >= 4:
        return True
    return False


def _is_ambiguous_measure_only(matched: list[tuple[str, str]]) -> bool:
    """매칭이 모호 측정 라벨뿐이면 True."""
    if not matched:
        return False
    for _, lab in matched:
        compact = re.sub(r"\s+", "", (lab or "").strip())
        if compact not in _AMBIGUOUS_MEASURE_LABELS and not _is_generic_area_label(lab):
            return False
    return True


def _is_generic_area_label(lab: str) -> bool:
    compact = re.sub(r"\s+", "", (lab or "").strip())
    if compact in {"면적", "넓이"}:
        return True
    # 「건축물면적」「연면적」은 변별력 있음
    if compact in _AMBIGUOUS_MEASURE_LABELS:
        return True
    return False


def _named_dataset_ineligible(question: str) -> bool:
    """named_dataset shallow compiler가 소화 못 하는 연산이면 True.

    테이블명 블랙리스트가 아니라 질의 형태·연산자 기준.
    """
    from txt2sql.domain import (
        extract_usage,
        extract_usages,
        looks_like_measure_threshold,
    )
    from txt2sql.query_understanding.contract import extract_contract

    q = question.strip()
    if not q:
        return True

    # 행정구역 경계 면적은 place_area_qa가 담당 — named 적격성도 열어 둠
    from txt2sql.place_area_qa import is_place_boundary_area_question

    if is_place_boundary_area_question(q):
        return False

    # 업로드 특화(인구·시가화) + 장소만이면 허용
    upload_attr = bool(
        re.search(r"\d+\s*(대|세)", q)
        and any(k in q for k in ("인구", "유동", "활동"))
    ) or ("시가화" in q)

    if looks_like_measure_threshold(q) and not upload_attr:
        return True

    # 「80~200㎡」처럼 구간 임계 — 이상/이하 없이도 컴파일 불가
    if (
        not upload_attr
        and re.search(r"\d+(?:\.\d+)?\s*[~～\-–]\s*\d+", q)
        and any(k in q for k in ("면적", "높이", "층", "㎡", "m2", "평", "미터"))
    ):
        return True

    if any(k in q for k in _RANK_CUES) and not upload_attr:
        return True

    # 필드 간 비교(건축물면적 > 연면적 등) — shallow SELECT로 불가
    from txt2sql.query_understanding.operators import COMPARE_PATTERNS

    if any(re.search(pat, q) for pat in COMPARE_PATTERNS):
        return True
    if (
        any(k in q for k in ("보다 큰", "보다 작", "보다 높", "보다 낮"))
        and sum(
            1
            for k in (
                "건축물면적",
                "건축면적",
                "건물면적",
                "연면적",
                "대지면적",
                "높이",
            )
            if k in q
        )
        >= 2
    ):
        return True

    # 분산·중앙값·상관 등 — shallow SELECT로 불가
    if any(k in q for k in _STATS_OP_CUES) and not upload_attr:
        return True

    # 그룹·평균 집계는 shallow SELECT로 불가
    if not upload_attr and any(
        k in q
        for k in (
            "평균",
            "합계",
            "별로",
            "구별",
            "군별",
            "동별",
            "용도별",
            "구·군별",
            "구군별",
            "시군구별",
        )
    ):
        return True

    has_building = any(k in q for k in _BUILDING_ENTITY_CUES)
    has_count = any(k in q for k in _COUNT_CUES) or bool(
        re.search(r"(몇\s*(채|개|동)|채야|개야)", q)
    )
    has_usage = bool(extract_usage(q) or extract_usages(q))
    if has_building and has_count:
        return True
    if has_building and looks_like_measure_threshold(q):
        return True
    # 용도 필터+건물 목록/조회 — named는 용도 WHERE를 컴파일하지 않음
    if has_usage and (has_building or any(k in q for k in ("보여", "목록", "조회", "출력"))):
        if not upload_attr:
            return True

    contract = extract_contract(q)
    op = contract.operation or ""
    if op in {"rank", "group_rank", "percentile"}:
        return True
    # named의 면적대비(_wants_area_ratio)는 업로드 인구·시가화에서 지원
    if op == "ratio" and not upload_attr:
        return True
    if op in {"count", "aggregate"} and not upload_attr:
        return True
    return False


def _question_wants_busan(q: str) -> bool:
    if is_busan_wide(q):
        return True
    return bool(re.search(r"(?<![가-힣])부산(?:만|시|광역|전체|내|에서)?(?![가-힣진])", q))


def try_named_dataset_query(
    conn: psycopg.Connection,
    question: str,
) -> NamedDatasetQuery | None:
    """표시명 또는 고유 컬럼 표시명으로 업로드/커스텀 테이블을 조회한다."""
    q = question.strip()
    if not q:
        return None
    if any(k in q for k in ("속성데이터", "속성 데이터", "스키마", "컬럼목록", "컬럼 목록")):
        return None
    if "속성" in q and any(k in q for k in ("뭐야", "무엇", "설명", "의미")) and "데이터" in q:
        if not _wants_named_data_query(q):
            return None
    # 컴파일 불가 연산 → 테이블과 무관하게 양보
    if _named_dataset_ineligible(q):
        return None

    meta_rows = _load_table_meta(conn)
    exact = tables_matching_exact_display_names(q, meta_rows)
    matched_cols: list[tuple[str, str]] = []
    table: str | None = None
    display = ""
    remainder = q

    if exact:
        table = exact[0]
        display = next(
            (
                str(r.get("display_name") or table)
                for r in meta_rows
                if str(r.get("table_name")) == table
            ),
            table,
        )
        remainder = q.replace(display, " ").strip()
    else:
        bound = _bind_table_by_column_display(conn, q)
        if bound is None:
            return None
        table, matched_cols, display = bound
        if _is_ambiguous_measure_only(matched_cols):
            return None
        if not _wants_named_data_query(q):
            has_scope = bool(
                extract_gu(q) or extract_place(q) or _question_wants_busan(q)
            )
            if not (has_scope or _has_distinctive_column_labels(matched_cols)):
                return None
        for _, lab in matched_cols:
            remainder = remainder.replace(lab, " ")
            remainder = remainder.replace(_normalize_attr_query(lab), " ")
        remainder = re.sub(r"\s+", " ", remainder).strip()

    cols = _load_columns(conn, table)
    if not cols:
        return None

    gu = extract_gu(q)
    place = extract_place(q)
    busan = _question_wants_busan(q)
    # 표시명·컬럼명에 섞인 가짜 place 제거
    false_places = set(distinctive_label_tokens(display))
    for _, lab in matched_cols:
        false_places.update(re.findall(r"[가-힣]{2,}", lab))
    false_places.update({"활동인구", "유동인구", "시가화용지", "행정동"})
    if place and place in false_places:
        place = None
    if place and display and place in display and place not in remainder:
        place = None
    # 「부산만」/광역시는 시 단위 필터이지 동 이름이 아님
    if place in {"부산", "부산시", "부산광역시"}:
        place = None
        busan = True
    # 시·구·군은 ADM_NM ILIKE('%성남시%')가 아니라 코드 기반으로 필터
    if place and place.endswith(("구", "군", "시")):
        gu = gu or place
        place = None

    if not matched_cols:
        matched_cols = _match_requested_columns(remainder, cols)
        # 컬럼 바인딩 경로에서 remainder만으로 못 찾으면 전체 질문으로 재시도
        if not matched_cols:
            matched_cols = _match_requested_columns(q, cols)
        if matched_cols and _is_ambiguous_measure_only(matched_cols):
            # 정확 표시명 경로에서 모호 라벨만 남은 경우: 목록이면 허용, 속성 전용이면 양보
            if not exact:
                return None

    where = _place_where(
        conn, table, cols, gu=gu, place=place, busan_wide=busan and not gu and not place
    )
    area_ratio = _wants_area_ratio(q)
    select_sql, select_cols, order_sql = _build_select_sql(
        cols,
        matched_cols,
        area_ratio=area_ratio,
        question=q,
    )
    if not select_sql:
        return None
    where_sql = " AND ".join(where) if where else "TRUE"
    sql = (
        f"SELECT {select_sql}\n"
        f'FROM "{table}"\n'
        f"WHERE {where_sql}\n"
        f"ORDER BY {order_sql}\n"
        f"LIMIT 100;"
    )
    if matched_cols:
        intent = "named_dataset_attr"
    elif gu or place or busan:
        intent = "named_dataset_place_list"
    else:
        intent = "named_dataset_list"
    return NamedDatasetQuery(
        intent=intent,
        sql=sql,
        table=table,
        display_name=display or table,
        select_cols=tuple(select_cols),
        matched_columns=tuple(matched_cols),
    )


def _bind_table_by_column_display(
    conn: psycopg.Connection,
    question: str,
) -> tuple[str, list[tuple[str, str]], str] | None:
    """질문에 나온 컬럼 표시명으로 테이블을 고른다(업로드 테이블 우선)."""
    try:
        rows = conn.execute(
            """
            SELECT table_name, column_name, display_name
            FROM column_metadata
            WHERE display_name IS NOT NULL
              AND display_name <> ''
              AND table_name NOT LIKE 'temp_%%'
            """
        ).fetchall()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    text = _normalize_attr_query(question)
    compact = re.sub(r"\s+", "", text)
    hits: list[tuple[int, str, str, str]] = []
    for row in rows:
        table = str(row["table_name"])
        col = str(row["column_name"])
        disp = str(row["display_name"] or "").strip()
        if len(disp) < 2:
            continue
        labels = {disp, _normalize_attr_query(disp)}
        if "활동인구" in disp:
            labels.add(disp.replace("활동인구", "유동인구"))
        if re.search(r"\d+대", disp):
            labels.add(re.sub(r"(\d+)대", r"\1세", disp))
        best = 0
        for lab in labels:
            if _label_hits_query(lab, text, compact):
                best = max(best, len(re.sub(r"\s+", "", lab)))
        if best:
            hits.append((best, table, col, disp))
    if not hits:
        return None
    # 모호 측정 라벨만으로 잡은 후보는 제외 (높이/면적/층수/용도…)
    distinctive = [
        h
        for h in hits
        if _is_distinctive_attr_label(h[3])
        and re.sub(r"\s+", "", h[3].strip()) not in _AMBIGUOUS_MEASURE_LABELS
    ]
    candidates = distinctive if distinctive else []
    if not candidates:
        return None
    candidates.sort(key=lambda x: (-x[0], x[1], x[2]))
    # 최장 일치 길이의 후보만 남김
    top_len = candidates[0][0]
    pool = [h for h in candidates if h[0] == top_len]
    tables = {h[1] for h in pool}
    if len(tables) != 1:
        # 동일 길이로 여러 테이블이면 모호 → 포기(RAG/clarify에 맡김)
        return None
    table = next(iter(tables))
    matched = [(h[2], h[3]) for h in pool if h[1] == table]
    # 같은 컬럼 중복 제거, 최장 표시명 우선
    seen: set[str] = set()
    uniq: list[tuple[str, str]] = []
    for col, disp in sorted(matched, key=lambda x: -len(x[1])):
        if col in seen:
            continue
        seen.add(col)
        uniq.append((col, disp))
        if len(uniq) >= 3:
            break
    display = ""
    try:
        row = conn.execute(
            """
            SELECT display_name FROM table_metadata
            WHERE schema_name = 'public' AND table_name = %s
            """,
            (table,),
        ).fetchone()
        display = str((row or {}).get("display_name") or table)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        display = table
    return table, uniq, display


def named_dataset_display_tokens(conn: psycopg.Connection, question: str) -> set[str]:
    """clarify 미지용어에서 뺄 표시명 토큰."""
    meta_rows = _load_table_meta(conn)
    exact = tables_matching_exact_display_names(question, meta_rows)
    out: set[str] = set()
    for row in meta_rows:
        name = str(row.get("table_name") or "")
        display = str(row.get("display_name") or "")
        if name not in exact and not (display and display in question):
            continue
        out.update(distinctive_label_tokens(display))
        for tok in re.findall(r"[가-힣]{2,}", display):
            if len(tok) >= 2:
                out.add(tok)
    return out


def _load_table_meta(conn: psycopg.Connection) -> list[dict[str, str]]:
    try:
        rows = conn.execute(
            """
            SELECT table_name, display_name, description, category
            FROM table_metadata
            WHERE schema_name = 'public'
            """
        ).fetchall()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return []
    return [
        {
            "table_name": str(r["table_name"]),
            "display_name": str(r.get("display_name") or ""),
            "description": str(r.get("description") or ""),
            "category": str(r.get("category") or ""),
        }
        for r in rows
    ]


def _load_columns(
    conn: psycopg.Connection, table: str
) -> list[dict[str, Any]]:
    try:
        meta = conn.execute(
            """
            SELECT column_name, display_name, description, data_type, unit
            FROM column_metadata
            WHERE table_name = %s
            """,
            (table,),
        ).fetchall()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        meta = []
    info = conn.execute(
        """
        SELECT column_name, udt_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table,),
    ).fetchall()
    meta_by = {str(r["column_name"]): r for r in meta}
    out: list[dict[str, Any]] = []
    for row in info:
        name = str(row["column_name"])
        m = meta_by.get(name) or {}
        out.append(
            {
                "column_name": name,
                "display_name": str(m.get("display_name") or ""),
                "description": str(m.get("description") or ""),
                "data_type": str(m.get("data_type") or row.get("udt_name") or ""),
                "udt_name": str(row.get("udt_name") or ""),
                "unit": str(m.get("unit") or ""),
            }
        )
    return out


def _is_geom(col: dict[str, Any]) -> bool:
    udt = str(col.get("udt_name") or "").lower()
    name = str(col.get("column_name") or "").lower()
    return udt in _GEOM_UDT or name in {"geometry", "geom", "wkb_geometry", "shape"}


def _preferred_name_col(cols: list[dict[str, Any]]) -> str | None:
    for col in cols:
        name = str(col["column_name"])
        disp = str(col.get("display_name") or "")
        blob = f"{name} {disp}".lower()
        if any(h in blob for h in _NAME_COL_HINTS):
            return name
    return None


def _code_cols(cols: list[dict[str, Any]]) -> list[str]:
    scored: list[tuple[int, str]] = []
    for col in cols:
        name = str(col["column_name"])
        disp = str(col.get("display_name") or "")
        blob = f"{name} {disp}".lower()
        if not any(h in blob for h in _CODE_COL_HINTS):
            continue
        score = 0
        if "sigungu" in blob or "시군구" in blob:
            score += 30
        if "sgg" in blob:
            score += 20
        if name.lower() == "adm_cd" or "행정동 코드" in disp:
            score += 5
        scored.append((score, name))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [name for _, name in scored]


def _legal_dongs_for_gu(gu: str) -> list[str]:
    gaz = load_gazetteer()
    out: list[str] = []
    for dong, gus in gaz.legal_dong_sigungu.items():
        if gu in gus or gu.replace("구", "") in " ".join(gus):
            out.append(dong)
    return out


def _dong_name_predicates(
    name_col: str, dongs: list[str], *, min_stem: int = 1
) -> list[str]:
    preds: list[str] = []
    for dong in dongs:
        stem = dong[:-1] if dong.endswith("동") else dong
        if not stem or len(stem) < min_stem:
            continue
        esc = re.escape(stem)
        # 짧은 스템(서·선)은 LIKE 확장을 금지하고 정규식만 사용
        preds.append(f'"{name_col}" ~ \'^{esc}[0-9]*동$\'')
        if len(stem) >= 2:
            preds.append(f'"{name_col}" LIKE \'{stem}%동\'')
    seen: set[str] = set()
    uniq: list[str] = []
    for p in preds:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def _admin_match_prefixes(name: str) -> list[str]:
    """시·군·구 법정동(PNU) 접두. 시 단위(…0)는 하위 구 4자리도 포함."""
    gaz = load_gazetteer()
    raw: list[str] = []
    for c in gaz.sigungu_pnu_candidates.get(name, ()) or ():
        text = str(c).strip()
        if text and text not in raw:
            raw.append(text)
    if not raw:
        pref = gaz.sigungu_pnu_prefix.get(name)
        if pref:
            raw.append(str(pref).strip())
    out: list[str] = []
    for code in raw:
        if code and code not in out:
            out.append(code)
        # 성남시(41130) → 41131/41133/41135 매칭을 위해 4113
        if len(code) == 5 and code.endswith("0"):
            short = code[:4]
            if short and short not in out:
                out.append(short)
    return out


def _admdong_code_col(cols: list[dict[str, Any]]) -> str | None:
    preferred = (
        "admdong_cd",
        "adm_dong_cd",
        "legaldong_cd",
        "legal_dong_cd",
        "bjdong_cd",
        "stdg_cd",
    )
    by_lower = {
        str(col["column_name"]).lower(): str(col["column_name"]) for col in cols
    }
    for key in preferred:
        if key in by_lower:
            return by_lower[key]
    return None


def _place_where(
    conn: psycopg.Connection,
    table: str,
    cols: list[dict[str, Any]],
    *,
    gu: str | None,
    place: str | None,
    busan_wide: bool = False,
) -> list[str]:
    where: list[str] = []
    name_col = _preferred_name_col(cols)
    code_cols = _code_cols(cols)
    admdong_col = _admdong_code_col(cols)

    # 동·읍·면 등 지명만 이름 부분일치. 시·구·군은 위에서 gu로 접힘.
    if place and name_col:
        esc = place.replace("'", "''")
        where.append(f'"{name_col}" ILIKE \'%{esc}%\'')
        return where

    if busan_wide and not gu:
        prefix = SIDO_CENSUS_PREFIX.get("부산광역시") or "21"
        parts: list[str] = []
        for col in code_cols:
            parts.append(f'CAST("{col}" AS text) LIKE \'{prefix}%\'')
        # ADM_CD가 code_cols에 없을 수도 있음
        if name_col and "ADM_CD" not in code_cols:
            # 일부 테이블은 코드 컬럼명만 다를 수 있어 물리 ADM_CD 검사
            for col in cols:
                if str(col["column_name"]).upper() == "ADM_CD":
                    parts.append(f'CAST("ADM_CD" AS text) LIKE \'{prefix}%\'')
                    break
        if parts:
            where.append("(" + " OR ".join(dict.fromkeys(parts)) + ")")
        return where

    if not gu:
        return where

    esc_gu = gu.replace("'", "''")
    parts: list[str] = []
    dong_parts: list[str] = []
    if name_col:
        dongs = _legal_dongs_for_gu(gu)
        if dongs:
            dong_parts.extend(_dong_name_predicates(name_col, dongs))
            parts.extend(dong_parts)
        # 시 이름(성남시)을 ADM_NM에 넣는 테이블은 거의 없음 → 구·군만 이름 보조
        if gu.endswith(("구", "군")):
            parts.append(f'"{name_col}" ILIKE \'%{esc_gu}%\'')

    legal = busan_gu_code(gu)
    codes: list[str] = []
    if legal:
        codes.append(legal)
    try:
        gaz = load_gazetteer()
        for c in gaz.sigungu_pnu_candidates.get(gu, ()) or ():
            if c not in codes:
                codes.append(str(c))
        pref = gaz.sigungu_pnu_prefix.get(gu)
        if pref and str(pref) not in codes:
            codes.append(str(pref))
    except Exception:
        pass
    pnu_prefixes = _admin_match_prefixes(gu)
    for code in pnu_prefixes:
        if code not in codes:
            codes.append(code)

    for col in code_cols:
        for code in codes:
            parts.append(f'CAST("{col}" AS text) LIKE \'{code}%\'')
            parts.append(f'CAST("{col}" AS text) = \'{code}\'')
    if admdong_col:
        for code in pnu_prefixes or codes:
            parts.append(f'CAST("{admdong_col}" AS text) LIKE \'{code}%\'')

    # 법정동 코드 → 센서스 sigungu_cd 발견 (성남시 4113* → 31021/22/23)
    primary_code = code_cols[0] if code_cols else None
    if admdong_col and primary_code and pnu_prefixes:
        pnu_preds = [
            f'CAST("{admdong_col}" AS text) LIKE \'{pref}%\'' for pref in pnu_prefixes
        ]
        discovered_pnu = _discover_local_codes(
            conn,
            table,
            admdong_col,
            primary_code,
            pnu_preds,
            sido_prefix=None,
        )
        if discovered_pnu and (
            "sigungu" in primary_code.lower() or "sgg" in primary_code.lower()
        ):
            code_preds = [
                f'CAST("{primary_code}" AS text) = \'{code}\''
                for code in discovered_pnu
            ]
            where.append("(" + " OR ".join(code_preds) + ")")
            return where
        if discovered_pnu:
            for code in discovered_pnu:
                parts.append(f'CAST("{primary_code}" AS text) = \'{code}\'')
        elif pnu_preds:
            # 센서스 코드 컬럼이 없어도 법정동 코드로 바로 필터
            where.append("(" + " OR ".join(pnu_preds) + ")")
            return where

    # 발견용은 스템 길이>=2 동만 사용(서·선 전국 오탐 방지)
    discover_preds = (
        _dong_name_predicates(name_col, _legal_dongs_for_gu(gu), min_stem=2)
        if name_col
        else []
    )
    sido_prefix = None
    if gu in BUSAN_GU_CODES:
        sido_prefix = SIDO_CENSUS_PREFIX.get("부산광역시")
    if name_col and code_cols and discover_preds:
        primary_code = code_cols[0]
        discovered = _discover_local_codes(
            conn,
            table,
            name_col,
            primary_code,
            discover_preds,
            sido_prefix=sido_prefix,
        )
        if discovered and (
            "sigungu" in primary_code.lower() or "sgg" in primary_code.lower()
        ):
            code_preds = [
                f'CAST("{primary_code}" AS text) = \'{code}\'' for code in discovered
            ]
            where.append("(" + " OR ".join(code_preds) + ")")
            return where
        for code in discovered:
            parts.append(f'CAST("{primary_code}" AS text) = \'{code}\'')

    if parts:
        where.append("(" + " OR ".join(parts) + ")")
    return where


def _discover_local_codes(
    conn: psycopg.Connection,
    table: str,
    name_col: str,
    code_col: str,
    seed_preds: list[str],
    *,
    sido_prefix: str | None = None,
) -> list[str]:
    if not seed_preds:
        return []
    extra = ""
    if sido_prefix:
        extra = (
            f' AND CAST("{code_col}" AS text) LIKE \'{sido_prefix}%\''
        )
    sql = (
        f'SELECT DISTINCT CAST("{code_col}" AS text) AS code\n'
        f'FROM "{table}"\n'
        f"WHERE ({' OR '.join(seed_preds)})\n"
        f"AND \"{code_col}\" IS NOT NULL{extra}\n"
        f"LIMIT 20"
    )
    try:
        rows = conn.execute(sql).fetchall()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return []
    out: list[str] = []
    for row in rows:
        code = str(row.get("code") or "").strip()
        if code and code not in out:
            out.append(code)
    return out


def _normalize_attr_query(text: str) -> str:
    t = text
    for a, b in _SYNONYM_PAIRS:
        t = t.replace(a, b)
    t = re.sub(r"(\d+)\s*세", r"\1대", t)
    t = t.replace("유동인구", "활동인구")
    return t


def _label_hits_query(lab: str, text: str, compact: str) -> bool:
    lab = _normalize_attr_query(lab.strip())
    text_n = _normalize_attr_query(text)
    compact_n = _normalize_attr_query(compact)
    if len(lab) < 2:
        return False
    lab_c = re.sub(r"\s+", "", lab)
    compact_n = re.sub(r"\s+", "", compact_n)
    # 「0대」가 「10대」에 부분일치하지 않도록 숫자+대/세는 경계 검사
    m = re.fullmatch(r"(\d+)(대|세)(.*)", lab_c)
    if m:
        num, unit, rest = m.group(1), m.group(2), m.group(3)
        pat = rf"(?<!\d){re.escape(num)}{unit}{re.escape(rest)}"
        return bool(re.search(pat, compact_n)) or bool(re.search(pat, text_n))
    return lab in text_n or lab in compact_n or lab_c in compact_n


def _match_requested_columns(
    remainder: str,
    cols: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    """(physical, display) 최장 표시명 일치."""
    text = _normalize_attr_query(remainder)
    compact = re.sub(r"\s+", "", text)
    candidates: list[tuple[int, str, str]] = []
    for col in cols:
        if _is_geom(col):
            continue
        name = str(col["column_name"])
        disp = str(col.get("display_name") or "").strip()
        labels = [x for x in (disp, name) if x]
        expanded: list[str] = []
        for lab in labels:
            expanded.append(lab)
            expanded.append(_normalize_attr_query(lab))
            if "활동인구" in lab:
                expanded.append(lab.replace("활동인구", "유동인구"))
            if re.search(r"\d+대", lab):
                expanded.append(re.sub(r"(\d+)대", r"\1세", lab))
        for lab in expanded:
            if _label_hits_query(lab, text, compact):
                candidates.append((len(re.sub(r"\s+", "", lab)), name, disp or name))
    candidates.sort(key=lambda x: (-x[0], x[1]))
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for _, name, disp in candidates:
        if name in seen:
            continue
        seen.add(name)
        out.append((name, disp))
        if len(out) >= 3:
            break
    return out


def _choose_select_cols(
    cols: list[dict[str, Any]],
    matched: list[tuple[str, str]],
) -> list[str]:
    name_col = _preferred_name_col(cols)
    picked: list[str] = []
    if name_col:
        picked.append(name_col)
    # 식별용 코드 하나
    for col in cols:
        n = str(col["column_name"])
        if n.upper() in {"ADM_CD", "BASE_DATE"} and n not in picked:
            picked.append(n)
            break
    if matched:
        for name, _ in matched:
            if name not in picked:
                picked.append(name)
        return picked
    # 목록: geometry 제외 상위 컬럼 (너무 많으면 핵심만)
    preferred = {
        "urban_pc",
        "urban_area",
        "tot",
        "n10",
        "sqkm",
        "ha",
        "sigungu_cd",
    }
    for col in cols:
        n = str(col["column_name"])
        if _is_geom(col) or n in picked:
            continue
        if n in preferred or n.lower() in preferred:
            picked.append(n)
        if len(picked) >= 10:
            break
    if len(picked) < 3:
        for col in cols:
            n = str(col["column_name"])
            if _is_geom(col) or n in picked:
                continue
            picked.append(n)
            if len(picked) >= 8:
                break
    return picked


def _wants_area_ratio(question: str) -> bool:
    q = question.strip()
    if not q:
        return False
    if any(k in q for k in _AREA_RATIO_CUES):
        return True
    if "면적" in q and any(k in q for k in _AREA_RATIO_TRANSFORM):
        return True
    return False


def _pick_area_column(cols: list[dict[str, Any]], question: str) -> str | None:
    """면적 분모로 쓸 물리 컬럼. 기본은 행정동 면적(ha/sqkm), 시가화 언급 시 urban_area."""
    names = {str(c["column_name"]).lower(): str(c["column_name"]) for c in cols}
    q = question
    if "시가화" in q and "urban_area" in names:
        return names["urban_area"]
    if "㎢" in q or "km2" in q.lower() or "제곱킬로" in q:
        if "sqkm" in names:
            return names["sqkm"]
    if "ha" in q or "헥타" in q:
        if "ha" in names:
            return names["ha"]
    # 기본: 행정동 면적 컬럼 (값은 아래에서 ㎡로 환산)
    for key in ("ha", "sqkm", "urban_area"):
        if key in names:
            return names[key]
    for col in cols:
        name = str(col["column_name"])
        disp = str(col.get("display_name") or "")
        if _is_geom(col):
            continue
        if "면적" in disp and name.lower() not in {"urban_pc"}:
            return name
    return None


def _normalize_area_unit(unit: str) -> str:
    """메타 unit → m2|ha|sqkm|''."""
    u = (unit or "").strip().lower().replace(" ", "")
    u = u.replace("ｍ", "m").replace("²", "2").replace("㎡", "m2").replace("㎢", "km2")
    if u in {"m2", "sqm", "제곱미터", "평방미터"}:
        return "m2"
    if u in {"ha", "hectare", "hectares", "헥타르", "헥타", "㏊"}:
        return "ha"
    if u in {"km2", "sqkm", "제곱킬로미터", "평방킬로미터"}:
        return "sqkm"
    return ""


def _resolve_area_unit(col: dict[str, Any]) -> str:
    """컬럼 메타 unit을 우선하되, 명백한 불일치는 설명·컬럼명으로 보정.

    예: unit='m2' 이지만 컬럼명 ha + 설명에 '헥타르' → 실제 저장은 ha.
    """
    name = str(col.get("column_name") or "").lower()
    unit = _normalize_area_unit(str(col.get("unit") or ""))
    desc = str(col.get("description") or "")
    disp = str(col.get("display_name") or "")
    blob = f"{desc} {disp}"

    if "헥타" in blob or "hectare" in blob.lower():
        return "ha"
    if "제곱킬로" in blob or "㎢" in blob:
        return "sqkm"
    if unit:
        # unit=m2 인데 컬럼명이 ha 단독이면 메타 오표기 가능성이 큼 → ha 유지
        if unit == "m2" and name == "ha":
            return "ha"
        return unit
    if name == "ha":
        return "ha"
    if name == "sqkm":
        return "sqkm"
    if name in {"urban_area", "area_m2"}:
        return "m2"
    if "㎡" in blob or "제곱미터" in blob:
        return "m2"
    return "m2"


def _area_denominator_sql(
    area_col: str, cols: list[dict[str, Any]]
) -> tuple[str, str, str]:
    """(SQL 분모 표현, 별칭 접미사, 해석용 면적 별칭). 결과는 항상 ㎡ 기준."""
    col = next(
        (c for c in cols if str(c.get("column_name")) == area_col),
        {"column_name": area_col, "unit": "", "description": ""},
    )
    kind = _resolve_area_unit(col)
    if kind == "ha":
        return (
            f'(CAST("{area_col}" AS float8) * 10000.0)',
            "per_m2",
            "area_m2",
        )
    if kind == "sqkm":
        return (
            f'(CAST("{area_col}" AS float8) * 1000000.0)',
            "per_m2",
            "area_m2",
        )
    # 이미 ㎡
    alias = "area_m2" if area_col.lower() != "urban_area" else "urban_area"
    return f'CAST("{area_col}" AS float8)', "per_m2", alias


def _ratio_alias(metric_col: str, suffix: str) -> str:
    return f"{metric_col}_{suffix}"


def _build_select_sql(
    cols: list[dict[str, Any]],
    matched: list[tuple[str, str]],
    *,
    area_ratio: bool,
    question: str,
) -> tuple[str, list[str], str]:
    """SELECT 절, 결과 컬럼명, ORDER BY 절."""
    base_cols = _choose_select_cols(cols, matched)
    name_col = _preferred_name_col(cols)
    order_name = name_col or (base_cols[0] if base_cols else "1")
    default_order = f'"{order_name}" ASC NULLS LAST'

    if not area_ratio or not matched:
        select_sql = ", ".join(f'"{c}"' for c in base_cols)
        return select_sql, base_cols, default_order

    area_col = _pick_area_column(cols, question)
    if not area_col:
        select_sql = ", ".join(f'"{c}"' for c in base_cols)
        return select_sql, base_cols, default_order

    denom_sql, ratio_suffix, area_alias = _area_denominator_sql(area_col, cols)
    physical = {str(c["column_name"]) for c in cols}
    parts: list[str] = []
    out_cols: list[str] = []
    ratio_aliases: list[str] = []
    for col in base_cols:
        if col == area_col:
            continue
        if col in {m for m, _ in matched} and col in physical:
            alias = _ratio_alias(col, ratio_suffix)
            parts.append(
                "("
                f'CAST("{col}" AS float8) / NULLIF({denom_sql}, 0)'
                f') AS "{alias}"'
            )
            out_cols.append(alias)
            ratio_aliases.append(alias)
            continue
        parts.append(f'"{col}"')
        out_cols.append(col)
    # 해석용 면적(㎡)
    if area_alias == area_col:
        parts.append(f'"{area_col}"')
        out_cols.append(area_col)
    else:
        parts.append(f'{denom_sql} AS "{area_alias}"')
        out_cols.append(area_alias)
    if not ratio_aliases:
        select_sql = ", ".join(f'"{c}"' for c in base_cols)
        return select_sql, base_cols, default_order
    order_sql = (
        f'"{ratio_aliases[0]}" DESC NULLS LAST, "{order_name}" ASC NULLS LAST'
    )
    return ", ".join(parts), out_cols, order_sql
