"""PostGIS 공간 질의 템플릿."""

from __future__ import annotations

import re

from txt2sql.dataset_tables import resolve_basic_zone_table, resolve_building_table
from txt2sql.domain import extract_place, place_a4_predicate

def _d010() -> str:
    return resolve_building_table()


_BND = "BND_ADM_DONG_PG"


def _bas() -> str:
    return resolve_basic_zone_table()

_LIST_COLS = (
    'b."A0", b."A4", b."A5", b."A9", b."A12", b."A14", b."A16", b."A24", b."A26"'
)
_BAS_COLS = 't."BAS_ID", t."SIG_KOR_NM", t."BAS_AR"'
_BND_COLS = 'd."ADM_CD", d."ADM_NM"'


def extract_place_token(question: str) -> str | None:
    """질문에서 동/구 명칭 후보를 추출."""
    return extract_place(question)


def admin_dong_name_predicate(place: str, alias: str = "d") -> str:
    """법정동 구서동 → 행정동 구서1동/구서2동. 구서1동은 해당 행정동만."""
    safe = place.replace("'", "''")
    col = f'{alias}."ADM_NM"' if alias else '"ADM_NM"'
    if re.fullmatch(r"[가-힣]+\d+동", safe):
        return f"{col} = '{safe}'"
    stem = safe[:-1] if safe.endswith("동") else safe
    return f"({col} = '{safe}' OR {col} ~ '^{stem}[0-9]+동$')"


def admin_dong_where(place: str, alias: str = "d", *, adm_cd_prefix: str | None = None) -> str:
    """행정동명 조건. 동명이 전국에서 유일하면 센서스 ADM_CD 접두어를 붙인다."""
    pred = admin_dong_name_predicate(place, alias)
    extra = _adm_cd_like_sql(alias, place, adm_cd_prefix)
    if extra:
        return f"{pred} AND {extra}"
    return pred


def _adm_cd_like_sql(
    alias: str, place: str | None, prefix: str | None = None
) -> str:
    from txt2sql.gazetteer import unique_adm_cd_prefix

    code = (prefix or unique_adm_cd_prefix(place) or "").strip()
    if re.fullmatch(r"\d{2}", code):
        return f"{alias}.\"ADM_CD\" LIKE '{code}%'"
    return ""


def _prefix_a_cols(frag: str, alias: str) -> str:
    if not alias:
        return frag
    return re.sub(r'(?<!\.)"A(\d+)"', rf'{alias}."A\1"', frag)


def building_scope(
    place: str | None,
    gu: str | None,
    *,
    question: str = "",
) -> tuple[str, str, str, str]:
    """건물 질의 범위. (kind, FROM, WHERE, 컬럼접두어).

    kind: admin=행정경계, a4=법정동 주소, gu=구군, none=장소 없음.
    """
    from txt2sql.gazetteer import is_legal_dong, is_locality, uses_admin_boundary

    if place and uses_admin_boundary(place, question=question):
        frm = (
            f'"{_d010()}" b\n'
            f'JOIN "{_BND}" d\n'
            "  ON ST_Intersects(b.geometry, d.geometry)"
        )
        where = [admin_dong_where(place)]
        if gu:
            where.append(_prefix_a_cols(place_a4_predicate(gu), "b"))
        return "admin", frm, " AND ".join(where), "b."
    if place and is_legal_dong(place):
        pred = place_a4_predicate(place)
        if gu:
            pred = f"({pred}) AND {place_a4_predicate(gu)}"
        return "a4", f'"{_d010()}"', pred, ""
    if gu:
        return "gu", f'"{_d010()}"', place_a4_predicate(gu), ""
    if place and is_locality(place):
        return "a4", f'"{_d010()}"', place_a4_predicate(place), ""
    if place:
        return "a4", f'"{_d010()}"', place_a4_predicate(place), ""
    return "none", f'"{_d010()}"', "TRUE", ""


def scoped_count_sql(
    place: str | None,
    gu: str | None,
    extra: list[str] | None = None,
    *,
    question: str = "",
) -> tuple[str, str]:
    """(kind, COUNT SQL)."""
    kind, frm, where, prefix = building_scope(place, gu, question=question)
    parts = [where] if where and where != "TRUE" else []
    for item in extra or []:
        parts.append(_prefix_a_cols(item, prefix.rstrip(".")))
    where_sql = " AND ".join(p for p in parts if p) or "TRUE"
    sql = f"SELECT COUNT(*) AS cnt\nFROM {frm}\nWHERE {where_sql};"
    return kind, sql


def scoped_list_sql(
    place: str | None,
    gu: str | None,
    extra: list[str] | None = None,
    *,
    order_col: str = "A14",
    limit: int = 100,
) -> tuple[str, str]:
    kind, frm, where, prefix = building_scope(place, gu)
    parts = [where] if where and where != "TRUE" else []
    for item in extra or []:
        parts.append(_prefix_a_cols(item, prefix.rstrip(".")))
    where_sql = " AND ".join(p for p in parts if p) or "TRUE"
    p = prefix
    sql = (
        f"SELECT {p}\"A0\", {p}\"A4\", {p}\"A5\", {p}\"A9\", {p}\"A12\", "
        f"{p}\"A14\", {p}\"A16\", {p}\"A24\", {p}\"A26\", {p}\"A13\",\n"
        "       COUNT(*) OVER() AS total_n\n"
        f"FROM {frm}\n"
        f"WHERE {where_sql}\n"
        f'ORDER BY {p}"{order_col}" DESC NULLS LAST\n'
        f"LIMIT {limit};"
    )
    return kind, sql


def building_in_dong_count_sql(place: str, extra: str = "") -> str:
    extra_sql = f" AND {extra}" if extra else ""
    return (
        'SELECT COUNT(DISTINCT b."A1") AS cnt\n'
        f'FROM "{_d010()}" b\n'
        f'JOIN "{_BND}" d\n'
        "  ON ST_Intersects(b.geometry, d.geometry)\n"
        f"WHERE {admin_dong_where(place)}{extra_sql};"
    )


def building_in_dong_list_sql(place: str, *, limit: int = 50) -> str:
    return (
        f"SELECT {_LIST_COLS}\n"
        f'FROM "{_d010()}" b\n'
        f'JOIN "{_BND}" d\n'
        "  ON ST_Intersects(b.geometry, d.geometry)\n"
        f"WHERE {admin_dong_where(place)}\n"
        'ORDER BY b."A14" DESC NULLS LAST\n'
        f"LIMIT {limit};"
    )


def _place_buffer_zone(place: str) -> str:
    pred = admin_dong_where(place)
    return (
        "(\n"
        "  SELECT ST_Union(d.geometry) AS geom\n"
        f'  FROM "{_BND}" d\n'
        f"  WHERE {pred}\n"
        ") z"
    )


def place_buffer_count_sql(
    place: str,
    meters: str,
    expand_deg: str,
    *,
    exterior: bool = False,
) -> str:
    extra = "\n  AND NOT ST_Intersects(b.geometry, z.geom)" if exterior else ""
    return (
        'SELECT COUNT(DISTINCT b."A1") AS cnt\n'
        f'FROM "{_d010()}" b\n'
        f"CROSS JOIN {_place_buffer_zone(place)}\n"
        "WHERE z.geom IS NOT NULL\n"
        f"  AND b.geometry && ST_Expand(z.geom, {expand_deg})\n"
        "  AND ST_DWithin(\n"
        "    b.geometry::geography,\n"
        "    z.geom::geography,\n"
        f"    {meters}\n"
        f"  ){extra};"
    )


def place_buffer_list_sql(
    place: str,
    meters: str,
    expand_deg: str,
    *,
    limit: int = 50,
    exterior: bool = False,
) -> str:
    extra = "\n  AND NOT ST_Intersects(b.geometry, z.geom)" if exterior else ""
    return (
        f"SELECT {_LIST_COLS}\n"
        f'FROM "{_d010()}" b\n'
        f"CROSS JOIN {_place_buffer_zone(place)}\n"
        "WHERE z.geom IS NOT NULL\n"
        f"  AND b.geometry && ST_Expand(z.geom, {expand_deg})\n"
        "  AND ST_DWithin(\n"
        "    b.geometry::geography,\n"
        "    z.geom::geography,\n"
        f"    {meters}\n"
        f"  ){extra}\n"
        "ORDER BY ST_Distance(b.geometry::geography, z.geom::geography),\n"
        '  b."A14" DESC NULLS LAST\n'
        f"LIMIT {limit};"
    )


def _bas_scope(gu: str | None, bas_id: str | None, alias: str = "t") -> str:
    if bas_id:
        safe = bas_id.replace("'", "''")
        return f'{alias}."BAS_ID" = \'{safe}\''
    if gu:
        safe = gu.replace("'", "''")
        return f'{alias}."SIG_KOR_NM" = \'{safe}\''
    return "TRUE"


def building_bas_count_sql(gu: str | None = None, bas_id: str | None = None) -> str:
    return (
        'SELECT COUNT(DISTINCT b."A0") AS cnt\n'
        f'FROM "{_d010()}" b\n'
        f'JOIN "{_bas()}" t\n'
        "  ON b.geometry && t.geometry\n"
        " AND ST_Intersects(b.geometry, t.geometry)\n"
        f"WHERE {_bas_scope(gu, bas_id)};"
    )


def building_bas_list_sql(
    gu: str | None = None,
    bas_id: str | None = None,
    *,
    limit: int = 50,
) -> str:
    return (
        f"SELECT DISTINCT ON (b.\"A0\") {_LIST_COLS}\n"
        f'FROM "{_d010()}" b\n'
        f'JOIN "{_bas()}" t\n'
        "  ON b.geometry && t.geometry\n"
        " AND ST_Intersects(b.geometry, t.geometry)\n"
        f"WHERE {_bas_scope(gu, bas_id)}\n"
        'ORDER BY b."A0", b."A14" DESC NULLS LAST\n'
        f"LIMIT {limit};"
    )


def _join_op(op: str) -> str:
    if op == "within":
        return "ST_Within(t.geometry, d.geometry)"
    if op == "touches":
        # 기초구역·행정동은 격자 체계가 달라 Touches만으로는 0건인 경우가 많다.
        # 인접 = 맞닿거나, 걸치되 동 안에 완전히 들어가지 않는 구역.
        return (
            "(ST_Touches(t.geometry, d.geometry) OR "
            "(ST_Intersects(t.geometry, d.geometry) "
            "AND NOT ST_Within(t.geometry, d.geometry)))"
        )
    if op == "contains":
        return "ST_Contains(d.geometry, t.geometry)"
    return "ST_Intersects(t.geometry, d.geometry)"


def bas_dong_count_sql(place: str, op: str = "intersects") -> str:
    return (
        'SELECT COUNT(DISTINCT t."BAS_ID") AS cnt\n'
        f'FROM "{_bas()}" t\n'
        f'JOIN "{_BND}" d\n'
        f"  ON t.geometry && d.geometry AND {_join_op(op)}\n"
        f"WHERE {admin_dong_where(place)};"
    )


def bas_dong_building_group_sql(place: str, op: str = "intersects") -> str:
    """행정동 ∩ 기초구역별 건물 수 (BND-BAS-D010 3-way join)."""
    if op == "within":
        bas_join = "ST_Within(t.geometry, d.geometry)"
    else:
        bas_join = "ST_Intersects(d.geometry, t.geometry)"
    return (
        'SELECT t."BAS_ID", COUNT(DISTINCT b."A1")::bigint AS n\n'
        f'FROM "{_BND}" d\n'
        f'JOIN "{_bas()}" t ON {bas_join}\n'
        f'JOIN "{_d010()}" b ON ST_Intersects(b.geometry, t.geometry) '
        "AND ST_Intersects(b.geometry, d.geometry)\n"
        f"WHERE {admin_dong_where(place)}\n"
        "GROUP BY 1\n"
        "ORDER BY n DESC;"
    )


def bas_dong_count_and_max_sql(place: str, op: str = "intersects") -> str:
    return (
        'SELECT COUNT(DISTINCT t."BAS_ID") AS n,\n'
        '       MAX(t."BAS_AR") AS max_ar\n'
        f'FROM "{_bas()}" t\n'
        f'JOIN "{_BND}" d\n'
        f"  ON t.geometry && d.geometry AND {_join_op(op)}\n"
        f"WHERE {admin_dong_where(place)};"
    )


def bas_dong_list_sql(place: str, op: str = "intersects", *, limit: int = 50) -> str:
    return (
        f"SELECT DISTINCT {_BAS_COLS}\n"
        f'FROM "{_bas()}" t\n'
        f'JOIN "{_BND}" d\n'
        f"  ON t.geometry && d.geometry AND {_join_op(op)}\n"
        f"WHERE {admin_dong_where(place)}\n"
        'ORDER BY t."BAS_AR" DESC NULLS LAST\n'
        f"LIMIT {limit};"
    )


def bas_dong_buffer_count_sql(place: str, meters: str, expand_deg: str) -> str:
    return (
        'SELECT COUNT(DISTINCT t."BAS_ID") AS cnt\n'
        f'FROM "{_bas()}" t\n'
        f"CROSS JOIN {_place_buffer_zone(place)}\n"
        "WHERE z.geom IS NOT NULL\n"
        f"  AND t.geometry && ST_Expand(z.geom, {expand_deg})\n"
        "  AND ST_DWithin(\n"
        "    t.geometry::geography,\n"
        "    z.geom::geography,\n"
        f"    {meters}\n"
        "  );"
    )


def bas_dong_nearest_sql(place: str) -> str:
    return (
        f"SELECT {_BAS_COLS},\n"
        "  ST_Distance(t.geometry::geography, z.geom::geography) AS dist_m\n"
        f'FROM "{_bas()}" t\n'
        f"CROSS JOIN {_place_buffer_zone(place)}\n"
        "WHERE z.geom IS NOT NULL\n"
        "ORDER BY t.geometry <-> z.geom\n"
        "LIMIT 1;"
    )


def dong_neighbor_sql(place: str, *, limit: int = 50) -> str:
    return (
        f"SELECT DISTINCT {_BND_COLS}\n"
        f'FROM "{_BND}" a\n'
        f'JOIN "{_BND}" d\n'
        "  ON a.geometry && d.geometry\n"
        " AND ST_Intersects(a.geometry, d.geometry)\n"
        " AND a.\"ADM_CD\" <> d.\"ADM_CD\"\n"
        f"WHERE {admin_dong_where(place, 'a')}\n"
        f"  {_neighbor_prefix_sql(place)}"
        'ORDER BY d."ADM_NM"\n'
        f"LIMIT {limit};"
    )


def _neighbor_prefix_sql(place: str) -> str:
    extra = _adm_cd_like_sql("d", place)
    return f"AND {extra}\n" if extra else ""


def bas_gu_bnd_intersect_count_sql(gu: str) -> str:
    """구 기초구역 ∩ 해당 시도 센서스 행정동."""
    from txt2sql.gazetteer import unique_sigungu_adm_prefix

    safe = gu.replace("'", "''")
    extra = _adm_cd_like_sql("d", None, unique_sigungu_adm_prefix(gu))
    prefix_sql = f"\n  AND {extra}" if extra else ""
    return (
        'SELECT COUNT(DISTINCT t."BAS_ID") AS cnt\n'
        f'FROM "{_bas()}" t\n'
        f'JOIN "{_BND}" d\n'
        "  ON t.geometry && d.geometry\n"
        " AND ST_Intersects(t.geometry, d.geometry)\n"
        f"WHERE t.\"SIG_KOR_NM\" = '{safe}'"
        f"{prefix_sql};"
    )


def legal_dong_admin_share_sql(
    legal_dong: str,
    admin_dongs: list[str],
    *,
    usage: str | None = None,
) -> str:
    """법정동(A4) 건물을 행정동 경계로 나눠 건수·비율을 구한다."""
    a4 = place_a4_predicate(legal_dong)
    usage_sql = ""
    if usage:
        safe_u = usage.replace("'", "''")
        if usage == "공공용시설":
            usage_sql = " AND (b.\"A9\" = '공공용시설' OR b.\"A9\" ILIKE '%공공%')"
        else:
            usage_sql = f' AND b."A9" = \'{safe_u}\''
    if admin_dongs:
        ins = ", ".join("'" + n.replace("'", "''") + "'" for n in admin_dongs)
        admin_pred = f'd."ADM_NM" IN ({ins})'
    else:
        admin_pred = admin_dong_name_predicate(legal_dong, "d")
    return (
        "WITH bldg AS (\n"
        '  SELECT b."A0", b.geometry\n'
        f'  FROM "{_d010()}" b\n'
        f"  WHERE {a4}{usage_sql}\n"
        "),\n"
        "assigned AS (\n"
        '  SELECT DISTINCT ON (b."A0")\n'
        '    b."A0",\n'
        '    d."ADM_NM" AS admin_dong\n'
        "  FROM bldg b\n"
        f'  JOIN "{_BND}" d\n'
        "    ON b.geometry && d.geometry\n"
        "   AND ST_Intersects(b.geometry, d.geometry)\n"
        f"  WHERE {admin_pred}\n"
        f"    {_share_prefix_sql(legal_dong, admin_dongs)}"
        '  ORDER BY b."A0",\n'
        "    ST_Area(ST_Intersection(b.geometry, d.geometry)) DESC NULLS LAST\n"
        ")\n"
        "SELECT COALESCE(a.admin_dong, '미분류') AS admin_dong,\n"
        "       COUNT(*) AS n,\n"
        "       ROUND(\n"
        "         100.0 * COUNT(*) / NULLIF((SELECT COUNT(*) FROM bldg), 0),\n"
        "         1\n"
        "       ) AS pct\n"
        "FROM bldg b\n"
        'LEFT JOIN assigned a ON a."A0" = b."A0"\n'
        "GROUP BY 1\n"
        "ORDER BY CASE WHEN COALESCE(a.admin_dong, '미분류') = '미분류' "
        "THEN 1 ELSE 0 END,\n"
        "         1;"
    )


def _share_prefix_sql(legal_dong: str, admin_dongs: list[str]) -> str:
    probe = (admin_dongs[0] if admin_dongs else legal_dong) or ""
    extra = _adm_cd_like_sql("d", probe)
    return f"AND {extra}\n" if extra else ""


def legal_dong_admin_members_sql(place: str) -> str:
    """법정동에 대응하는 행정동(연산1동…) 목록."""
    where = admin_dong_where(place, "d")
    return (
        'SELECT d."ADM_CD" AS adm_cd, d."ADM_NM" AS admin_dong\n'
        f'FROM "{_BND}" d\n'
        f"WHERE {where}\n"
        'ORDER BY d."ADM_NM";'
    )


def spatial_fewshot(place: str | None) -> str:
    sample = place or "예시동"
    return (
        "Required pattern example:\n"
        f"{building_in_dong_count_sql(sample)}\n"
        "Place buffer (boundary + N meters) uses ST_DWithin geography "
        "against ST_Union of matching BND_ADM_DONG_PG polygons.\n"
        "Building ∩ 기초구역 → join resolved basic-zone table with ST_Intersects.\n"
        "Adapt table/columns only if needed; keep ST_Intersects and boundary join."
    )
