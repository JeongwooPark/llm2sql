"""검증된 SELECT를 UNLOGGED 임시 테이블 + GeoServer 레이어로 발행한다."""

from __future__ import annotations

import re
import threading
import time
import uuid
from typing import Any

import psycopg

from txt2sql.config import Settings, database_url_for
from txt2sql.db import assert_readonly_sql, connect
from txt2sql.map.geoserver import GeoServerClient
from txt2sql.map.labels import infer_label_field, labels_for_layer
from txt2sql.map.sql import MapPlan, map_scope_key, pad_lonlat_extent, plan_map_sql

MAP_SCHEMA = "llm2sql_map"
_LAYER_NAME_RE = re.compile(r"^temp_[0-9a-f]{8,32}$")
_SESSION_ID_RE = re.compile(r"^[0-9a-f]{8,64}$", re.I)
_SCHEMA_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_CATALOG_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_cleanup_started = False
_cleanup_lock = threading.Lock()


def is_safe_layer_name(name: str) -> bool:
    return bool(_LAYER_NAME_RE.fullmatch(name or ""))


def is_safe_session_id(session_id: str | None) -> bool:
    return bool(session_id and _SESSION_ID_RE.fullmatch(session_id))


def layer_is_published(settings: Settings, layer: str) -> bool:
    """임시 분석 테이블이 아직 있으면 True."""
    if not is_safe_layer_name(layer):
        return False
    try:
        schema = _valid_schema(settings.map_schema)
        with connect(database_url_for(settings, "map")) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = %s AND table_name = %s
                    LIMIT 1
                    """,
                    (schema, layer),
                )
                return cur.fetchone() is not None
    except Exception:
        return False


def is_catalog_layer_name(name: str) -> bool:
    """GeoServer KorDB 레이어명(영문·숫자·밑줄). 임시 temp_* 와 구분한다."""
    return bool(_CATALOG_NAME_RE.fullmatch(name or "")) and not is_safe_layer_name(
        name
    )


def _valid_schema(schema: str) -> str:
    name = schema or MAP_SCHEMA
    if not _SCHEMA_RE.fullmatch(name):
        raise ValueError("허용되지 않은 맵 스키마입니다.")
    return name


def publish_query_layer(
    settings: Settings,
    *,
    question: str,
    sql: str | None,
    route: str | None,
    ok: bool,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    """성공한 질의에 지도를 붙인다. 실패해도 예외를 올리지 않고 dict를 반환한다."""
    if not settings.geoserver_url:
        return None
    plan = plan_map_sql(
        question=question,
        sql=sql,
        route=route,
        ok=ok,
        map_limit=settings.map_max_features,
    )
    if plan is None:
        return None
    client = GeoServerClient(settings)
    if not client.check():
        return {
            "available": False,
            "error": "GeoServer에 연결할 수 없습니다.",
        }
    try:
        return _publish_plan(settings, client, plan, session_id=session_id)
    except Exception as exc:
        return {
            "available": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _publish_plan(
    settings: Settings,
    client: GeoServerClient,
    plan: MapPlan,
    *,
    session_id: str | None,
) -> dict[str, Any]:
    schema = _valid_schema(settings.map_schema)
    layer = f"temp_{uuid.uuid4().hex[:16]}"
    assert_readonly_sql(plan.sql if plan.sql.rstrip().endswith(";") else plan.sql + ";")
    with connect(database_url_for(settings, "map")) as conn:
        _ensure_schema(conn, schema)
        _create_temp_table(conn, schema, layer, plan.sql)
        geom_meta = _prepare_geometry(conn, schema, layer)
        if geom_meta is None:
            _drop_table(conn, schema, layer)
            conn.commit()
            return {
                "available": False,
                "error": "결과 테이블에 geometry 컬럼이 없습니다.",
            }
        cols = _attribute_columns(conn, schema, layer)
        label_field = infer_label_field(cols)
        stats = _table_stats(conn, schema, layer, geom_meta["srid"])
        _track_layer(conn, schema, layer, session_id or "")
        conn.commit()

    srs = f"EPSG:{geom_meta['srid']}" if geom_meta["srid"] else "EPSG:4326"
    created = client.create_featuretype(
        layer, layer, srs=srs if geom_meta["srid"] not in {0, None} else "EPSG:4326",
        title=plan.title,
    )
    if not created:
        with connect(database_url_for(settings, "map")) as conn:
            _drop_table(conn, schema, layer)
            conn.commit()
        return {
            "available": False,
            "error": "GeoServer 레이어 등록에 실패했습니다.",
        }

    feature_count = int(stats.get("n") or 0)
    wfs_allowed = feature_count <= int(settings.map_wfs_max_features)
    evicted: list[str] = []
    if is_safe_session_id(session_id):
        keep = max(1, int(settings.map_max_analysis_layers or 8))
        evicted = trim_session_layers(settings, session_id, keep=keep)
    return {
        "available": True,
        "layer": layer,
        "workspace": client.workspace,
        "title": plan.title,
        "wms_url": client.wms_url(),
        "wfs_url": client.wfs_url(),
        "extent": pad_lonlat_extent(stats.get("extent")),
        "srs": "EPSG:4326",
        "native_srs": srs,
        "feature_count": feature_count,
        "geom_type": geom_meta.get("type") or "GEOMETRY",
        "rendering": "wms",
        "wfs_allowed": wfs_allowed,
        "kind": plan.kind,
        "label_field": label_field,
        "evicted": evicted,
    }


def fetch_layer_attributes(
    settings: Settings,
    layer: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    schema = _valid_schema(settings.map_schema)
    if is_safe_layer_name(layer):
        table = layer
    elif is_catalog_layer_name(layer):
        client = GeoServerClient(settings)
        catalog = {item["name"] for item in client.catalog_layers()}
        if layer not in catalog:
            raise ValueError("허용되지 않은 레이어 이름입니다.")
        table = layer
    else:
        raise ValueError("허용되지 않은 레이어 이름입니다.")
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    with connect(database_url_for(settings, "map")) as conn:
        table = _resolve_table_name(conn, schema, table)
        cols = _attribute_columns(conn, schema, table)
        if not cols:
            labels = labels_for_layer(settings, layer, columns=[])
            return {
                "columns": [],
                "rows": [],
                "total": 0,
                "title": labels.get("title") or "",
                "display_names": labels.get("fields") or {},
            }
        quoted = ", ".join(_ident(c) for c in cols)
        rel = f"{schema}.{_ident(table)}"
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM {rel}")  # noqa: S608
            total = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute(
                f"SELECT {quoted} FROM {rel} LIMIT %s OFFSET %s",  # noqa: S608
                (limit, offset),
            )
            rows = [dict(r) for r in cur.fetchall()]
    labels = labels_for_layer(settings, layer, columns=cols)
    return {
        "columns": cols,
        "rows": rows,
        "total": total,
        "title": labels.get("title") or "",
        "display_names": labels.get("fields") or {},
    }


def layer_owner_session(settings: Settings, layer: str) -> str | None:
    """Return owning session_id for a temp layer, if tracked."""
    if not is_safe_layer_name(layer):
        return None
    schema = _valid_schema(settings.map_schema)
    try:
        with connect(database_url_for(settings, "map")) as conn:
            _ensure_schema(conn, schema)
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT session_id FROM {schema}.layer_sessions WHERE layer_name = %s",
                    (layer,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                sid = str(row.get("session_id") or "").strip()
                return sid or None
    except Exception:
        return None


def delete_published_layer(
    settings: Settings,
    layer: str,
    *,
    session_id: str | None = None,
    enforce_owner: bool = False,
) -> bool:
    if not is_safe_layer_name(layer):
        raise ValueError("허용되지 않은 레이어 이름입니다.")
    schema = _valid_schema(settings.map_schema)
    if enforce_owner:
        if not is_safe_session_id(session_id):
            raise ValueError("세션 소유권 확인이 필요합니다.")
        owner = layer_owner_session(settings, layer)
        if owner is not None and owner != session_id:
            raise ValueError("이 세션이 소유하지 않은 레이어입니다.")
    client = GeoServerClient(settings)
    if client.enabled:
        client.delete_layer(layer)
    with connect(database_url_for(settings, "map")) as conn:
        _drop_table(conn, schema, layer)
        with conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {schema}.layer_sessions WHERE layer_name = %s",
                (layer,),
            )
        conn.commit()
    return True


def cleanup_session_layers(settings: Settings, session_id: str) -> int:
    """한 대화 세션의 임시 분석 레이어를 모두 삭제한다."""
    if not is_safe_session_id(session_id):
        raise ValueError("허용되지 않은 세션입니다.")
    schema = _valid_schema(settings.map_schema)
    client = GeoServerClient(settings)
    removed = 0
    with connect(database_url_for(settings, "map")) as conn:
        _ensure_schema(conn, schema)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT layer_name FROM {schema}.layer_sessions WHERE session_id = %s",
                (session_id,),
            )
            names = [str(row["layer_name"]) for row in cur.fetchall()]
        for name in names:
            if not is_safe_layer_name(name):
                continue
            if client.enabled:
                client.delete_layer(name)
            _drop_table(conn, schema, name)
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {schema}.layer_sessions WHERE layer_name = %s",
                    (name,),
                )
            removed += 1
        conn.commit()
    return removed


def trim_session_layers(settings: Settings, session_id: str, *, keep: int) -> list[str]:
    """세션당 최신 keep개만 남기고 오래된 분석 레이어를 삭제한다."""
    if not is_safe_session_id(session_id) or keep < 1:
        return []
    schema = _valid_schema(settings.map_schema)
    client = GeoServerClient(settings)
    evicted: list[str] = []
    with connect(database_url_for(settings, "map")) as conn:
        _ensure_schema(conn, schema)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT layer_name FROM {schema}.layer_sessions
                WHERE session_id = %s
                ORDER BY created_at DESC, layer_name DESC
                OFFSET %s
                """,
                (session_id, int(keep)),
            )
            names = [str(row["layer_name"]) for row in cur.fetchall()]
        for name in names:
            if not is_safe_layer_name(name):
                continue
            if client.enabled:
                client.delete_layer(name)
            _drop_table(conn, schema, name)
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {schema}.layer_sessions WHERE layer_name = %s",
                    (name,),
                )
            evicted.append(name)
        conn.commit()
    return evicted


def cleanup_expired_layers(settings: Settings, *, force: bool = False) -> int:
    schema = _valid_schema(settings.map_schema)
    hours = max(1, int(settings.map_retention_hours or 24))
    client = GeoServerClient(settings)
    removed = 0
    with connect(database_url_for(settings, "map")) as conn:
        _ensure_schema(conn, schema)
        with conn.cursor() as cur:
            if force:
                cur.execute(f"SELECT layer_name FROM {schema}.layer_sessions")
            else:
                cur.execute(
                    f"SELECT layer_name FROM {schema}.layer_sessions "
                    f"WHERE created_at < NOW() - (%s || ' hours')::interval",
                    (str(hours),),
                )
            names = [str(row["layer_name"]) for row in cur.fetchall()]
        for name in names:
            if not is_safe_layer_name(name):
                continue
            if client.enabled:
                client.delete_layer(name)
            _drop_table(conn, schema, name)
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {schema}.layer_sessions WHERE layer_name = %s",
                    (name,),
                )
            removed += 1
        conn.commit()
    return removed


def start_cleanup_scheduler(settings: Settings, interval_sec: int = 300) -> None:
    global _cleanup_started
    with _cleanup_lock:
        if _cleanup_started:
            return
        _cleanup_started = True

    def worker() -> None:
        while True:
            try:
                cleanup_expired_layers(settings)
            except Exception:
                pass
            time.sleep(interval_sec)

    threading.Thread(target=worker, daemon=True).start()


def _ensure_schema(conn: psycopg.Connection, schema: str) -> None:
    schema = _valid_schema(schema)
    with conn.cursor() as cur:
        if schema != "public":
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {schema}.layer_sessions (
                layer_name TEXT PRIMARY KEY,
                session_id TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    conn.commit()


def _create_temp_table(
    conn: psycopg.Connection, schema: str, layer: str, sql: str
) -> None:
    select_sql = strip_trailing_semicolon(sql)
    ddl = (
        f"CREATE UNLOGGED TABLE {schema}.{layer} AS\n"
        f"{select_sql}"
    )
    with conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '45s'")
        cur.execute(ddl)


def _prepare_geometry(
    conn: psycopg.Connection, schema: str, layer: str
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, udt_name
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            """,
            (schema, layer),
        )
        cols = list(cur.fetchall())
    geom_col = None
    for row in cols:
        name = str(row["column_name"])
        udt = str(row["udt_name"] or "").lower()
        if name.lower() in {"geometry", "geom", "the_geom"} or udt in {
            "geometry",
            "geography",
        }:
            geom_col = name
            break
    if geom_col is None:
        return None
    with conn.cursor() as cur:
        if geom_col != "geometry":
            cur.execute(
                f'ALTER TABLE {schema}.{layer} RENAME COLUMN {_ident(geom_col)} TO geometry'
            )
        try:
            cur.execute(
                f"CREATE INDEX {layer}_geom_idx ON {schema}.{layer} USING GIST (geometry)"
            )
        except Exception:
            pass
        cur.execute(f"ANALYZE {schema}.{layer}")
        cur.execute(
            f"SELECT ST_SRID(geometry) AS srid, GeometryType(geometry) AS gtype "
            f"FROM {schema}.{layer} WHERE geometry IS NOT NULL LIMIT 1"
        )
        meta = cur.fetchone() or {}
    srid = int(meta.get("srid") or 0)
    gtype = str(meta.get("gtype") or "GEOMETRY")
    return {"column": "geometry", "srid": srid, "type": gtype}


def _table_stats(
    conn: psycopg.Connection, schema: str, layer: str, srid: int
) -> dict[str, Any]:
    extent_expr = "ST_Extent(geometry)"
    if srid and srid != 4326:
        extent_expr = "ST_Extent(ST_Transform(geometry, 4326))"
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM {schema}.{layer}")
        n = int((cur.fetchone() or {}).get("n") or 0)
        cur.execute(
            f"SELECT {extent_expr} AS ext FROM {schema}.{layer} WHERE geometry IS NOT NULL"
        )
        ext = (cur.fetchone() or {}).get("ext")
    return {"n": n, "extent": _parse_box(ext)}


def _parse_box(value: Any) -> list[float] | None:
    if value is None:
        return None
    text = str(value)
    match = re.search(
        r"BOX\(\s*([-\d.]+)\s+([-\d.]+)\s*,\s*([-\d.]+)\s+([-\d.]+)\s*\)",
        text,
        re.I,
    )
    if not match:
        return None
    return [float(match.group(i)) for i in range(1, 5)]


def _resolve_table_name(
    conn: psycopg.Connection, schema: str, layer: str
) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s
              AND table_name IN (%s, lower(%s))
            ORDER BY CASE WHEN table_name = %s THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (schema, layer, layer, layer),
        )
        row = cur.fetchone()
        return str(row["table_name"]) if row else layer


def _attribute_columns(
    conn: psycopg.Connection, schema: str, layer: str
) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, udt_name
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (schema, layer),
        )
        skip = {"geometry", "geom", "the_geom", "geography"}
        cols: list[str] = []
        for row in cur.fetchall():
            name = str(row["column_name"])
            udt = str(row["udt_name"] or "").lower()
            if name.lower() in skip or udt in skip:
                continue
            cols.append(name)
        return cols


def _track_layer(
    conn: psycopg.Connection, schema: str, layer: str, session_id: str
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {schema}.layer_sessions (layer_name, session_id)
            VALUES (%s, %s)
            ON CONFLICT (layer_name) DO UPDATE SET session_id = EXCLUDED.session_id
            """,
            (layer, session_id),
        )


def _drop_table(conn: psycopg.Connection, schema: str, layer: str) -> None:
    if not is_safe_layer_name(layer):
        return
    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {schema}.{layer} CASCADE")


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def strip_trailing_semicolon(sql: str) -> str:
    return sql.strip().rstrip(";").strip()
