"""데이터 관리 REST API."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from txt2sql.config import Settings
from txt2sql.data import catalog, csv_meta, upload as shp_upload
from txt2sql.data.names import split_schema_table
from txt2sql.observability import mask_text
from txt2sql.security.auth import AuthContext
from txt2sql.security import ensure_admin, ensure_user
from txt2sql.security.http import http_from_public
from txt2sql.security.errors import PublicError, UserInputError, correlation_id
from txt2sql.security.upload_io import PayloadTooLarge, read_upload_limited


async def _upload_chunks(upload: UploadFile, *, chunk_size: int = 64 * 1024):
    while True:
        chunk = await upload.read(chunk_size)
        if not chunk:
            break
        yield chunk


logger = logging.getLogger(__name__)


class TableMetaIn(BaseModel):
    display_name: str = ""
    description: str = ""
    category: str = ""


class ColumnMetaIn(BaseModel):
    display_name: str = ""
    description: str = ""
    data_type: str = ""
    unit: str = ""


class UpdateMetadataRequest(BaseModel):
    table_name: str = Field(..., min_length=1, max_length=200)
    table_metadata: TableMetaIn = Field(default_factory=TableMetaIn)
    column_metadata: dict[str, ColumnMetaIn] = Field(default_factory=dict)
    new_table_name: str | None = None


def _safe_http(exc: Exception, *, corr: str, fallback: str) -> HTTPException:
    if isinstance(exc, PublicError):
        return HTTPException(
            status_code=exc.status_code,
            detail={
                "detail": exc.message,
                "code": exc.code,
                "correlation_id": corr,
            },
        )
    if isinstance(exc, ValueError):
        return HTTPException(
            status_code=400,
            detail={"detail": str(exc), "code": "bad_request", "correlation_id": corr},
        )
    logger.exception("data api error corr=%s err=%s", corr, mask_text(str(exc)))
    return HTTPException(
        status_code=500,
        detail={
            "detail": f"{fallback} (참조: {corr})",
            "code": "internal_error",
            "correlation_id": corr,
        },
    )


def create_data_router(
    get_settings: Callable[[], Settings],
    *,
    get_auth: Callable[..., Any] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/data", tags=["data"])

    def _auth_dep():
        if get_auth is None:
            async def _anon() -> AuthContext:
                return AuthContext(role="admin", subject="local", via="loopback")

            return _anon
        return get_auth

    auth_dep = _auth_dep()

    @router.get("/tables")
    def list_tables(auth: AuthContext = Depends(auth_dep)) -> dict[str, Any]:
        ensure_user(auth)
        tables = catalog.list_spatial_tables(get_settings())
        return {"ok": True, "tables": tables}

    @router.get("/tables/{table_name}/structure")
    def table_structure(
        table_name: str,
        request: Request,
        auth: AuthContext = Depends(auth_dep),
    ) -> dict[str, Any]:
        ensure_user(auth)
        corr = getattr(request.state, "correlation_id", None) or correlation_id()
        try:
            structure = catalog.get_table_structure(get_settings(), table_name)
        except Exception as exc:
            raise _safe_http(exc, corr=corr, fallback="구조를 조회하지 못했습니다.") from exc
        return {"ok": True, "structure": structure}

    @router.get("/tables/{table_name}/metadata")
    def table_metadata(
        table_name: str,
        request: Request,
        auth: AuthContext = Depends(auth_dep),
    ) -> dict[str, Any]:
        ensure_user(auth)
        settings = get_settings()
        corr = getattr(request.state, "correlation_id", None) or correlation_id()
        try:
            metadata = catalog.get_table_metadata(settings, table_name)
            comments = catalog.get_database_comments(settings, table_name)
        except Exception as exc:
            raise _safe_http(
                exc, corr=corr, fallback="메타데이터를 조회하지 못했습니다."
            ) from exc
        return {"ok": True, "metadata": metadata, "database_comments": comments}

    @router.get("/tables/{table_name}/metadata/csv")
    def download_metadata_csv(
        table_name: str,
        request: Request,
        auth: AuthContext = Depends(auth_dep),
    ) -> Response:
        ensure_user(auth)
        settings = get_settings()
        corr = getattr(request.state, "correlation_id", None) or correlation_id()
        try:
            structure = catalog.get_table_structure(settings, table_name)
            metadata = catalog.get_table_metadata(settings, table_name)
            comments = catalog.get_database_comments(settings, table_name)
            schema, table = split_schema_table(
                table_name, settings.map_schema or "public"
            )
        except Exception as exc:
            raise _safe_http(
                exc, corr=corr, fallback="CSV를 만들지 못했습니다."
            ) from exc
        content = csv_meta.build_metadata_csv(
            f"{schema}.{table}",
            structure=structure,
            table_metadata=metadata.get("table_metadata") or {},
            column_metadata=metadata.get("column_metadata") or {},
            comments=comments,
        )
        filename = csv_meta.csv_download_name(table)
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    @router.post("/tables/{table_name}/metadata/csv")
    async def upload_metadata_csv(
        table_name: str,
        request: Request,
        file: UploadFile = File(...),
        auth: AuthContext = Depends(auth_dep),
    ) -> dict[str, Any]:
        ensure_admin(auth)
        settings = get_settings()
        corr = getattr(request.state, "correlation_id", None) or correlation_id()
        filename = file.filename or "metadata.csv"
        if not filename.lower().endswith(".csv"):
            raise http_from_public(UserInputError("CSV 파일만 업로드할 수 있습니다."))
        try:
            raw = await read_upload_limited(
                _upload_chunks(file),
                max_bytes=int(settings.upload_csv_max_bytes),
            )
            structure = catalog.get_table_structure(settings, table_name)
            parsed = csv_meta.parse_metadata_csv(
                raw,
                expected_table=table_name,
                structure=structure,
                default_schema=settings.map_schema or "public",
            )
            existing = catalog.get_table_metadata(settings, table_name)
            table_meta, columns = csv_meta.merge_parsed_with_existing(parsed, existing)
            catalog.update_table_metadata(
                settings, parsed["table_name"], table_meta, columns
            )
            try:
                from txt2sql.data.coverage import sync_dataset_after_change

                sync_dataset_after_change(
                    settings, parsed["table_name"], auto_metadata=False
                )
            except Exception:
                pass
        except Exception as exc:
            raise _safe_http(
                exc, corr=corr, fallback="CSV 메타데이터를 저장하지 못했습니다."
            ) from exc
        skipped = parsed.get("skipped_columns") or []
        message = "CSV 메타데이터를 저장했습니다."
        if skipped:
            message += f" 편집할 수 없는 컬럼 {len(skipped)}개는 건너뛰었습니다."
        return {"ok": True, "message": message, "skipped_columns": skipped}

    @router.get("/tables/{table_name}/display-name")
    def table_display_name(
        table_name: str,
        request: Request,
        auth: AuthContext = Depends(auth_dep),
    ) -> dict[str, Any]:
        ensure_user(auth)
        corr = getattr(request.state, "correlation_id", None) or correlation_id()
        try:
            name = catalog.get_table_display_name(get_settings(), table_name)
        except Exception as exc:
            raise _safe_http(exc, corr=corr, fallback="표시명을 조회하지 못했습니다.") from exc
        return {"ok": True, "display_name": name}

    @router.get("/tables/{table_name}/parse")
    def parse_table(
        table_name: str,
        request: Request,
        auth: AuthContext = Depends(auth_dep),
    ) -> dict[str, Any]:
        ensure_user(auth)
        corr = getattr(request.state, "correlation_id", None) or correlation_id()
        try:
            parsed = catalog.parse_table_code(get_settings(), table_name)
        except Exception as exc:
            raise _safe_http(exc, corr=corr, fallback="코드를 해석하지 못했습니다.") from exc
        if parsed is None:
            raise http_from_public(UserInputError(
                "테이블 코드를 해석할 수 없습니다. (예: AL_D198_26_20250704)"
            ))
        return {"ok": True, "parsed_metadata": parsed}

    @router.post("/metadata")
    def update_metadata(
        body: UpdateMetadataRequest,
        request: Request,
        auth: AuthContext = Depends(auth_dep),
    ) -> dict[str, Any]:
        ensure_admin(auth)
        settings = get_settings()
        corr = getattr(request.state, "correlation_id", None) or correlation_id()
        table_name = body.table_name
        columns = {
            key: value.model_dump() for key, value in body.column_metadata.items()
        }
        try:
            if body.new_table_name:
                table_name = catalog.rename_table(
                    settings, table_name, body.new_table_name
                )
            catalog.update_table_metadata(
                settings,
                table_name,
                body.table_metadata.model_dump(),
                columns,
            )
            try:
                from txt2sql.data.coverage import sync_dataset_after_change

                sync_dataset_after_change(
                    settings, table_name, auto_metadata=False
                )
            except Exception:
                pass
        except Exception as exc:
            raise _safe_http(
                exc, corr=corr, fallback="메타데이터를 저장하지 못했습니다."
            ) from exc
        message = "메타데이터가 업데이트되었습니다."
        payload: dict[str, Any] = {"ok": True, "message": message}
        if body.new_table_name:
            payload["new_table_name"] = table_name
            payload["message"] = "테이블명 변경 및 메타데이터가 업데이트되었습니다."
        return payload

    @router.post("/upload")
    async def upload_shapefile(
        request: Request,
        shapefile: UploadFile = File(...),
        replace_existing: bool = Form(False),
        confirm_table_name: str | None = Form(None),
        auth: AuthContext = Depends(auth_dep),
    ) -> dict[str, Any]:
        ensure_admin(auth)
        settings = get_settings()
        corr = getattr(request.state, "correlation_id", None) or correlation_id()
        filename = shapefile.filename or "upload.zip"
        try:
            content = await read_upload_limited(
                _upload_chunks(shapefile),
                max_bytes=int(settings.upload_zip_max_bytes),
            )
            result = shp_upload.process_zip_upload(
                settings,
                filename=filename,
                content=content,
                replace_existing=replace_existing,
                confirm_table_name=confirm_table_name,
            )
        except PayloadTooLarge as exc:
            raise _safe_http(exc, corr=corr, fallback="") from exc
        except Exception as exc:
            raise _safe_http(
                exc, corr=corr, fallback="업로드를 처리하지 못했습니다."
            ) from exc
        return result

    return router
