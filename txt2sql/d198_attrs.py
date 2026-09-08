"""용도별건물공간정보(AL_D198) 전 속성 인식·필터 추출."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from txt2sql.domain import (
    extract_age_years,
    extract_gu,
    extract_place,
    extract_structure,
    looks_like_age_question,
)
from txt2sql.units import (
    UNIT_TOKEN,
    convert_for_schema,
    display_unit,
    find_bin_width,
    has_convertible_area_unit,
    pyeong_threshold,
    sql_number,
)

DATASET_HINTS = ("용도별건물공간정보", "용도별건물", "AL_D198", "D198")

# D010과 겹치지 않거나, D198에서만 쓰는 속성·값
EXCLUSIVE_HINTS = (
    "집합건물",
    "집합건축물",
    "일반건축물대장",
    "대장종류",
    "표제부",
    "건물주부",
    "주건축물",
    "부속건축물",
    "세부용도",
    "건물용도분류",
    "용도분류",
    "주요용도명",
    "주요용도코드",
    "건물대지면적",
    "건물건축면적",
    "건물연면적",
    "건물높이",
    "건폐율",
    "용적율",
    "용적률",
    "허가일자",
    "허가일",
    "특수지구분코드",
    "특수지구분명",
    "도형ID",
    "도형아이디",
    "GIS건물통합식별번호",
    "건물식별번호",
    "데이터기준일자",
    "문교사회용",
    "농수산용",
    "다가구주택",
    "다세대주택",
    "아파트",
    "오피스텔",
    "지하층",
)

_REL_OPS = {
    "초과": ">",
    "넘는": ">",
    "미만": "<",
    "이하": "<=",
    "이상": ">=",
}


@dataclass(frozen=True)
class D198Attr:
    col: str
    label: str
    aliases: tuple[str, ...]
    kind: str  # numeric | text | code | date | id
    exclusive: bool = False
    unit: str = ""
    values: tuple[tuple[str, str], ...] = ()  # (질문 표현, 저장 값)


# 컬럼 정의 — 긴 alias가 먼저 매칭되도록 aliases는 길이순 정렬해 둔다.
D198_ATTRS: tuple[D198Attr, ...] = (
    D198Attr("A0", "도형ID", ("도형아이디", "도형ID", "도형id"), "id", exclusive=True),
    D198Attr(
        "A1",
        "GIS건물통합식별번호",
        ("GIS건물통합식별번호", "건물통합식별번호"),
        "id",
        exclusive=True,
    ),
    D198Attr("A2", "고유번호", ("고유번호", "PNU", "pnu"), "id", exclusive=True),
    D198Attr("A3", "법정동코드", ("법정동코드",), "code", exclusive=True),
    D198Attr("A4", "법정동명", ("법정동명", "법정동"), "text"),
    D198Attr(
        "A5",
        "특수지구분코드",
        ("특수지구분코드",),
        "code",
        exclusive=True,
        values=(("1", "1"), ("2", "2")),
    ),
    D198Attr(
        "A6",
        "특수지구분명",
        ("특수지구분명", "특수지구분"),
        "text",
        exclusive=True,
        values=(("일반지번", "일반"), ("산지", "산"), ("산번지", "산")),
    ),
    D198Attr("A7", "지번", ("지번",), "text"),
    D198Attr("A8", "건물식별번호", ("건물식별번호",), "id", exclusive=True),
    D198Attr(
        "A9",
        "집합건물구분코드",
        ("집합건물구분코드",),
        "code",
        exclusive=True,
        values=(("1", "1"), ("2", "2")),
    ),
    D198Attr(
        "A10",
        "집합건물구분",
        ("집합건물구분", "집합건물", "집합건축물"),
        "text",
        exclusive=True,
        values=(("집합건축물", "집합건축물"), ("일반건축물", "일반건축물")),
    ),
    D198Attr(
        "A11",
        "대장종류코드",
        ("대장종류코드",),
        "code",
        exclusive=True,
        values=(("2", "2"), ("3", "3")),
    ),
    D198Attr(
        "A12",
        "대장종류",
        ("대장종류", "일반건축물대장", "표제부"),
        "text",
        exclusive=True,
        values=(("일반건축물대장", "일반건축물대장"), ("표제부", "표제부")),
    ),
    D198Attr("A13", "건물명", ("건물명", "건물이름"), "text"),
    D198Attr("A14", "건물동명", ("건물동명",), "text"),
    D198Attr(
        "A15",
        "건물주부구분코드",
        ("건물주부구분코드", "주부구분코드"),
        "code",
        exclusive=True,
        values=(("0", "0"), ("1", "1")),
    ),
    D198Attr(
        "A16",
        "건물주부구분명",
        ("건물주부구분명", "건물주부구분", "주건축물", "부속건축물"),
        "text",
        exclusive=True,
        values=(("주건축물", "주건축물"), ("부속건축물", "부속건축물")),
    ),
    D198Attr(
        "A17",
        "건물대지면적",
        ("건물대지면적",),
        "numeric",
        exclusive=True,
        unit="㎡",
    ),
    D198Attr(
        "A18",
        "건물건축면적",
        ("건물건축면적",),
        "numeric",
        exclusive=True,
        unit="㎡",
    ),
    D198Attr(
        "A19",
        "건물연면적",
        ("건물연면적",),
        "numeric",
        exclusive=True,
        unit="㎡",
    ),
    D198Attr(
        "A20",
        "용적율",
        ("용적율", "용적률"),
        "numeric",
        exclusive=True,
        unit="%",
    ),
    D198Attr(
        "A21",
        "건폐율",
        ("건폐율",),
        "numeric",
        exclusive=True,
        unit="%",
    ),
    D198Attr(
        "A22",
        "건축물구조코드",
        ("건축물구조코드", "구조코드"),
        "code",
        exclusive=True,
    ),
    D198Attr(
        "A23",
        "건축물구조명",
        ("건축물구조명", "구조명"),
        "text",
        exclusive=True,
        values=(
            ("철골철근콘크리트", "철골철근콘크리트구조"),
            ("철근콘크리트", "철근콘크리트구조"),
            ("철골콘크리트", "철골콘크리트구조"),
            ("프리케스트콘크리트", "프리케스트콘크리트구조"),
            ("기타콘크리트", "기타콘크리트구조"),
            ("블록구조", "블록구조"),
            ("블럭구조", "블록구조"),
            ("벽돌구조", "벽돌구조"),
            ("일반철골", "일반철골구조"),
            ("경량철골", "경량철골구조"),
            ("일반목구조", "일반목구조"),
        ),
    ),
    D198Attr(
        "A24",
        "주요용도코드",
        ("주요용도코드",),
        "code",
        exclusive=True,
    ),
    D198Attr(
        "A25",
        "주요용도명",
        ("주요용도명", "주요용도"),
        "text",
        exclusive=True,
        values=(
            ("제2종근린생활시설", "제2종근린생활시설"),
            ("제1종근린생활시설", "제1종근린생활시설"),
            ("교육연구시설", "교육연구시설"),
            ("자동차관련시설", "자동차관련시설"),
            ("노유자시설", "노유자시설"),
            ("공동주택", "공동주택"),
            ("단독주택", "단독주택"),
            ("업무시설", "업무시설"),
            ("숙박시설", "숙박시설"),
            ("창고시설", "창고시설"),
            ("종교시설", "종교시설"),
            ("의료시설", "의료시설"),
        ),
    ),
    D198Attr(
        "A26",
        "세부용도코드",
        ("세부용도코드",),
        "code",
        exclusive=True,
    ),
    D198Attr(
        "A27",
        "세부용도명",
        ("세부용도명", "세부용도"),
        "text",
        exclusive=True,
        values=(
            ("다가구주택", "다가구주택"),
            ("다세대주택", "다세대주택"),
            ("일반음식점", "일반음식점"),
            ("오피스텔", "오피스텔"),
            ("단독주택", "단독주택"),
            ("소매점", "소매점"),
            ("아파트", "아파트"),
            ("사무소", "사무소"),
            ("학원", "학원"),
        ),
    ),
    D198Attr(
        "A28",
        "건물용도분류코드",
        ("건물용도분류코드", "용도분류코드"),
        "code",
        exclusive=True,
        values=(
            ("1", "1"),
            ("2", "2"),
            ("3", "3"),
            ("4", "4"),
            ("5", "5"),
            ("6", "6"),
            ("7", "7"),
        ),
    ),
    D198Attr(
        "A29",
        "건물용도분류명",
        ("건물용도분류명", "건물용도분류", "용도분류"),
        "text",
        exclusive=True,
        values=(
            ("문교사회용", "문교사회용"),
            ("농수산용", "농수산용"),
            ("주거용", "주거용"),
            ("상업용", "상업용"),
            ("공업용", "공업용"),
            ("공공용", "공공용"),
            ("기타", "기타"),
        ),
    ),
    D198Attr(
        "A30",
        "건물높이",
        ("건물높이",),
        "numeric",
        exclusive=True,
        unit="m",
    ),
    D198Attr("A31", "지상층", ("지상층수", "지상층"), "numeric", unit="층"),
    D198Attr(
        "A32",
        "지하층",
        ("지하층수", "지하층"),
        "numeric",
        exclusive=True,
        unit="층",
    ),
    D198Attr(
        "A33",
        "허가일자",
        ("허가일자", "허가일"),
        "date",
        exclusive=True,
    ),
    D198Attr(
        "A34",
        "사용승인일자",
        ("사용승인일자", "사용승인일", "준공일자", "준공일", "건설일자", "건설일"),
        "date",
    ),
    D198Attr(
        "A35",
        "데이터기준일자",
        ("데이터기준일자", "데이터기준일", "기준일자"),
        "date",
        exclusive=True,
    ),
)

COLUMN_LABELS: dict[str, str] = {a.col: a.label for a in D198_ATTRS}
ATTR_BY_COL: dict[str, D198Attr] = {a.col: a for a in D198_ATTRS}

# 데이터셋이 명시되면 D010 대응 용어도 D198 컬럼으로 해석
_DATASET_NUMERIC_ALIASES: tuple[tuple[str, str], ...] = (
    ("대지면적", "A17"),
    ("건축면적", "A18"),
    ("건축물면적", "A18"),
    ("건물면적", "A18"),
    ("연면적", "A19"),
    ("높이", "A30"),
    ("층수", "A31"),
)

D198_SELECT_COLS = (
    "A0",
    "A4",
    "A5",
    "A6",
    "A7",
    "A10",
    "A12",
    "A13",
    "A14",
    "A16",
    "A17",
    "A18",
    "A19",
    "A20",
    "A21",
    "A23",
    "A25",
    "A27",
    "A29",
    "A30",
    "A31",
    "A32",
    "A33",
    "A34",
)


@dataclass
class D198Parsed:
    filters: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    order_col: str | None = None
    order_asc: bool = False
    dataset_hint: bool = False
    rank: bool = False
    lookup: bool = False


@dataclass(frozen=True)
class YearStatsSpec:
    """연도별·연대별·N년 단위 건립 건수."""

    mode: str  # year | decade | bin
    decades: tuple[int, ...] = ()
    date_col: str = "A34"
    bin_years: int = 1


def _normalize_usage_typos(question: str) -> str:
    q = question
    q = q.replace("단독추택", "단독주택")
    q = q.replace("공동추택", "공동주택")
    return q


_GRAIN_TAIL = r"(?:단위|간격|별|씩)"
# 긴 수부터 매칭 (이십이 십보다 우선)
_HANGUL_YEAR_BINS: tuple[tuple[str, int], ...] = (
    ("오십", 50),
    ("사십", 40),
    ("삼십", 30),
    ("이십", 20),
    ("십", 10),
    ("아홉", 9),
    ("여덟", 8),
    ("일곱", 7),
    ("여섯", 6),
    ("육", 6),
    ("다섯", 5),
    ("오", 5),
    ("넷", 4),
    ("사", 4),
    ("셋", 3),
    ("삼", 3),
    ("둘", 2),
    ("이", 2),
    ("일", 1),
)
DECADE_GRAIN_HINTS = (
    "10년 단위",
    "10년단위",
    "10년 별",
    "10년별",
    "10년 간격",
    "십년 단위",
    "십년단위",
    "연대별",
    "년대별",
    "년대 단위",
)
YEAR_GRAIN_HINTS = (
    "년도별",
    "연도별",
    "각년도",
    "각 년도",
    "각연도",
    "각 연도",
    "연도 별",
    "년도 별",
    "1년 단위",
    "1년단위",
    "연도 단위",
    "년도 단위",
)


def year_stats_grain(question: str) -> int | None:
    """집계 단위(년). 1=연도별, 5=5년, 10=10년. 후속 단위 지정이 우선."""
    q = _normalize_usage_typos(question.strip())
    m = re.search(rf"(\d+)\s*년\s*{_GRAIN_TAIL}", q)
    if m:
        n = int(m.group(1))
        if n == 1:
            return 1
        if 2 <= n <= 50:
            return n
    m = re.search(r"(\d+)\s*년\s*(?:으로\s*)?(?:묶)", q)
    if m:
        n = int(m.group(1))
        if 2 <= n <= 50:
            return n
    for word, n in _HANGUL_YEAR_BINS:
        if re.search(rf"{word}\s*년\s*{_GRAIN_TAIL}", q):
            return n
    if any(k in q for k in DECADE_GRAIN_HINTS) or "년대" in q or "연대별" in q:
        return 10
    if any(k in q for k in YEAR_GRAIN_HINTS):
        return 1
    return None


def looks_like_year_stats_question(question: str) -> bool:
    """각년도별 건립 수, 70년대·2000년대 건립 수 등."""
    q = _normalize_usage_typos(question.strip())
    if not q:
        return False
    # 목록·보여주기(특정 년대 필터)는 연도 분포 통계가 아님
    if any(k in q for k in ("보여줘", "보여 줘", "목록", "리스트", "나열", "찾아줘", "찾아 줘")):
        if re.search(r"((?:19|20)\d{2})\s*년대", q) and "별" not in q:
            return False
    if re.search(r"년대인", q) or (
        re.search(r"((?:19|20)\d{2})\s*년대", q)
        and any(k in q for k in ("몇 채", "몇채", "채수", "건수", "것은 몇"))
        and "별" not in q
        and "분포" not in q
        and "단위" not in q
    ):
        return False
    countish = any(
        k in q for k in ("몇", "건수", "채수", "통계", "분포", "년도별", "연도별", "년대별")
    ) or bool(re.search(r"(?<![가-힣])수(?![가-힣])", q)) or "건물 수" in q or "건물수" in q
    grain = year_stats_grain(q)
    by_year = grain == 1 or any(k in q for k in YEAR_GRAIN_HINTS)
    by_bin = grain is not None and grain >= 2
    built = any(
        k in q
        for k in ("건립", "지어", "준공", "사용승인", "건설", "건축")
    )
    if by_year and (countish or built):
        if re.search(r"\d+\s*층", q) or any(
            k in q for k in ("연면적", "건축면적", "높이", "철근", "구조")
        ):
            return False
        return True
    if by_bin and (countish or built):
        if re.search(r"\d+\s*층", q) or any(
            k in q for k in ("연면적", "건축면적", "높이", "철근", "구조")
        ):
            return False
        return True
    return False


def parse_year_stats(question: str) -> YearStatsSpec | None:
    q = _normalize_usage_typos(question.strip())
    if not looks_like_year_stats_question(q):
        return None
    found: list[tuple[int, int]] = []
    for m in re.finditer(r"((?:19|20)\d{2})\s*년대", q):
        found.append((m.start(), (int(m.group(1)) // 10) * 10))
    for m in re.finditer(r"(?<!\d)([5-9]0)\s*년대", q):
        found.append((m.start(), 1900 + int(m.group(1))))
    found.sort(key=lambda item: item[0])
    uniq: list[int] = []
    seen: set[int] = set()
    for _, d in found:
        if 1900 <= d <= 2020 and d not in seen:
            seen.add(d)
            uniq.append(d)
    grain = year_stats_grain(q)
    if uniq:
        return YearStatsSpec(mode="decade", decades=tuple(uniq), bin_years=10)
    if grain == 10:
        if "년대" in q or "연대별" in q:
            return YearStatsSpec(
                mode="decade",
                decades=(1970, 1980, 1990, 2000, 2010, 2020),
                bin_years=10,
            )
        return YearStatsSpec(mode="decade", bin_years=10)
    if grain is not None and grain >= 2:
        return YearStatsSpec(mode="bin", bin_years=grain)
    return YearStatsSpec(mode="year", bin_years=1)


def session_has_year_stats(session: Any) -> bool:
    """직전 결과가 연도/연대 건립 통계인지."""
    if session is None:
        return False
    if str(getattr(session, "last_route", "") or "") == "d198_year_stats":
        return True
    rows = list(getattr(session, "last_rows", None) or [])
    if rows and any(k in rows[0] for k in ("year", "decade", "period")):
        return True
    sql = str(getattr(session, "last_sql", "") or "")
    if re.search(r"\bAS (year|decade|period)\b", sql, flags=re.I):
        return True
    full = str(
        getattr(session, "last_full_question", None)
        or getattr(session, "last_question", None)
        or ""
    )
    return looks_like_year_stats_question(full)


def is_year_grain_followup(question: str, session: Any) -> bool:
    """직전 연도별 건립 통계를 N년 단위로 바꿔 달라는 후속."""
    grain = year_stats_grain(question)
    if grain is None or grain < 2:
        return False
    return session_has_year_stats(session)


def rows_as_bin_counts(
    rows: list[dict[str, Any]], bin_years: int
) -> list[dict[str, Any]]:
    if bin_years <= 1 or not rows:
        return list(rows)
    if bin_years == 10:
        return rows_as_decade_counts(rows)
    sample = rows[0]
    if sample.get("year") is None:
        return []
    buckets: dict[int, int] = {}
    for row in rows:
        raw = row.get("year", row.get("period", row.get("decade")))
        n = row.get("n", row.get("cnt"))
        if raw is None:
            continue
        try:
            start = (int(raw) // bin_years) * bin_years
            buckets[start] = buckets.get(start, 0) + int(n or 0)
        except (TypeError, ValueError):
            continue
    return [{"period": k, "n": v} for k, v in sorted(buckets.items())]


def rows_as_decade_counts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    if rows[0].get("decade") is not None and rows[0].get("year") is None:
        return list(rows)
    if rows[0].get("year") is None:
        return []
    buckets: dict[int, int] = {}
    for row in rows:
        raw = row.get("year", row.get("decade"))
        n = row.get("n", row.get("cnt"))
        if raw is None:
            continue
        try:
            decade = (int(raw) // 10) * 10
            buckets[decade] = buckets.get(decade, 0) + int(n or 0)
        except (TypeError, ValueError):
            continue
    return [{"decade": k, "n": v} for k, v in sorted(buckets.items())]


def wrap_year_sql_as_decade(sql: str) -> str:
    """연도 GROUP BY SQL을 10년 단위 집계로 감싼다."""
    return wrap_year_sql_as_bin(sql, 10)


def wrap_year_sql_as_bin(sql: str, bin_years: int) -> str:
    """연도 GROUP BY SQL을 N년 단위 집계로 감싼다. 감쌀 수 없으면 빈 문자열."""
    if bin_years <= 1:
        return sql
    body = (sql or "").strip().rstrip(";").strip()
    if not body:
        return ""
    alias = "decade" if bin_years == 10 else "period"
    head = re.split(r"\bFROM\b", body, maxsplit=1, flags=re.I)[0]
    if bin_years == 10 and re.search(r"\bAS decade\b", head, flags=re.I):
        return sql if sql.endswith(";") else f"{sql.rstrip()};"
    if re.search(r"\bAS period\b", head, flags=re.I):
        return ""
    if bin_years != 10 and re.search(r"\bAS decade\b", head, flags=re.I):
        return ""
    body = re.sub(r"\s+ORDER BY\s+.+$", "", body, flags=re.I | re.S)
    return (
        f"SELECT (FLOOR(year / {bin_years}) * {bin_years})::int AS {alias}, SUM(n) AS n\n"
        f"FROM (\n{body}\n) AS year_rows\n"
        "GROUP BY 1\n"
        "ORDER BY 1;"
    )


def format_year_stats_label(
    row: dict[str, Any],
    *,
    question: str = "",
    spec: YearStatsSpec | None = None,
) -> str:
    """연도·연대·N년 구간 라벨."""
    if spec is None and question:
        spec = parse_year_stats(question)
    bin_years = 1
    if spec is not None and spec.bin_years:
        bin_years = spec.bin_years
    elif question:
        grain = year_stats_grain(question)
        if grain is not None:
            bin_years = grain
    raw = row.get("period")
    if raw is None and spec is not None and spec.mode == "bin":
        raw = row.get("year")
    if raw is not None:
        try:
            start = int(raw)
        except (TypeError, ValueError):
            return str(raw)
        width = bin_years if bin_years >= 2 else 5
        return f"{start}~{start + width - 1}년"
    if spec is not None and spec.mode == "decade":
        raw = row.get("decade", row.get("year"))
        try:
            return f"{int(raw)}년대"
        except (TypeError, ValueError):
            return str(raw)
    if row.get("decade") is not None and row.get("year") is None:
        try:
            return f"{int(row['decade'])}년대"
        except (TypeError, ValueError):
            return str(row["decade"])
    raw = row.get("year", row.get("decade"))
    try:
        return f"{int(raw)}년"
    except (TypeError, ValueError):
        return str(raw)


@dataclass(frozen=True)
class ValueBinSpec:
    """면적·높이·층수 등 수치 구간 건수."""

    col: str
    label: str
    unit: str
    bin_width: float
    width_label: str = ""
    source_unit: str | None = None
    source_width: float | None = None


_VALUE_BIN_METRICS: tuple[tuple[str, str, str, str, float], ...] = (
    ("건물연면적", "A19", "연면적", "㎡", 1000.0),
    ("건물건축면적", "A18", "건축면적", "㎡", 100.0),
    ("건물대지면적", "A17", "대지면적", "㎡", 100.0),
    ("건축물면적", "A18", "건축면적", "㎡", 100.0),
    ("건축면적", "A18", "건축면적", "㎡", 100.0),
    ("대지면적", "A17", "대지면적", "㎡", 100.0),
    ("연면적", "A19", "연면적", "㎡", 1000.0),
    ("건물면적", "A18", "건축면적", "㎡", 100.0),
    ("건물높이", "A30", "높이", "m", 5.0),
    ("지상층수", "A31", "지상층", "층", 1.0),
    ("지상층", "A31", "지상층", "층", 1.0),
    ("층수", "A31", "지상층", "층", 1.0),
    ("높이", "A30", "높이", "m", 5.0),
    ("면적", "A19", "연면적", "㎡", 1000.0),
)
SIZE_BIN_HINTS = (
    "크기별",
    "크기 별",
    "구간별",
    "구간 별",
    "단위별",
    "단위 별",
    "크기 단위",
    "면적별",
    "면적 별",
    "높이별",
    "층수별",
    "층별",
)
_BIN_RESIZE = re.compile(
    r"(\d+(?:\.\d+)?)\s*"
    r"(?:㎡|제곱미터|m2|m²|평방미터|킬로미터|km|㎞|평(?!수|형|방)|미터|m|층)?"
    r"\s*(?:단위로|단위|간격|별|씩|으로)?\s*묶"
)
_BIN_WIDTH = re.compile(
    r"(\d+(?:\.\d+)?)\s*"
    r"(?:㎡|제곱미터|m2|m²|평방미터|킬로미터|km|㎞|평(?!수|형|방)|미터|m|층)?"
    r"\s*(?:단위|간격|별|씩)"
)


def looks_like_value_bin_question(question: str) -> bool:
    """연면적 크기별·100㎡ 단위·2000단위로 묶어라 등 수치 구간 집계."""
    q = _normalize_usage_typos(question.strip())
    if not q:
        return False
    threshold = bool(re.search(r"(이상|이하|초과|미만|넘는)", q))
    if threshold and not any(k in q for k in ("단위", "구간", "크기별", "크기 별", "묶")):
        return False
    has_area_unit = has_convertible_area_unit(q)
    has_metric = has_area_unit or any(alias in q for alias, *_ in _VALUE_BIN_METRICS)
    has_hint = any(h in q for h in SIZE_BIN_HINTS)
    has_width = bool(_BIN_WIDTH.search(q) or _BIN_RESIZE.search(q))
    if year_stats_grain(q) is not None and not has_area_unit and not any(
        k in q for k in ("크기", "구간", "면적", "높이", "층수", "층별")
    ):
        return False
    if has_width:
        return True
    return bool(has_metric and (has_hint or "묶" in q))


def parse_value_bin(question: str) -> ValueBinSpec | None:
    q = _normalize_usage_typos(question.strip())
    if not looks_like_value_bin_question(q):
        return None
    col, label, unit, default_w = "A19", "연면적", "㎡", 1000.0
    for alias, a_col, a_label, a_unit, a_w in sorted(
        _VALUE_BIN_METRICS, key=lambda item: len(item[0]), reverse=True
    ):
        if alias in q:
            col, label, unit, default_w = a_col, a_label, a_unit, a_w
            break
    width = default_w
    width_label = ""
    source_unit: str | None = None
    source_width: float | None = None
    converted_w = find_bin_width(q, unit)
    if converted_w is None and col in {"A17", "A18", "A19"}:
        as_len = find_bin_width(q, "m")
        if as_len is not None:
            col, label, unit, default_w = "A30", "높이", "m", 5.0
            converted_w = as_len
    elif converted_w is None and col == "A30":
        as_area = find_bin_width(q, "㎡")
        if as_area is not None:
            col, label, unit, default_w = "A19", "연면적", "㎡", 1000.0
            converted_w = as_area
    if converted_w is not None:
        width = converted_w.canonical
        source_unit = converted_w.source_unit
        source_width = converted_w.original
        if converted_w.source_unit == "pyeong":
            width_label = (
                f"{sql_number(converted_w.original)}평"
                f"({sql_number(round(converted_w.canonical, 1))}㎡)"
            )
        else:
            width_label = converted_w.label
    else:
        m = re.search(
            r"(\d+(?:\.\d+)?)\s*(?:㎡|제곱미터|m2|m²|평방미터)",
            q,
        )
        if m:
            width = float(m.group(1))
            if col == "A30":
                col, label, unit, default_w = "A19", "연면적", "㎡", 1000.0
        elif col == "A30":
            m = re.search(
                r"(\d+(?:\.\d+)?)\s*(?:미터|m)\s*(?:단위|간격|별|씩)",
                q,
            )
            if m:
                width = float(m.group(1))
        elif col == "A31":
            m = re.search(r"(\d+)\s*층\s*(?:단위|간격|별|씩)", q)
            if m:
                width = float(m.group(1))
        else:
            m = _BIN_RESIZE.search(q) or re.search(
                rf"(\d+(?:\.\d+)?)\s*{_GRAIN_TAIL}", q
            )
            if m:
                width = float(m.group(1))
    if width <= 0:
        return None
    if col in {"A17", "A18", "A19"} and width > 100_000:
        width = 100_000
    if col == "A30" and width > 50:
        width = 50
    if col == "A31" and width > 10:
        width = 10
    return ValueBinSpec(
        col=col,
        label=label,
        unit=unit,
        bin_width=width,
        width_label=width_label,
        source_unit=source_unit,
        source_width=source_width,
    )


def session_has_value_context(session: Any) -> bool:
    if session is None:
        return False
    if session_has_year_stats(session):
        return True
    if str(getattr(session, "last_route", "") or "") == "d198_value_bins":
        return True
    full = str(
        getattr(session, "last_full_question", None)
        or getattr(session, "last_question", None)
        or ""
    )
    return looks_like_value_bin_question(full) or looks_like_year_stats_question(full)


def is_value_bin_followup(question: str, session: Any) -> bool:
    if not looks_like_value_bin_question(question):
        return False
    return session_has_value_context(session)


def format_value_bin_label(row: dict[str, Any], spec: ValueBinSpec) -> str:
    raw = row.get("period", row.get("year"))
    try:
        start = int(raw)
    except (TypeError, ValueError):
        return str(raw)
    width = spec.bin_width
    if width >= 1:
        end = start + int(width) - 1
        schema = f"{start:g}~{end:g}{spec.unit}"
    else:
        schema = f"{start:g}~{start + width:g}{spec.unit}"
    extra = _source_bin_range(start, spec)
    if extra:
        return f"{schema} ({extra})"
    return schema


def _source_bin_range(start: int, spec: ValueBinSpec) -> str | None:
    """질문 단위(평·km 등)로 같은 구간의 환산 표기."""
    if not spec.source_unit or not spec.source_width or not spec.bin_width:
        return None
    if spec.source_unit in {"m2", "m", "floor", "percent"}:
        return None
    idx = int(round(float(start) / spec.bin_width))
    src_w = float(spec.source_width)
    start_src = idx * src_w
    unit = display_unit(spec.source_unit)
    if src_w >= 2 and abs(src_w - round(src_w)) < 1e-9:
        end_src = start_src + int(round(src_w)) - 1
        return f"{sql_number(start_src)}~{sql_number(end_src)}{unit}"
    if abs(src_w - round(src_w)) < 1e-9:
        return f"{sql_number(start_src)}{unit}"
    end_src = start_src + src_w
    return f"{sql_number(start_src)}~{sql_number(end_src)}{unit}"


def value_bin_sane_sql(col: str) -> str:
    if col in {"A17", "A18", "A19"}:
        return f'"{col}" > 0 AND "{col}" <= 500000'
    if col == "A30":
        return '"A30" > 0 AND "A30" <= 600'
    if col == "A31":
        return '"A31" > 0 AND "A31" <= 80'
    return f'"{col}" > 0'


def has_dataset_hint(question: str) -> bool:
    q = question.strip()
    upper = q.upper()
    return any(h in q for h in ("용도별건물공간정보", "용도별건물")) or (
        "AL_D198" in upper or re.search(r"\bD198\b", upper) is not None
    )


def has_exclusive_signal(question: str) -> bool:
    q = question.strip()
    return any(h in q for h in EXCLUSIVE_HINTS)


def is_d198_attr_question(question: str) -> bool:
    """용도별건물 속성질의인지 (건물명 조회·메타보다 우선)."""
    q = _normalize_usage_typos(question.strip())
    if not q:
        return False
    if looks_like_year_stats_question(q):
        return True
    if looks_like_value_bin_question(q):
        return True
    if any(k in q for k in ("산업단지", "기초구역")):
        return False
    if any(
        k in q
        for k in (
            "GIS건물통합정보",
            "건물통합정보",
            "산업단지_전국",
            "센서스 기반 행정구역",
            "도로명주소 기초구역",
        )
    ):
        return False
    upper = q.upper()
    if any(k in upper for k in ("AL_D010", "AL_D060", "BND_ADM", "TL_KODIS")):
        return False
    if re.search(r"\bD010\b", upper) or re.search(r"\bD060\b", upper):
        return False
    if _is_schema_question(q):
        return False
    if "몇 가지" in q or "몇가지" in q:
        return False
    if re.search(r"(주요)?용도명?\s*종류", q):
        return False
    if _is_building_age_elapsed(q):
        return False
    if any(k in q for k in ("가장", "제일")) and any(
        k in q for k in ("최근", "오래된", "오래 된", "오래전")
    ) and any(
        k in q
        for k in ("지어진", "지은", "준공", "사용승인", "허가일", "건설일")
    ):
        return True
    if "건설일" in q and any(
        k in q for k in ("최근", "지어", "제외", "없는", "가장", "제일")
    ):
        return True
    if re.search(r"(?:19|20)\d{2}\s*년", q) and any(
        k in q
        for k in (
            "지어진",
            "지은",
            "준공",
            "사용승인",
            "허가일",
            "허가일자",
        )
    ):
        return True
    if re.search(
        r"(허가일|사용승인일|준공일).{0,20}(?:19|20)\d{2}\s*년",
        q,
    ):
        return True
    return has_dataset_hint(q) or has_exclusive_signal(q)


def _is_schema_question(q: str) -> bool:
    if any(k in q for k in ("이상", "이하", "초과", "미만", "몇", "채", "목록")):
        return False
    return any(k in q for k in ("컬럼", "칼럼", "스키마", "속성 설명", "의미가"))


def _is_building_age_elapsed(q: str) -> bool:
    """'30년 이상' 같은 경과년수. 달력 연도(2020년)는 제외."""
    if not looks_like_age_question(q):
        return False
    if re.search(r"(?:19|20)\d{2}\s*년", q):
        return False
    return extract_age_years(q) is not None


def _rel_op(rel: str) -> str:
    return _REL_OPS.get(rel, ">=")


def _sql_str(value: str) -> str:
    return value.replace("'", "''")


def _eq(col: str, value: str) -> str:
    return f"TRIM(COALESCE(\"{col}\"::text, '')) = '{_sql_str(str(value))}'"


def _like(col: str, value: str) -> str:
    return f"\"{col}\" ILIKE '%{_sql_str(str(value))}%'"


def _date_valid(col: str) -> str:
    return f"\"{col}\" ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}'"


def _add_filter(parsed: D198Parsed, col: str, sql: str, label: str) -> None:
    if sql in parsed.filters:
        return
    parsed.filters.append(sql)
    if col not in parsed.columns:
        parsed.columns.append(col)
    if label not in parsed.labels:
        parsed.labels.append(label)


def parse_d198_question(question: str) -> D198Parsed | None:
    """질문 → D198 필터. 해당 없으면 None."""
    q = _normalize_usage_typos(question.strip())
    if not is_d198_attr_question(q):
        return None

    named = has_dataset_hint(q)
    parsed = D198Parsed(dataset_hint=named)

    _parse_numeric(q, parsed, named)
    _parse_dates(q, parsed)
    _parse_values(q, parsed, named)
    _parse_codes_ids(q, parsed)
    _parse_special_land(q, parsed, named)
    _parse_structure(q, parsed, named)
    _parse_rank(q, parsed, named)

    if not parsed.filters and not parsed.rank and not named:
        if looks_like_year_stats_question(q):
            parsed.labels.append("연도별 건립")
            return parsed
        if looks_like_value_bin_question(q):
            parsed.labels.append("수치 구간")
            return parsed
        return None
    if not parsed.filters and not parsed.rank and named:
        # 데이터셋+장소만: 해당 구/동 전체 조회
        parsed.labels.append("용도별건물")
    return parsed


def _parse_numeric(q: str, parsed: D198Parsed, named: bool) -> None:
    attrs = [a for a in D198_ATTRS if a.kind == "numeric"]
    pairs: list[tuple[str, str]] = []
    for attr in attrs:
        for alias in sorted(attr.aliases, key=len, reverse=True):
            pairs.append((alias, attr.col))
    if named:
        pairs.extend(_DATASET_NUMERIC_ALIASES)
    seen_cols: set[str] = set()
    for alias, col in sorted(pairs, key=lambda x: len(x[0]), reverse=True):
        if col in seen_cols:
            continue
        # 「건폐율 40% 이상 70% 이하」→ BETWEEN (UNIT_TOKEN 캡처 중첩 회피)
        between = re.search(
            rf"{re.escape(alias)}\s*(?:이|가)?\s*"
            rf"(\d+(?:\.\d+)?)\s*(%|퍼센트|㎡|m2|m²|층)?\s*이상\s*"
            rf"(\d+(?:\.\d+)?)\s*(%|퍼센트|㎡|m2|m²|층)?\s*이하",
            q,
        )
        if between:
            lo_n, lo_u, hi_n, hi_u = (
                between.group(1),
                between.group(2) or "",
                between.group(3),
                between.group(4) or "",
            )
            attr = ATTR_BY_COL[col]
            target = attr.unit or "㎡"
            lo_c = convert_for_schema(lo_n, lo_u or target, target)
            hi_c = convert_for_schema(hi_n, hi_u or target, target)
            if lo_c is not None and hi_c is not None:
                _add_filter(
                    parsed,
                    col,
                    f'"{col}" BETWEEN {lo_c.sql} AND {hi_c.sql}',
                    f"{attr.label} {lo_c.label}~{hi_c.label}",
                )
                parsed.order_col = parsed.order_col or col
                seen_cols.add(col)
                continue
        m = re.search(
            rf"{re.escape(alias)}\s*(?:이|가)?\s*(\d+(?:\.\d+)?)\s*"
            rf"{UNIT_TOKEN}\s*"
            rf"(이상|이하|초과|미만|넘는)",
            q,
        )
        if not m:
            continue
        n, unit, rel = m.group(1), m.group(2), m.group(3)
        attr = ATTR_BY_COL[col]
        converted = convert_for_schema(n, unit, attr.unit or "㎡")
        if converted is None:
            continue
        _add_filter(
            parsed,
            col,
            f'"{col}" {_rel_op(rel)} {converted.sql}',
            f"{attr.label} {converted.label} {rel}",
        )
        parsed.order_col = parsed.order_col or col
        seen_cols.add(col)
    if "A31" not in seen_cols:
        floor_m = re.search(
            r"지상\s*(?:층수?)?[이가]?\s*(\d+)\s*층\s*(이상|이하|초과|미만|넘는)",
            q,
        )
        if floor_m is None:
            floor_m = re.search(
                r"(\d+)\s*층\s*(이상|이하|초과|미만|넘는)",
                q,
            )
            if floor_m and "지하" in q[max(0, floor_m.start() - 4) : floor_m.start()]:
                floor_m = None
        if floor_m is not None:
            n, rel = floor_m.group(1), floor_m.group(2)
            _add_filter(
                parsed,
                "A31",
                f'"A31" {_rel_op(rel)} {n}',
                f"지상층 {n}층 {rel}",
            )
            parsed.order_col = parsed.order_col or "A31"
            seen_cols.add("A31")
    if "A19" not in seen_cols:
        hit = pyeong_threshold(q)
        if hit is not None:
            converted, rel = hit
            _add_filter(
                parsed,
                "A19",
                f'"A19" {_rel_op(rel)} {converted.sql}',
                f"건물연면적 {converted.label} {rel}",
            )
            parsed.order_col = parsed.order_col or "A19"


def _parse_dates(q: str, parsed: D198Parsed) -> None:
    if looks_like_value_bin_question(q):
        return
    if looks_like_year_stats_question(q):
        _add_filter(
            parsed,
            "A34",
            f"({_date_valid('A34')})",
            "사용승인일자 있음",
        )
        return
    built_like = any(
        k in q for k in ("지어진", "지은", "준공", "건설일", "건설일자", "건립")
    )
    permit_like = "허가" in q and not any(
        k in q for k in ("지어", "준공", "사용승인")
    )
    for attr in D198_ATTRS:
        if attr.kind != "date":
            continue
        mentioned = any(a in q for a in attr.aliases)
        if not mentioned:
            if attr.col == "A33" and permit_like:
                mentioned = True
            elif attr.col == "A34" and built_like and not permit_like:
                mentioned = True
            else:
                continue
        decade_m = re.search(r"((?:19|20)\d)0\s*년대", q)
        if decade_m:
            start = int(decade_m.group(1) + "0")
            col = attr.col
            sql = (
                f'"{col}"::text ~ \'^[0-9]{{4}}\' AND '
                f"LEFT(regexp_replace(\"{col}\"::text, '[^0-9]', '', 'g'), 4)::int "
                f"BETWEEN {start} AND {start + 9}"
            )
            _add_filter(parsed, col, sql, f"{attr.label} {start}년대")
            continue
        m = re.search(
            rf"(?:19|20)\d{{2}}\s*년\s*(이후|이전|이래|까지)?",
            q,
        )
        year_m = re.search(r"((?:19|20)\d{2})\s*년", q)
        if year_m is None:
            # 값이 없으면 '일자가 있는 건물'
            _add_filter(
                parsed,
                attr.col,
                f"({_date_valid(attr.col)})",
                f"{attr.label} 있음",
            )
            continue
        year = int(year_m.group(1))
        rel = m.group(1) if m else None
        col = attr.col
        if rel in {"이후", "이래"}:
            sql = f"{_date_valid(col)} AND \"{col}\"::date >= '{year}-01-01'"
            label = f"{attr.label} {year}년 이후"
        elif rel in {"이전", "까지"}:
            sql = f"{_date_valid(col)} AND \"{col}\"::date < '{year}-01-01'"
            label = f"{attr.label} {year}년 이전"
        else:
            sql = (
                f"{_date_valid(col)} AND \"{col}\"::date >= '{year}-01-01' "
                f"AND \"{col}\"::date < '{year + 1}-01-01'"
            )
            label = f"{attr.label} {year}년"
        _add_filter(parsed, col, sql, label)


def _parse_values(q: str, parsed: D198Parsed, named: bool) -> None:
    # 긴 값 표현 우선
    candidates: list[tuple[int, D198Attr, str, str]] = []
    for attr in D198_ATTRS:
        if not attr.values:
            continue
        if not named and not attr.exclusive:
            continue
        for alias, stored in attr.values:
            if alias in q:
                candidates.append((len(alias), attr, alias, stored))
    candidates.sort(key=lambda x: x[0], reverse=True)
    used_spans: list[tuple[int, int]] = []
    grouped: dict[str, list[tuple[str, str]]] = {}
    for _, attr, alias, stored in candidates:
        start = q.find(alias)
        if start < 0:
            continue
        end = start + len(alias)
        if any(not (end <= a or start >= b) for a, b in used_spans):
            continue
        # '일반건축물'이 '일반건축물대장'의 일부면 스킵
        if alias == "일반건축물" and "일반건축물대장" in q:
            continue
        if alias in {"1", "2", "3", "4", "5", "6", "7", "0", "기타", "일반"}:
            if not any(a in q for a in attr.aliases):
                continue
        if attr.col == "A25" and "세부용도" in q and "주요용도" not in q:
            continue
        if attr.col == "A27" and "주요용도" in q and "세부용도" not in q:
            continue
        if attr.col == "A10" and "집합" not in alias and any(
            k in q for k in ("대장", "표제부")
        ):
            continue
        used_spans.append((start, end))
        pred = _eq(attr.col, stored)
        if attr.kind == "text" and attr.col in {"A23", "A13", "A14"}:
            pred = _like(attr.col, stored.rstrip("구조"))
            if attr.col == "A23":
                pred = f"\"A23\" ILIKE '%{_sql_str(stored.rstrip('구조'))}%'"
        # A27 세부용도 별칭은 정확 일치(골드 A27 = '아파트')
        if attr.col == "A27":
            pred = _eq(attr.col, stored)
        grouped.setdefault(attr.col, []).append((pred, f"{attr.label} {stored}"))
    for col, items in grouped.items():
        preds = [p for p, _ in items]
        labels = [lb for _, lb in items]
        sql = preds[0] if len(preds) == 1 else "(" + " OR ".join(preds) + ")"
        _add_filter(parsed, col, sql, " 또는 ".join(labels))


def _parse_codes_ids(q: str, parsed: D198Parsed) -> None:
    for attr in D198_ATTRS:
        if attr.kind not in {"code", "id"}:
            continue
        if not any(a in q for a in attr.aliases):
            continue
        alias_pat = "|".join(re.escape(a) for a in sorted(attr.aliases, key=len, reverse=True))
        m = re.search(
            rf"(?:{alias_pat})\s*(?:이|가|은|는)?\s*"
            rf"([0-9A-Za-z._-]{{1,40}})",
            q,
        )
        if not m:
            if re.search(rf"(?:{alias_pat}).{{0,8}}있", q):
                _add_filter(
                    parsed,
                    attr.col,
                    f'"{attr.col}" IS NOT NULL AND TRIM("{attr.col}"::text) <> \'\'',
                    f"{attr.label} 있음",
                )
            continue
        raw = m.group(1).rstrip("인.,")
        if raw in {"있는", "있나", "있어", "이상", "이하", "이후", "이전"}:
            _add_filter(
                parsed,
                attr.col,
                f'"{attr.col}" IS NOT NULL AND TRIM("{attr.col}"::text) <> \'\'',
                f"{attr.label} 있음",
            )
            continue
        if attr.kind == "id" and attr.col == "A0":
            if raw.isdigit():
                _add_filter(parsed, attr.col, f'"{attr.col}" = {int(raw)}', f"{attr.label} {raw}")
                parsed.lookup = True
            continue
        _add_filter(parsed, attr.col, _eq(attr.col, raw), f"{attr.label} {raw}")
        parsed.lookup = True

    _parse_named_text(q, parsed)


def _parse_named_text(q: str, parsed: D198Parsed) -> None:
    """건물명·동명·지번 등 라벨 뒤 문자열."""
    for col, aliases in (
        ("A13", ("건물명", "건물이름")),
        ("A14", ("건물동명",)),
        ("A7", ("지번",)),
    ):
        if col in parsed.columns:
            continue
        if not any(a in q for a in aliases):
            continue
        alias_pat = "|".join(re.escape(a) for a in aliases)
        m = re.search(
            rf"(?:{alias_pat})\s*(?:이|가|은|는)?\s*"
            rf"([가-힣0-9A-Za-z()._-]{{2,40}})",
            q,
        )
        if not m:
            continue
        raw = re.sub(r"(인|은|는|이|가)$", "", m.group(1))
        if raw in {"있는", "무엇", "뭐", "얼마", "몇"}:
            _add_filter(
                parsed,
                col,
                f'"{col}" IS NOT NULL AND TRIM("{col}") <> \'\'',
                f"{ATTR_BY_COL[col].label} 있음",
            )
            continue
        _add_filter(parsed, col, _like(col, raw), f"{ATTR_BY_COL[col].label} {raw}")
        parsed.lookup = True


def _parse_special_land(q: str, parsed: D198Parsed, named: bool) -> None:
    if "A5" in parsed.columns or "A6" in parsed.columns:
        return
    mountain = ("산지", "산번지", "산 번지", "임야", "산에 있는")
    if any(k in q for k in mountain) and (named or "특수지" in q):
        _add_filter(
            parsed,
            "A5",
            "(\"A5\"::text = '2' OR TRIM(COALESCE(\"A6\", '')) = '산')",
            "산지",
        )
        return
    if named and any(k in q for k in ("일반지번", "특수지 일반", "특수지구분 일반")):
        _add_filter(
            parsed,
            "A5",
            "(\"A5\"::text = '1' OR TRIM(COALESCE(\"A6\", '')) = '일반')",
            "일반지번",
        )


def _parse_structure(q: str, parsed: D198Parsed, named: bool) -> None:
    if "A23" in parsed.columns:
        return
    if not named:
        return
    st = extract_structure(q)
    if st is None:
        return
    token = st[0]
    _add_filter(
        parsed,
        "A23",
        f"\"A23\" ILIKE '%{_sql_str(token)}%'",
        f"{token} 구조",
    )


def _parse_rank(q: str, parsed: D198Parsed, named: bool) -> None:
    if not any(k in q for k in ("가장", "제일", "최대", "상위", "1등")):
        return
    recent = any(
        k in q
        for k in (
            "최근",
            "오래된",
            "오래 된",
            "오래됨",
            "지어진",
            "새로 지은",
            "나중에",
        )
    )
    if recent:
        parsed.rank = True
        parsed.order_asc = any(
            k in q for k in ("오래", "먼저 지은", "최초", "가장 먼저")
        )
        if "허가" in q and not any(k in q for k in ("지어", "준공", "사용승인")):
            parsed.order_col = "A33"
            parsed.labels.append("허가일자 최근" if not parsed.order_asc else "허가일자 오래된")
        else:
            parsed.order_col = "A34"
            parsed.labels.append(
                "사용승인일자 최근" if not parsed.order_asc else "사용승인일자 오래된"
            )
        return
    metrics: list[tuple[str, str]] = [
        ("건폐율", "A21"),
        ("용적율", "A20"),
        ("용적률", "A20"),
        ("건물높이", "A30"),
        ("건물연면적", "A19"),
        ("건물건축면적", "A18"),
        ("건물면적", "A18"),
        ("건축면적", "A18"),
        ("건물대지면적", "A17"),
        ("지하층", "A32"),
    ]
    if named:
        metrics.extend(
            (("높이", "A30"), ("연면적", "A19"), ("지상층", "A31"), ("대지면적", "A17"))
        )
    for alias, col in metrics:
        if alias in q:
            parsed.rank = True
            parsed.order_col = col
            attr = ATTR_BY_COL[col]
            if attr.label not in parsed.labels:
                parsed.labels.append(f"{attr.label} 최고")
            return


def rank_sane_sql(col: str) -> str | None:
    if col == "A30":
        return '"A30" > 0 AND "A30" <= 600'
    if col == "A20":
        return '"A20" > 0 AND "A20" <= 1500'
    if col == "A21":
        return '"A21" > 0 AND "A21" <= 150'
    if col in {"A17", "A18", "A19"}:
        return f'"{col}" > 0'
    if col in {"A31", "A32"}:
        return f'"{col}" > 0 AND "{col}" <= 80'
    if col in {"A33", "A34"}:
        return f"\"{col}\" ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$'"
    return None
