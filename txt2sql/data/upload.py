"""Shapefile ZIP → PostGIS + GeoServer (llm2_geodb shapefile_uploader 대응)."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any

from psycopg import sql

from txt2sql.config import Settings, database_url_for
from txt2sql.data.names import is_protected_table, is_safe_ident, table_from_shapefile
from txt2sql.db import connect
from txt2sql.map.geoserver import GeoServerClient
from txt2sql.security.errors import PublicError, UserInputError
from txt2sql.security.upload_io import (
    find_single_shapefile,
    safe_extract_zip,
    validate_zip_members,
)

TARGET_EPSG = 4326
CHUNK_SIZE = 1000
_ENCODINGS = ("utf-8", "euc-kr", "cp949", "iso-8859-1")


class TableExistsConflict(PublicError):
    def __init__(self, table_name: str) -> None:
        super().__init__(
            f"테이블이 이미 있습니다: {table_name}. "
            "교체하려면 replace_existing=true와 confirm_table_name을 지정하세요.",
            status_code=409,
            code="table_exists",
        )


def process_zip_upload(
    settings: Settings,
    *,
    filename: str,
    content: bytes,
    replace_existing: bool = False,
    confirm_table_name: str | None = None,
) -> dict[str, Any]:
    if not (filename or "").lower().endswith(".zip"):
        raise UserInputError("Shapefile ZIP 파일만 업로드할 수 있습니다.")
    if not content:
        raise UserInputError("파일이 비어 있습니다.")

    members = validate_zip_members(
        content,
        max_entries=int(settings.upload_zip_max_entries),
        max_uncompressed_total=int(settings.upload_zip_max_uncompressed),
        max_entry_uncompressed=min(
            int(settings.upload_zip_max_uncompressed),
            100 * 1024 * 1024,
        ),
    )

    with tempfile.TemporaryDirectory(prefix="txt2sql_shp_") as tmp:
        extract_dir = Path(tmp) / "extracted"
        safe_extract_zip(content, extract_dir, members=members)
        shp = find_single_shapefile(extract_dir)
        table_name = table_from_shapefile(shp.name)
        if is_protected_table(table_name):
            raise UserInputError("이 테이블명은 업로드할 수 없습니다.")

        exists = _table_exists(settings, table_name)
        if exists:
            if not replace_existing:
                raise TableExistsConflict(table_name)
            confirm = (confirm_table_name or "").strip()
            if confirm != table_name:
                raise UserInputError(
                    "테이블 교체 확인 이름이 일치하지 않습니다.",
                    code="replace_confirm_mismatch",
                )

        staging = f"stg_{uuid.uuid4().hex[:12]}"
        if not is_safe_ident(staging):
            raise UserInputError("staging 테이블명을 만들 수 없습니다.")
        try:
            rows = _upload_shapefile(settings, shp, staging)
            _promote_staging(settings, staging, table_name, replace=exists)
        except Exception:
            _drop_table_if_exists(settings, staging)
            raise

        geoserver_ok = _register_geoserver(settings, table_name)
        wired: dict[str, Any] = {}
        try:
            from txt2sql.data.coverage import register_uploaded_dataset

            wired = register_uploaded_dataset(settings, table_name)
        except Exception:
            wired = {}
        message = f"업로드 성공: {table_name} ({rows}건)"
        if not geoserver_ok:
            message += ". GeoServer 레이어 등록은 실패했거나 건너뛰었습니다."
        extra = str(wired.get("message") or "").strip()
        if extra:
            message += f". {extra}"
        elif wired.get("d198_coverage"):
            message += ". 질의 엔진 커버리지를 갱신했습니다."
        return {
            "ok": True,
            "table_name": table_name,
            "rows": rows,
            "geoserver": geoserver_ok,
            "wired": wired,
            "message": message,
            "replaced": bool(exists),
        }


def _admin_url(settings: Settings) -> str:
    return database_url_for(settings, "admin")


def _table_exists(settings: Settings, table_name: str) -> bool:
    schema = settings.map_schema or "public"
    if not is_safe_ident(schema) or not is_safe_ident(table_name):
        return False
    with connect(_admin_url(settings)) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = %s AND table_name = %s
                LIMIT 1
                """,
                (schema, table_name),
            )
            return cur.fetchone() is not None


def _drop_table_if_exists(settings: Settings, table_name: str) -> None:
    schema = settings.map_schema or "public"
    if not is_safe_ident(schema) or not is_safe_ident(table_name):
        return
    with connect(_admin_url(settings)) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("DROP TABLE IF EXISTS {}.{}").format(
                    sql.Identifier(schema),
                    sql.Identifier(table_name),
                )
            )
        conn.commit()


def _promote_staging(
    settings: Settings,
    staging: str,
    final: str,
    *,
    replace: bool,
) -> None:
    schema = settings.map_schema or "public"
    with connect(_admin_url(settings)) as conn:
        with conn.cursor() as cur:
            if replace:
                cur.execute(
                    sql.SQL("DROP TABLE IF EXISTS {}.{}").format(
                        sql.Identifier(schema),
                        sql.Identifier(final),
                    )
                )
            cur.execute(
                sql.SQL("ALTER TABLE {}.{} RENAME TO {}").format(
                    sql.Identifier(schema),
                    sql.Identifier(staging),
                    sql.Identifier(final),
                )
            )
        conn.commit()


def _upload_shapefile(settings: Settings, shp_path: Path, table_name: str) -> int:
    try:
        import geopandas as gpd
        from sqlalchemy import create_engine
    except ImportError as exc:
        raise RuntimeError(
            "geopandas가 필요합니다. `uv sync` 후 다시 시도하세요."
        ) from exc

    encoding, gdf = _read_shapefile(shp_path)
    if gdf is None:
        raise UserInputError("Shapefile을 읽지 못했습니다.")
    if gdf.crs is not None:
        try:
            if gdf.crs.to_epsg() != TARGET_EPSG:
                gdf = gdf.to_crs(epsg=TARGET_EPSG)
        except Exception:
            gdf = gdf.to_crs(epsg=TARGET_EPSG)
    if getattr(gdf, "geometry", None) is None:
        raise UserInputError("Shapefile에 geometry가 없습니다.")
    gdf = _convert_to_utf8(gdf, encoding)
    schema = settings.map_schema or "public"
    engine = create_engine(_sqlalchemy_url(_admin_url(settings)))
    total = len(gdf)
    try:
        for start in range(0, max(total, 1), CHUNK_SIZE):
            chunk = gdf.iloc[start : start + CHUNK_SIZE]
            chunk.to_postgis(
                name=table_name,
                con=engine,
                schema=schema,
                if_exists="replace" if start == 0 else "append",
                index=False,
            )
    finally:
        engine.dispose()
    return total


def _read_shapefile(shp_path: Path) -> tuple[str, Any]:
    import geopandas as gpd

    last_error: Exception | None = None
    for encoding in _ENCODINGS:
        try:
            gdf = gpd.read_file(shp_path, encoding=encoding)
            return encoding, gdf
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
        except Exception as exc:
            last_error = exc
            if "encoding" in str(exc).lower():
                continue
            try:
                return encoding, gpd.read_file(shp_path)
            except Exception as inner:
                last_error = inner
    if last_error:
        raise UserInputError(f"Shapefile 읽기 실패: {last_error}") from last_error
    raise UserInputError("Shapefile 읽기 실패")


def _convert_to_utf8(gdf: Any, source_encoding: str) -> Any:
    if (source_encoding or "").lower() == "utf-8":
        return gdf
    import pandas as pd

    for col in gdf.columns:
        if col == "geometry":
            continue
        if gdf[col].dtype == "object":
            try:
                gdf[col] = gdf[col].astype(str).where(pd.notna(gdf[col]), None)
            except Exception:
                continue
    return gdf


def _sqlalchemy_url(database_url: str) -> str:
    url = database_url or ""
    if url.startswith("postgresql+"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    return url


def _register_geoserver(settings: Settings, table_name: str) -> bool:
    client = GeoServerClient(settings)
    if not client.enabled:
        return False
    return client.ensure_featuretype(table_name, title=table_name)
