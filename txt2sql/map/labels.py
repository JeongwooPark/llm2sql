"""table_metadata / column_metadata → 지도 레이어·필드 한글 표시명."""

from __future__ import annotations

import re
from typing import Any

from txt2sql.building_row import infer_building_schema_from_columns
from txt2sql.config import Settings
from txt2sql.d198_attrs import COLUMN_LABELS as D198_COLUMN_LABELS
from txt2sql.db import connect
from txt2sql.map.sql import pad_lonlat_extent

_DATE_SUFFIX = re.compile(r"_\d{8}$")
_REGION_DATE = re.compile(r"_\d{2}_\d{8}$")
_SKIP_COLS = {"geometry", "geom", "the_geom", "geography", "boundedby", "bbox"}
_FALLBACK_FIELDS_D198: dict[str, str] = dict(D198_COLUMN_LABELS)
_FALLBACK_FIELDS_D198.update(
    {
        "A0": "도형ID",
        "A4": "법정동명",
        "A5": "특수지구분코드",
        "A6": "특수지구분명",
        "A7": "지번",
        "A13": "건물명",
        "A18": "건물건축면적",
        "A19": "건물연면적",
        "A25": "주요용도명",
        "A30": "건물높이",
        "A31": "지상층",
        "A32": "지하층",
        "A33": "허가일자",
        "A34": "사용승인일자",
    }
)
_PREFERRED_PREFIXES = (
    "AL_D010",
    "BND_ADM",
    "TL_KODIS",
    "AL_D060",
    "AL_D198",
)

# 메타에 없을 때 쓰는 GIS 공통 코드 폴백
_FALLBACK_FIELDS: dict[str, str] = {
    "A4": "법정동명",
    "A5": "지번",
    "A9": "용도명",
    "A12": "건물면적",
    "A14": "연면적",
    "A15": "대지면적",
    "A16": "높이",
    "A24": "건물명",
    "A25": "주요용도명",
    "A26": "지상층수",
    "A27": "지하층수",
    "A33": "허가일자",
    "A34": "사용승인일자",
    "ADM_CD": "행정동코드",
    "ADM_NM": "행정동명",
    "BASE_DATE": "기준일",
    "BAS_ID": "기초구역번호",
    "BAS_AR": "기초구역면적",
    "CTP_KOR_NM": "시도명",
    "SIG_CD": "시군구코드",
    "SIG_KOR_NM": "시군구명",
    "cnt": "건수",
    "count": "건수",
    "CNT": "건수",
    "n": "건수",
}

# 지도 라벨로 쓰기 좋은 컬럼 우선순위 (건물명 → 행정동명 → …)
LABEL_FIELD_PRIORITY: tuple[str, ...] = (
    "A24",
    "ADM_NM",
    "A13",
    "SIG_KOR_NM",
    "A5",
    "BAS_ID",
)


def infer_label_field(columns: list[str] | None) -> str | None:
    """결과 컬럼에서 지도에 그릴 라벨 필드를 고른다."""
    if not columns:
        return None
    by_upper = {str(name).upper(): str(name) for name in columns if name}
    for key in LABEL_FIELD_PRIORITY:
        hit = by_upper.get(key.upper())
        if hit:
            return hit
    return None


def normalize_field_key(key: str) -> str:
    """GetFeatureInfo/GML 키에서 실제 컬럼명만 남긴다."""
    text = (key or "").strip()
    if not text:
        return text
    if ":" in text:
        text = text.split(":")[-1]
    if "." in text:
        text = text.split(".")[-1]
    return text.strip()


def table_name_candidates(name: str) -> list[str]:
    """GeoServer 레이어명에서 메타데이터 table_name 후보를 만든다."""
    short = (name or "").split(":")[-1].strip()
    if not short:
        return []
    out: list[str] = []
    for item in (short, short.lower()):
        if item and item not in out:
            out.append(item)
        region = _REGION_DATE.sub("", item)
        if region and region not in out:
            out.append(region)
        dated = _DATE_SUFFIX.sub("", item)
        if dated and dated not in out:
            out.append(dated)
    return out


def _priority(table_name: str) -> int:
    for i, prefix in enumerate(_PREFERRED_PREFIXES):
        if table_name.upper().startswith(prefix):
            return i
    return len(_PREFERRED_PREFIXES)


class MetaIndex:
    """public.table_metadata / column_metadata 한 번에 읽어 표시명을 찾는다."""

    def __init__(
        self,
        tables: dict[str, str] | None = None,
        columns: dict[str, dict[str, str]] | None = None,
        units: dict[str, dict[str, str]] | None = None,
        display_names: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.tables = {k: v for k, v in (tables or {}).items() if v}
        self.columns = columns or {}
        self.units = units or {}
        self.display_names = display_names or {}
        self._by_lower = {k.lower(): k for k in self.tables}

    @classmethod
    def load(cls, settings: Settings) -> MetaIndex:
        tables: dict[str, str] = {}
        columns: dict[str, dict[str, str]] = {}
        units: dict[str, dict[str, str]] = {}
        display_names: dict[str, dict[str, str]] = {}
        try:
            with connect(settings.database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT table_name, display_name
                        FROM table_metadata
                        WHERE schema_name = 'public'
                        """
                    )
                    for row in cur.fetchall():
                        name = str(row["table_name"] or "")
                        label = str(row["display_name"] or "").strip()
                        if name and label:
                            tables[name] = label
                    cur.execute(
                        """
                        SELECT table_name, column_name, display_name, unit
                        FROM column_metadata
                        WHERE schema_name = 'public'
                        """
                    )
                    for row in cur.fetchall():
                        tname = str(row["table_name"] or "")
                        cname = str(row["column_name"] or "")
                        label = str(row["display_name"] or "").strip()
                        unit = str(row["unit"] or "").strip()
                        if not tname or not cname or not label:
                            continue
                        display_names.setdefault(tname, {})[cname] = label
                        if unit:
                            units.setdefault(tname, {})[cname] = unit
                        if unit and unit not in label:
                            label = f"{label}({unit})"
                        columns.setdefault(tname, {})[cname] = label
        except Exception:
            return cls()
        return cls(tables, columns, units, display_names)

    def resolve_table(self, layer: str) -> str | None:
        short = (layer or "").split(":")[-1]
        if not short or short.startswith("temp_"):
            return None
        for cand in table_name_candidates(short):
            if cand in self.tables:
                return cand
            hit = self._by_lower.get(cand.lower())
            if hit:
                return hit
        matches = [
            name
            for name in self.tables
            if name.startswith(short) or short.startswith(name)
        ]
        if not matches:
            return None
        matches.sort(key=lambda n: (_priority(n), -len(n), n))
        return matches[0]

    def table_title(self, layer: str) -> str:
        resolved = self.resolve_table(layer)
        if resolved and self.tables.get(resolved):
            return self.tables[resolved]
        return (layer or "").split(":")[-1] or layer

    def field_label(self, column: str, *, table: str | None = None) -> str:
        if table and table in self.columns:
            hit = self.columns[table].get(column)
            if hit:
                return hit
            lower = {k.lower(): v for k, v in self.columns[table].items()}
            if column.lower() in lower:
                return lower[column.lower()]
        ranked: list[tuple[int, str]] = []
        for tname, fields in self.columns.items():
            if column in fields:
                ranked.append((_priority(tname), fields[column]))
            else:
                for key, value in fields.items():
                    if key.lower() == column.lower():
                        ranked.append((_priority(tname), value))
                        break
        if ranked:
            ranked.sort()
            return ranked[0][1]
        if table and str(table).upper().startswith("AL_D198"):
            fallback = _FALLBACK_FIELDS_D198
        else:
            fallback = _FALLBACK_FIELDS
        return (
            fallback.get(column)
            or fallback.get(column.upper())
            or column
        )

    def _lookup_map(
        self,
        store: dict[str, dict[str, str]],
        column: str,
        *,
        table: str | None = None,
    ) -> str:
        if table and table in store:
            hit = store[table].get(column)
            if hit:
                return hit
            lower = {k.lower(): v for k, v in store[table].items()}
            if column.lower() in lower:
                return lower[column.lower()]
        ranked: list[tuple[int, str]] = []
        for tname, fields in store.items():
            if column in fields:
                ranked.append((_priority(tname), fields[column]))
            else:
                for key, value in fields.items():
                    if key.lower() == column.lower():
                        ranked.append((_priority(tname), value))
                        break
        if ranked:
            ranked.sort()
            return ranked[0][1]
        return ""

    def field_display_name(self, column: str, *, table: str | None = None) -> str:
        """단위를 붙이지 않은 메타데이터 표시명."""
        hit = self._lookup_map(self.display_names, column, table=table)
        if hit:
            return hit
        label = self.field_label(column, table=table)
        return label or column

    def field_unit(self, column: str, *, table: str | None = None) -> str:
        return self._lookup_map(self.units, column, table=table)

    def fields_for(self, layer: str, columns: list[str] | None = None) -> dict[str, str]:
        table = self.resolve_table(layer)
        schema = infer_building_schema_from_columns(columns)
        if not table and schema == "d198":
            table = "AL_D198"
        elif not table and schema == "d010":
            table = "AL_D010_26_20250704"
        known = dict(self.columns.get(table or "", {}))
        if columns is None:
            columns = list(known.keys())
        out: dict[str, str] = {}
        for col in columns:
            logical = normalize_field_key(col)
            label = (
                self.field_label(logical, table=table)
                if table
                else self.field_label(logical)
            )
            if not table and schema == "d198":
                label = (
                    _FALLBACK_FIELDS_D198.get(logical)
                    or _FALLBACK_FIELDS_D198.get(logical.upper())
                    or label
                )
            elif not table and schema == "d010":
                label = (
                    _FALLBACK_FIELDS.get(logical)
                    or _FALLBACK_FIELDS.get(logical.upper())
                    or label
                )
            out[col] = label
            if logical != col:
                out[logical] = label
        return out


def label_catalog_layers(
    settings: Settings, layers: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    index = MetaIndex.load(settings)
    layer_names = [
        str(item.get("name") or "").strip()
        for item in layers
        if str(item.get("name") or "").strip()
    ]
    geom_types = _catalog_geom_types(settings, names=layer_names)
    need_fallback = any(
        not (isinstance(item.get("extent"), list) and len(item.get("extent") or []) == 4)
        for item in layers
    )
    fallback_ext = (
        _catalog_estimated_extents(settings, names=layer_names) if need_fallback else {}
    )
    out: list[dict[str, Any]] = []
    for item in layers:
        row = dict(item)
        name = str(row.get("name") or "")
        row["title"] = index.table_title(name)
        row["display_name"] = row["title"]
        row["source_table"] = index.resolve_table(name)
        row["fields"] = index.fields_for(name)
        row["geom_type"] = (
            geom_types.get(name)
            or geom_types.get(name.lower())
            or row.get("geom_type")
            or ""
        )
        raw_ext = row.get("extent")
        if not (isinstance(raw_ext, list) and len(raw_ext) == 4):
            raw_ext = fallback_ext.get(name) or fallback_ext.get(name.lower())
        extent = pad_lonlat_extent(raw_ext if isinstance(raw_ext, list) else None)
        row["extent"] = extent or []
        out.append(row)
    return out


def _catalog_geom_types(
    settings: Settings, *, names: list[str] | None = None
) -> dict[str, str]:
    """PostGIS geometry_columns에서 레이어별 도형 종류를 읽는다."""
    wanted = {n for n in (names or []) if n and not n.startswith("temp_")}
    out: dict[str, str] = {}
    try:
        with connect(settings.database_url) as conn:
            with conn.cursor() as cur:
                if wanted:
                    cur.execute(
                        """
                        SELECT f_table_name, type
                        FROM geometry_columns
                        WHERE f_table_name = ANY(%s)
                        """,
                        (sorted(wanted),),
                    )
                else:
                    cur.execute(
                        """
                        SELECT f_table_name, type
                        FROM geometry_columns
                        WHERE f_table_name NOT LIKE 'temp\\_%' ESCAPE '\\'
                        """
                    )
                for row in cur.fetchall():
                    name = str(row["f_table_name"] or "")
                    gtype = str(row["type"] or "").strip()
                    if name and gtype:
                        out[name] = gtype
                        out[name.lower()] = gtype
    except Exception:
        return out
    return out


def _catalog_estimated_extents(
    settings: Settings, *, names: list[str] | None = None
) -> dict[str, list[float]]:
    """통계 기반 bbox. GeoServer bbox가 없을 때 줌용 폴백.

    temp_* 수천 개에 ST_EstimatedExtent를 돌리면 KorDB 목록이 수 분 걸린다.
    카탈로그에 올라온 레이어명만 대상으로 한다.
    """
    wanted = {n for n in (names or []) if n and not n.startswith("temp_")}
    if not wanted:
        return {}
    out: dict[str, list[float]] = {}
    try:
        with connect(settings.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT f_table_schema, f_table_name, f_geometry_column, srid
                    FROM geometry_columns
                    WHERE f_table_name = ANY(%s)
                    """,
                    (sorted(wanted),),
                )
                meta = list(cur.fetchall())
            for row in meta:
                schema = str(row["f_table_schema"] or "public")
                table = str(row["f_table_name"] or "")
                geom = str(row["f_geometry_column"] or "geometry")
                srid = int(row["srid"] or 0)
                if not table:
                    continue
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT ST_EstimatedExtent(%s, %s, %s) AS box",
                            (schema, table, geom),
                        )
                        box = (cur.fetchone() or {}).get("box")
                    ext = _estimated_box_lonlat(conn, box, srid)
                except Exception:
                    conn.rollback()
                    continue
                if ext:
                    out[table] = ext
                    out[table.lower()] = ext
    except Exception:
        return out
    return out


def _estimated_box_lonlat(conn: Any, box: Any, srid: int) -> list[float] | None:
    if box is None:
        return None
    nums = re.findall(r"[-+]?\d+(?:\.\d+)?", str(box))
    if len(nums) < 4:
        return None
    minx, miny, maxx, maxy = (
        float(nums[0]),
        float(nums[1]),
        float(nums[2]),
        float(nums[3]),
    )
    if maxx <= minx or maxy <= miny:
        return None
    looks_lonlat = 120.0 <= minx <= 140.0 and 30.0 <= miny <= 45.0
    if srid in {0, 4326} or looks_lonlat:
        return [minx, miny, maxx, maxy]
    if srid <= 0:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ST_XMin(g) AS minx, ST_YMin(g) AS miny,
                       ST_XMax(g) AS maxx, ST_YMax(g) AS maxy
                FROM ST_Transform(
                  ST_SetSRID(ST_MakeEnvelope(%s, %s, %s, %s, %s), %s),
                  4326
                ) AS g
                """,
                (minx, miny, maxx, maxy, srid, srid),
            )
            row = cur.fetchone() or {}
        ext = [
            float(row["minx"]),
            float(row["miny"]),
            float(row["maxx"]),
            float(row["maxy"]),
        ]
        if ext[2] <= ext[0] or ext[3] <= ext[1]:
            return None
        return ext
    except Exception:
        conn.rollback()
        return None


def _read_columns(settings: Settings, layer: str) -> list[str]:
    schema = (settings.map_schema or "public").strip() or "public"
    try:
        with connect(settings.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT column_name, udt_name
                    FROM information_schema.columns
                    WHERE table_schema = %s
                      AND table_name IN (%s, lower(%s))
                    ORDER BY ordinal_position
                    """,
                    (schema, layer, layer),
                )
                cols: list[str] = []
                for row in cur.fetchall():
                    name = str(row["column_name"])
                    udt = str(row["udt_name"] or "").lower()
                    if name.lower() in _SKIP_COLS or udt in _SKIP_COLS:
                        continue
                    cols.append(name)
                return cols
    except Exception:
        return []


def labels_for_layer(
    settings: Settings,
    layer: str,
    *,
    columns: list[str] | None = None,
) -> dict[str, Any]:
    index = MetaIndex.load(settings)
    cols = columns
    if cols is None and (layer or "").startswith("temp_"):
        cols = _read_columns(settings, layer)
    title = index.table_title(layer)
    if (layer or "").startswith("temp_") and title == (layer or "").split(":")[-1]:
        title = ""
    return {
        "layer": layer,
        "title": title,
        "source_table": index.resolve_table(layer),
        "fields": index.fields_for(layer, cols),
    }
