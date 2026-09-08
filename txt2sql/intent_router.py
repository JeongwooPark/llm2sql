"""고빈도 GIS 질의 패턴을 규칙으로 해석해 SQL을 직접 생성한다."""

from __future__ import annotations

import re
from dataclasses import dataclass

import psycopg
from psycopg.rows import dict_row

from txt2sql.d198_attrs import (
    D198_SELECT_COLS,
    ValueBinSpec,
    YearStatsSpec,
    parse_d198_question,
    parse_value_bin,
    parse_year_stats,
    rank_sane_sql,
    value_bin_sane_sql,
)
from txt2sql.domain import (
    D198_TABLES,
    DONG_PATTERN,
    GU_PATTERN,
    LENGTH_DIST_PATTERN,
    _NAME_STOP,
    age_date_predicate,
    busan_gu_code,
    calendar_year_predicate_sql,
    d198_gu_for_dong,
    d198_table_for_gu,
    dong_requires_gu,
    extract_age_compare,
    extract_age_years,
    extract_building_name_candidate,
    extract_gu,
    extract_industrial_name,
    extract_industrial_names,
    extract_place,
    extract_special_land,
    extract_structure,
    extract_usage,
    is_busan_wide,
    legal_dong_guess,
    looks_like_age_question,
    looks_like_building_name_lookup,
    looks_like_measure_threshold,
    wants_map_display,
    _FALSE_DONG,
    place_a4_predicate,
    sane_floor_area_sql,
    sane_footprint_sql,
    sane_height_sql,
)
from txt2sql.dataset_tables import resolve_basic_zone_table, resolve_building_table
from txt2sql.spatial_templates import (
    place_buffer_count_sql,
    place_buffer_list_sql,
    scoped_count_sql,
    scoped_list_sql,
)
from txt2sql.spatial_router import try_spatial_route
from txt2sql.units import UNIT_TOKEN, convert_for_schema, pyeong_threshold, sql_number


@dataclass(frozen=True)
class RoutedQuery:
    intent: str
    sql: str


_GU = GU_PATTERN
_DONG = DONG_PATTERN
_COUNT_HINT = (
    "몇",
    "개수",
    "건수",
    "채",
    "채수",
    "수",
    "세어",
    "구해",
    "알려",
    "조회",
    "얼마",
    "얼마나",
    "되나요",
    "레코드",
    "있는지",
    "찾아",
    "확인",
)
_LIST_HINT = (
    "무엇이 있어",
    "뭐가 있어",
    "뭐 있어",
    "어떤 게 있어",
    "어떤것이 있어",
    "어떤 것이 있어",
    "목록",
    "리스트",
)


def _wants_scalar_count(q: str) -> bool:
    """Scalar COUNT intent — excludes compare/group/duplicate questions."""
    if any(k in q for k in ("차이", "평균", "비교", "중복", "합계", "비율")):
        return False
    if "별" in q and any(k in q for k in ("보여", "집계", "나눠", "구분", "각각")):
        return False
    if re.search(r"주거용\s*(과|와)\s*상업용|상업용\s*(과|와)\s*주거용", q):
        return False
    return True


def _wants_count(q: str) -> bool:
    # 순위·최댓값 질의는 건수가 아님
    if any(k in q for k in ("가장", "제일", "최대", "상위", "1등", "큰 순", "높은 순")):
        return False
    if not _wants_scalar_count(q):
        return False
    if any(k in q for k in _COUNT_HINT):
        return True
    # "해운대구 건물?"처럼 짧은 물음만 건수로 간주
    stripped = q.rstrip()
    if stripped.endswith(("?", "？")) and len(stripped) <= 24:
        return True
    return False


def _wants_list(q: str) -> bool:
    return any(k in q for k in _LIST_HINT)


def _d010() -> str:
    return resolve_building_table()


def _bas() -> str:
    return resolve_basic_zone_table()



def _usage_kind_specs() -> list[tuple[str, str, str, str]]:
    from txt2sql.domain import D198_BY_GU

    specs: list[tuple[str, str, str, str]] = []
    for gu, table in D198_BY_GU.items():
        stem = gu.replace("구", "").replace("군", "")
        key = stem if len(stem) >= 2 else gu
        specs.append((key, f"usage_kinds_{stem or gu}", table, gu))
    return specs


def _swap_d198_for_d010(sql: str) -> str:
    return re.sub(r"AL_D198_[0-9]+_[0-9]+", _d010(), sql)


def _count_sql(intent: str, table: str, where: str) -> RoutedQuery:
    return RoutedQuery(
        intent,
        f'SELECT COUNT(*) AS cnt\nFROM "{table}"\nWHERE {where};',
    )


def _a4_place_filters(place: str | None, gu: str | None) -> list[str]:
    from txt2sql.gazetteer import is_legal_dong, uses_admin_boundary

    where: list[str] = []
    if place and uses_admin_boundary(place):
        if gu:
            where.append(place_a4_predicate(gu))
        return where
    if place and is_legal_dong(place):
        where.append(place_a4_predicate(place))
        if gu:
            where.append(place_a4_predicate(gu))
    elif gu:
        where.append(place_a4_predicate(gu))
    elif place:
        where.append(place_a4_predicate(place))
    return where


def _d198_select_list() -> str:
    cols = ", ".join(f'"{c}"' for c in D198_SELECT_COLS)
    return f"SELECT {cols}"


def _year_stats_has_other_metric(q: str) -> bool:
    """층/면적/구조가 있으면 연도 분포 전용 라우트를 쓰지 않는다."""
    if extract_structure(q):
        return True
    if _parse_floor_threshold(q) is not None or _parse_height_threshold(q) is not None:
        return True
    if _parse_area_threshold(q) is not None or _area_threshold_hits(q):
        return True
    return bool(re.search(r"\d+\s*층", q))


def _d198_year_stats_sql(
    tables: list[str],
    where_sql: str,
    stats: YearStatsSpec,
) -> RoutedQuery:
    date_col = stats.date_col
    year_expr = f"SUBSTRING(\"{date_col}\" FROM 1 FOR 4)"
    bin_years = getattr(stats, "bin_years", 1) or 1
    if stats.mode == "decade" or bin_years == 10:
        period_expr = f"(FLOOR(({year_expr})::int / 10) * 10)::int"
        alias = "decade"
    elif stats.mode == "bin" or bin_years > 1:
        period_expr = (
            f"(FLOOR(({year_expr})::int / {bin_years}) * {bin_years})::int"
        )
        alias = "period"
    else:
        period_expr = f"({year_expr})::int"
        alias = "year"
    extra = [where_sql, f"{year_expr} ~ '^[0-9]{{4}}$'"]
    if stats.mode == "decade" and stats.decades:
        ins = ", ".join(str(d) for d in stats.decades)
        extra.append(f"{period_expr} IN ({ins})")
    where = " AND ".join(p for p in extra if p and p != "TRUE")

    def one(tbl: str) -> str:
        return (
            f'SELECT {period_expr} AS {alias}, COUNT(*) AS n\n'
            f'FROM "{tbl}"\n'
            f"WHERE {where}\n"
            f"GROUP BY 1"
        )

    if len(tables) == 1:
        sql = f"{one(tables[0])}\nORDER BY 1;"
    else:
        inner = "\nUNION ALL\n".join(one(t) for t in tables)
        sql = (
            f"SELECT {alias}, SUM(n) AS n\n"
            f"FROM (\n{inner}\n) AS year_parts\n"
            f"GROUP BY 1\n"
            f"ORDER BY 1;"
        )
    return RoutedQuery("d198_year_stats", sql)


def _d198_value_bin_sql(
    tables: list[str],
    where_sql: str,
    spec: ValueBinSpec,
) -> RoutedQuery:
    col = spec.col
    width = spec.bin_width
    wsql = str(int(width)) if float(width).is_integer() else f"{width:g}"
    period_expr = f'(FLOOR("{col}" / {wsql}) * {wsql})::int'
    extra = [where_sql, value_bin_sane_sql(col)]
    where = " AND ".join(p for p in extra if p and p != "TRUE")

    def one(tbl: str) -> str:
        return (
            f"SELECT {period_expr} AS period, COUNT(*) AS n\n"
            f'FROM "{tbl}"\n'
            f"WHERE {where}\n"
            f"GROUP BY 1"
        )

    if len(tables) == 1:
        sql = f"{one(tables[0])}\nORDER BY 1;"
    else:
        inner = "\nUNION ALL\n".join(one(t) for t in tables)
        sql = (
            "SELECT period, SUM(n) AS n\n"
            f"FROM (\n{inner}\n) AS value_parts\n"
            "GROUP BY 1\n"
            "ORDER BY 1;"
        )
    return RoutedQuery("d198_value_bins", sql)


def _route_d198_attr(
    q: str,
    *,
    conn: psycopg.Connection | None,
) -> RoutedQuery | None:
    """용도별건물공간정보(AL_D198) 속성 필터·목록·건수·순위."""
    # 「1990년대 준공된 건물을 보여줘」는 SQP decade list가 담당
    if re.search(r"((?:19|20)\d{2})\s*년대", q) and any(
        k in q for k in ("보여줘", "보여 줘", "목록", "리스트", "나열", "찾아줘")
    ):
        return None
    parsed = parse_d198_question(q)
    if parsed is None:
        return None
    # 건폐율·용적률만(D010 공통 metric)이면 D010 catalog 경로에 맡긴다 (MAIN485 grain).
    cols = {c.upper() for c in (parsed.columns or [])}
    exclusive_cols = cols - {"A20", "A21"}
    if cols and not exclusive_cols:
        from txt2sql.domain import extract_usage, extract_usage_classes, extract_detail_usages

        if not (
            extract_usage(q)
            or extract_usage_classes(q)
            or extract_detail_usages(q)
            or looks_like_age_question(q)
        ):
            return None
    gu = extract_gu(q)
    place = extract_place(q)
    table = _resolve_d198_table(q, conn=conn, gu=gu, place=place)
    if table:
        tables = [table]
    elif (
        parsed.dataset_hint
        or parse_year_stats(q) is not None
        or parse_value_bin(q) is not None
    ):
        tables = list(D198_TABLES)
    else:
        return None

    where = list(_a4_place_filters(place, gu))
    where.extend(parsed.filters)
    if parsed.rank and parsed.order_col:
        sane = rank_sane_sql(parsed.order_col)
        if sane:
            where.append(sane)
    where_sql = " AND ".join(where) if where else "TRUE"
    vbin = parse_value_bin(q)
    if vbin is not None:
        return _d198_value_bin_sql(tables, where_sql, vbin)
    stats = parse_year_stats(q)
    if stats is not None and not _year_stats_has_other_metric(q):
        return _d198_year_stats_sql(tables, where_sql, stats)

    order_col = parsed.order_col or "A19"
    order_dir = "ASC" if parsed.order_asc else "DESC"
    select = _d198_select_list()

    if parsed.rank:
        limit = 1
        intent = "d198_attr_rank"
    elif parsed.lookup:
        limit = 20
        intent = "d198_attr_lookup"
    elif _wants_threshold_list(q) or not any(k in q for k in _AREA_COUNTISH):
        limit = 100
        intent = "d198_attr_list"
    else:
        intent = "d198_attr_count"
        if len(tables) == 1:
            return _count_sql(intent, tables[0], where_sql)
        parts = [
            f'SELECT COUNT(*) AS c FROM "{t}" WHERE {where_sql}' for t in tables
        ]
        sql = (
            "SELECT COALESCE(SUM(c), 0) AS cnt\n"
            "FROM (\n  "
            + "\n  UNION ALL\n  ".join(parts)
            + "\n) AS d198_parts;"
        )
        return RoutedQuery(intent, sql)

    def list_sql(tbl: str, n: int) -> str:
        return (
            f"{select}\n"
            f'FROM "{tbl}"\n'
            f"WHERE {where_sql}\n"
            f'ORDER BY "{order_col}" {order_dir} NULLS LAST\n'
            f"LIMIT {n};"
        )

    if len(tables) == 1:
        return RoutedQuery(intent, list_sql(tables[0], limit))

    inner = "\nUNION ALL\n".join(
        f'  {select} FROM "{t}" WHERE {where_sql}' for t in tables
    )
    sql = (
        f"{select}\n"
        f"FROM (\n{inner}\n) AS d198_u\n"
        f'ORDER BY "{order_col}" {order_dir} NULLS LAST\n'
        f"LIMIT {limit};"
    )
    return RoutedQuery(intent, sql)


def _route_catalog_attr(q: str) -> RoutedQuery | None:
    """GIS건물통합·산업단지·행정구역·기초구역 전 속성."""
    from txt2sql.catalog_attrs import match_catalog, place_filters

    parsed = match_catalog(q)
    if parsed is None:
        return None
    ds = parsed.dataset
    where = list(place_filters(ds, q))
    where.extend(parsed.filters)
    where_sql = " AND ".join(where) if where else "TRUE"
    order_col = parsed.order_col or ds.order_col
    order_dir = "ASC" if parsed.order_asc else "DESC"
    cols = ", ".join(f'"{c}"' for c in ds.select_cols)
    select = f"SELECT {cols}"
    prefix = ds.intent_prefix

    date_stats = _catalog_base_date_sql(q, parsed, where_sql)
    if date_stats is not None:
        return date_stats

    if parsed.rank:
        limit, intent = 1, f"{prefix}_rank"
    elif parsed.lookup:
        limit, intent = 20, f"{prefix}_lookup"
    elif _wants_threshold_list(q) or not any(k in q for k in _AREA_COUNTISH):
        limit, intent = 100, f"{prefix}_list"
    else:
        return _count_sql(f"{prefix}_count", ds.table, where_sql)

    sql = (
        f"{select}\n"
        f'FROM "{ds.table}"\n'
        f"WHERE {where_sql}\n"
        f'ORDER BY "{order_col}" {order_dir} NULLS LAST\n'
        f"LIMIT {limit};"
    )
    return RoutedQuery(intent, sql)


def _catalog_base_date_sql(q: str, parsed, where_sql: str):
    """자료의 기준일 → MIN/MAX/COUNT. 행 목록이 아니다."""
    if not any(k in q for k in ("기준일", "데이터기준일", "데이터기준일자")):
        return None
    if any(k in q for k in ("이후", "이전", "이래", "년까지")):
        return None
    if re.search(r"(19|20)\d{2}\s*년", q):
        return None
    ds = parsed.dataset
    date_attr = next(
        (
            a
            for a in ds.attrs
            if a.kind == "date" and any(al in q for al in a.aliases)
        ),
        None,
    )
    if date_attr is None:
        return None
    col = date_attr.col
    return RoutedQuery(
        f"{ds.intent_prefix}_base_date",
        (
            f'SELECT MIN("{col}") AS min_base, MAX("{col}") AS max_base, '
            f'COUNT(*) AS n\n'
            f'FROM "{ds.table}"\n'
            f"WHERE {where_sql};"
        ),
    )


_AREA_METRICS = (
    ("건물면적", "A12"),
    ("건축물면적", "A12"),
    ("건축면적", "A12"),
    ("연면적", "A14"),
    ("대지면적", "A15"),
    ("면적", "A14"),
)
_AREA_LISTISH = (
    "목록",
    "리스트",
    "보여",
    "것은",
    "인것",
    "인 것",
    "인것은",
    "것들",
    "어떤",
    "찾아",
    "알려줘",
    "이름",
    "건물명",
    "명칭",
)
_AREA_COUNTISH = (
    "몇",
    "건수",
    "개수",
    "채수",
    "수는",
    "개가",
    "채야",
    "몇개",
    "건물 수",
    "건물수",
)


def _rel_op(rel: str) -> str:
    if rel in ("초과", "넘는"):
        return ">"
    if rel == "미만":
        return "<"
    if rel in ("이하", "까지", "사이"):
        return "<="
    return ">="


def _parse_lo_hi_range(
    q: str,
    prefix: str,
    schema_unit: str,
) -> tuple[tuple[str, str], tuple[str, str]] | None:
    """((lo_op, lo_sql), (hi_op, hi_sql)) — N 이상 M 미만 등 한 지표 구간."""
    m = re.search(
        rf"{prefix}"
        rf"(\d+(?:\.\d+)?)\s*{UNIT_TOKEN}\s*"
        r"(이상|초과|부터)\s*"
        rf"(\d+(?:\.\d+)?)\s*{UNIT_TOKEN}\s*"
        r"(이하|미만|까지|사이)",
        q,
    )
    if not m:
        return None
    lo = convert_for_schema(m.group(1), m.group(2), schema_unit)
    hi = convert_for_schema(m.group(4), m.group(5), schema_unit)
    if lo is None or hi is None or lo.canonical >= hi.canonical:
        return None
    return (_rel_op(m.group(3)), lo.sql), (_rel_op(m.group(6)), hi.sql)


def _parse_area_threshold(q: str) -> tuple[str, str, str] | None:
    """(col, op, value) — 면적 N 이상/이하. 기초구역 면적은 제외."""
    hits = _area_threshold_hits(q)
    if hits:
        return hits[0]
    hit = pyeong_threshold(q)
    if hit is not None:
        converted, rel = hit
        return "A14", _rel_op(rel), converted.sql
    return None


def _area_threshold_hits(q: str) -> list[tuple[str, str, str]]:
    """등장한 면적 필드별 (col, op, value_sql). 제네릭 '면적'은 구체 필드가 없을 때만."""
    if "기초구역" in q:
        return []
    hits: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    specific = any(
        k in q for k in ("연면적", "건축면적", "건물면적", "대지면적", "건축물면적")
    )
    for label, col in _AREA_METRICS:
        if col in seen:
            continue
        if label == "면적" and specific:
            continue
        m = re.search(
            rf"{label}\s*(?:이|가)?\s*(\d+(?:\.\d+)?)\s*"
            rf"{UNIT_TOKEN}\s*"
            r"(이상|이하|초과|미만|넘는)",
            q,
        )
        if not m:
            continue
        converted = convert_for_schema(m.group(1), m.group(2), "㎡")
        if converted is None:
            continue
        seen.add(col)
        hits.append((col, _rel_op(m.group(3)), converted.sql))
    # 「1만 제곱미터」 등 만(萬) 표기
    for label, col in _AREA_METRICS:
        if col in seen:
            continue
        if label == "면적" and specific:
            continue
        m = re.search(
            rf"{label}\s*(?:이|가)?\s*(\d+(?:\.\d+)?)\s*만\s*"
            rf"(?:제곱미터|평방미터|㎡|m2|m²)?\s*"
            r"(이상|이하|초과|미만|넘는)",
            q,
        )
        if not m:
            continue
        value = float(m.group(1)) * 10000.0
        seen.add(col)
        hits.append((col, _rel_op(m.group(2)), f"{value:g}"))
    return hits


def _parse_area_predicates(q: str) -> tuple[str, list[str]] | None:
    """(order_col, extras) — 단일 필드 임계/구간만. 다중 면적 필드는 Plan으로."""
    if "기초구역" in q:
        return None
    hits = _area_threshold_hits(q)
    if len({col for col, _, _ in hits}) >= 2:
        return None
    for label, col in _AREA_METRICS:
        pair = _parse_lo_hi_range(q, rf"{re.escape(label)}\s*(?:이|가)?\s*", "㎡")
        if pair is None:
            continue
        (lo_op, lo_v), (hi_op, hi_v) = pair
        return col, [f'"{col}" {lo_op} {lo_v}', f'"{col}" {hi_op} {hi_v}']
    parsed = _parse_area_threshold(q)
    if parsed is None:
        return None
    col, op, area = parsed
    extras = [f'"{c}" {o} {v}' for c, o, v in hits] if hits else [f'"{col}" {op} {area}']
    return col, extras


def _wants_threshold_list(q: str) -> bool:
    return any(k in q for k in _AREA_LISTISH) and not any(
        k in q for k in _AREA_COUNTISH
    )


def _route_map_display(q: str) -> RoutedQuery | None:
    """「건물데이터를 표시하라」→ 건수 SQL. 지도는 COUNT를 피처로 올린다."""
    if not wants_map_display(q):
        return None
    if "산업단지" in q or "기초구역" in q:
        return None
    place = extract_place(q)
    gu = extract_gu(q)
    if not place and not gu:
        return None
    extra: list[str] = []
    usage = extract_usage(q)
    if usage:
        extra.append(f"\"A9\" = '{usage}'")
    year_sql = calendar_year_predicate_sql(q, col="A13")
    if year_sql:
        extra.append(year_sql)
    if gu and (not place or str(place).endswith(("구", "군"))):
        where = [place_a4_predicate(gu), *extra]
        sql = (
            'SELECT COUNT(*) AS cnt\n'
            f'FROM "{_d010()}"\n'
            f'WHERE {" AND ".join(where)};'
        )
        return RoutedQuery("building_map_display", sql)
    kind, sql = scoped_count_sql(place, gu, extra)
    if kind == "none":
        return None
    return RoutedQuery("building_map_display", sql)


def _route_measure_threshold(
    q: str,
    *,
    extras: list[str],
    order_col: str,
    list_intent: str,
    count_intent: str,
) -> RoutedQuery | None:
    """동·구 수치 임계 — 목록 또는 건수."""
    place = extract_place(q)
    gu = extract_gu(q)
    extra = list(extras)
    usage = extract_usage(q)
    if usage:
        extra.append(f'"A9" = \'{usage}\'')
    year_sql = calendar_year_predicate_sql(q, col="A13")
    if year_sql:
        extra.append(year_sql)
    kind, sql = (
        scoped_list_sql(place, gu, extra, order_col=order_col)
        if _wants_threshold_list(q)
        else scoped_count_sql(place, gu, extra)
    )
    if kind == "none":
        return None
    if _wants_threshold_list(q):
        return RoutedQuery(list_intent, sql)
    return RoutedQuery(count_intent, sql)


def _route_building_area_threshold(q: str) -> RoutedQuery | None:
    parsed = _parse_area_predicates(q)
    if parsed is None:
        return None
    col, extras = parsed
    return _route_measure_threshold(
        q,
        extras=extras,
        order_col=col,
        list_intent="building_area_threshold_list",
        count_intent="building_area_threshold_count",
    )


def _parse_height_threshold(q: str) -> tuple[str, str] | None:
    if "높이" not in q:
        return None
    m = re.search(
        rf"높이[가이]?\s*(\d+(?:\.\d+)?)\s*{UNIT_TOKEN}\s*"
        r"(이상|이하|초과|미만|넘는)",
        q,
    )
    if not m:
        return None
    converted = convert_for_schema(m.group(1), m.group(2), "m")
    if converted is None:
        return None
    return _rel_op(m.group(3)), converted.sql


def _parse_floor_threshold(q: str) -> tuple[str, str] | None:
    hangul_floor = {
        "한": "1",
        "두": "2",
        "세": "3",
        "네": "4",
        "다섯": "5",
        "여섯": "6",
        "일곱": "7",
        "여덟": "8",
        "아홉": "9",
        "열": "10",
        "십": "10",
    }
    m = re.search(
        r"(?:지상\s*층?|층수|지상층)[이가]?\s*(\d+)\s*층\s*(이상|이하|초과|미만|넘는)",
        q,
    )
    if m:
        return _rel_op(m.group(2)), m.group(1)
    m = re.search(
        r"(?:지상\s*층?|층수|지상층)[이가]?\s*([가-힣]+)\s*층\s*(이상|이하|초과|미만|넘는)",
        q,
    )
    if m:
        digits = hangul_floor.get(m.group(1))
        if digits:
            return _rel_op(m.group(2)), digits
    m = re.search(
        r"지상\s*([가-힣]+)\s*층\s*(넘는|초과|이상|미만|이하)",
        q,
    )
    if m:
        digits = hangul_floor.get(m.group(1))
        if digits:
            return _rel_op(m.group(2)), digits
    m = re.search(r"(\d+)\s*층\s*(이상|이하|초과|미만|넘는)", q)
    if m:
        prefix = q[max(0, m.start() - 4) : m.start()]
        if "지하" in prefix:
            return None
        return _rel_op(m.group(2)), m.group(1)
    if "지상" not in q and "층수" not in q:
        return None
    m = re.search(r"지상\s*층?[이가]?\s*(\d+)\s*층", q)
    if not m:
        return None
    if "미만" in q:
        rel = "미만"
    elif "이하" in q:
        rel = "이하"
    elif "넘는" in q or "초과" in q:
        rel = "초과"
    elif "이상" in q:
        rel = "이상"
    else:
        return None
    return _rel_op(rel), m.group(1)


def _route_building_height_threshold(q: str) -> RoutedQuery | None:
    pair = _parse_lo_hi_range(q, r"높이[가이]?\s*", "m") if "높이" in q else None
    if pair is not None:
        extras = [f'"A16" {pair[0][0]} {pair[0][1]}', f'"A16" {pair[1][0]} {pair[1][1]}']
    else:
        parsed = _parse_height_threshold(q)
        if parsed is None:
            return None
        op, meters = parsed
        extras = [f'"A16" {op} {meters}']
    return _route_measure_threshold(
        q,
        extras=extras,
        order_col="A16",
        list_intent="building_height_threshold_list",
        count_intent="building_height_count",
    )


def _route_building_structure(q: str) -> RoutedQuery | None:
    """동·구(또는 시 전체) + 건축물구조(A11) 및/또는 특수지(A6/A7) 목록/건수."""
    from txt2sql.domain import structure_a11_predicate

    st = extract_structure(q)
    land = extract_special_land(q)
    if st is None and land is None:
        return None
    filters = _a4_place_filters(extract_place(q), extract_gu(q))
    # 장소 없이도 구조/특수지 exact·ILIKE count 허용(부산 전체 등)
    where = list(filters)
    if st:
        alias, pattern = st
        where.append(structure_a11_predicate(q, alias, pattern))
    if land:
        where.append(land[1])
    if "위반" in q:
        if any(k in q for k in ("아닌", "아니", "제외", "없는")):
            where.append("\"A20\" IS DISTINCT FROM 'Y'")
        else:
            where.append("\"A20\" = 'Y'")
    usage = extract_usage(q)
    if usage:
        where.append(f'"A9" = \'{usage}\'')
    where_sql = " AND ".join(where) if where else "TRUE"
    if st and land:
        list_intent, count_intent = "building_attr_list", "building_attr_count"
    elif st:
        list_intent, count_intent = (
            "building_structure_list",
            "building_structure_count",
        )
    else:
        list_intent, count_intent = (
            "building_special_land_list",
            "building_special_land_count",
        )
    if _wants_threshold_list(q) or not any(k in q for k in _AREA_COUNTISH):
        return RoutedQuery(
            list_intent,
            (
                'SELECT "A0", "A4", "A5", "A6", "A7", "A9", "A11", "A12", '
                '"A14", "A16", "A24", "A26",\n'
                "       COUNT(*) OVER() AS total_n\n"
                f'FROM "{_d010()}"\n'
                f"WHERE {where_sql}\n"
                'ORDER BY "A14" DESC NULLS LAST\n'
                "LIMIT 100;"
            ),
        )
    return _count_sql(count_intent, _d010(), where_sql)


def _parse_floor_range(q: str) -> tuple[tuple[str, str], tuple[str, str]] | None:
    m = re.search(
        r"(?:지상\s*층?|층수|지상층)[이가]?\s*(\d+)\s*층\s*(이상|초과|부터)\s*(\d+)\s*층\s*(이하|미만|까지|사이)",
        q,
    )
    if not m:
        m = re.search(
            r"(\d+)\s*층\s*(이상|초과|부터)\s*(\d+)\s*층\s*(이하|미만|까지|사이)",
            q,
        )
    if not m:
        return None
    lo, hi = int(m.group(1)), int(m.group(3))
    if lo >= hi:
        return None
    return (_rel_op(m.group(2)), str(lo)), (_rel_op(m.group(4)), str(hi))


def _route_building_floor_threshold(q: str) -> RoutedQuery | None:
    pair = _parse_floor_range(q)
    if pair is not None:
        extras = [f'"A26" {pair[0][0]} {pair[0][1]}', f'"A26" {pair[1][0]} {pair[1][1]}']
    else:
        parsed = _parse_floor_threshold(q)
        if parsed is None:
            return None
        op, floors = parsed
        extras = [f'"A26" {op} {floors}']
    return _route_measure_threshold(
        q,
        extras=extras,
        order_col="A26",
        list_intent="building_floor_threshold_list",
        count_intent="building_floor_count",
    )


def _route_usage_kinds(q: str) -> RoutedQuery | None:
    if not (("용도" in q) and ("종류" in q or "몇 가지" in q or "몇가지" in q)):
        return None
    for key, intent, table, like in _usage_kind_specs():
        if key in q:
            return RoutedQuery(
                intent,
                (
                    f'SELECT COUNT(DISTINCT "A25") AS cnt\n'
                    f'FROM "{table}"\n'
                    f"WHERE \"A4\" LIKE '%{like}%' AND \"A25\" IS NOT NULL;"
                ),
            )
    return None


def _legal_dong_for_filter(
    conn: psycopg.Connection | None, place: str
) -> str:
    """행정동은 법정동으로 치환하지 않는다 (BND 경로 유지)."""
    from txt2sql.gazetteer import uses_admin_boundary

    if uses_admin_boundary(place):
        return place
    if conn is None:
        return place
    from txt2sql.clarify_qa import _lookup_admin_dong

    if _lookup_admin_dong(conn, place):
        guessed = legal_dong_guess(place)
        if guessed:
            return guessed
    return place


def _looks_like_rank_ask(q: str) -> bool:
    if any(
        k in q
        for k in ("상위", "가장 높", "가장 큰", "제일 높", "제일 큰", "높은 순", "큰 순")
    ):
        return True
    return bool(
        re.search(r"\d+\s*(개|곳|채|동)\b", q)
        and any(k in q for k in ("높", "큰", "상위", "넓은"))
    )


def _numeric_families(q: str) -> set[str]:
    fam: set[str] = set()
    area_hits = _area_threshold_hits(q)
    area_cols = {col for col, _, _ in area_hits}
    if len(area_cols) >= 2:
        fam.add("area_multi")
        for col in area_cols:
            fam.add(f"area_{col}")
    elif area_hits or _parse_area_threshold(q) is not None:
        fam.add("area")
    if _parse_height_threshold(q) is not None:
        fam.add("height")
    if _parse_floor_threshold(q) is not None:
        fam.add("floors")
    if _has_temporal_metric(q):
        fam.add("temporal")
    return fam


def _has_temporal_metric(q: str) -> bool:
    if "년대" in q or looks_like_age_question(q):
        return True
    try:
        from txt2sql.query_understanding.temporal import parse_temporal_filters

        return bool(parse_temporal_filters(q))
    except Exception:
        return False


def _d198_exclusive_question(q: str) -> bool:
    return any(
        k in q
        for k in (
            "세부용도",
            "주요용도",
            "표제부",
            "집합건축물",
            "허가일",
            "허가일자",
            "용도분류",
            "문교사회용",
            "용도별건물",
        )
    )


def should_defer_compound_to_plan(q: str) -> bool:
    """단일 규칙 라우트가 조건을 일부만 먹을 복합질의는 SQP에 넘긴다."""
    if any(h in q for h in ("용도별건물", "AL_D198", "D198")):
        return False
    families = _numeric_families(q)
    if "area_multi" in families or len(families) >= 2:
        return True
    spatial_inside = any(
        k in q for k in ("안에", "내에", "내부", "안쪽", "경계 안", "경계안")
    )
    if spatial_inside and (families or _looks_like_rank_ask(q)):
        return True
    if _has_place_buffer_hint(q) and families:
        return True
    if extract_structure(q) and (
        "floors" in families or _looks_like_rank_ask(q) or families
    ):
        return True
    if extract_special_land(q) and families:
        return True
    if "지하" in q and "지상" in q:
        return True
    if (
        "area" in families
        and not any(k in q for k in _AREA_COUNTISH)
        and not _wants_threshold_list(q)
    ):
        return True
    if re.search(r"지하.{0,12}\d+", q) and "지상" not in q:
        return True
    if any(k in q for k in ("건물동명", "동명이")) and families:
        return True
    if any(k in q for k in ("평균", "합계")) and any(
        k in q for k in ("높이", "연면적", "건축면적", "대지면적", "층수", "지상")
    ):
        return True
    if "용도별" in q and any(k in q for k in ("개수", "건수", "분포", "구성")):
        return True
    if any(k in q for k in (" 또는 ", " 혹은 ", "이거나", "제외", "아닌", "빼고", "뺀")):
        if not _d198_exclusive_question(q):
            return True
    if any(k in q for k in ("사이", "부터", "까지", "건폐율", "용적율", "용적률", "위반")):
        if not _d198_exclusive_question(q):
            return True
    if any(k in q for k in ("비율", "퍼센트", "몇%", "%씩", "몇 프로")):
        return True
    return False


_METRIC_FAMILY_KEYS = (
    ("높이", "고도"),
    ("연면적",),
    ("건축면적", "건물면적"),
    ("건폐율",),
    ("용적율", "용적률"),
    ("지상층", "층수"),
)


def _route_sigungu_site_avg_vs_city(q: str) -> RoutedQuery | None:
    if not any(k in q for k in ("구·군별", "군별")):
        return None
    if "대지면적" not in q or "평균" not in q:
        return None
    if not any(k in q for k in ("전체 평균", "부산 전체", "비교")):
        return None
    from txt2sql.dataset_tables import resolve_building_table
    from txt2sql.semantic_plan.compiler import _sigungu_name_sql

    tbl = resolve_building_table() or "AL_D010_26_20250704"
    gl = _sigungu_name_sql("b")
    num = 'NULLIF(TRIM(b."A15"::text), \'\')::float8'
    sql = (
        f"WITH g AS (\n"
        f"  SELECT {gl} AS gu, AVG({num}) AS avg_lot FROM \"{tbl}\" b GROUP BY 1\n"
        f"), city AS (SELECT AVG({num}) AS city_avg FROM \"{tbl}\" b)\n"
        f"SELECT g.gu, g.avg_lot, city.city_avg, g.avg_lot - city.city_avg AS diff\n"
        f"FROM g, city ORDER BY g.avg_lot DESC NULLS LAST"
    )
    return RoutedQuery("sigungu_site_avg_vs_city", sql)


def _route_sigungu_avg_ground_floors(q: str) -> RoutedQuery | None:
    """구·군별 평균 지상층수 (RAG ROUND double 오류 회피)."""
    if not any(k in q for k in ("구·군별", "구별", "군별")):
        return None
    if "평균" not in q:
        return None
    if not any(k in q for k in ("지상층", "층수")):
        return None
    if any(
        k in q
        for k in ("대지면적", "연면적", "건축면적", "높이", "건폐", "용적", "비교")
    ):
        return None
    from txt2sql.dataset_tables import resolve_building_table
    from txt2sql.semantic_plan.compiler import _sigungu_name_sql

    tbl = resolve_building_table() or "AL_D010_26_20250704"
    gl = _sigungu_name_sql("b")
    num = "NULLIF(TRIM(b.\"A26\"::text), '')::float8"
    sql = (
        f'SELECT {gl} AS gu, AVG({num}) AS avg_floors\n'
        f'FROM "{tbl}" b\n'
        f"WHERE {gl} IS NOT NULL\n"
        f"GROUP BY 1 ORDER BY avg_floors DESC NULLS LAST"
    )
    return RoutedQuery("sigungu_avg_ground_floors", sql)


def _route_dual_gu_approval_year_median(q: str) -> RoutedQuery | None:
    """두 구의 준공(사용승인)연도 중앙값 비교."""
    if not any(k in q for k in ("중앙값", "중위")):
        return None
    if not any(k in q for k in ("준공", "사용승인")):
        return None
    if "비교" not in q and "와" not in q and "과" not in q:
        return None
    from txt2sql.domain import BUSAN_GU_CODES, d198_table_for_gu

    gus = sorted(
        (g for g in BUSAN_GU_CODES if g in q),
        key=lambda name: q.index(name),
    )
    if len(gus) < 2:
        return None
    left, right = gus[0], gus[1]
    t_left, t_right = d198_table_for_gu(left), d198_table_for_gu(right)
    if not t_left or not t_right:
        return None
    year_expr = "LEFT(regexp_replace(\"A34\"::text, '[^0-9]', '', 'g'), 4)::int"
    year_ok = "\"A34\"::text ~ '^[0-9]{4}'"
    sql = (
        "SELECT\n"
        "  PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY y) "
        "FILTER (WHERE src='a') AS a_median_year,\n"
        "  PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY y) "
        "FILTER (WHERE src='b') AS b_median_year\n"
        "FROM (\n"
        f"  SELECT 'a' AS src, {year_expr} AS y "
        f'FROM "{t_left}" WHERE {year_ok}\n'
        "  UNION ALL\n"
        f"  SELECT 'b', {year_expr} "
        f'FROM "{t_right}" WHERE {year_ok}\n'
        ") t"
    )
    return RoutedQuery("dual_gu_approval_year_median", sql)


def _route_d198_legal_dong_topn(q: str) -> RoutedQuery | None:
    """D198: gu + (temporal cutoff 또는 용도) + top-N legal_dong by building count."""
    import re

    if "법정동" not in q or not any(k in q for k in ("보여", "알려", "목록", "곳")):
        return None
    if not any(k in q for k in ("가장 많은", "많은", "상위", "TOP", "top")):
        return None
    m_lim = re.search(r"(\d+)\s*곳", q)
    if not m_lim:
        return None
    limit = int(m_lim.group(1))
    gu = extract_gu(q)
    if not gu:
        return None
    from txt2sql.domain import d198_table_for_gu, extract_usage

    table = d198_table_for_gu(gu)
    if not table:
        return None
    year_cutoff = None
    m_year = re.search(r"(\d{4})\s*년\s*이전", q)
    if m_year:
        year_cutoff = m_year.group(1)
    elif "이전 준공" in q or "이전에 준공" in q:
        m2 = re.search(r"(\d{4})", q)
        if m2:
            year_cutoff = m2.group(1)
    usage = extract_usage(q)
    if not year_cutoff and not usage:
        return None
    where_parts: list[str] = []
    if year_cutoff:
        where_parts.extend([f'"A34" IS NOT NULL', f'"A34" < \'{year_cutoff}\''])
    if usage:
        safe = usage.replace("'", "''")
        where_parts.append(f"\"A25\" = '{safe}'")
    sql = (
        f'SELECT "A4" AS bjd, COUNT(*)::bigint AS n\n'
        f'FROM "{table}"\n'
        f"WHERE {' AND '.join(where_parts)}\n"
        f"GROUP BY 1 ORDER BY n DESC NULLS LAST LIMIT {limit}"
    )
    return RoutedQuery("d198_legal_dong_topn", sql)


def _route_d198_null_date_group(q: str) -> RoutedQuery | None:
    if "법정동별" not in q or not any(k in q for k in ("집계", "별로")):
        return None
    gu = extract_gu(q)
    if not gu:
        return None
    from txt2sql.domain import d198_table_for_gu

    table = d198_table_for_gu(gu)
    if not table:
        return None
    if "사용승인일" in q and any(k in q for k in ("NULL", "null", "없")):
        null_col = "A34"
    elif "허가일" in q and any(k in q for k in ("NULL", "null", "없")):
        null_col = "A33"
    else:
        return None
    where = f'(TRIM(COALESCE("{null_col}"::text, \'\')) = \'\')'
    sql = (
        f'SELECT "A4" AS bjd, COUNT(*)::bigint AS n\n'
        f'FROM "{table}"\n'
        f"WHERE {where}\n"
        f"GROUP BY 1 ORDER BY n DESC"
    )
    return RoutedQuery("d198_null_date_group", sql)


def _route_bas_admin_overlap(q: str) -> RoutedQuery | None:
    """기초구역×행정동 겹침 HAVING(개수 이상)."""
    if "기초구역" not in q or "행정동" not in q:
        return None
    if not any(k in q for k in ("겹치", "교차", "걸친")):
        return None
    import re

    m = re.search(r"(\d+)\s*개\s*이상", q)
    threshold = int(m.group(1)) if m else 3
    wants_list = any(k in q for k in ("찾아", "보여", "목록", "리스트"))
    wants_count = any(k in q for k in ("몇", "집계", "수"))
    if not wants_list and not wants_count:
        return None
    sql = (
        f'SELECT t."BAS_ID", COUNT(DISTINCT d."ADM_NM")::bigint AS n\n'
        f'FROM "{_bas()}" t\n'
        f'JOIN "BND_ADM_DONG_PG" d ON ST_Intersects(t.geometry, d.geometry)\n'
        f"WHERE d.\"ADM_CD\" LIKE '21%'\n"
        f"GROUP BY 1 HAVING COUNT(DISTINCT d.\"ADM_NM\") >= {threshold}\n"
        f"ORDER BY n DESC NULLS LAST\n"
        f"LIMIT 50"
    )
    return RoutedQuery("bas_admin_overlap", sql)


def _route_industrial_building_group(q: str) -> RoutedQuery | None:
    """산업단지별 내부 건물 수/평균 연면적."""
    if "산업단지별" not in q:
        return None
    if not any(k in q for k in ("건물", "건축물")):
        return None
    if "경계" in q:
        return None
    wants_avg = "평균" in q and any(k in q for k in ("연면적", "면적"))
    wants_count = any(k in q for k in ("수", "집계", "몇"))
    if not wants_avg and not wants_count:
        return None
    if wants_avg:
        sql = (
            'SELECT COALESCE(NULLIF(TRIM(i."A8"), \'\'), i."A9") AS park,\n'
            '       AVG(NULLIF(TRIM(b."A14"::text), \'\')::float8) AS avg_gfa\n'
            'FROM "AL_D060_00_20250804" i\n'
            'JOIN "AL_D010_26_20250704" b ON ST_Intersects(b.geometry, i.geometry)\n'
            "WHERE i.\"A4\" LIKE '26%'\n"
            "GROUP BY 1 ORDER BY avg_gfa DESC NULLS LAST;"
        )
        return RoutedQuery("industrial_building_avg_gfa", sql)
    sql = (
        'SELECT COALESCE(NULLIF(TRIM(i."A8"), \'\'), i."A9") AS park,\n'
        '       COUNT(DISTINCT b."A1")::bigint AS n\n'
        'FROM "AL_D060_00_20250804" i\n'
        'JOIN "AL_D010_26_20250704" b ON ST_Intersects(b.geometry, i.geometry)\n'
        "WHERE i.\"A4\" LIKE '26%'\n"
        "GROUP BY 1 ORDER BY n DESC NULLS LAST;"
    )
    return RoutedQuery("industrial_building_count_by_park", sql)


def _route_pnu_duplicate(q: str) -> RoutedQuery | None:
    """D198 PNU 중복(HAVING COUNT>1)."""
    if "PNU" not in q.upper() and "pnu" not in q:
        return None
    if not any(k in q for k in ("두 번", "중복", "이상 나타", "중복된")):
        return None
    if "D198" not in q.upper() and "용도별" not in q:
        return None
    gu = extract_gu(q)
    from txt2sql.domain import d198_table_for_gu

    table = d198_table_for_gu(gu) if gu else None
    if not table:
        return None
    sql = (
        'SELECT "A2" AS pnu, COUNT(*)::bigint AS n\n'
        f'FROM "{table}"\n'
        "WHERE TRIM(COALESCE(\"A2\"::text, '')) <> ''\n"
        "GROUP BY 1 HAVING COUNT(*) > 1\n"
        "ORDER BY n DESC NULLS LAST\n"
        "LIMIT 20;"
    )
    return RoutedQuery("d198_pnu_duplicate", sql)


def _route_industrial_admin_sig_group(q: str) -> RoutedQuery | None:
    if "산업단지" not in q or "행정동" not in q:
        return None
    if not any(k in q for k in ("구·군별", "구별", "군별")):
        return None
    if not any(k in q for k in ("집계", "수를", "개수", "몇")):
        return None
    d060 = "AL_D060_00_20250804"
    bnd = "BND_ADM_DONG_PG"
    sql = (
        f'SELECT LEFT(d."ADM_CD", 5) AS adm_sig, COUNT(DISTINCT d."ADM_NM")::bigint AS n\n'
        f'FROM "{d060}" i JOIN "{bnd}" d ON ST_Intersects(i.geometry, d.geometry)\n'
        f"WHERE i.\"A4\" LIKE '26%' AND d.\"ADM_CD\" LIKE '21%'\n"
        f"GROUP BY 1 ORDER BY n DESC"
    )
    return RoutedQuery("industrial_admin_sig_group", sql)


def _legacy_must_yield_to_plan(question: str) -> bool:
    """복합 Contract는 capability 평가 후 Plan으로 넘긴다."""
    from txt2sql.route_capability import select_execution_path

    return select_execution_path(question) == "semantic_plan"


def try_route(
    question: str,
    conn: psycopg.Connection | None = None,
) -> RoutedQuery | None:
    q = question.strip()
    from txt2sql.count_routes import match_priority_count_route

    priority = match_priority_count_route(q)
    if priority is not None:
        return RoutedQuery(priority.intent, priority.sql)
    # Candidate detection only. Execution is gated by RouteCapability.

    name_cmp = _route_building_name_set_compare(q)
    if name_cmp is not None:
        return name_cmp

    sigungu_cmp = _route_sigungu_site_avg_vs_city(q)
    if sigungu_cmp is not None:
        return sigungu_cmp
    sigungu_floors = _route_sigungu_avg_ground_floors(q)
    if sigungu_floors is not None:
        return sigungu_floors
    dual_median = _route_dual_gu_approval_year_median(q)
    if dual_median is not None:
        return dual_median

    null_group = _route_d198_null_date_group(q)
    if null_group is not None:
        return null_group

    d198_topn = _route_d198_legal_dong_topn(q)
    if d198_topn is not None:
        return d198_topn

    ind_admin = _route_industrial_admin_sig_group(q)
    if ind_admin is not None:
        return ind_admin
    bas_adm = _route_bas_admin_overlap(q)
    if bas_adm is not None:
        return bas_adm
    ind_build = _route_industrial_building_group(q)
    if ind_build is not None:
        return ind_build
    pnu_dup = _route_pnu_duplicate(q)
    if pnu_dup is not None:
        return pnu_dup

    # 산업단지 관련 규칙 라우트 (건물명보다 우선)
    for industrial_fn in (
        _route_buildings_in_industrial,
        _route_industrial_names,
        _route_industrial_area_rank,
        _route_industrial_count_by_sigungu,
        _route_industrial_count,
    ):
        industrial = industrial_fn(q)
        if industrial is not None:
            return industrial

    overlap = _route_building_industrial_bas_overlap(q)
    if overlap is not None:
        return overlap

    pnu_match = _route_d010_d198_pnu_match(q)
    if pnu_match is not None:
        return pnu_match

    bas_rank = _route_basic_zone_area_rank(q)
    if bas_rank is not None:
        return bas_rank

    # 행정동·기초구역·건물 공간 연산 (속성 COUNT/행정구 오탐보다 우선)
    spatial_hit = try_spatial_route(q)
    if spatial_hit is not None:
        return spatial_hit

    # 지도 표출 — 카탈로그·SQP 주소 목록보다 우선
    map_hit = _route_map_display(q)
    if map_hit is not None:
        return map_hit

    # 구조·특수지(+위반) — 카탈로그 위반 단독 매칭보다 우선
    struct_early = _route_building_structure(q)
    if struct_early is not None:
        return struct_early

    # 용도별건물공간정보(D198) 전 속성 — D010 면적/산지 오탐보다 우선
    # 특정 건물명+사용승인일 조회는 카탈로그(A13 있음) 오탐보다 이름 조회가 우선
    if not looks_like_building_name_lookup(q):
        d198_hit = _route_d198_attr(q, conn=conn)
        if d198_hit is not None:
            return d198_hit

        catalog_hit = _route_catalog_attr(q)
        if catalog_hit is not None:
            return catalog_hit

    # D198 주요용도명 종류 — 건물명 조회보다 우선
    usage_kinds = _route_usage_kinds(q)
    if usage_kinds is not None:
        return usage_kinds

    # 면적·높이·층수 임계(동/구) — 건물명 ILIKE 오탐보다 우선
    area_hit = _route_building_area_threshold(q)
    if area_hit is not None:
        return area_hit
    height_hit = _route_building_height_threshold(q)
    if height_hit is not None:
        return height_hit
    floor_hit = _route_building_floor_threshold(q)
    if floor_hit is not None:
        return floor_hit

    # 순위·최댓값 — 「가장 큰 아파트」가 건물명 조회로 빠지지 않게
    ranked_early = _route_building_rank(q)
    if ranked_early is not None:
        return ranked_early

    # 지명 버퍼(동 경계 + N m) — 「주변」이 건물명 ILIKE로 빠지지 않게
    place_buf = _route_place_buffer(q)
    if place_buf is not None:
        return place_buf

    # 건축 경과년수 — 건물명 ILIKE / 장소 count 오탐보다 우선
    aged = _route_building_age(q, conn=conn)
    if aged is not None:
        return aged

    # 특정 건물명(고유명사) 조회 — clarify/LLM보다 우선
    name_hit = _route_building_name_lookup(q)
    if name_hit is not None:
        return name_hit

    # 좌표 버퍼 (LLM이 D198로 빠지는 경우 방지)
    m = re.search(
        r"(?:좌표|점)?\s*\(?\s*(12\d\.\d+)\s*[, ]\s*(35\.\d+)\s*\)?.*?"
        r"(\d+(?:\.\d+)?)\s*(킬로미터|㎞|km|미터|m)(?![a-zA-Z²2])",
        q,
    )
    if m and any(k in q for k in ("이내", "근처", "버퍼", "주변", "반경")):
        lon, lat = m.group(1), m.group(2)
        converted = convert_for_schema(m.group(3), m.group(4), "m")
        if converted is not None:
            meters = converted.sql
            return RoutedQuery(
                "buffer_count",
                (
                    'SELECT COUNT(*) AS cnt\n'
                    f'FROM "{_d010()}" b\n'
                    "WHERE ST_DWithin(\n"
                    "  b.geometry::geography,\n"
                    f"  ST_SetSRID(ST_MakePoint({lon}, {lat}), 4326)::geography,\n"
                    f"  {meters}\n"
                    ");"
                ),
            )

    # 구 기초구역 ∩ 산업단지
    m = re.search(rf"{_GU}\s*기초구역.{{0,20}}교차.{{0,20}}산업단지", q)
    if not m:
        m = re.search(rf"산업단지.{{0,24}}{_GU}\s*기초구역", q)
    if m and ("산업단지" in q and "기초구역" in q and "교차" in q):
        gu = m.group(1)
        return RoutedQuery(
            "industrial_bas_intersect",
            (
                'SELECT COUNT(DISTINCT i."A0") AS cnt\n'
                'FROM "AL_D060_00_20250804" i\n'
                f'JOIN "{_bas()}" t\n'
                "  ON ST_Intersects(i.geometry, t.geometry)\n"
                f'WHERE t."SIG_KOR_NM" = \'{gu}\';'
            ),
        )

    # 기초구역 개수
    m = re.search(rf"{_GU}\s*기초구역", q)
    if (
        m
        and _wants_count(q)
        and "산업단지" not in q
        and "교차" not in q
        and "면적" not in q
    ):
        gu = m.group(1)
        return RoutedQuery(
            "bas_count",
            (
                'SELECT COUNT(*) AS cnt\n'
                f'FROM "{_bas()}"\n'
                f'WHERE "SIG_KOR_NM" = \'{gu}\';'
            ),
        )

    # 건축 경과년수 — 위에서 이미 처리 (건물명보다 앞)
    # 공공시설 등 목록
    listed = _route_facility_list(q, conn=conn)
    if listed is not None:
        return listed

    # 구 주요용도명/용도 종류 → D198 (구별 테이블)
    # place/usage COUNT보다 먼저 매칭해야 "건물의 주요용도명 종류"가 건물건수로 오탐되지 않음
    usage_kinds = _route_usage_kinds(q)
    if usage_kinds is not None:
        return usage_kinds

    # 장소(동 우선) + 용도 COUNT / 행정동 공간 COUNT
    usage_count = _route_place_usage_count(q, conn=conn)
    if usage_count is not None:
        return usage_count

    # 장소(구/동) + 건물 건수 (용도 미지정)
    place_count = _route_place_building_count(q)
    if place_count is not None:
        return place_count

    # 동 공간 포함 (안에/내부/안쪽/경계 안)
    m = re.search(
        rf"{_DONG}\s*(?:안(?:에|쪽)?|내부|경계\s*안).{{0,12}}건물",
        q,
    )
    if not m:
        m = re.search(rf"건물.{{0,12}}{_DONG}\s*(?:안(?:에|쪽)?|내부|경계\s*안)", q)
        if m:
            dong = m.group(1)
        else:
            dong = None
    else:
        dong = m.group(1)
    if dong and dong_requires_gu(dong) and extract_gu(q) is None:
        dong = None
    if dong and _wants_count(q):
        from txt2sql.spatial_templates import building_in_dong_count_sql

        return RoutedQuery("building_in_dong_spatial", building_in_dong_count_sql(dong))
    if dong and _wants_list(q):
        from txt2sql.spatial_templates import building_in_dong_list_sql

        return RoutedQuery(
            "building_in_dong_spatial_list",
            building_in_dong_list_sql(dong),
        )

    # 산업단지 코드 prefix
    if "산업단지" in q and re.search(r"\b26\b|26으로", q) and _wants_count(q):
        if "교차" not in q and "기초구역" not in q:
            return RoutedQuery(
                "industrial_code_prefix",
                (
                    'SELECT COUNT(*) AS cnt\n'
                    'FROM "AL_D060_00_20250804"\n'
                    'WHERE "A4" LIKE \'26%\';'
                ),
            )

    # 구 연면적 상위 N
    m = re.search(rf"{_GU}.{{0,20}}연면적.{{0,16}}(?:상위\s*)?(\d+)", q)
    if m and any(k in q for k in ("상위", "큰 순", "연면적")):
        gu, n = m.group(1), m.group(2)
        only_area = any(k in q for k in ("면적값", "면적 값", "연면적만", "값만"))
        cols = (
            '"A14" AS v'
            if only_area or (n == "1" and "가장" in q)
            else '"A4", "A9", "A14"'
        )
        if only_area or (n == "1" and "가장" in q and "보여" not in q):
            if "가장" in q and n == "1":
                cols = '"A14" AS v'
                return RoutedQuery(
                    "building_area_top1_value",
                    (
                        f"SELECT {cols}\n"
                        f'FROM "{_d010()}"\n'
                        f'WHERE "A4" LIKE \'%{gu}%\'\n'
                        'ORDER BY "A14" DESC NULLS LAST\n'
                        "LIMIT 1;"
                    ),
                )
        return RoutedQuery(
            "building_area_topn",
            (
                f"SELECT {cols}\n"
                f'FROM "{_d010()}"\n'
                f'WHERE "A4" LIKE \'%{gu}%\'\n'
                'ORDER BY "A14" DESC NULLS LAST\n'
                f"LIMIT {n};"
            ),
        )

    # 구 기초구역 면적 상위 N (면적값이면 BAS_AR만)
    m = re.search(rf"{_GU}\s*기초구역.{{0,24}}(?:상위\s*)?(\d+)", q)
    if m and any(k in q for k in ("면적", "큰 순", "상위")):
        gu, n = m.group(1), m.group(2)
        if any(k in q for k in ("면적값", "면적 값", "면적만")) or (
            n == "1" and "면적" in q
        ):
            return RoutedQuery(
                "bas_area_topn_value",
                (
                    'SELECT "BAS_AR" AS v\n'
                    f'FROM "{_bas()}"\n'
                    f'WHERE "SIG_KOR_NM" = \'{gu}\'\n'
                    'ORDER BY "BAS_AR" DESC NULLS LAST\n'
                    f"LIMIT {n};"
                ),
            )
        return RoutedQuery(
            "bas_area_topn",
            (
                'SELECT "BAS_AR", "BAS_ID", "SIG_KOR_NM"\n'
                f'FROM "{_bas()}"\n'
                f'WHERE "SIG_KOR_NM" = \'{gu}\'\n'
                'ORDER BY "BAS_AR" DESC NULLS LAST\n'
                f"LIMIT {n};"
            ),
        )

    ranked = _route_building_rank(q)
    if ranked is not None:
        return ranked

    return None


def _resolve_d198_table(
    q: str,
    *,
    conn: psycopg.Connection | None,
    gu: str | None,
    place: str | None,
) -> str | None:
    table = d198_table_for_gu(gu)
    if table:
        return table
    gu_from_place = d198_gu_for_dong(place, question=q)
    table = d198_table_for_gu(gu_from_place)
    if table:
        return table
    if conn is None or not place:
        return None
    with conn.cursor(row_factory=dict_row) as cur:
        for tbl in D198_TABLES:
            cur.execute(
                f"""
                SELECT 1 AS ok FROM "{tbl}"
                WHERE "A4" LIKE %s
                LIMIT 1
                """,
                (f"%{place}%",),
            )
            if cur.fetchone():
                return tbl
    return None


_PLACE_BUFFER_HINT = ("주변", "근처", "인근", "버퍼", "반경")
_LENGTH_DIST = LENGTH_DIST_PATTERN


def _has_place_buffer_hint(q: str) -> bool:
    if any(k in q for k in _PLACE_BUFFER_HINT):
        return True
    return bool(re.search(rf"{_LENGTH_DIST}\s*(?:안|이내)", q))


def _wants_place_buffer_list(q: str) -> bool:
    """「있는 건물은?」은 목록. 「몇 채/건수」는 건수."""
    if any(k in q for k in ("몇", "개수", "건수", "채", "세어", "구해")):
        return False
    if any(
        k in q
        for k in (
            "목록",
            "리스트",
            "보여",
            "있는 건물",
            "건물은",
            "건물들",
            "무엇",
            "어떤",
        )
    ):
        return True
    return "있는" in q


def _route_place_buffer(q: str) -> RoutedQuery | None:
    """법정·행정동 경계의 N m 버퍼 안 건물 (ST_DWithin geography)."""
    if not _has_place_buffer_hint(q):
        return None
    if re.search(r"12\d\.\d+", q) or "좌표" in q:
        return None
    if looks_like_measure_threshold(q):
        return None
    if looks_like_age_question(q):
        return None
    if any(k in q for k in ("산업단지", "기초구역")):
        return None
    locational = any(k in q for k in _PLACE_BUFFER_HINT)
    if not locational and any(
        k in q for k in ("높이", "연면적", "건물면적", "대지면적", "층수")
    ):
        return None
    if not any(k in q for k in ("건물", "건축물", "채")):
        return None

    m = re.search(rf"{_DONG}[^\d]{{0,28}}{_LENGTH_DIST}", q)
    if m:
        dong, num, unit = m.group(1), m.group(2), m.group(3)
    else:
        m = re.search(rf"{_LENGTH_DIST}[^\d]{{0,28}}{_DONG}", q)
        if not m:
            return None
        num, unit, dong = m.group(1), m.group(2), m.group(3)
    if dong in _FALSE_DONG:
        return None

    converted = convert_for_schema(num, unit, "m")
    if converted is None:
        return None
    expand_deg = sql_number(max(0.0015, converted.canonical / 111000.0 * 1.5))
    meters = converted.sql
    exterior = any(k in q for k in ("경계 밖", "바깥", "외부"))
    if _wants_place_buffer_list(q):
        intent = "place_buffer_outside_list" if exterior else "place_buffer_list"
        return RoutedQuery(
            intent,
            place_buffer_list_sql(dong, meters, expand_deg, exterior=exterior),
        )
    intent = "place_buffer_outside_count" if exterior else "place_buffer_count"
    return RoutedQuery(
        intent,
        place_buffer_count_sql(dong, meters, expand_deg, exterior=exterior),
    )


def _route_place_building_count(q: str) -> RoutedQuery | None:
    """용도 없이 구/동 건물 건수만 묻는 경우."""
    if not any(k in q for k in ("건물", "건축물", "채")):
        return None
    if extract_usage(q):
        return None
    from txt2sql.domain import extract_detail_usages

    if extract_detail_usages(q):
        return None
    if not _wants_count(q):
        return None
    if looks_like_age_question(q):
        return None
    if "산업단지" in q or "기초구역" in q:
        return None
    if any(k in q for k in ("연면적", "건물면적", "대지면적", "면적", "높이", "지상층", "층수", "넘는")):
        return None
    if re.search(r"지상\s*[가-힣\d]+\s*층", q):
        return None
    if "용도별" in q:
        return None
    if any(k in q for k in ("안에", "내부", "안쪽", "경계 안", "교차", "겹치", "인접")):
        return None
    if _has_place_buffer_hint(q):
        return None
    if any(k in q for k in ("비율", "퍼센트", "몇%", "%씩")):
        return None

    place = extract_place(q)
    gu = extract_gu(q)
    if place and dong_requires_gu(place) and gu is None:
        return None
    if not place and not gu:
        if is_busan_wide(q):
            return RoutedQuery(
                "building_place_count",
                f'SELECT COUNT(*) AS cnt\nFROM "{_d010()}";',
            )
        return None

    kind, sql = scoped_count_sql(place, gu, question=q)
    if kind == "none":
        if is_busan_wide(q):
            return RoutedQuery(
                "building_place_count",
                f'SELECT COUNT(*) AS cnt\nFROM "{_d010()}";',
            )
        return None
    intent = "building_in_dong_spatial" if kind == "admin" else "building_place_count"
    return RoutedQuery(intent, sql)


def _route_place_usage_count(
    q: str,
    *,
    conn: psycopg.Connection | None,
) -> RoutedQuery | None:
    from txt2sql.domain import extract_detail_usages

    # 아파트·오피스텔·다가구주택 등은 D198 세부용도 경로로 넘긴다.
    if extract_detail_usages(q):
        return None
    from txt2sql.dataset_grain import simple_building_usage_count
    from txt2sql.domain import d198_gu_for_dong, d198_table_for_gu, extract_usages

    or_tokens = ("또는", "혹은", "이거나")
    union_tokens = ("합친", "합계", "더한", "합산")
    multi_usages = extract_usages(q) if any(k in q for k in (*or_tokens, *union_tokens)) else []
    if len(multi_usages) >= 2:
        place = extract_place(q)
        gu = extract_gu(q)
        gu_for_d198 = gu or (d198_gu_for_dong(place, question=q) if place else None)
        d198_table = d198_table_for_gu(gu_for_d198)
        # Coverage + main usage → D198 A25 IN (...); else D010 A9 IN (...).
        if d198_table and not simple_building_usage_count(q):
            where_parts: list[str] = []
            if place:
                where_parts.extend(_a4_place_filters(place, gu_for_d198))
            in_vals = ", ".join(f"'{value}'" for value in multi_usages)
            where_parts.append(f"\"A25\" IN ({in_vals})")
            where_sql = " AND ".join(where_parts) if where_parts else "TRUE"
            return RoutedQuery(
                "building_usage_count",
                f'SELECT COUNT(*) AS cnt\nFROM "{d198_table}"\nWHERE {where_sql};',
            )
        # Preserve all usages on D010 (do not drop to first usage only).
        if place or gu:
            in_vals = ", ".join(f"'{value}'" for value in multi_usages)
            extra = [f"\"A9\" IN ({in_vals})"]
            kind, sql = scoped_count_sql(place, gu, extra)
            if kind != "none":
                intent = (
                    "building_admin_dong_usage_count"
                    if kind == "admin"
                    else "building_usage_count"
                )
                return RoutedQuery(intent, sql)

    usage = extract_usage(q)
    if not usage or "산업단지" in q:
        return None
    if not _wants_count(q):
        return None
    # 평균·합계 질의는 COUNT 라우트가 가로채지 않게
    if any(k in q for k in ("평균", "합계", "총합", "최대", "최소", "중앙값")):
        return None
    if any(k in q for k in ("비율", "퍼센트", "몇%", "%씩")):
        return None
    if ("연면적" in q or "건물면적" in q or "대지면적" in q or "면적" in q) and any(
        k in q for k in ("이상", "이하", "초과", "미만", "넘는")
    ):
        return None
    if looks_like_age_question(q):
        return None

    place = extract_place(q)
    gu = extract_gu(q)
    if not place and not gu:
        return None

    gu_for_d198 = gu or (d198_gu_for_dong(place, question=q) if place else None)
    d198_table = d198_table_for_gu(gu_for_d198)
    if d198_table and not simple_building_usage_count(q):
        where_parts: list[str] = []
        if place:
            where_parts.extend(_a4_place_filters(place, gu))
        if usage == "공공용시설":
            where_parts.append("(\"A25\" = '공공용시설' OR \"A25\" ILIKE '%공공%')")
        else:
            where_parts.append(f"\"A25\" = '{usage}'")
        where_sql = " AND ".join(where_parts) if where_parts else "TRUE"
        return RoutedQuery(
            "building_usage_count",
            f'SELECT COUNT(*) AS cnt\nFROM "{d198_table}"\nWHERE {where_sql};',
        )

    if usage == "공공용시설":
        extra = ["(\"A9\" = '공공용시설' OR \"A9\" ILIKE '%공공%')"]
    else:
        extra = [f"\"A9\" = '{usage}'"]
    kind, sql = scoped_count_sql(place, gu, extra)
    if kind == "none":
        return None
    intent = (
        "building_admin_dong_usage_count" if kind == "admin" else "building_usage_count"
    )
    return RoutedQuery(intent, sql)


def _route_building_age(
    q: str,
    *,
    conn: psycopg.Connection | None,
) -> RoutedQuery | None:
    from txt2sql.domain import is_permit_approval_lag_question

    if is_permit_approval_lag_question(q):
        return None
    # 법정동·구별 상위 랭킹은 age count가 가로채지 않음
    if any(k in q for k in ("법정동", "구별", "구·군")) and any(
        k in q for k in ("많은", "상위", "순위", "보여줘")
    ):
        return None
    if not looks_like_age_question(q):
        return None
    years = extract_age_years(q)
    if years is None:
        return None

    gu = extract_gu(q)
    place = extract_place(q)
    usage = extract_usage(q)
    compare = extract_age_compare(q)
    date_col = "A33" if "허가" in q and "사용승인" not in q else "A34"

    table = _resolve_d198_table(q, conn=conn, gu=gu, place=place)
    tables: list[str]
    if table:
        tables = [table]
    elif is_busan_wide(q) or (gu is None and place is None):
        # 부산 전체·장소 미지정 → 등록된 D198 테이블 합산
        tables = list(D198_TABLES)
    else:
        return None

    where: list[str] = []
    if place and place.endswith("동"):
        # Use boundary-safe A4 predicate (avoid '%서동%' matching 구서동).
        where.extend(_a4_place_filters(place, gu))
    elif gu:
        where.extend(_a4_place_filters(None, gu))

    if usage and usage != "공공용시설":
        where.append(f'"A25" = \'{usage}\'')
    elif usage == "공공용시설":
        where.append("(\"A29\" = '공공용' OR \"A25\" ILIKE '%공공%')")

    where.append(age_date_predicate(date_col, years, compare))
    where_sql = " AND ".join(where) if where else age_date_predicate(
        date_col, years, compare
    )

    if len(tables) == 1:
        sql = (
            "SELECT COUNT(*) AS cnt\n"
            f'FROM "{tables[0]}"\n'
            f"WHERE {where_sql};"
        )
        intent = "building_age_count"
    else:
        parts = [
            f'SELECT COUNT(*) AS c FROM "{t}" WHERE {where_sql}' for t in tables
        ]
        sql = (
            "SELECT COALESCE(SUM(c), 0) AS cnt\n"
            "FROM (\n  "
            + "\n  UNION ALL\n  ".join(parts)
            + "\n) AS age_parts;"
        )
        intent = "building_age_count_d198_partial"

    return RoutedQuery(intent, sql)


def _route_facility_list(
    q: str,
    *,
    conn: psycopg.Connection | None,
) -> RoutedQuery | None:
    if not _wants_list(q):
        return None
    usage = extract_usage(q)
    public = usage == "공공용시설" or any(
        k in q for k in ("공공시설", "공공시설물", "공공용")
    )
    if not public and usage is None:
        return None
    place = extract_place(q)
    gu = extract_gu(q)
    if not place and not gu:
        return None

    table = _resolve_d198_table(q, conn=conn, gu=gu, place=place)
    if table and public:
        where: list[str] = []
        if place and place.endswith("동"):
            dong = _legal_dong_for_filter(conn, place)
            where.append(f'"A4" LIKE \'%{dong}%\'')
        elif gu:
            where.append(f'"A4" LIKE \'%{gu}%\'')
        where.append("(\"A29\" = '공공용' OR \"A25\" ILIKE '%공공%' OR \"A28\" = '5')")
        where_sql = " AND ".join(where)
        return RoutedQuery(
            "public_facility_list",
            (
                'SELECT "A25" AS usage, "A13" AS name, "A7" AS jibeon, COUNT(*) AS n\n'
                f'FROM "{table}"\n'
                f"WHERE {where_sql}\n"
                'GROUP BY "A25", "A13", "A7"\n'
                "ORDER BY n DESC NULLS LAST\n"
                "LIMIT 30;"
            ),
        )

    where2: list[str] = []
    if place and place.endswith("동"):
        where2.append(place_a4_predicate(place))
        if gu:
            where2.append(f'"A4" LIKE \'%{gu}%\'')
    elif gu:
        where2.append(f'"A4" LIKE \'%{gu}%\'')
    if public:
        where2.append("(\"A9\" = '공공용시설' OR \"A9\" ILIKE '%공공%')")
    elif usage:
        where2.append(f'"A9" = \'{usage}\'')
    where_sql2 = " AND ".join(where2)
    return RoutedQuery(
        "facility_usage_list",
        (
            'SELECT "A9" AS usage, "A24" AS name, "A5" AS jibeon, COUNT(*) AS n\n'
            f'FROM "{_d010()}"\n'
            f"WHERE {where_sql2}\n"
            'GROUP BY "A9", "A24", "A5"\n'
            "ORDER BY n DESC NULLS LAST\n"
            "LIMIT 30;"
        ),
    )


_RANK_SUPERLATIVE = (
    "가장 큰",
    "제일 큰",
    "가장큰",
    "제일큰",
    "가장 넓은",
    "제일 넓은",
    "가장넓은",
    "제일넓은",
    "가장 높",
    "제일 높",
    "가장높은",
    "제일높은",
    "최대",
    "1등",
    "최고",
)


def _explicit_count_intent(q: str) -> bool:
    """이름/자료 질의와 구분되는 명시적 건수 의도."""
    if any(
        k in q
        for k in (
            "자료",
            "데이터",
            "데이터셋",
            "테이블",
            "컬럼",
            "속성",
            "이름",
            "명칭",
            "뭐야",
            "무엇",
            "설명",
        )
    ):
        # 「산업단지 수는?」은 허용, 「자료의 이름은?」은 제외
        if not any(k in q for k in ("몇", "개수", "건수", "수는", "수가", "개야", "몇 개", "몇개")):
            return False
    if any(
        k in q
        for k in ("몇", "개수", "건수", "채수", "개야", "몇 개", "몇개", "수는", "수가", "얼마")
    ):
        return True
    # 「산업단지 수?」「산업단지수는」
    if re.search(r"수\s*\??\s*$", q) or re.search(r"단지\s*수", q):
        return True
    return False


def _industrial_scope_sql(q: str) -> str:
    """산업단지 조회 범위 SQL (시군구코드/부산 전역)."""
    gu = extract_gu(q)
    code = busan_gu_code(gu)
    if code:
        return f"\"A4\" = '{code}'"
    if (
        is_busan_wide(q)
        or "부산" in q
        or re.search(r"\b26\b|26으로", q)
        or gu is None
    ):
        return "\"A4\" LIKE '26%'"
    return f"(\"A8\" ILIKE '%{gu}%' OR \"A9\" ILIKE '%{gu}%')"


def _industrial_distinct_names_sql(scope: str) -> str:
    return (
        "SELECT DISTINCT name FROM (\n"
        "  SELECT COALESCE(NULLIF(TRIM(\"A8\"), ''), NULLIF(TRIM(\"A9\"), '')) AS name\n"
        '  FROM "AL_D060_00_20250804"\n'
        f"  WHERE {scope}\n"
        ") t\n"
        "WHERE name IS NOT NULL AND BTRIM(name) <> ''"
    )


def _industrial_names_list_sql(scope: str) -> str:
    return (
        "SELECT DISTINCT name FROM (\n"
        '  SELECT TRIM("A8") AS name FROM "AL_D060_00_20250804"\n'
        f"  WHERE {scope} AND \"A8\" IS NOT NULL AND BTRIM(\"A8\") <> ''\n"
        "  UNION\n"
        '  SELECT TRIM("A9") AS name FROM "AL_D060_00_20250804"\n'
        f"  WHERE {scope} AND \"A9\" IS NOT NULL AND BTRIM(\"A9\") <> ''\n"
        ") t\n"
        "WHERE name IS NOT NULL AND BTRIM(name) <> ''\n"
        "  AND name <> '일반산업단지'\n"
        "ORDER BY name;"
    )


def _route_industrial_area_rank(q: str) -> RoutedQuery | None:
    """산업단지 면적 상위 N."""
    if "산업단지" not in q:
        return None
    if _catalog_owns_industrial(q):
        return None
    if not any(k in q for k in ("면적", "넓은", "큰")):
        return None
    if not any(k in q for k in ("상위", "큰 순", "큰순", "많은 순", "순위", "가장", "제일")) and not re.search(
        r"\d+\s*개", q
    ):
        return None
    if any(k in q for k in ("건물", "건축물")) and any(
        k in q for k in ("내", "안", "속한", "포함")
    ):
        return None
    n = _extract_top_n(q, default=10)
    limit_n = max(1, min(n, 50))
    scope = _industrial_scope_sql(q)
    return RoutedQuery(
        "industrial_area_rank",
        (
            'SELECT COALESCE(NULLIF(TRIM("A8"), \'\'), NULLIF(TRIM("A9"), \'\')) AS name,\n'
            '       "A4",\n'
            '       ST_Area(geography(ST_Transform(geometry, 4326))) AS area_m2\n'
            f'FROM "AL_D060_00_20250804"\n'
            f"WHERE {scope}\n"
            "  AND geometry IS NOT NULL\n"
            "ORDER BY area_m2 DESC NULLS LAST\n"
            f"LIMIT {limit_n};"
        ),
    )


def _route_buildings_in_industrial(q: str) -> RoutedQuery | None:
    """구·동 건물 중 산업단지와 교차(단지 내)하는 건물 수."""
    if "산업단지" not in q:
        return None
    if "건물" not in q and "건축물" not in q:
        return None
    if not any(k in q for k in ("내", "안", "속한", "포함", "교차", "위치한", "있는")):
        return None
    if not _explicit_count_intent(q) and not _wants_count(q):
        return None
    # 자료명 질의 제외
    if any(k in q for k in ("자료", "데이터셋", "테이블", "이름", "명칭")):
        return None

    names = extract_industrial_names(q)
    gu = extract_gu(q)
    place = extract_place(q)
    intersect = (
        "EXISTS ("
        'SELECT 1 FROM "AL_D060_00_20250804" i '
        "WHERE ST_Intersects(b.geometry, i.geometry)"
    )
    if names:
        clauses = []
        for name in names:
            safe = name.replace("'", "''")
            clauses.append(
                f'(i."A8" ILIKE \'%{safe}%\' OR i."A9" ILIKE \'%{safe}%\')'
            )
        intersect += " AND (" + " OR ".join(clauses) + ")"
    intersect += ")"
    where_b: list[str] = [intersect]
    if place and place.endswith("동"):
        where_b.append(
            f'(b."A4" LIKE \'% {place}\' OR b."A4" = \'{place}\')'
        )
        if gu:
            where_b.append(f'b."A4" LIKE \'%{gu}%\'')
    elif gu:
        where_b.append(f'b."A4" LIKE \'%{gu}%\'')

    height = _parse_height_threshold(q) if "높이" in q else None
    if height is not None:
        op, meters = height
        where_b.append(f'NULLIF(TRIM(b."A16"::text), \'\')::float8 {op} {meters}')

    where_sql = " AND ".join(where_b)
    return RoutedQuery(
        "buildings_in_industrial",
        (
            'SELECT COUNT(*) AS cnt\n'
            f'FROM "{_d010()}" b\n'
            f"WHERE {where_sql};"
        ),
    )


def _route_building_industrial_bas_overlap(q: str) -> RoutedQuery | None:
    """건물이 산업단지·기초구역과 동시에 겹치는 건수."""
    if "산업단지" not in q or "기초구역" not in q:
        return None
    if not any(k in q for k in ("동시", "겹치", "겸", "모두")):
        return None
    if not _wants_count(q):
        return None
    return RoutedQuery(
        "building_industrial_bas_overlap",
        (
            'SELECT COUNT(DISTINCT b."A1") AS cnt\n'
            f'FROM "{_d010()}" b\n'
            "WHERE EXISTS (\n"
            '  SELECT 1 FROM "AL_D060_00_20250804" i\n'
            "  WHERE i.\"A4\" LIKE '26%' AND ST_Intersects(b.geometry, i.geometry)\n"
            ")\n"
            "  AND EXISTS (\n"
            f'  SELECT 1 FROM "{_bas()}" t\n'
            "  WHERE ST_Intersects(b.geometry, t.geometry)\n"
            ");"
        ),
    )


def _route_d010_d198_pnu_match(q: str) -> RoutedQuery | None:
    """D010↔D198 PNU(A2) 매칭 건수."""
    if not _wants_count(q):
        return None
    if not re.search(r"D010.*D198|D198.*D010", q, re.I):
        return None
    if "PNU" not in q.upper() and "연결" not in q:
        return None
    gu = extract_gu(q)
    from txt2sql.domain import d198_table_for_gu

    d198 = d198_table_for_gu(gu)
    code = busan_gu_code(gu)
    if not d198 or not code:
        return None
    return RoutedQuery(
        "d010_d198_pnu_match",
        (
            f'SELECT COUNT(*) AS cnt\nFROM "{_d010()}" d\n'
            f'JOIN "{d198}" u ON d."A2" = u."A2"\n'
            f'WHERE d."A3" LIKE \'{code}%\';'
        ),
    )


def _route_industrial_count(q: str) -> RoutedQuery | None:
    """산업단지 개수(단지명 유니크). 도형 COUNT(*)가 아님."""
    if "산업단지" not in q:
        return None
    if _catalog_owns_industrial(q):
        return None
    if any(k in q for k in ("교차", "기초구역")):
        return None
    # 구·군별 집계는 전용 라우트
    if any(k in q for k in ("구·군별", "구별", "군별")):
        return None
    # 건물∩산업단지는 별도 라우트
    if any(k in q for k in ("건물", "건축물", "공장", "창고")) and any(
        k in q for k in ("내", "안", "속한", "포함", "교차")
    ):
        return None
    # 이름/목록 질의는 건수가 아님
    if any(k in q for k in ("이름", "명칭", "목록", "리스트", "어떤")) and not any(
        k in q for k in ("몇", "개수", "건수", "수는", "수가", "개야")
    ):
        return None
    if not _explicit_count_intent(q):
        return None

    scope = _industrial_scope_sql(q)
    return RoutedQuery(
        "industrial_count",
        f'SELECT COUNT(*) AS cnt\nFROM "AL_D060_00_20250804"\nWHERE {scope};',
    )


def _route_industrial_count_by_sigungu(q: str) -> RoutedQuery | None:
    """구·군별 산업단지 수 (기초구역 SIG와 교차)."""
    if "산업단지" not in q:
        return None
    if not any(k in q for k in ("구·군별", "구별", "군별")):
        return None
    if any(k in q for k in ("건물", "건축물", "행정동", "평균", "연면적")):
        return None
    if not any(k in q for k in ("몇", "개수", "수", "집계", "알려")):
        return None
    sql = (
        'SELECT t."SIG_KOR_NM" AS gu, COUNT(DISTINCT i."A0")::bigint AS n\n'
        f'FROM "{_bas()}" t\n'
        'JOIN "AL_D060_00_20250804" i ON ST_Intersects(t.geometry, i.geometry)\n'
        "WHERE i.\"A4\" LIKE '26%'\n"
        "GROUP BY 1 ORDER BY n DESC NULLS LAST"
    )
    return RoutedQuery("industrial_count_by_sigungu", sql)


def _catalog_owns_industrial(q: str) -> bool:
    """산업단지 전용 필드가 있으면 카탈로그 속성 라우트에 맡긴다."""
    from txt2sql.catalog_attrs import match_catalog

    parsed = match_catalog(q)
    return bool(
        parsed is not None
        and parsed.dataset.key == "d060"
        and (parsed.filters or parsed.rank)
    )


def _route_industrial_names(q: str) -> RoutedQuery | None:
    """산업단지 명칭 목록."""
    if "산업단지" not in q:
        return None
    if _catalog_owns_industrial(q):
        return None
    if any(k in q for k in ("교차", "기초구역")):
        return None
    if ("건물" in q or "건축물" in q) and any(
        k in q for k in ("내", "안", "속한", "포함")
    ):
        return None
    if not any(k in q for k in ("이름", "명칭", "목록", "리스트", "어떤", "무엇")):
        return None
    # 순수 건수 질의 제외
    if _explicit_count_intent(q) and not any(
        k in q for k in ("이름", "명칭", "목록", "리스트")
    ):
        return None

    scope = _industrial_scope_sql(q)
    return RoutedQuery("industrial_names", _industrial_names_list_sql(scope))


def _route_building_name_lookup(q: str) -> RoutedQuery | None:
    """건물명(A24/A13) 부분일치로 특정 단지·건물 정보 조회."""
    if not looks_like_building_name_lookup(q):
        return None
    name = extract_building_name_candidate(q)
    if not name:
        return None

    name_tokens = [
        t
        for t in name.split()
        if t not in _NAME_STOP and t not in {"큰", "높은", "넓은", "많은"}
    ]
    if not name_tokens:
        return None

    place = extract_place(q)
    gu = extract_gu(q)
    if place and re.fullmatch(r"[가-힣]+\d+동", place):
        guessed = legal_dong_guess(place)
        if guessed:
            place = guessed
    where_d010 = _a4_place_filters(place, gu)
    where_d198 = list(where_d010)
    for token in name_tokens:
        from txt2sql.domain import expand_building_name_aliases

        variants = expand_building_name_aliases(token)
        like_d010 = " OR ".join(
            f"\"A24\" ILIKE '%{v.replace(chr(39), chr(39)+chr(39))}%'" for v in variants
        )
        like_d198 = " OR ".join(
            f"\"A13\" ILIKE '%{v.replace(chr(39), chr(39)+chr(39))}%'" for v in variants
        )
        where_d010.append(f"({like_d010})")
        where_d198.append(f"({like_d198})")

    d010_sql = " AND ".join(where_d010) if where_d010 else "TRUE"
    d198_sql = " AND ".join(where_d198) if where_d198 else "TRUE"
    d010_select = (
        'SELECT "A0"::text AS "A0", "A4"::text AS "A4", "A5"::text AS "A5", '
        '"A9"::text AS "A9", "A11"::text AS "A11", "A12"::float8 AS "A12", '
        '"A13"::text AS "A13", '
        '"A14"::float8 AS "A14", "A16"::float8 AS "A16", "A19"::text AS "A19", '
        '"A24"::text AS "A24", "A25"::text AS "A25", "A26"::float8 AS "A26"\n'
        f'FROM "{_d010()}"\n'
        f"WHERE {d010_sql}"
    )
    d198_selects = []
    for tbl in D198_TABLES:
        d198_selects.append(
            'SELECT "A0"::text AS "A0", "A4"::text AS "A4", '
            '"A7"::text AS "A5", "A25"::text AS "A9", "A23"::text AS "A11", '
            '"A18"::float8 AS "A12", "A34"::text AS "A13", '
            '"A19"::float8 AS "A14", '
            '"A30"::float8 AS "A16", "A0"::text AS "A19", '
            '"A13"::text AS "A24", "A25"::text AS "A25", "A31"::float8 AS "A26"\n'
            f'FROM "{tbl}"\n'
            f"WHERE {d198_sql}"
        )
    inner = "\nUNION ALL\n".join([d010_select, *d198_selects])
    return RoutedQuery(
        "building_name_lookup",
        (
            "SELECT * FROM (\n"
            "  SELECT DISTINCT ON (\"A4\", \"A5\", \"A24\") *\n"
            "  FROM (\n"
            f"{inner}\n"
            "  ) AS named_hits\n"
            '  ORDER BY "A4", "A5", "A24", "A14" DESC NULLS LAST\n'
            ") AS named_dedup\n"
            'ORDER BY "A24" NULLS LAST, "A14" DESC NULLS LAST\n'
            "LIMIT 20;"
        ),
    )


def _route_building_rank(q: str) -> RoutedQuery | None:
    if "기초구역" in q:
        return None
    # 구별 top-2 차이 등은 순위 라우트로 가로채지 않음
    if any(k in q for k in ("각 구", "구·군", "두 번째", "두번째", "차이")):
        return None
    # 세부용도·용도분류는 D198 grain — D010 A9 rank로 가로채지 않음
    from txt2sql.domain import extract_detail_usages, extract_usage_classes

    if extract_detail_usages(q) or extract_usage_classes(q):
        return None
    metric_col = None
    metric_name = None
    has_super = any(k in q for k in _RANK_SUPERLATIVE)
    top_n = _extract_top_n(q, default=1)
    sort_asc = any(k in q for k in ("작은", "낮은", "낮은 순", "작은 순")) and not any(
        k in q for k in ("높은", "큰 ", "큰순", "높은 순")
    )
    if any(k in q for k in ("용적률", "용적율")) and (
        has_super or top_n > 1 or any(k in q for k in ("높은", "낮은"))
    ):
        metric_col, metric_name = "A18", "용적률"
    elif "건폐율" in q and (
        has_super or top_n > 1 or any(k in q for k in ("높은", "낮은"))
    ):
        metric_col, metric_name = "A17", "건폐율"
    elif any(k in q for k in ("건물면적", "건축물면적", "건축면적")) and (
        has_super or top_n > 1 or sort_asc
    ):
        metric_col, metric_name = "A12", "건물면적"
    elif "연면적" in q and (has_super or top_n > 1):
        metric_col, metric_name = "A14", "연면적"
    elif "대지면적" in q and (has_super or top_n > 1):
        metric_col, metric_name = "A15", "대지면적"
    elif any(k in q for k in ("가장 높", "제일 높", "가장높은", "제일높은")) or (
        "높" in q
        and any(k in q for k in ("가장", "제일", "최대", "1등", "최고", "상위"))
    ) or ("높은" in q and top_n > 1):
        metric_col, metric_name = "A16", "높이"
    elif ("지상층" in q or "층수" in q or "지상 층" in q) and any(
        k in q for k in ("가장 많", "제일 많", "가장 높", "최대", "1등", "제일 높", "상위")
    ):
        metric_col, metric_name = "A26", "지상층"
    elif any(
        k in q
        for k in (
            "가장 큰",
            "제일 큰",
            "가장큰",
            "제일큰",
            "가장 넓은",
            "제일 넓은",
            "가장넓은",
            "제일넓은",
        )
    ) or (
        top_n > 1
        and any(k in q for k in ("큰", "넓은"))
        and any(k in q for k in ("건물", "아파트", "주택", "건축물", "것물"))
    ):
        # 지표 미지정 시 연면적(규모)으로 해석
        metric_col, metric_name = "A14", "연면적"
    else:
        return None

    place = extract_place(q)
    gu = extract_gu(q)
    where = _a4_place_filters(place, gu)
    # 장소 없음·부산시 전체 → AL_D010 전역 (부산 DB)

    usage_sql = extract_usage(q)
    if usage_sql:
        where.append(f'"A9" = \'{usage_sql}\'')

    if metric_col == "A16":
        where.append(sane_height_sql("A16", "A26"))
    elif metric_col == "A12":
        where.append(sane_footprint_sql("A12", "A14"))
        if "양수" in q or sort_asc:
            where.append('"A12" > 0')
    elif metric_col == "A14":
        where.append(sane_floor_area_sql("A14"))
    elif metric_col == "A15":
        where.append('"A15" > 0 AND "A15" <= 2000000')
    elif metric_col == "A18":
        if "정상" in q:
            where.append('"A18" > 0 AND "A18" <= 1')
        else:
            # D010 용적률은 0~1000 범위(골드 sane bound)
            where.append('"A18" > 0 AND "A18" <= 1000')
    elif metric_col == "A17":
        # 정상 건폐율(비율 0~1). 「정상값」명시 시 상한 1.
        if "정상" in q:
            where.append('"A17" > 0 AND "A17" <= 1')
        else:
            where.append('"A17" > 0 AND "A17" <= 100')

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    limit_n = max(1, min(top_n, 20))
    direction = "ASC" if sort_asc else "DESC"
    order_expr = f'("{metric_col}"::float8)' if metric_col in {
        "A12", "A14", "A15", "A16", "A17", "A18", "A26"
    } else f'"{metric_col}"'

    return RoutedQuery(
        f"building_rank_{metric_name}",
        (
            'SELECT "A0", "A4", "A5", "A9", "A12", "A14", "A15", "A16", "A17", "A18", "A19", "A24", "A25", "A26"\n'
            f'FROM "{_d010()}"'
            f"{where_sql}\n"
            f"ORDER BY {order_expr} {direction} NULLS LAST\n"
            f"LIMIT {limit_n};"
        ),
    )


def _route_basic_zone_area_rank(q: str) -> RoutedQuery | None:
    """기초구역 면적 최대/상위. 건물 연면적 순위와 구분한다."""
    if "기초구역" not in q:
        return None
    if any(k in q for k in ("건물", "건축물", "아파트", "주택")):
        return None
    if "교차" in q and any(k in q for k in ("개수", "몇", "채수")):
        return None
    if any(k in q for k in ("이상", "이하", "초과", "미만")) and any(
        k in q for k in ("개수", "몇", "개야", "채수")
    ):
        return None
    if not any(k in q for k in ("면적", "큰", "최대", "상위", "제일")):
        return None
    gu = extract_gu(q)
    n = _extract_top_n(q, default=1)
    limit_n = max(1, min(n, 20))
    where = f'WHERE "SIG_KOR_NM" = \'{gu}\'\n' if gu else ""
    value_only = any(k in q for k in ("면적값", "면적 값", "면적만", "얼마", "몇 제곱"))
    wants_identity = any(
        k in q for k in ("것은", "것은?", "것?", "아이디", "ID", "번호", "어디")
    )
    if value_only and not wants_identity:
        return RoutedQuery(
            "bas_area_topn_value",
            (
                'SELECT "BAS_AR" AS v\n'
                f'FROM "{_bas()}"\n'
                f"{where}"
                'ORDER BY "BAS_AR" DESC NULLS LAST\n'
                f"LIMIT {limit_n};"
            ),
        )
    return RoutedQuery(
        "bas_area_topn",
        (
            'SELECT "BAS_AR", "BAS_ID", "SIG_KOR_NM"\n'
            f'FROM "{_bas()}"\n'
            f"{where}"
            'ORDER BY "BAS_AR" DESC NULLS LAST\n'
            f"LIMIT {limit_n};"
        ),
    )


def _route_building_name_set_compare(q: str) -> RoutedQuery | None:
    """대학·POI 두 집합을 건물명 ILIKE COUNT로 비교한다. 좌표 추측 금지."""
    if "비교" not in q:
        return None
    if not any(k in q for k in ("건물", "건축물", "채", "수")):
        return None
    match = re.search(
        r"([가-힣A-Za-z0-9]+(?:대학교|대학|학교|병원|시장|역))"
        r"\s*(?:와|과)\s*"
        r"([가-힣A-Za-z0-9]+(?:대학교|대학|학교|병원|시장|역))",
        q,
    )
    if not match:
        return None
    left, right = match.group(1), match.group(2)
    if left == right:
        return None
    sl = left.replace("'", "''")
    sr = right.replace("'", "''")
    return RoutedQuery(
        "building_name_set_compare",
        (
            "SELECT\n"
            f"  COUNT(*) FILTER (WHERE \"A24\" ILIKE '%{sl}%') AS n1,\n"
            f"  COUNT(*) FILTER (WHERE \"A24\" ILIKE '%{sr}%') AS n2\n"
            f'FROM "{_d010()}";'
        ),
    )


def _extract_top_n(question: str, *, default: int = 1, max_n: int = 20) -> int:
    """『상위 3』『3개』『탑5』 등에서 N 추출. 여러 개면 마지막 값을 쓴다."""
    q = question.strip()
    patterns = (
        r"상위\s*(\d+)\s*개?",
        r"탑\s*(\d+)",
        r"top\s*(\d+)",
        r"(\d+)\s*개",
        r"(\d+)\s*곳",
        r"(\d+)\s*채",
        r"(\d+)\s*건(?!물)",  # '3건물'이 아닌 '3건'
    )
    found: list[tuple[int, int]] = []
    for pat in patterns:
        for m in re.finditer(pat, q, flags=re.IGNORECASE):
            n = int(m.group(1))
            if 1 <= n <= max_n:
                found.append((m.start(), n))
    if not found:
        return default
    found.sort()
    return found[-1][1]


def fix_common_sql_mistakes(sql: str, question: str | None = None) -> str:
    """LLM SQL의 고빈도 실수를 후처리로 교정."""
    out = re.sub(
        r'"A3"\s+LIKE\s+\'%([가-힣0-9]+(?:구|동))%\'',
        r'"A4" LIKE \'%\1%\'',
        sql,
    )
    if "ST_DWithin" in out or "ST_DWITHIN" in out.upper():
        out = _swap_d198_for_d010(out)

    q = (question or "").strip()
    if not q:
        return out

    # D198 주요용도명 종류 → A25 고정
    kinds = _route_usage_kinds(q)
    if kinds is not None:
        return kinds.sql

    # 등록되지 않은 구/용도 COUNT인데 D198을 쓰면 AL_D010으로 교정
    gu = extract_gu(q)
    age_q = looks_like_age_question(q)
    if (
        gu
        and d198_table_for_gu(gu) is None
        and not age_q
        and "AL_D198_" in out
        and "주요용도" not in q
    ):
        out = _swap_d198_for_d010(out)
        # D198 컬럼 → D010 대응 컬럼
        out = re.sub(r'"A25"', '"A9"', out)
        out = re.sub(r'"A19"', '"A14"', out)
        out = re.sub(r'"A30"', '"A16"', out)
        out = re.sub(r'"A31"', '"A26"', out)

    # text 일자 < CURRENT_DATE → 명시적 date 캐스트 (실행 오류 방지)
    out = re.sub(
        r'"(A13|A22|A33|A34)"\s*([<>]=?)\s*(CURRENT_DATE)',
        r'"\1"::date \2 \3',
        out,
        flags=re.I,
    )

    # PostgreSQL: round(double precision, int) 없음 → numeric 캐스트
    def _round_numeric(match: re.Match[str]) -> str:
        expr = match.group(1).strip()
        digits = match.group(2)
        if "::numeric" in expr.lower():
            return match.group(0)
        return f"ROUND(({expr})::numeric, {digits})"

    out = re.sub(
        r"(?i)\bROUND\s*\(\s*([^,]+?)\s*,\s*(\d+)\s*\)",
        _round_numeric,
        out,
    )

    # 두 구 준공연도 중앙값 비교는 규칙 SQL로 고정
    dual_median = _route_dual_gu_approval_year_median(q)
    if dual_median is not None:
        return dual_median.sql

    # D198 사용승인·허가일 질의는 규칙 SQL로 고정 (D010 A13 오인 방지)
    d198_hit = _route_d198_attr(q, conn=None)
    if d198_hit is not None:
        return d198_hit.sql

    forced = _route_building_rank(q)
    if forced is not None:
        # 순위 질의는 규칙 SQL로 고정 (잘못된 ORDER BY/컬럼 별칭 방지)
        return forced.sql
    return out