"""GIS건물통합·산업단지·행정구역·기초구역 전 속성 인식."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from txt2sql.domain import (
    busan_gu_code,
    extract_gu,
    extract_place,
    is_busan_wide,
    place_a4_predicate,
)
from txt2sql.units import UNIT_TOKEN, convert_for_schema, pyeong_threshold

_REL_OPS = {
    "초과": ">",
    "넘는": ">",
    "미만": "<",
    "이하": "<=",
    "까지": "<=",
    "사이": "<=",
    "이상": ">=",
    "부터": ">=",
}


@dataclass(frozen=True)
class Attr:
    col: str
    label: str
    aliases: tuple[str, ...]
    kind: str  # numeric | text | code | date | id
    exclusive: bool = False
    unit: str = ""
    values: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Dataset:
    key: str
    table: str
    hints: tuple[str, ...]
    attrs: tuple[Attr, ...]
    select_cols: tuple[str, ...]
    order_col: str
    intent_prefix: str
    unit_count: str = "건"


@dataclass
class Parsed:
    dataset: Dataset
    filters: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    order_col: str | None = None
    order_asc: bool = False
    dataset_hint: bool = False
    rank: bool = False
    lookup: bool = False


def _rel_op(rel: str) -> str:
    return _REL_OPS.get(rel, ">=")


def _sql_str(value: str) -> str:
    return value.replace("'", "''")


def _eq(col: str, value: str) -> str:
    return f"TRIM(COALESCE(\"{col}\"::text, '')) = '{_sql_str(str(value))}'"


def _like(col: str, value: str) -> str:
    return f"\"{col}\" ILIKE '%{_sql_str(str(value))}%'"


def _date_valid(col: str) -> str:
    return f"\"{col}\"::text ~ '^[0-9]{{4}}'"


def _add(parsed: Parsed, col: str, sql: str, label: str) -> None:
    if sql in parsed.filters:
        return
    parsed.filters.append(sql)
    if col not in parsed.columns:
        parsed.columns.append(col)
    if label not in parsed.labels:
        parsed.labels.append(label)


def _is_schema_question(q: str) -> bool:
    if any(k in q for k in ("이상", "이하", "초과", "미만", "몇", "채")):
        return False
    # 「목록」만으로는 스키마가 아님(건물 목록 등). 컬럼/속성 목록은 아래에서 잡음.
    if "목록" in q and not any(
        k in q for k in ("컬럼", "칼럼", "속성", "필드", "스키마")
    ):
        return False
    return any(
        k in q
        for k in (
            "컬럼",
            "칼럼",
            "스키마",
            "속성 설명",
            "의미가",
            "속성데이터",
            "속성 데이터",
            "필드목록",
            "필드 목록",
            "컬럼목록",
            "컬럼 목록",
            "칼럼목록",
            "칼럼 목록",
        )
    )


def _has_hint(q: str, ds: Dataset) -> bool:
    upper = q.upper()
    for h in ds.hints:
        if h.isascii():
            if h.upper() in upper:
                return True
        elif h in q:
            return True
    return False


# --- D010 GIS건물통합정보 ---
D010_ATTRS: tuple[Attr, ...] = (
    Attr("A0", "원천도형ID", ("원천도형ID", "원천도형아이디"), "id", exclusive=True),
    Attr("A1", "GIS건물통합식별번호", ("GIS건물통합식별번호", "건물통합식별번호"), "id", exclusive=True),
    Attr("A2", "고유번호", ("고유번호", "PNU", "pnu"), "id", exclusive=True),
    Attr("A3", "법정동코드", ("법정동코드",), "code", exclusive=True),
    Attr("A4", "법정동명", ("법정동명", "법정동"), "text"),
    Attr("A5", "지번", ("지번",), "text"),
    Attr(
        "A6",
        "특수지코드",
        ("특수지코드",),
        "code",
        exclusive=True,
        values=(("1", "1"), ("2", "2")),
    ),
    Attr(
        "A7",
        "특수지구분명",
        ("특수지구분명",),
        "text",
        values=(("일반지번", "일반"), ("산지", "산")),
    ),
    Attr("A8", "건축물용도코드", ("건축물용도코드", "용도코드"), "code", exclusive=True),
    Attr("A9", "건축물용도명", ("건축물용도명",), "text"),
    Attr("A10", "건축물구조코드", ("건축물구조코드", "구조코드"), "code", exclusive=True),
    Attr("A11", "건축물구조명", ("건축물구조명",), "text"),
    Attr("A12", "건축물면적", ("건축물면적", "건물면적"), "numeric", unit="㎡"),
    Attr("A13", "사용승인일자", ("사용승인일자", "사용승인일"), "date", exclusive=True),
    Attr("A14", "연면적", ("연면적",), "numeric", unit="㎡"),
    Attr("A15", "대지면적", ("대지면적",), "numeric", unit="㎡"),
    Attr("A16", "높이", ("높이", "건물높이"), "numeric", unit="m"),
    Attr("A17", "건폐율", ("건폐율",), "numeric", exclusive=True, unit="%"),
    Attr("A18", "용적율", ("용적율", "용적률"), "numeric", exclusive=True, unit="%"),
    Attr("A19", "건축물ID", ("건축물ID", "건축물아이디"), "id", exclusive=True),
    Attr(
        "A20",
        "위반건축물여부",
        ("위반건축물여부", "위반건축물"),
        "text",
        exclusive=True,
        values=(("위반", "Y"), ("Y", "Y"), ("N", "N")),
    ),
    Attr("A21", "참조체계연계키", ("참조체계연계키",), "id", exclusive=True),
    Attr("A22", "데이터기준일자", ("데이터기준일자", "데이터기준일"), "date", exclusive=True),
    Attr("A23", "원천시도시군구코드", ("원천시도시군구코드",), "code", exclusive=True),
    Attr("A24", "건물명", ("건물명",), "text"),
    Attr("A25", "건물동명", ("건물동명",), "text", exclusive=True),
    Attr("A26", "지상층", ("지상층수", "지상층"), "numeric", unit="층"),
    Attr("A27", "지하층", ("지하층수", "지하층"), "numeric", exclusive=True, unit="층"),
    Attr("A28", "데이터생성변경일자", ("데이터생성변경일자",), "date", exclusive=True),
)

D010 = Dataset(
    key="d010",
    table="AL_D010_26_20250704",
    hints=("GIS건물통합정보", "건물통합정보", "AL_D010", "D010"),
    attrs=D010_ATTRS,
    select_cols=(
        "A0",
        "A4",
        "A5",
        "A7",
        "A9",
        "A11",
        "A12",
        "A13",
        "A14",
        "A16",
        "A17",
        "A18",
        "A20",
        "A24",
        "A25",
        "A26",
        "A27",
    ),
    order_col="A14",
    intent_prefix="d010_attr",
    unit_count="채",
)

# --- D060 산업단지 ---
D060_ATTRS: tuple[Attr, ...] = (
    Attr("A0", "원천도형ID", ("원천도형ID",), "id", exclusive=True),
    Attr("A1", "도면번호", ("도면번호",), "id", exclusive=True),
    Attr("A2", "주제도명", ("주제도명", "주제도"), "text", exclusive=True),
    Attr("A3", "데이터기준일자", ("데이터기준일자", "데이터기준일", "기준일"), "date", exclusive=True),
    Attr("A4", "원천시도시군구코드", ("원천시도시군구코드", "시군구코드"), "code", exclusive=True),
    Attr("A5", "용도지역지구코드", ("용도지역지구코드",), "code", exclusive=True),
    Attr(
        "A6",
        "용도지역지구코드명",
        ("용도지역지구코드명", "용도지역지구", "용도지역"),
        "text",
        exclusive=True,
        values=(
            ("일반산업단지", "일반산업단지"),
            ("도시첨단산업단지", "도시첨단산업단지"),
            ("국가산업단지", "국가산업단지"),
            ("지방산업단지", "지방산업단지"),
            ("농공단지", "농공단지"),
            ("준산업단지", "준산업단지"),
        ),
    ),
    Attr("A7", "고시일자", ("고시일자", "고시일"), "date", exclusive=True),
    Attr("A8", "객체내용", ("객체내용",), "text", exclusive=True),
    Attr("A9", "업무참조내용", ("업무참조내용", "업무참조"), "text", exclusive=True),
)

D060 = Dataset(
    key="d060",
    table="AL_D060_00_20250804",
    hints=("산업단지_전국", "AL_D060", "D060"),
    attrs=D060_ATTRS,
    select_cols=("A0", "A2", "A4", "A5", "A6", "A7", "A8", "A9"),
    order_col="A0",
    intent_prefix="d060_attr",
    unit_count="개",
)

# --- BND 행정구역 ---
BND_ATTRS: tuple[Attr, ...] = (
    Attr("ADM_CD", "행정동코드", ("행정동코드", "행정구역코드"), "code", exclusive=True),
    Attr("ADM_NM", "행정동명", ("행정동명",), "text"),
    Attr("BASE_DATE", "기준일", ("기준일", "정보갱신 기준일", "갱신기준일"), "date", exclusive=True),
)

BND = Dataset(
    key="bnd",
    table="BND_ADM_DONG_PG",
    hints=("행정구역", "센서스 기반 행정구역", "행정구역 동명", "BND_ADM", "BND"),
    attrs=BND_ATTRS,
    select_cols=("ADM_CD", "ADM_NM", "BASE_DATE"),
    order_col="ADM_NM",
    intent_prefix="bnd_attr",
    unit_count="개",
)

# --- BAS 기초구역 ---
BAS_ATTRS: tuple[Attr, ...] = (
    Attr("BAS_ID", "기초구역번호", ("기초구역번호", "기초구역ID"), "id", exclusive=True),
    Attr("BAS_AR", "기초구역면적", ("기초구역면적",), "numeric", unit="㎡"),
    Attr("BAS_MGT_SN", "기초구역관리번호", ("기초구역관리번호",), "id", exclusive=True),
    Attr("CTP_KOR_NM", "시도명", ("시도명",), "text", exclusive=True),
    Attr("SIG_CD", "시군구코드", ("시군구코드",), "code", exclusive=True),
    Attr("SIG_KOR_NM", "시군구명", ("시군구명",), "text"),
    Attr("NTFC_DE", "고시일자", ("고시일자", "고시일"), "date", exclusive=True),
    Attr("OPERT_DE", "작업일자", ("작업일자",), "date", exclusive=True),
    Attr("MVMN_DE", "이동일자", ("이동일자",), "date", exclusive=True),
    Attr(
        "MVMN_RESN",
        "이동사유",
        ("이동사유",),
        "text",
        exclusive=True,
        values=(
            ("최초생성", "국가기초구역 최초생성"),
            ("경계 변경", "기초구역 경계 변경에 의한 기초구역 변경"),
            ("행정구역 변경", "행정구역 변경에 의한 기초구역 변경"),
        ),
    ),
)

BAS = Dataset(
    key="bas",
    table="TL_KODIS_BAS_26_202507",
    hints=("도로명주소 기초구역", "TL_KODIS", "KODIS"),
    attrs=BAS_ATTRS,
    select_cols=(
        "BAS_ID",
        "BAS_AR",
        "BAS_MGT_SN",
        "SIG_CD",
        "SIG_KOR_NM",
        "CTP_KOR_NM",
        "NTFC_DE",
        "MVMN_DE",
        "MVMN_RESN",
    ),
    order_col="BAS_AR",
    intent_prefix="bas_attr",
    unit_count="개",
)

DATASETS: tuple[Dataset, ...] = (D010, D060, BND, BAS)


def _exclusive_hit(q: str, ds: Dataset) -> bool:
    for attr in ds.attrs:
        if not attr.exclusive:
            continue
        if any(a in q for a in attr.aliases):
            return True
        if any(alias in q for alias, _stored in attr.values if len(alias) >= 2):
            return True
    return False


def _plain_dataset_count_only(q: str, ds: Dataset) -> bool:
    """데이터셋+건수만 있고 속성 필터가 없으면 기존 전용 라우트에 맡긴다."""
    if ds.key == "d060" and "산업단지" in q:
        fieldish = _exclusive_hit(q, ds) or any(
            a in q for attr in ds.attrs for a in attr.aliases
        )
        return not fieldish
    if ds.key == "bas" and "기초구역" in q and not _has_hint(q, ds):
        if not _exclusive_hit(q, ds):
            # 면적 상위는 기존 라우트
            if "상위" in q or "큰 순" in q:
                return True
            if not any(a in q for a in ("기초구역번호", "기초구역면적", "관리번호", "이동", "고시", "작업")):
                return True
    if ds.key == "bnd" and any(
        k in q for k in ("건물", "건축물", "주택", "아파트", "채")
    ):
        return True
    return False


def is_catalog_question(question: str, ds: Dataset) -> bool:
    q = question.strip()
    if not q or _is_schema_question(q):
        return False
    if ds.key != "d198" and any(k in q for k in ("용도별건물공간정보", "용도별건물")):
        return False
    if ds.key == "d010" and any(
        k in q for k in ("산업단지", "기초구역", "행정구역")
    ):
        return False
    if ds.key == "d060" and ("건물" in q or "건축물" in q) and "산업단지" in q:
        return False
    if _plain_dataset_count_only(q, ds):
        return False
    hinted = _has_hint(q, ds)
    if ds.key == "d010":
        # 산지·특수지는 기존 D010 전용 라우트 유지 (데이터셋명 있을 때만 카탈로그)
        if any(k in q for k in ("산지", "특수지", "일반지번")):
            return hinted
        return hinted or _exclusive_hit(q, ds)
    if ds.key == "d060":
        if hinted:
            return True
        return "산업단지" in q and _exclusive_hit(q, ds)
    if ds.key == "bas":
        return hinted or ("기초구역" in q and _exclusive_hit(q, ds))
    if ds.key == "bnd":
        if hinted or _exclusive_hit(q, ds):
            return True
        # 「…_행정동」업로드 표시명에 붙은 접미는 BND 단서로 쓰지 않음
        if "행정동" in q and not re.search(r"_행정동", q):
            return True
        return False
    return hinted or _exclusive_hit(q, ds)


def place_filters(ds: Dataset, q: str) -> list[str]:
    gu = extract_gu(q)
    place = extract_place(q)
    if ds.key == "d010":
        where: list[str] = []
        from txt2sql.gazetteer import is_legal_dong, uses_admin_boundary

        if place and uses_admin_boundary(place):
            if gu:
                where.append(place_a4_predicate(gu))
        elif place and is_legal_dong(place):
            where.append(place_a4_predicate(place))
            if gu:
                where.append(place_a4_predicate(gu))
        elif gu:
            where.append(place_a4_predicate(gu))
        elif place:
            where.append(place_a4_predicate(place))
        return where
    if ds.key == "d060":
        if gu:
            code = busan_gu_code(gu)
            if code:
                return [f"\"A4\" = '{code}'"]
        if is_busan_wide(q) or "부산" in q:
            return ["\"A4\" LIKE '26%'"]
        return []
    if ds.key == "bnd":
        where = []
        if gu:
            code = busan_gu_code(gu)
            if code:
                where.append(f"(\"ADM_CD\" LIKE '{code}%' OR \"ADM_NM\" ILIKE '%{gu}%')")
            else:
                where.append(f'"ADM_NM" ILIKE \'%{gu}%\'')
        if place:
            from txt2sql.gazetteer import is_locality
            from txt2sql.spatial_templates import admin_dong_name_predicate

            if is_locality(place):
                where.append(admin_dong_name_predicate(place, alias=""))
                where.append('"ADM_CD" LIKE \'21%\'')
        if not where and (is_busan_wide(q) or "부산" in q):
            where.append("\"ADM_CD\" LIKE '26%'")
        return where
    if ds.key == "bas":
        if gu:
            return [f'"SIG_KOR_NM" = \'{gu}\'']
        if is_busan_wide(q) or "부산" in q:
            return ['"CTP_KOR_NM" = \'부산광역시\'']
        return []
    return []


def parse_dataset(question: str, ds: Dataset) -> Parsed | None:
    q = question.strip()
    if not is_catalog_question(q, ds):
        return None
    named = _has_hint(q, ds) or (
        ds.key == "d060" and "산업단지" in q and _exclusive_hit(q, ds)
    ) or (
        ds.key == "bas" and "기초구역" in q
    ) or (
        ds.key == "bnd" and any(k in q for k in ("행정구역", "행정동", "센서스"))
    )
    parsed = Parsed(dataset=ds, dataset_hint=named)
    _parse_numeric(q, parsed, ds, named)
    _parse_dates(q, parsed, ds)
    _parse_values(q, parsed, ds, named)
    _parse_codes_ids(q, parsed, ds)
    _parse_named_text(q, parsed, ds)
    _parse_rank(q, parsed, ds, named)
    if not parsed.filters and not parsed.rank and not named:
        return None
    if not parsed.filters and not parsed.rank and named:
        parsed.labels.append(ds.hints[0] if ds.hints else ds.key)
    return parsed


def match_catalog(question: str) -> Parsed | None:
    """우선순위: 산업단지 속성 → 기초구역 속성 → 행정구역 → GIS건물통합."""
    q = question.strip()
    order = (D060, BAS, BND, D010)
    for ds in order:
        parsed = parse_dataset(q, ds)
        if parsed is not None:
            return parsed
    return None


def is_catalog_attr_question(question: str) -> bool:
    """네 데이터셋 전 속성 질의인지 (건물명 조회·스키마 메타보다 우선)."""
    return match_catalog(question) is not None


def _parse_numeric(q: str, parsed: Parsed, ds: Dataset, named: bool) -> None:
    pairs: list[tuple[str, str]] = []
    for attr in ds.attrs:
        if attr.kind != "numeric":
            continue
        for alias in attr.aliases:
            pairs.append((alias, attr.col))
    seen: set[str] = set()
    for alias, col in sorted(pairs, key=lambda x: len(x[0]), reverse=True):
        if col in seen:
            continue
        m = re.search(
            rf"{re.escape(alias)}\s*(?:이|가)?\s*(\d+(?:\.\d+)?)\s*"
            rf"{UNIT_TOKEN}\s*"
            r"(이상|초과|부터)\s*"
            rf"(\d+(?:\.\d+)?)\s*{UNIT_TOKEN}\s*"
            r"(이하|미만|까지|사이)",
            q,
        )
        if m:
            attr = next(a for a in ds.attrs if a.col == col)
            lo = convert_for_schema(m.group(1), m.group(2), attr.unit or "㎡")
            hi = convert_for_schema(m.group(4), m.group(5), attr.unit or "㎡")
            if lo is None or hi is None or lo.canonical >= hi.canonical:
                continue
            lo_op = _rel_op(m.group(3))
            hi_op = _rel_op(m.group(6))
            _add(
                parsed,
                col,
                f'"{col}" {lo_op} {lo.sql}',
                f"{attr.label} {lo.label} {m.group(3)}",
            )
            _add(
                parsed,
                col,
                f'"{col}" {hi_op} {hi.sql}',
                f"{attr.label} {hi.label} {m.group(6)}",
            )
            parsed.order_col = parsed.order_col or col
            seen.add(col)
            continue
        m = re.search(
            rf"{re.escape(alias)}\s*(?:이|가)?\s*(\d+(?:\.\d+)?)\s*"
            rf"{UNIT_TOKEN}\s*"
            rf"(이상|이하|초과|미만|넘는)",
            q,
        )
        if not m:
            continue
        attr = next(a for a in ds.attrs if a.col == col)
        n, unit, rel = m.group(1), m.group(2), m.group(3)
        converted = convert_for_schema(n, unit, attr.unit or "㎡")
        if converted is None:
            continue
        _add(
            parsed,
            col,
            f'"{col}" {_rel_op(rel)} {converted.sql}',
            f"{attr.label} {converted.label} {rel}",
        )
        parsed.order_col = parsed.order_col or col
        seen.add(col)
    if ds.key == "d010" and "A14" not in seen and "기초구역" not in q:
        hit = pyeong_threshold(q)
        if hit is not None:
            converted, rel = hit
            _add(
                parsed,
                "A14",
                f'"A14" {_rel_op(rel)} {converted.sql}',
                f"연면적 {converted.label} {rel}",
            )
            parsed.order_col = parsed.order_col or "A14"


def _parse_dates(q: str, parsed: Parsed, ds: Dataset) -> None:
    for attr in ds.attrs:
        if attr.kind != "date":
            continue
        if not any(a in q for a in attr.aliases):
            continue
        year_m = re.search(r"((?:19|20)\d{2})\s*년", q)
        if year_m is None:
            _add(
                parsed,
                attr.col,
                f"({_date_valid(attr.col)})",
                f"{attr.label} 있음",
            )
            continue
        year = int(year_m.group(1))
        rel_m = re.search(rf"{year}\s*년\s*(이후|이전|이래|까지)?", q)
        rel = rel_m.group(1) if rel_m else None
        col = attr.col
        yexpr = (
            f"LEFT(regexp_replace(\"{col}\"::text, '[^0-9]', '', 'g'), 4)"
        )
        if rel in {"이후", "이래"}:
            sql = f"{_date_valid(col)} AND {yexpr} >= '{year}'"
            label = f"{attr.label} {year}년 이후"
        elif rel in {"이전", "까지"}:
            sql = f"{_date_valid(col)} AND {yexpr} < '{year}'"
            label = f"{attr.label} {year}년 이전"
        else:
            sql = f"{_date_valid(col)} AND {yexpr} = '{year}'"
            label = f"{attr.label} {year}년"
        _add(parsed, col, sql, label)


def _parse_values(q: str, parsed: Parsed, ds: Dataset, named: bool) -> None:
    candidates: list[tuple[int, Attr, str, str]] = []
    for attr in ds.attrs:
        if not attr.values:
            continue
        if not named and not attr.exclusive:
            continue
        for alias, stored in attr.values:
            if alias in q:
                candidates.append((len(alias), attr, alias, stored))
    candidates.sort(key=lambda x: x[0], reverse=True)
    used: list[tuple[int, int]] = []
    for _, attr, alias, stored in candidates:
        start = q.find(alias)
        if start < 0:
            continue
        end = start + len(alias)
        if any(not (end <= a or start >= b) for a, b in used):
            continue
        if alias in {"Y", "N", "1", "2"} and not any(a in q for a in attr.aliases):
            continue
        used.append((start, end))
        pred = _eq(attr.col, stored)
        if attr.kind == "text" and len(stored) >= 2:
            pred = _like(attr.col, stored)
        _add(parsed, attr.col, pred, f"{attr.label} {stored}")


def _parse_codes_ids(q: str, parsed: Parsed, ds: Dataset) -> None:
    for attr in ds.attrs:
        if attr.kind not in {"code", "id"}:
            continue
        if not any(a in q for a in attr.aliases):
            continue
        alias_pat = "|".join(re.escape(a) for a in sorted(attr.aliases, key=len, reverse=True))
        m = re.search(
            rf"(?:{alias_pat})\s*(?:이|가|은|는)?\s*([0-9A-Za-z._-]{{1,40}})",
            q,
        )
        if not m:
            if re.search(rf"(?:{alias_pat}).{{0,8}}있", q):
                _add(
                    parsed,
                    attr.col,
                    f'"{attr.col}" IS NOT NULL AND TRIM("{attr.col}"::text) <> \'\'',
                    f"{attr.label} 있음",
                )
            continue
        raw = m.group(1).rstrip("인.,")
        if raw in {"있는", "있나", "있어", "이상", "이하", "이후", "이전"}:
            _add(
                parsed,
                attr.col,
                f'"{attr.col}" IS NOT NULL AND TRIM("{attr.col}"::text) <> \'\'',
                f"{attr.label} 있음",
            )
            continue
        if attr.kind == "id" and raw.isdigit() and attr.col in {"A0"}:
            _add(parsed, attr.col, f'"{attr.col}" = {int(raw)}', f"{attr.label} {raw}")
            parsed.lookup = True
            continue
        _add(parsed, attr.col, _eq(attr.col, raw), f"{attr.label} {raw}")
        parsed.lookup = True


def _parse_named_text(q: str, parsed: Parsed, ds: Dataset) -> None:
    text_attrs = [a for a in ds.attrs if a.kind == "text" and a.col not in parsed.columns]
    for attr in text_attrs:
        if not any(a in q for a in attr.aliases):
            continue
        alias_pat = "|".join(re.escape(a) for a in attr.aliases)
        m = re.search(
            rf"(?:{alias_pat})\s*(?:이|가|은|는)?\s*([가-힣0-9A-Za-z()._-]{{2,40}})",
            q,
        )
        if not m:
            if re.search(rf"(?:{alias_pat}).{{0,8}}있", q):
                _add(
                    parsed,
                    attr.col,
                    f'"{attr.col}" IS NOT NULL AND TRIM("{attr.col}"::text) <> \'\'',
                    f"{attr.label} 있음",
                )
            continue
        raw = re.sub(r"(인|은|는|이|가|의)$", "", m.group(1))
        if raw in {
            "있는",
            "무엇",
            "뭐",
            "얼마",
            "몇",
            "건물",
            "아파트",
            "주택",
            "공동주택",
        }:
            _add(
                parsed,
                attr.col,
                f'"{attr.col}" IS NOT NULL AND TRIM("{attr.col}"::text) <> \'\'',
                f"{attr.label} 있음",
            )
            continue
        _add(parsed, attr.col, _like(attr.col, raw), f"{attr.label} {raw}")
        parsed.lookup = True


def _parse_rank(q: str, parsed: Parsed, ds: Dataset, named: bool) -> None:
    if not any(k in q for k in ("가장", "제일", "최대", "상위", "1등")):
        return
    metrics = [(a.aliases[0], a.col) for a in ds.attrs if a.kind == "numeric"]
    for alias, col in metrics:
        if alias in q:
            parsed.rank = True
            parsed.order_col = col
            return
    if named and any(k in q for k in ("가장 큰", "제일 큰", "가장 넓은")):
        parsed.rank = True
        parsed.order_col = ds.order_col


def all_attrs() -> list[tuple[Dataset, Attr]]:
    out: list[tuple[Dataset, Attr]] = []
    for ds in DATASETS:
        for attr in ds.attrs:
            out.append((ds, attr))
    return out
