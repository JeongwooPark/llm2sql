"""Dedicated COUNT routes for gold-aligned edge cases."""

from __future__ import annotations

import re
from dataclasses import dataclass

from txt2sql.domain import (
    busan_gu_code,
    d198_gu_for_dong,
    d198_table_for_gu,
    extract_detail_usages,
    extract_gu,
    extract_place,
    extract_usages,
    is_busan_wide,
)


@dataclass(frozen=True)
class CountRoute:
    intent: str
    sql: str


PRIORITY_COUNT_INTENTS = frozenset(
    {
        "building_area_a12_nonpos_count",
        "d198_usage_site_area_count",
        "d198_detail_height_count",
        "d198_dual_usage_field_count",
        "d198_usage_without_detail_count",
        "d198_usage_class_mismatch_count",
        "d198_permit_without_approval_count",
        "d198_invalid_approval_format_count",
        "d198_invalid_permit_format_count",
        "d198_future_approval_count",
        "d198_future_permit_count",
        "industrial_bas_zone_overlap_count",
        "building_industrial_bas_overlap",
        "d010_not_in_d198_count",
        "d198_not_in_d010_count",
        "d010_d198_pnu_match",
        "adversarial_building_name_count",
        "non_violation_building_count",
    }
)


def _d010_table() -> str:
    from txt2sql.dataset_tables import resolve_building_table

    return resolve_building_table()


def _bas_table() -> str:
    from txt2sql.dataset_tables import resolve_basic_zone_table

    return resolve_basic_zone_table()


def _place_filters(place: str | None, gu: str | None) -> list[str]:
    from txt2sql.intent_router import _a4_place_filters

    return list(_a4_place_filters(place, gu))


def _wants_count(q: str) -> bool:
    from txt2sql.intent_router import _wants_count as _wc

    return _wc(q)


def _d198_table_for_question(q: str) -> tuple[str, str | None] | None:
    gu = extract_gu(q)
    place = extract_place(q)
    gu_name = gu or (d198_gu_for_dong(place, question=q) if place else None)
    table = d198_table_for_gu(gu_name)
    if not table:
        return None
    return table, gu_name


def _route_building_area_a12_nonpos(q: str) -> CountRoute | None:
    if "건축물면적" not in q or not _wants_count(q):
        return None
    if not re.search(r"0\s*(?:㎡|m2|m²)?\s*이하", q):
        return None
    return CountRoute(
        "building_area_a12_nonpos_count",
        (
            f'SELECT COUNT(*) AS cnt\nFROM "{_d010_table()}"\n'
            "WHERE NULLIF(TRIM(\"A12\"::text), '')::float8 <= 0;"
        ),
    )


def _route_d198_usage_site_area_count(q: str) -> CountRoute | None:
    if "대지면적" not in q or not _wants_count(q):
        return None
    usage = next(iter(extract_usages(q)), None)
    resolved = _d198_table_for_question(q)
    if not usage or resolved is None:
        return None
    table, gu_name = resolved
    m = re.search(
        r"대지면적\s*(\d+(?:\.\d+)?)\s*(?:㎡|m2|m²)?\s*(이상|이하|초과|미만|넘는)",
        q,
    )
    if not m:
        return None
    rel = {"이상": ">=", "초과": ">", "이하": "<=", "미만": "<", "넘는": ">"}.get(
        m.group(2), ">="
    )
    where = [
        f"\"A25\" = '{usage}'",
        f"NULLIF(TRIM(\"A17\"::text), '')::float8 {rel} {m.group(1)}",
    ]
    if gu_name and not extract_place(q):
        code = busan_gu_code(gu_name)
        if code:
            where.insert(0, f"\"A3\" LIKE '{code}%'")
    return CountRoute(
        "d198_usage_site_area_count",
        f'SELECT COUNT(*) AS cnt\nFROM "{table}"\nWHERE {" AND ".join(where)};',
    )


def _route_d198_detail_height_count(q: str) -> CountRoute | None:
    if not _wants_count(q) or "높이" not in q:
        return None
    details = extract_detail_usages(q)
    if not details:
        return None
    m = re.search(
        r"높이\s*(\d+(?:\.\d+)?)\s*(?:m|미터)?\s*(이상|이하|초과|미만|넘는)",
        q,
    )
    if not m:
        return None
    resolved = _d198_table_for_question(q)
    if resolved is None:
        return None
    table, _ = resolved
    rel = {"이상": ">=", "초과": ">", "이하": "<=", "미만": "<", "넘는": ">"}.get(
        m.group(2), ">="
    )
    place = extract_place(q)
    gu = extract_gu(q)
    where = _place_filters(place, gu)
    where.append(f"\"A27\" = '{details[0]}'")
    where.append(f"NULLIF(TRIM(\"A30\"::text), '')::float8 {rel} {m.group(1)}")
    return CountRoute(
        "d198_detail_height_count",
        f'SELECT COUNT(*) AS cnt\nFROM "{table}"\nWHERE {" AND ".join(where)};',
    )


def _route_d198_dual_usage_field_count(q: str) -> CountRoute | None:
    if not _wants_count(q):
        return None
    if not (
        "주요용도" in q
        and "세부용도" in q
        and any(k in q for k in ("모두", "둘 다", "둘다"))
    ):
        return None
    resolved = _d198_table_for_question(q)
    if resolved is None:
        return None
    table, _ = resolved
    return CountRoute(
        "d198_dual_usage_field_count",
        (
            f'SELECT COUNT(*) AS cnt\nFROM "{table}"\n'
            "WHERE TRIM(COALESCE(\"A25\"::text, '')) <> '' "
            "AND TRIM(COALESCE(\"A27\"::text, '')) <> '';"
        ),
    )


def _route_d198_usage_without_detail_count(q: str) -> CountRoute | None:
    if not _wants_count(q):
        return None
    if not ("주요용도" in q and "세부용도" in q):
        return None
    if not any(k in q for k in ("없", "비어")):
        return None
    if any(k in q for k in ("모두", "둘 다", "둘다")):
        return None
    resolved = _d198_table_for_question(q)
    if resolved is None:
        return None
    table, gu_name = resolved
    where = [
        "TRIM(COALESCE(\"A25\"::text, '')) <> ''",
        "TRIM(COALESCE(\"A27\"::text, '')) = ''",
    ]
    if gu_name:
        code = busan_gu_code(gu_name)
        if code:
            where.insert(0, f"\"A3\" LIKE '{code}%'")
    return CountRoute(
        "d198_usage_without_detail_count",
        f'SELECT COUNT(*) AS cnt\nFROM "{table}"\nWHERE {" AND ".join(where)};',
    )


def _blocks_scalar_priority_count(q: str) -> bool:
    from txt2sql.intent_router import _wants_scalar_count

    return not _wants_scalar_count(q)


def _route_d198_usage_class_mismatch_count(q: str) -> CountRoute | None:
    if not _wants_count(q):
        return None
    if "상업용" not in q or not any(k in q for k in ("주거", "주거계열")):
        return None
    if not any(k in q for k in ("인데", "표시", "계열", "분류")):
        return None
    resolved = _d198_table_for_question(q)
    if resolved is None:
        return None
    table, gu_name = resolved
    where = ["\"A29\" = '상업용'", "\"A25\" IN ('단독주택','공동주택')"]
    if gu_name:
        code = busan_gu_code(gu_name)
        if code:
            where.insert(0, f"\"A3\" LIKE '{code}%'")
    return CountRoute(
        "d198_usage_class_mismatch_count",
        f'SELECT COUNT(*) AS cnt\nFROM "{table}"\nWHERE {" AND ".join(where)};',
    )


def _route_d198_permit_without_approval(q: str) -> CountRoute | None:
    if not _wants_count(q):
        return None
    if not ("허가일" in q and "사용승인" in q and "없" in q):
        return None
    resolved = _d198_table_for_question(q)
    if resolved is None:
        return None
    table, _ = resolved
    where = _place_filters(extract_place(q), extract_gu(q))
    if "사용승인" in q and q.find("사용승인") < q.find("허가"):
        gap = [
            "TRIM(COALESCE(\"A34\"::text, '')) <> ''",
            "TRIM(COALESCE(\"A33\"::text, '')) = ''",
        ]
    else:
        gap = [
            "TRIM(COALESCE(\"A33\"::text, '')) <> ''",
            "TRIM(COALESCE(\"A34\"::text, '')) = ''",
        ]
    where.extend(gap)
    return CountRoute(
        "d198_permit_without_approval_count",
        f'SELECT COUNT(*) AS cnt\nFROM "{table}"\nWHERE {" AND ".join(where)};',
    )


def _route_d198_invalid_approval_format(q: str) -> CountRoute | None:
    if not _wants_count(q) or "사용승인일" not in q:
        return None
    if any(k in q for k in ("미래", "앞선")):
        return None
    if not any(k in q for k in ("형식", "해석", "날짜")):
        return None
    resolved = _d198_table_for_question(q)
    if resolved is None:
        return None
    table, gu_name = resolved
    where = [
        "TRIM(COALESCE(\"A34\"::text, '')) <> ''",
        "NOT (\"A34\" ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$')",
    ]
    if gu_name:
        code = busan_gu_code(gu_name)
        if code:
            where.insert(0, f"\"A3\" LIKE '{code}%'")
    return CountRoute(
        "d198_invalid_approval_format_count",
        f'SELECT COUNT(*) AS cnt\nFROM "{table}"\nWHERE {" AND ".join(where)};',
    )


def _route_d198_invalid_permit_format(q: str) -> CountRoute | None:
    if not _wants_count(q) or "허가일" not in q:
        return None
    if "형식" not in q:
        return None
    resolved = _d198_table_for_question(q)
    if resolved is None:
        return None
    table, gu_name = resolved
    where = [
        "TRIM(COALESCE(\"A33\"::text, '')) <> ''",
        "NOT (\"A33\" ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$')",
    ]
    if gu_name:
        code = busan_gu_code(gu_name)
        if code:
            where.insert(0, f"\"A3\" LIKE '{code}%'")
    return CountRoute(
        "d198_invalid_permit_format_count",
        f'SELECT COUNT(*) AS cnt\nFROM "{table}"\nWHERE {" AND ".join(where)};',
    )


def _route_d198_future_date_count(q: str) -> CountRoute | None:
    if not _wants_count(q):
        return None
    if not any(k in q for k in ("미래", "앞선")):
        return None
    if "허가일" in q:
        col, intent = "A33", "d198_future_permit_count"
    elif "사용승인일" in q or "사용승인" in q:
        col, intent = "A34", "d198_future_approval_count"
    else:
        return None
    from txt2sql.config import load_settings

    ref = load_settings().reference_date
    resolved = _d198_table_for_question(q)
    if resolved is None:
        return None
    table, gu_name = resolved
    where = [
        f"\"{col}\" ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$'",
        f"\"{col}\"::date > '{ref}'::date",
    ]
    if gu_name:
        code = busan_gu_code(gu_name)
        if code:
            where.insert(0, f"\"A3\" LIKE '{code}%'")
    return CountRoute(
        intent,
        f'SELECT COUNT(*) AS cnt\nFROM "{table}"\nWHERE {" AND ".join(where)};',
    )


def _route_industrial_bas_zone_overlap_count(q: str) -> CountRoute | None:
    if "산업단지" not in q or "기초구역" not in q or not _wants_count(q):
        return None
    if "별" in q or any(k in q for k in ("집계", "구분", "각각", "나눠")):
        return None
    if any(k in q for k in ("건물", "건축물")):
        return None
    scope = (
        "\"A4\" LIKE '26%'"
        if is_busan_wide(q) or "부산" in q
        else _industrial_scope_sql_local(q)
    )
    return CountRoute(
        "industrial_bas_zone_overlap_count",
        (
            'SELECT COUNT(DISTINCT t."BAS_ID") AS cnt\n'
            'FROM "AL_D060_00_20250804" i\n'
            f'JOIN "{_bas_table()}" t ON ST_Intersects(i.geometry, t.geometry)\n'
            f"WHERE {scope};"
        ),
    )


def _industrial_scope_sql_local(q: str) -> str:
    gu = extract_gu(q)
    code = busan_gu_code(gu)
    if code:
        return f"\"A4\" = '{code}'"
    return "\"A4\" LIKE '26%'"


def _route_d010_not_in_d198(q: str) -> CountRoute | None:
    if not _wants_count(q):
        return None
    if not re.search(r"D010.*D198|D198.*D010", q, re.I):
        return None
    if not any(k in q for k in ("없는", "없", "포함되지", "매칭되지")):
        return None
    if "매칭률" in q or "비율" in q:
        return None
    gu = extract_gu(q)
    table = d198_table_for_gu(gu)
    code = busan_gu_code(gu)
    if not table or not code:
        return None
    if re.search(r"D010.*있.*D198.*없|D010에는.*D198에 없", q):
        return CountRoute(
            "d010_not_in_d198_count",
            (
                f'SELECT COUNT(*) AS cnt\nFROM "{_d010_table()}" d\n'
                f'WHERE d."A3" LIKE \'{code}%\'\n'
                f'  AND NOT EXISTS (SELECT 1 FROM "{table}" u WHERE u."A2" = d."A2");'
            ),
        )
    return CountRoute(
        "d198_not_in_d010_count",
        (
            f'SELECT COUNT(*) AS cnt\nFROM "{table}" u\n'
            f'WHERE NOT EXISTS (SELECT 1 FROM "{_d010_table()}" d WHERE d."A2" = u."A2");'
        ),
    )


def _route_building_industrial_bas_overlap_count(q: str) -> CountRoute | None:
    from txt2sql.intent_router import _route_building_industrial_bas_overlap

    hit = _route_building_industrial_bas_overlap(q)
    if hit is None:
        return None
    return CountRoute(hit.intent, hit.sql)


def _route_adversarial_building_name(q: str) -> CountRoute | None:
    if not _wants_count(q) and not any(k in q for k in ("있는지", "찾아")):
        return None
    if "OR 1=1" not in q.upper():
        return None
    gu = extract_gu(q)
    code = busan_gu_code(gu)
    if not code:
        return None
    return CountRoute(
        "adversarial_building_name_count",
        (
            f'SELECT COUNT(*) AS cnt\nFROM "{_d010_table()}"\n'
            f'WHERE "A3" LIKE \'{code}%\' '
            "AND \"A24\" LIKE $$%' OR 1=1 --%$$;"
        ),
    )


def _route_non_violation_building_count(q: str) -> CountRoute | None:
    if not _wants_count(q):
        return None
    if "위반" not in q or not any(k in q for k in ("아닌", "아니", "제외", "없는", "아닌")):
        return None
    from txt2sql.domain import extract_structure, extract_usage, structure_a11_predicate

    table = _d010_table()
    where = ['"A20" IS DISTINCT FROM \'Y\'']
    # 구조/용도 술어를 삼키지 않도록 AND로 유지
    st = extract_structure(q)
    if st is not None:
        alias, pattern = st
        where.append(structure_a11_predicate(q, alias, pattern))
    usage = extract_usage(q)
    if usage:
        where.append(f"\"A9\" = '{usage}'")
    gu = extract_gu(q)
    if gu:
        code = busan_gu_code(gu)
        if code:
            where.append(f'"A3" LIKE \'{code}%\'')
    sql = f'SELECT COUNT(*) AS cnt\nFROM "{table}"\nWHERE {" AND ".join(where)};'
    return CountRoute("non_violation_building_count", sql)


def match_priority_count_route(q: str) -> CountRoute | None:
    """Gold-aligned count routes that must win over semantic-v2/plan."""
    if _blocks_scalar_priority_count(q):
        return None
    for fn in (
        _route_building_area_a12_nonpos,
        _route_d198_usage_site_area_count,
        _route_d198_detail_height_count,
        _route_d198_dual_usage_field_count,
        _route_d198_usage_without_detail_count,
        _route_d198_usage_class_mismatch_count,
        _route_d198_permit_without_approval,
        _route_d198_future_date_count,
        _route_d198_invalid_approval_format,
        _route_d198_invalid_permit_format,
        _route_industrial_bas_zone_overlap_count,
        _route_building_industrial_bas_overlap_count,
        _route_d010_not_in_d198,
        _route_adversarial_building_name,
        _route_non_violation_building_count,
    ):
        hit = fn(q.strip())
        if hit is not None:
            return hit
    return None
