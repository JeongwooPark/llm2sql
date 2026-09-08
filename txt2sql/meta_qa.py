"""데이터/속성 설명형 질문에 메타데이터로 답변한다."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row

from txt2sql.domain import d198_gu_mentioned, d198_table_for_gu, gu_from_d198_table

# 질문 의도 감지
_META_HINTS = (
    "뭐야",
    "무엇",
    "뭔가",
    "의미",
    "뜻",
    "설명",
    "알려줘",
    "알려 줘",
    "어떤 데이터",
    "무슨 데이터",
    "무슨 테이블",
    "어떤 테이블",
    "어떤 컬럼",
    "무슨 컬럼",
    "속성",
    "필드",
    "컬럼",
    "칼럼",
    "스키마",
    "구조",
    "목록",
    "리스트",
    "소개",
    "개요",
    "메타",
    "정의",
    "무엇인지",
    "어떤 정보",
    "담겨",
    "포함",
    "사용가능",
    "사용 가능",
    "데이터셋",
    "데이터 이름",
    "데이터이름",
    "자료 이름",
    "테이블 이름",
    "테이블명",
    "들어있는",
    "담긴 내용",
    "내용은",
    "내용이",
)

_DATA_QUERY_HINTS = (
    "몇",
    "건수",
    "개수",
    "채수",
    "건물 수",
    "건물수",
    "구역 수",
    "구역수",
    "레코드 수",
    "레코드수",
    "수를",
    "수는",
    "수가",
    "상위",
    "조회해",
    "세어",
    "구해",
    "이내",
    "근처",
    "버퍼",
    "교차",
    "안에",
    "내부",
    "넘는",
    "이상",
    "가장 큰",
    "순위",
    "비율",
    "퍼센트",
    "몇%",
    "%",
)

# 「인구관련 데이터가 있는가」류 — 주제 키워드 → 메타 검색 동의어
_TOPIC_SYNONYMS: dict[str, tuple[str, ...]] = {
    "인구": ("인구", "활동인구", "유동인구"),
    "유동인구": ("유동인구", "활동인구", "인구"),
    "활동인구": ("활동인구", "유동인구", "인구"),
    "시가화": ("시가화", "시가화용지", "urban"),
    "건물": ("건물", "건축물", "건물통합"),
    "산업단지": ("산업단지",),
    "용도": ("용도별건물", "용도"),
}
_TOPIC_AVAIL_CUES = (
    "관련 데이터",
    "관련데이터",
    "관련 자료",
    "관련자료",
    "데이터가 있",
    "데이터 있",
    "자료가 있",
    "자료 있",
    "데이터셋이 있",
    "테이블이 있",
    "존재하는가",
    "존재하나",
    "존재해",
    "보유하",
    "가지고 있",
    "갖고 있",
)

_TABLE_ALIASES: dict[str, tuple[str, ...]] = {
    "AL_D010_26_20250704": (
        "건물",
        "부산 건물",
        "부산건물",
        "gis건물",
        "건물통합",
        "d010",
        "al_d010",
    ),
    "AL_D060_00_20250804": ("산업단지", "단지", "d060", "al_d060"),
    "BND_ADM_DONG_PG": ("행정동", "행정구역", "동경계", "법정동경계", "bnd"),
    "TL_KODIS_BAS_26_202507": ("기초구역", "기초구역번호", "kodis", "tl_kodis"),
    "pnu_def": ("pnu", "필지", "필지고유번호"),
}

_COL_TOKEN = re.compile(
    r'(?:"(?P<q>[A-Za-z_][A-Za-z0-9_]*)"|\b(?P<u>A\d+|BAS_[A-Z]+|ADM_[A-Z]+|SIG_[A-Z]+|PNU|[A-Z][A-Z0-9_]{1,20})\b)',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MetaAnswer:
    intent: str
    answer: str
    tables: list[str]
    rows: list[dict[str, Any]]


_META_COUNT_HINTS = (
    "사용가능",
    "사용 가능",
    "가용",
    "보유 데이터",
    "데이터셋",
    "데이터 개수",
    "테이블 개수",
    "테이블 수",
    "데이터 수",
)


def _is_place_usage_overview(q: str) -> bool:
    """지역 건물의 용도 구성·주요 용도 설명인지 (컬럼/스키마 설명이 아님)."""
    if "용도" not in q:
        return False
    if any(k in q for k in ("컬럼", "칼럼", "속성", "필드", "스키마", "테이블명")):
        return False
    explainish = any(
        k in q
        for k in (
            "설명",
            "주요",
            "어떤",
            "알려",
            "보여",
            "구성",
            "분포",
            "종류",
            "무엇",
            "뭐야",
            "뭐가",
        )
    )
    if not explainish:
        return False
    has_place = bool(
        re.search(r"[가-힣0-9]{1,12}동", q) or re.search(r"[가-힣]{1,6}구", q)
    )
    has_building = any(k in q for k in ("건물", "건축물", "주택", "아파트"))
    return has_place or has_building


def _named_dataset_question(q: str) -> bool:
    """질문에 특정 데이터셋/테이블 표시명·물리명이 명시됐는지."""
    if any(
        k in q
        for k in (
            "GIS건물",
            "건물통합",
            "용도별건물",
            "산업단지",
            "기초구역",
            "행정구역 동",
            "AL_D",
            "BND_",
            "TL_KODIS",
        )
    ):
        return True
    return "_" in q and any(
        k in q for k in ("정보", "건물", "단지", "구역", "행정동", "용지", "면적")
    )


def _asks_dataset_summary(q: str) -> bool:
    return any(k in q for k in ("요약", "개요", "한눈에", "정리해")) and (
        "데이터" in q or "자료" in q or _named_dataset_question(q)
    )


def _asks_d198_where(q: str) -> bool:
    """용도별건물공간정보의 구 커버리지 질문."""
    if not any(k in q for k in ("용도별건물공간정보", "용도별건물")):
        return False
    return any(k in q for k in ("어디", "어느 구", "구까지", "어느 지역", "어느구"))


def _answer_d198_where(conn: psycopg.Connection) -> MetaAnswer:
    from txt2sql.domain import D198_BY_GU, D198_TABLES

    tables = list(D198_TABLES) or list(D198_BY_GU.values())
    if not tables:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT DISTINCT table_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name LIKE 'AL_D198%'
                ORDER BY 1
                """
            )
            tables = [str(r["table_name"]) for r in cur.fetchall()]
    lines = [
        f"{i}. table_name={name}"
        for i, name in enumerate(tables, start=1)
    ]
    if D198_BY_GU:
        lines.append(
            "구 범위: " + ", ".join(f"{gu}={tbl}" for gu, tbl in D198_BY_GU.items())
        )
    return MetaAnswer(
        intent="meta_d198_coverage",
        answer="\n".join(lines) if lines else "용도별건물공간정보 테이블을 찾지 못했습니다.",
        tables=tables,
        rows=[{"table_name": name} for name in tables],
    )


def is_metadata_question(question: str) -> bool:
    """데이터 설명/속성 질의인지 판별 (집계·공간 조회와 구분)."""
    q = question.strip()
    if not q:
        return False
    from txt2sql.domain import extract_gu, extract_place, is_busan_wide

    # 산업단지 명칭·목록·면적순위는 실데이터 조회 (카탈로그 메타가 아님)
    if "산업단지" in q and any(
        k in q for k in ("이름", "명칭", "목록", "리스트", "면적", "큰 순", "상위")
    ):
        if not any(
            k in q
            for k in (
                "자료",
                "데이터셋",
                "데이터",
                "테이블",
                "스키마",
                "컬럼",
                "속성",
                "필드",
            )
        ):
            return False

    if _asks_d198_where(q):
        return True
    # 「인구관련 데이터가 있는가?」류 주제 보유 여부
    if _asks_topic_availability(q):
        return True
    # 「연면적과 건축물면적은 같은 필드야?」
    if re.search(r"같은\s*필드|다른\s*필드|동일\s*(?:컬럼|필드)|필드야\s*\??", q):
        return True
    _schema_meta_keys = (
        "데이터",
        "테이블",
        "자료",
        "데이터셋",
        "스키마",
        "컬럼",
        "칼럼",
        "속성",
        "필드",
        "의미",
        "뜻",
    )
    # 「연산동 건물 수를 알려줘」「금정구 기초구역 수를 알려줘」는 스키마 메타가 아님
    if any(k in q for k in ("건물", "건축물", "기초구역", "구역")) and any(
        k in q for k in ("몇", "수", "채", "건수", "개수", "채수")
    ):
        if (is_busan_wide(q) or extract_place(q) or extract_gu(q)) and not any(
            k in q for k in _schema_meta_keys
        ):
            return False
    if is_busan_wide(q) and any(k in q for k in ("건물", "건축물")) and any(
        k in q for k in ("몇", "수", "채", "건수")
    ):
        if not any(k in q for k in ("데이터", "테이블", "자료", "데이터셋", "스키마")):
            return False
    # 카탈로그 개수: "사용가능한 데이터는 몇개야?"
    if _asks_catalog_count(q):
        return True
    # 장소+시설 목록 질의는 메타가 아님 ("구서동 공공시설물은 무엇이 있어?")
    inventory = (
        "무엇이 있어",
        "뭐가 있어",
        "뭐 있어",
        "어떤 게 있어",
        "어떤것이 있어",
        "어떤 것이 있어",
    )
    if any(k in q for k in inventory) and any(
        k in q
        for k in (
            "동",
            "구",
            "건물",
            "시설",
            "주택",
            "아파트",
            "공공",
            "용도",
        )
    ):
        return False
    from txt2sql.spatial_router import _looks_like_admin_members

    if _looks_like_admin_members(q):
        return False
    # 지역 건물 용도 분포/설명은 스키마 메타가 아님 ("동래구 건물의 주요 용도들을 설명하라")
    if _is_place_usage_overview(q):
        return False
    # 특정 건물명 조회는 메타가 아님
    from txt2sql.domain import (
        extract_special_land,
        extract_structure,
        looks_like_building_name_lookup,
    )

    if looks_like_building_name_lookup(q):
        return False
    from txt2sql.d198_attrs import is_d198_attr_question
    from txt2sql.catalog_attrs import is_catalog_attr_question

    if is_d198_attr_question(q):
        return False
    if is_catalog_attr_question(q):
        return False
    # 장소+건축 구조/특수지(산지)는 스키마 설명이 아님
    if (extract_structure(q) or extract_special_land(q)) and (
        re.search(r"[가-힣0-9]{1,12}동", q) or re.search(r"[가-힣]{1,6}구", q)
    ):
        return False
    # 「지번은?」「주소는?」처럼 짧은 속성 질문은 스키마 메타가 아님
    if re.fullmatch(
        r"(지번|주소|이름|건물명|높이|용도|연면적|건물면적|층수|몇\s*층)"
        r"(은|는|이|가)?\s*\??",
        q,
    ):
        return False
    # 특정 데이터셋명 + 요약/내용 → 메타 (프로필 집계가 아님)
    if _named_dataset_question(q) and any(
        k in q for k in ("요약", "개요", "설명", "내용", "들어있는", "담긴", "소개")
    ):
        return True
    # 동·구 + 특징/비교 설명은 건물 프로필 (스키마 설명이 아님)
    # 단, 데이터셋 표시명이 명시된 요약은 위에서 메타로 처리
    profile_like = any(
        k in q
        for k in (
            "특징",
            "특성",
            "비교",
            "요약",
            "분포",
            "프로필",
            "경향",
            "평균",
        )
    )
    if (
        profile_like
        and not _named_dataset_question(q)
        and (
            re.search(r"[가-힣0-9]{1,12}동", q)
            or re.search(r"[가-힣]{1,6}구", q)
            or any(
                k in q
                for k in (
                    "아파트",
                    "공동주택",
                    "단독주택",
                    "건물",
                    "건축물",
                )
            )
        )
    ):
        return False
    # "비교 설명" 등 서술형도 메타의 '설명'만으로 잡지 않음
    if "설명" in q and profile_like:
        return False
    # 명확한 데이터 조회면 메타가 아님
    if any(k in q for k in _DATA_QUERY_HINTS) and not any(
        k in q
        for k in (
            "컬럼",
            "칼럼",
            "속성",
            "필드",
            "의미",
            "뜻",
            "설명",
            "스키마",
            "구조",
            "데이터셋",
            "테이블",
        )
    ):
        return False
    if any(k in q for k in _META_HINTS):
        if not ("구조" in q and "구조물" in q and "스키마" not in q and "테이블" not in q and "컬럼" not in q):
            return True
        # 「구조물」만 있고 데이터 구조가 아니면 메타 힌트에서 제외하고 계속 판별
        if any(k in q for k in _META_HINTS if k != "구조"):
            return True
    # "A4는?", "법정동명이 뭐야" 류
    if _COL_TOKEN.search(q) and any(
        k in q for k in ("뭐", "무엇", "의미", "뜻", "설명", "컬럼", "속성", "필드")
    ):
        return True
    if any(
        k in q
        for k in (
            "테이블 목록",
            "데이터 목록",
            "보유 데이터",
            "어떤 자료",
            "사용가능",
            "사용 가능",
            "데이터 이름",
            "데이터이름",
            "데이터셋 이름",
            "데이터셋명",
            "자료 이름",
            "테이블 이름",
            "테이블명",
        )
    ):
        return True
    # 특정 데이터셋명 + 내용/설명
    if _asks_table_desc(q) and (
        "GIS" in q
        or "AL_" in q
        or "건물통합" in q
        or "용도별건물" in q
        or "산업단지" in q
        or "기초구역" in q
        or "행정구역" in q
        or "_" in q
    ):
        return True
    return False


def _asks_catalog_count(q: str) -> bool:
    if not any(k in q for k in ("몇", "개수", "갯수", "몇개", "몇 개")):
        return False
    if any(k in q for k in _META_COUNT_HINTS):
        return True
    # "데이터는 몇", "테이블은 몇"
    if ("데이터" in q or "테이블" in q or "자료" in q or "데이터셋" in q) and any(
        k in q for k in ("몇", "개수", "갯수")
    ):
        # 실데이터 집계와 구분
        if any(
            k in q
            for k in (
                "건물",
                "기초구역",
                "산업단지",
                "채수",
                "건수",
                "채야",
                "아파트",
                "주택",
                "용도",
            )
        ):
            return False
        return True
    return False


def _asks_topic_availability(q: str) -> bool:
    """「인구관련 데이터가 있는가」「유동인구 자료 있어?」류."""
    text = q.strip()
    if not text:
        return False
    if any(k in text for k in _TOPIC_AVAIL_CUES):
        return bool(_topic_keywords(text)) or "관련" in text
    if ("데이터" in text or "자료" in text or "데이터셋" in text or "테이블" in text) and any(
        k in text
        for k in ("있는가", "있나요", "있어?", "있어", "있을까", "있나", "있니", "있습니까")
    ):
        # 전체 카탈로그 개수 질문과 구분
        if _asks_catalog_count(text):
            return False
        return bool(_topic_keywords(text)) or "관련" in text
    return False


def _topic_keywords(q: str) -> list[str]:
    """주제 검색용 키워드(동의어 포함)."""
    found: list[str] = []
    # 「XXX관련」토큰
    for m in re.finditer(r"([가-힣A-Za-z0-9]{2,16})\s*관련", q):
        token = m.group(1).strip()
        if token and token not in {"데이터", "자료", "정보", "테이블"}:
            found.append(token)
            found.extend(_TOPIC_SYNONYMS.get(token, ()))
    for key, syns in sorted(_TOPIC_SYNONYMS.items(), key=lambda x: -len(x[0])):
        if key in q:
            found.append(key)
            found.extend(syns)
    # 중복 제거, 짧은 일반어 제외
    out: list[str] = []
    seen: set[str] = set()
    for term in found:
        t = term.strip()
        if len(t) < 2 or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _answer_topic_availability(conn: psycopg.Connection, question: str) -> MetaAnswer:
    """메타데이터에서 주제 관련 테이블·컬럼을 찾아 보유 여부를 답한다."""
    keywords = _topic_keywords(question)
    if not keywords:
        # 「관련」만 있고 주제가 불명확하면 카탈로그로 안내
        return MetaAnswer(
            intent="meta_topic_availability",
            answer=(
                "어떤 주제의 데이터인지 더 구체적으로 알려 주세요. "
                "예: 「인구관련 데이터가 있는가?」, 「산업단지 자료 있어?」"
            ),
            tables=[],
            rows=[],
        )

    like_params = [f"%{k}%" for k in keywords]
    # table display/description OR column display
    table_clauses = " OR ".join(
        ["t.display_name ILIKE %s", "COALESCE(t.description,'') ILIKE %s", "t.table_name ILIKE %s"]
        * len(keywords)
    )
    # expand params: for each keyword, 3 likes
    table_params: list[str] = []
    for p in like_params:
        table_params.extend([p, p, p])

    col_clauses = " OR ".join(["c.display_name ILIKE %s", "c.column_name ILIKE %s"] * len(keywords))
    col_params: list[str] = []
    for p in like_params:
        col_params.extend([p, p])

    sql = f"""
        SELECT DISTINCT
            t.table_name,
            t.display_name,
            t.category,
            t.description
        FROM table_metadata t
        LEFT JOIN column_metadata c
          ON c.table_name = t.table_name
        WHERE t.schema_name = 'public'
          AND t.table_name NOT LIKE 'temp_%%'
          AND (
            ({table_clauses})
            OR ({col_clauses})
          )
        ORDER BY t.display_name NULLS LAST, t.table_name
        LIMIT 20
    """
    rows: list[dict[str, Any]] = []
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, table_params + col_params)
        rows = list(cur.fetchall())

    topic_label = keywords[0]
    if not rows:
        return MetaAnswer(
            intent="meta_topic_availability",
            answer=(
                f"현재 메타데이터 기준으로 「{topic_label}」관련 전용 데이터셋은 "
                "찾지 못했습니다. 「어떤 데이터가 있어?」로 전체 목록을 확인할 수 있습니다."
            ),
            tables=[],
            rows=[],
        )

    # 샘플 컬럼(인구 등) — 상위 테이블만 한 번에 조회
    sample_cols: dict[str, list[str]] = {}
    top_tables = [str(r["table_name"]) for r in rows[:5]]
    if top_tables:
        col_disp_clause = " OR ".join(["display_name ILIKE %s"] * len(keywords))
        col_sql = f"""
            SELECT table_name, display_name, column_name
            FROM column_metadata
            WHERE table_name = ANY(%s)
              AND display_name IS NOT NULL
              AND ({col_disp_clause})
            ORDER BY table_name,
              CASE
                WHEN display_name ~ '^[0-9]+대' THEN 0
                WHEN display_name LIKE '%%활동인구' THEN 1
                ELSE 2
              END,
              length(display_name) ASC
        """
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(col_sql, [top_tables, *like_params])
            for c in cur.fetchall():
                tname = str(c["table_name"])
                bucket = sample_cols.setdefault(tname, [])
                if len(bucket) >= 6:
                    continue
                bucket.append(str(c["display_name"] or c["column_name"]))

    lines = [f"네, 「{topic_label}」관련 데이터가 있습니다."]
    tables: list[str] = []
    for r in rows:
        tname = str(r["table_name"])
        tables.append(tname)
        disp = r.get("display_name") or tname
        lines.append(f"- 「{disp}」(`{tname}`)")
        samples = sample_cols.get(tname) or []
        if samples:
            lines.append("  예: " + ", ".join(samples[:4]))
    lines.append(
        "예: 「금정구 10세 유동인구」, 「부산시 20대 유동인구」처럼 물으시면 조회할 수 있습니다."
        if any("인구" in k for k in keywords)
        else "데이터셋 이름을 말씀해 주시면 목록·속성 조회를 이어갈 수 있습니다."
    )
    return MetaAnswer(
        intent="meta_topic_availability",
        answer="\n".join(lines),
        tables=tables,
        rows=rows,
    )


def _answer_field_equivalence(question: str) -> MetaAnswer | None:
    """「연면적과 건축물면적은 같은 필드야?」류 스키마 비교."""
    q = question.strip()
    if not re.search(r"같은\s*필드|다른\s*필드|동일\s*(?:컬럼|필드)|필드야\s*\??", q):
        return None
    pairs = (
        (
            ("연면적",),
            ("건축물면적", "건축면적", "건물면적"),
            "다른 필드다. 연면적=D010 A14(D198 A19), 건축물면적=D010 A12(D198 A18). "
            "같은 컬럼이 아니며 의미도 다릅니다.",
        ),
        (
            ("연면적",),
            ("대지면적",),
            "다른 필드다. 연면적=D010 A14, 대지면적=D010 A15. 데이터 컬럼이 다릅니다.",
        ),
        (
            ("건축물면적", "건축면적", "건물면적"),
            ("대지면적",),
            "다른 필드다. 건축물면적=D010 A12, 대지면적=D010 A15. 데이터 컬럼이 다릅니다.",
        ),
    )
    for lefts, rights, answer in pairs:
        if any(a in q for a in lefts) and any(b in q for b in rights):
            return MetaAnswer(intent="meta_field", answer=answer, tables=[], rows=[])
    if "필드" in q:
        return MetaAnswer(
            intent="meta_field",
            answer=(
                "필드(컬럼) 동등 여부는 데이터셋마다 다릅니다. "
                "비교할 두 속성 이름(예: 연면적·건축물면적)을 알려 주세요."
            ),
            tables=[],
            rows=[],
        )
    return None


def answer_metadata_question(
    conn: psycopg.Connection,
    question: str,
    *,
    force: bool = False,
) -> MetaAnswer | None:
    if not force and not is_metadata_question(question):
        return None

    q = question.strip()

    field_cmp = _answer_field_equivalence(q)
    if field_cmp is not None:
        return field_cmp

    if _asks_d198_where(q):
        return _answer_d198_where(conn)

    # 0) 사용 가능 데이터셋 개수
    if _asks_catalog_count(q):
        return _answer_catalog_count(conn)

    # 0.2) 주제별 보유 여부 (인구/건물 관련 데이터 있는가)
    if _asks_topic_availability(q):
        return _answer_topic_availability(conn, q)

    tables = _resolve_tables(conn, q)
    col_names = _extract_column_tokens(q)
    display_cols = _match_display_columns(conn, q, tables)

    # 0.5) 특정 데이터셋 요약 (컬럼 나열이 아닌 내용 요약)
    if _asks_dataset_summary(q):
        if not tables:
            tables = _default_tables(conn, q)
        if tables:
            return _answer_dataset_summary(conn, q, tables[:1])

    # 1) 전체 데이터/테이블 목록 (이름 질의 포함)
    if _asks_catalog(q) and not col_names:
        # 특정 주제(예: 산업단지) 자료 이름만 물은 경우
        if tables and any(k in q for k in ("이름", "명칭", "뭐야", "무엇")):
            return _answer_dataset_names(conn, tables)
        # '데이터 이름'이 표시명 컬럼 매칭에 걸려도 카탈로그가 우선
        return _answer_catalog(conn)

    # 2) 특정 컬럼(물리명) 의미
    if col_names:
        return _answer_columns(conn, q, tables, col_names)

    # 3) 한글 표시명으로 컬럼 찾기
    if display_cols:
        return _answer_display_columns(conn, q, display_cols)

    # 4) 테이블 설명 + 주요 속성
    if tables or _asks_table_desc(q):
        if not tables:
            tables = _default_tables(conn, q)
        return _answer_table_overview(conn, q, tables)

    # 5) 키워드만 있는 일반 설명
    if any(k in q for k in ("데이터", "자료", "스키마", "속성", "컬럼", "메타")):
        return _answer_catalog(conn)

    return None


def _asks_catalog(q: str) -> bool:
    return any(
        k in q
        for k in (
            "어떤 데이터",
            "무슨 데이터",
            "어떤 테이블",
            "무슨 테이블",
            "테이블 목록",
            "데이터 목록",
            "보유",
            "전체 데이터",
            "데이터 전체",
            "목록",
            "리스트",
            "소개",
            "개요",
            "어떤 정보",
            "사용가능",
            "사용 가능",
            "데이터 이름",
            "데이터이름",
            "데이터셋 이름",
            "데이터셋명",
            "데이터셋 목록",
            "자료 이름",
            "자료의 이름",
            "자료이름",
            "테이블 이름",
            "테이블명",
            "무슨 자료",
            "어떤 자료",
        )
    )


def _answer_catalog_count(conn: psycopg.Connection) -> MetaAnswer:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT table_name, display_name, category
            FROM table_metadata
            WHERE schema_name = 'public'
            ORDER BY category NULLS LAST, table_name
            """
        )
        rows = list(cur.fetchall())
    n = len(rows)
    names = ", ".join(
        (r["display_name"] or r["table_name"]) for r in rows
    )
    answer = (
        f"현재 사용 가능한 데이터셋은 {n}개입니다.\n"
        f"구성: {names}"
    )
    return MetaAnswer(
        intent="meta_catalog_count",
        answer=answer,
        tables=[r["table_name"] for r in rows],
        rows=rows,
    )


def _asks_table_desc(q: str) -> bool:
    if "구조물" in q and not any(
        k in q for k in ("스키마", "테이블", "컬럼", "칼럼", "데이터셋", "속성")
    ):
        keys = (
            "테이블",
            "데이터셋",
            "자료",
            "설명",
            "스키마",
            "속성",
            "컬럼",
            "칼럼",
            "필드",
            "뭐야",
            "무엇",
            "들어있는",
            "담긴",
            "내용",
            "포함",
            "요약",
            "개요",
        )
    else:
        keys = (
            "테이블",
            "데이터셋",
            "자료",
            "설명",
            "구조",
            "스키마",
            "속성",
            "컬럼",
            "칼럼",
            "필드",
            "뭐야",
            "무엇",
            "들어있는",
            "담긴",
            "내용",
            "포함",
            "요약",
            "개요",
        )
    return any(k in q for k in keys)


def _extract_column_tokens(q: str) -> list[str]:
    found: list[str] = []
    for m in _COL_TOKEN.finditer(q):
        name = (m.group("q") or m.group("u") or "").strip()
        if not name:
            continue
        # 너무 흔한 영단어 제외
        if name.lower() in {"like", "from", "where", "select", "count", "limit"}:
            continue
        # A숫자 / 알려진 키만 (대문자 유지)
        if re.fullmatch(r"A\d+", name, re.I):
            name = name.upper()
        elif name.isupper() or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            name = name.upper() if name.startswith(("A", "BAS", "ADM", "SIG")) else name
        else:
            continue
        if name not in found:
            found.append(name)
    return found


def _resolve_tables(conn: psycopg.Connection, q: str) -> list[str]:
    q_lower = q.lower()
    exact: list[str] = []
    alias_hit: list[str] = []

    # 물리 테이블명 직접
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name, display_name
            FROM table_metadata
            WHERE schema_name = 'public'
            ORDER BY table_name
            """
        )
        rows = list(cur.fetchall())

    for row in rows:
        table_name = row["table_name"]
        display_name = row["display_name"]
        if table_name.lower() in q_lower or table_name in q:
            exact.append(table_name)
            continue
        if display_name and display_name in q:
            exact.append(table_name)
            continue
        aliases = _table_aliases(table_name)
        for alias in aliases:
            # '건물'만으로는 D010만 잡히므로 아래에서 카테고리 확장
            if alias in ("건물", "부산 건물", "부산건물") and d198_gu_mentioned(q) is None:
                continue
            if alias.lower() in q_lower or alias in q:
                alias_hit.append(table_name)
                break

    # 표시명/물리명이 질문에 그대로 있으면 alias(행정동 등) 확장을 하지 않음
    hit = exact if exact else alias_hit

    # 일반 '건물' 질의 → 건물 카테고리 전체
    # 단, 특정 데이터셋명(표시명/물리명)이 이미 매칭되면 확장하지 않음
    specific_hit = bool(hit)
    if (
        not specific_hit
        and ("건물" in q or "용도별건물" in q)
        and d198_gu_mentioned(q) is None
    ):
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name FROM table_metadata
                WHERE schema_name = 'public' AND category = '건물'
                ORDER BY table_name
                """
            )
            for row in cur.fetchall():
                t = row["table_name"]
                if t not in hit:
                    hit.append(t)

    out: list[str] = []
    for t in hit:
        if t not in out:
            out.append(t)
    return out


def _table_aliases(table_name: str) -> tuple[str, ...]:
    if table_name in _TABLE_ALIASES:
        return _TABLE_ALIASES[table_name]
    gu = gu_from_d198_table(table_name)
    if not gu:
        return ()
    stem = gu.replace("구", "").replace("군", "")
    label = stem if len(stem) >= 2 else gu
    parts = table_name.split("_")
    pnu = parts[2] if len(parts) > 2 else ""
    return (label, gu, f"용도별건물 {label}", f"d198_{pnu}")


def _default_tables(conn: psycopg.Connection, q: str) -> list[str]:
    if "산업" in q:
        return ["AL_D060_00_20250804"]
    if "기초구역" in q:
        return ["TL_KODIS_BAS_26_202507"]
    if "행정" in q or "동경계" in q:
        return ["BND_ADM_DONG_PG"]
    mentioned = d198_gu_mentioned(q)
    if mentioned:
        table = d198_table_for_gu(mentioned)
        if table:
            return [table]
    if "건물" in q or "용도" in q:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name
                FROM table_metadata
                WHERE schema_name = 'public' AND category = '건물'
                ORDER BY table_name
                """
            )
            rows = [r["table_name"] for r in cur.fetchall()]
        return rows or ["AL_D010_26_20250704"]
    return ["AL_D010_26_20250704"]


def _match_display_columns(
    conn: psycopg.Connection,
    q: str,
    tables: list[str],
) -> list[dict[str, Any]]:
    """질문에 한글 속성명(법정동명, 연면적 등)이 있으면 매칭."""
    with conn.cursor(row_factory=dict_row) as cur:
        if tables:
            cur.execute(
                """
                SELECT table_name, column_name, display_name, description, data_type, unit
                FROM column_metadata
                WHERE schema_name = 'public'
                  AND table_name = ANY(%s)
                  AND display_name IS NOT NULL
                  AND display_name <> ''
                """,
                (tables,),
            )
        else:
            cur.execute(
                """
                SELECT table_name, column_name, display_name, description, data_type, unit
                FROM column_metadata
                WHERE schema_name = 'public'
                  AND display_name IS NOT NULL
                  AND display_name <> ''
                """
            )
        rows = list(cur.fetchall())

    hits: list[dict[str, Any]] = []
    # 긴 표시명 우선
    rows.sort(key=lambda r: len(r["display_name"] or ""), reverse=True)
    for row in rows:
        name = row["display_name"]
        if name and len(name) >= 2 and name in q:
            hits.append(row)
    return hits


def _answer_catalog(conn: psycopg.Connection) -> MetaAnswer:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT table_name, display_name, category, description
            FROM table_metadata
            WHERE schema_name = 'public'
            ORDER BY category NULLS LAST, table_name
            """
        )
        rows = list(cur.fetchall())

    lines = ["현재 조회 가능한 데이터셋은 다음과 같습니다."]
    for r in rows:
        disp = r["display_name"] or r["table_name"]
        cat = f" [{r['category']}]" if r.get("category") else ""
        desc = _short(r.get("description"), 100)
        extra = f" — {desc}" if desc else ""
        lines.append(f"- {disp} (`{r['table_name']}`){cat}{extra}")
    lines.append(
        "특정 테이블이나 컬럼(예: A4, 법정동명)의 의미를 물으시면 속성 기준으로 설명합니다."
    )
    return MetaAnswer(
        intent="meta_catalog",
        answer="\n".join(lines),
        tables=[r["table_name"] for r in rows],
        rows=rows,
    )


def _answer_dataset_names(
    conn: psycopg.Connection, tables: list[str]
) -> MetaAnswer:
    """특정 주제와 관련된 데이터셋 표시명·물리명만 짧게 안내."""
    rows: list[dict[str, Any]] = []
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT table_name, display_name, category, description
            FROM table_metadata
            WHERE schema_name = 'public' AND table_name = ANY(%s)
            ORDER BY table_name
            """,
            (tables,),
        )
        rows = list(cur.fetchall())
    if not rows:
        return MetaAnswer(
            intent="meta_catalog",
            answer="관련 데이터셋 이름을 메타데이터에서 찾지 못했습니다.",
            tables=[],
            rows=[],
        )
    if len(rows) == 1:
        r = rows[0]
        disp = r["display_name"] or r["table_name"]
        answer = (
            f"산업단지 관련 자료의 이름은 「{disp}」이며, "
            f"물리 테이블명은 `{r['table_name']}` 입니다."
            if "산업단지" in (r.get("display_name") or "")
            or r["table_name"].startswith("AL_D060")
            else (
                f"관련 자료의 이름은 「{disp}」(`{r['table_name']}`)입니다."
            )
        )
    else:
        lines = ["관련 데이터셋 이름은 다음과 같습니다."]
        for r in rows:
            disp = r["display_name"] or r["table_name"]
            lines.append(f"- 「{disp}」(`{r['table_name']}`)")
        answer = "\n".join(lines)
    return MetaAnswer(
        intent="meta_catalog",
        answer=answer,
        tables=[r["table_name"] for r in rows],
        rows=rows,
    )


def _answer_dataset_summary(
    conn: psycopg.Connection,
    question: str,
    tables: list[str],
) -> MetaAnswer:
    """특정 데이터셋의 짧은 내용 요약(건수·주요 용도 등)."""
    table = tables[0]
    meta = _load_table(conn, table)
    if not meta:
        return MetaAnswer(
            intent="meta_summary",
            answer="요청하신 데이터셋 메타데이터를 찾지 못했습니다.",
            tables=[],
            rows=[],
        )

    disp = meta["display_name"] or table
    cat = meta.get("category") or "미분류"
    desc = _short(meta.get("description"), 160) or "상세 설명은 메타데이터에 없습니다."
    cols = _load_columns(conn, table)
    col_names = {str(c.get("column_name") or "").upper() for c in cols}

    rows_out: list[dict[str, Any]] = []
    parts: list[str] = [
        f"「{disp}」(`{table}`) 요약입니다.",
        f"분류: {cat}. {desc}",
    ]

    # 전체 건수
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f'SELECT COUNT(*) AS cnt FROM "{table}"')
            cnt_row = cur.fetchone() or {}
            cnt = int(cnt_row.get("cnt") or 0)
            rows_out.append({"metric": "count", "value": cnt})
            parts.append(f"현재 레코드(도형) 수는 약 {cnt:,}건입니다.")
    except Exception:
        cnt = None

    # 용도 상위 (건물 통합 A9, 용도별 A25)
    usage_col = None
    if "A9" in col_names and table.startswith("AL_D010"):
        usage_col = "A9"
        usage_label = "건축물용도명"
    elif "A25" in col_names and table.startswith("AL_D198"):
        usage_col = "A25"
        usage_label = "주요용도명"
    if usage_col:
        try:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    f"""
                    SELECT COALESCE("{usage_col}", '(미상)') AS usage, COUNT(*) AS n
                    FROM "{table}"
                    WHERE "{usage_col}" IS NOT NULL
                    GROUP BY 1
                    ORDER BY 2 DESC
                    LIMIT 5
                    """
                )
                usages = list(cur.fetchall())
            if usages:
                rows_out.extend(usages)
                txt = ", ".join(
                    f"{u['usage']} {int(u['n']):,}건" for u in usages
                )
                parts.append(f"상위 {usage_label} 구성: {txt} 순입니다.")
        except Exception:
            pass

    # 핵심 속성만 짧게
    prefer = (
        "A4",
        "A5",
        "A9",
        "A11",
        "A12",
        "A14",
        "A16",
        "A24",
        "A25",
        "A26",
    )
    key_cols = []
    by_name = {
        str(c.get("column_name") or "").upper(): c for c in cols
    }
    for name in prefer:
        if name in by_name:
            key_cols.append(by_name[name])
    if key_cols:
        names = ", ".join(
            (c.get("display_name") or c.get("column_name") or "?") for c in key_cols[:8]
        )
        parts.append(f"자주 쓰는 속성은 {names} 등입니다.")
        parts.append(
            "전체 컬럼·코드표가 필요하면 「컬럼 설명해줘」처럼 속성 설명을 요청해 주세요."
        )

    return MetaAnswer(
        intent="meta_summary",
        answer=" ".join(parts),
        tables=[table],
        rows=rows_out,
    )


def _answer_table_overview(
    conn: psycopg.Connection,
    question: str,
    tables: list[str],
) -> MetaAnswer:
    parts: list[str] = []
    all_rows: list[dict[str, Any]] = []
    used: list[str] = []

    for table in tables[:3]:
        meta = _load_table(conn, table)
        if not meta:
            continue
        used.append(table)
        disp = meta["display_name"] or table
        cat = meta.get("category") or "미분류"
        desc = _short(meta.get("description"), 200) or "상세 설명은 메타데이터에 없습니다."
        cols = _load_columns(conn, table)
        all_rows.extend(cols)

        parts.append(f"「{disp}」(`{table}`)는 분류상 {cat} 데이터입니다.")
        parts.append(f"설명: {desc}")
        if cols:
            parts.append("주요 속성은 다음과 같습니다.")
            for c in cols[:12]:
                parts.append(_format_column_line(c))
            if len(cols) > 12:
                parts.append(f"… 외 {len(cols) - 12}개 컬럼이 더 있습니다.")
        parts.append("")

    if not parts:
        return MetaAnswer(
            intent="meta_table",
            answer="요청하신 테이블에 대한 메타데이터를 찾지 못했습니다.",
            tables=[],
            rows=[],
        )

    answer = "\n".join(parts).strip()
    if "속성" in question or "컬럼" in question or "칼럼" in question or "필드" in question:
        # already included
        pass
    return MetaAnswer(
        intent="meta_table",
        answer=answer,
        tables=used,
        rows=all_rows,
    )


def _answer_columns(
    conn: psycopg.Connection,
    question: str,
    tables: list[str],
    col_names: list[str],
) -> MetaAnswer:
    with conn.cursor(row_factory=dict_row) as cur:
        if tables:
            cur.execute(
                """
                SELECT table_name, column_name, display_name, description, data_type, unit
                FROM column_metadata
                WHERE schema_name = 'public'
                  AND table_name = ANY(%s)
                  AND upper(column_name) = ANY(%s)
                ORDER BY table_name, column_name
                """,
                (tables, [c.upper() for c in col_names]),
            )
        else:
            cur.execute(
                """
                SELECT table_name, column_name, display_name, description, data_type, unit
                FROM column_metadata
                WHERE schema_name = 'public'
                  AND upper(column_name) = ANY(%s)
                ORDER BY table_name, column_name
                """,
                ([c.upper() for c in col_names],),
            )
        rows = list(cur.fetchall())

    if not rows:
        names = ", ".join(col_names)
        hint = ""
        if tables:
            hint = f" (검색 테이블: {', '.join(tables)})"
        return MetaAnswer(
            intent="meta_column",
            answer=f"컬럼 {names}에 대한 속성 설명을 메타데이터에서 찾지 못했습니다.{hint}",
            tables=tables,
            rows=[],
        )

    # 동일 물리명이 테이블마다 다를 수 있음 → 모두 안내
    lines = [f"질문하신 속성({', '.join(col_names)})의 의미는 다음과 같습니다."]
    for r in rows:
        tmeta = _load_table(conn, r["table_name"])
        tdisp = (tmeta or {}).get("display_name") or r["table_name"]
        lines.append(f"[{tdisp} / `{r['table_name']}`]")
        lines.append(_format_column_line(r, detailed=True))
    if len({r["table_name"] for r in rows}) > 1:
        lines.append(
            "참고: 같은 컬럼명(예: A4)이라도 테이블마다 의미가 다를 수 있습니다."
        )
    return MetaAnswer(
        intent="meta_column",
        answer="\n".join(lines),
        tables=sorted({r["table_name"] for r in rows}),
        rows=rows,
    )


def _answer_display_columns(
    conn: psycopg.Connection,
    question: str,
    hits: list[dict[str, Any]],
) -> MetaAnswer:
    # 동일 display_name 그룹
    lines = ["질문하신 속성에 대한 설명입니다."]
    seen: set[tuple[str, str]] = set()
    tables: list[str] = []
    for r in hits:
        key = (r["table_name"], r["column_name"])
        if key in seen:
            continue
        seen.add(key)
        tables.append(r["table_name"])
        tmeta = _load_table(conn, r["table_name"])
        tdisp = (tmeta or {}).get("display_name") or r["table_name"]
        lines.append(f"[{tdisp} / `{r['table_name']}`]")
        lines.append(_format_column_line(r, detailed=True))
    return MetaAnswer(
        intent="meta_column_display",
        answer="\n".join(lines),
        tables=sorted(set(tables)),
        rows=hits,
    )


def _load_table(conn: psycopg.Connection, table: str) -> dict[str, Any] | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT table_name, display_name, category, description
            FROM table_metadata
            WHERE schema_name = 'public' AND table_name = %s
            """,
            (table,),
        )
        return cur.fetchone()


def _load_columns(conn: psycopg.Connection, table: str) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT table_name, column_name, display_name, description, data_type, unit
            FROM column_metadata
            WHERE schema_name = 'public' AND table_name = %s
            ORDER BY
              CASE
                WHEN column_name ~ '^A[0-9]+$' THEN 1
                WHEN column_name = 'geometry' THEN 3
                ELSE 2
              END,
              CASE
                WHEN column_name ~ '^A[0-9]+$'
                  THEN substring(column_name from 2)::int
                ELSE 0
              END,
              column_name
            """,
            (table,),
        )
        return list(cur.fetchall())


def _format_column_line(row: dict[str, Any], *, detailed: bool = False) -> str:
    col = row["column_name"]
    disp = row.get("display_name") or "(표시명 없음)"
    dtype = row.get("data_type") or ""
    unit = row.get("unit")
    unit_s = f", 단위: {unit}" if unit else ""
    desc = _clean_desc(row.get("description"))
    base = f"- `{col}` → {disp}"
    if dtype:
        base += f" ({dtype}{unit_s})"
    elif unit_s:
        base += f" ({unit_s.lstrip(', ')})"
    if detailed and desc:
        base += f"\n  {desc}"
    elif desc and not detailed:
        short = _short(desc, 70)
        if short:
            base += f" — {short}"
    return base


def _clean_desc(text: str | None) -> str:
    if not text:
        return ""
    # 너무 긴 코드표는 앞부분만
    t = re.sub(r"\s+", " ", text).strip()
    if "보조 설명:" in t:
        main, rest = t.split("보조 설명:", 1)
        main = main.strip(" ,;")
        rest = rest.strip()
        if len(rest) > 120:
            rest = rest[:117] + "…"
        if main:
            return f"{main} (코드표: {rest})" if rest else main
        return f"코드표: {rest}" if rest else ""
    return t


def _short(text: str | None, n: int) -> str:
    if not text:
        return ""
    t = re.sub(r"\s+", " ", text).strip()
    if "보조 설명:" in t:
        t = t.split("보조 설명:", 1)[0].strip(" ,;")
    if len(t) > n:
        return t[: n - 1] + "…"
    return t
