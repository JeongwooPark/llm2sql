"""지도 시각화 REST API."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from txt2sql.config import Settings
from txt2sql.map.choropleth import (
    PALETTES,
    ChoroplethError,
    apply_choropleth,
    field_stats,
    list_numeric_fields,
    preview,
    reset_choropleth,
)
from txt2sql.map.explain import explain_attributes
from txt2sql.map.geoserver import GeoServerClient
from txt2sql.map.labels import label_catalog_layers, labels_for_layer
from txt2sql.map.publish import (
    cleanup_session_layers,
    delete_published_layer,
    fetch_layer_attributes,
    is_safe_layer_name,
    is_safe_session_id,
    layer_owner_session,
)
from txt2sql.observability import mask_text
from txt2sql.security.auth import AuthContext
from txt2sql.security import ensure_admin, ensure_user
from txt2sql.security.http import http_from_public
from txt2sql.security.errors import PublicError, UserInputError, correlation_id

logger = logging.getLogger(__name__)


class LayerAttributesRequest(BaseModel):
    layer: str = Field(..., min_length=1)
    limit: int = Field(50, ge=1, le=500)
    offset: int = Field(0, ge=0)


class LayerLabelsRequest(BaseModel):
    layer: str = Field(..., min_length=1)
    columns: list[str] | None = None


class SessionCleanupRequest(BaseModel):
    session_id: str = Field(..., min_length=8, max_length=64)


class LayerDeleteRequest(BaseModel):
    session_id: str | None = Field(None, min_length=8, max_length=64)


# 업로드 공간테이블은 열이 수십 개일 수 있다. 거절(422)하지 않고 앞부분만 쓴다.
_EXPLAIN_MAX_COLUMNS = 80
_EXPLAIN_MAX_ROWS = 12
_EXPLAIN_MAX_PROPS = 80


class ExplainRequest(BaseModel):
    kind: Literal["identify", "table"] = "identify"
    title: str = ""
    layer: str = Field("", max_length=120)
    properties: dict[str, Any] | None = None
    columns: list[str] | None = Field(None, max_length=_EXPLAIN_MAX_COLUMNS)
    rows: list[dict[str, Any]] | None = Field(None, max_length=_EXPLAIN_MAX_ROWS)
    total: int | None = Field(None, ge=0, le=5_000_000)
    fields: dict[str, str] | None = None

    @field_validator("columns", mode="before")
    @classmethod
    def _trim_columns(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [str(item) for item in value[:_EXPLAIN_MAX_COLUMNS]]
        return value

    @field_validator("rows", mode="before")
    @classmethod
    def _trim_rows(cls, value: Any) -> Any:
        if isinstance(value, list):
            return list(value[:_EXPLAIN_MAX_ROWS])
        return value

    @field_validator("properties", mode="before")
    @classmethod
    def _trim_properties(cls, value: Any) -> Any:
        if isinstance(value, dict) and len(value) > _EXPLAIN_MAX_PROPS:
            return dict(list(value.items())[:_EXPLAIN_MAX_PROPS])
        return value

    @field_validator("fields", mode="before")
    @classmethod
    def _stringify_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        out: dict[str, str] = {}
        for key, label in value.items():
            if label is None:
                continue
            out[str(key)] = str(label)
            if len(out) >= 400:
                break
        return out

    @model_validator(mode="after")
    def _slim_rows_to_columns(self) -> ExplainRequest:
        cols = self.columns or []
        if cols and self.rows:
            keep = set(cols)
            self.rows = [
                {key: val for key, val in row.items() if key in keep}
                if isinstance(row, dict)
                else row
                for row in self.rows
            ]
        return self


class ChoroplethStatsRequest(BaseModel):
    layer: str = Field(..., min_length=1, max_length=120)
    field: str = Field(..., min_length=1, max_length=64)


class ChoroplethClassifyRequest(BaseModel):
    layer: str = Field(..., min_length=1, max_length=120)
    field: str = Field(..., min_length=1, max_length=64)
    method: str = Field("jenks", max_length=32)
    classes: int = Field(5, ge=1, le=9)
    palette: str = Field("YlOrRd", max_length=32)
    reverse: bool = False
    null_color: str = Field("#BDBDBD", max_length=7)
    stroke: str = Field("#666666", max_length=7)
    stroke_width: float = Field(0.7, ge=0, le=12)
    fill_opacity: float = Field(0.8, ge=0, le=1)
    break_values: list[float] | None = None
    manual_breaks: list[float] | None = None


class ChoroplethResetRequest(BaseModel):
    layer: str = Field(..., min_length=1, max_length=120)
    purge_style: bool = True
    style_name: str | None = Field(None, max_length=80)


def _choropleth_http(exc: Exception, *, corr: str) -> HTTPException:
    if isinstance(exc, ChoroplethError):
        return HTTPException(
            status_code=400,
            detail={
                "detail": str(exc),
                "code": "choropleth_error",
                "correlation_id": corr,
            },
        )
    logger.exception("choropleth error corr=%s err=%s", corr, mask_text(str(exc)))
    return HTTPException(
        status_code=500,
        detail={
            "detail": f"단계구분도 처리 중 오류가 발생했습니다. (참조: {corr})",
            "code": "internal_error",
            "correlation_id": corr,
        },
    )


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
    logger.exception("map api error corr=%s err=%s", corr, mask_text(str(exc)))
    return HTTPException(
        status_code=500,
        detail={
            "detail": f"{fallback} (참조: {corr})",
            "code": "internal_error",
            "correlation_id": corr,
        },
    )


def create_map_router(
    get_settings: Callable[[], Settings],
    *,
    get_ollama: Callable[[], Any] | None = None,
    get_auth: Callable[..., Any] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/map", tags=["map"])

    def _auth_dep():
        if get_auth is None:
            async def _anon() -> AuthContext:
                return AuthContext(role="admin", subject="local", via="loopback")

            return _anon
        return get_auth

    auth_dep = _auth_dep()

    @router.get("/status")
    def map_status(auth: AuthContext = Depends(auth_dep)) -> dict[str, Any]:
        ensure_user(auth)
        settings = get_settings()
        if not settings.geoserver_url:
            return {
                "enabled": False,
                "online": False,
                "message": "GeoServer가 설정되지 않았습니다. 채팅만 사용할 수 있습니다.",
            }
        client = GeoServerClient(settings)
        online = client.check()
        return {
            "enabled": True,
            "online": online,
            "workspace": client.workspace,
            "wms_url": client.wms_url(),
            "wfs_url": client.wfs_url(),
            "message": None
            if online
            else "GeoServer에 연결할 수 없습니다. 채팅은 유지됩니다.",
        }

    @router.get("/layers")
    def map_layers(auth: AuthContext = Depends(auth_dep)) -> dict[str, Any]:
        ensure_user(auth)
        settings = get_settings()
        client = GeoServerClient(settings)
        if not client.enabled or not client.check():
            return {"layers": [], "online": False}
        layers = label_catalog_layers(settings, client.catalog_layers())
        return {"layers": layers, "online": True}

    @router.get("/labels")
    def map_labels(
        layer: str,
        request: Request,
        columns: str | None = None,
        auth: AuthContext = Depends(auth_dep),
    ) -> dict[str, Any]:
        ensure_user(auth)
        corr = getattr(request.state, "correlation_id", None) or correlation_id()
        if not layer.strip():
            raise http_from_public(UserInputError("레이어가 필요합니다."))
        col_list = None
        if columns:
            col_list = [c for c in columns.split(",") if c.strip()]
        try:
            data = labels_for_layer(
                get_settings(), layer.strip(), columns=col_list
            )
        except Exception as exc:
            raise _safe_http(exc, corr=corr, fallback="라벨을 조회하지 못했습니다.") from exc
        return {"ok": True, **data}

    @router.post("/labels")
    def map_labels_post(
        body: LayerLabelsRequest,
        request: Request,
        auth: AuthContext = Depends(auth_dep),
    ) -> dict[str, Any]:
        ensure_user(auth)
        corr = getattr(request.state, "correlation_id", None) or correlation_id()
        try:
            data = labels_for_layer(
                get_settings(),
                body.layer.strip(),
                columns=body.columns,
            )
        except Exception as exc:
            raise _safe_http(exc, corr=corr, fallback="라벨을 조회하지 못했습니다.") from exc
        return {"ok": True, **data}

    @router.post("/attributes")
    def map_attributes(
        body: LayerAttributesRequest,
        request: Request,
        auth: AuthContext = Depends(auth_dep),
    ) -> dict[str, Any]:
        ensure_user(auth)
        corr = getattr(request.state, "correlation_id", None) or correlation_id()
        try:
            data = fetch_layer_attributes(
                get_settings(),
                body.layer,
                limit=body.limit,
                offset=body.offset,
            )
        except Exception as exc:
            raise _safe_http(exc, corr=corr, fallback="속성을 조회하지 못했습니다.") from exc
        return {"ok": True, **data}

    @router.post("/explain")
    def map_explain(
        body: ExplainRequest,
        request: Request,
        auth: AuthContext = Depends(auth_dep),
    ) -> dict[str, Any]:
        ensure_user(auth)
        corr = getattr(request.state, "correlation_id", None) or correlation_id()
        settings = get_settings()
        client = None
        if get_ollama is not None:
            try:
                client = get_ollama()
            except Exception:
                client = None
        try:
            data = explain_attributes(
                settings,
                kind=body.kind,
                title=body.title,
                layer=body.layer,
                properties=body.properties,
                columns=body.columns,
                rows=body.rows,
                total=body.total,
                fields=body.fields,
                client=client,
            )
        except Exception as exc:
            raise _safe_http(exc, corr=corr, fallback="설명을 만들지 못했습니다.") from exc
        return {"ok": True, **data}

    @router.delete("/layer/{name}")
    def map_delete_layer(
        name: str,
        request: Request,
        session_id: str | None = None,
        auth: AuthContext = Depends(auth_dep),
    ) -> dict[str, Any]:
        ensure_admin(auth)
        corr = getattr(request.state, "correlation_id", None) or correlation_id()
        if not is_safe_layer_name(name):
            raise http_from_public(UserInputError("허용되지 않은 레이어입니다."))
        try:
            owner = layer_owner_session(get_settings(), name)
            if owner and session_id:
                if not is_safe_session_id(session_id) or owner != session_id:
                    raise http_from_public(UserInputError(
                        "이 세션이 소유하지 않은 레이어입니다.",
                        code="layer_ownership",
                    ))
            elif owner and auth.via != "loopback" and auth.role != "admin":
                raise http_from_public(UserInputError(
                    "레이어 소유권을 확인할 수 없습니다.",
                    code="layer_ownership",
                ))
            delete_published_layer(
                get_settings(), name, session_id=session_id, enforce_owner=bool(session_id)
            )
        except Exception as exc:
            raise _safe_http(exc, corr=corr, fallback="레이어를 삭제하지 못했습니다.") from exc
        return {"ok": True}

    @router.post("/session/cleanup")
    def map_session_cleanup(
        body: SessionCleanupRequest,
        request: Request,
        auth: AuthContext = Depends(auth_dep),
    ) -> dict[str, Any]:
        ensure_admin(auth)
        corr = getattr(request.state, "correlation_id", None) or correlation_id()
        if not is_safe_session_id(body.session_id):
            raise http_from_public(UserInputError("허용되지 않은 세션입니다."))
        try:
            removed = cleanup_session_layers(get_settings(), body.session_id)
        except Exception as exc:
            raise _safe_http(
                exc, corr=corr, fallback="세션 레이어를 정리하지 못했습니다."
            ) from exc
        return {"ok": True, "removed": removed}

    @router.get("/choropleth/fields")
    def choropleth_fields(
        layer: str,
        request: Request,
        auth: AuthContext = Depends(auth_dep),
    ) -> dict[str, Any]:
        ensure_user(auth)
        corr = getattr(request.state, "correlation_id", None) or correlation_id()
        if not layer.strip():
            raise http_from_public(UserInputError("레이어가 필요합니다."))
        try:
            return list_numeric_fields(get_settings(), layer.strip())
        except Exception as extra:
            raise _choropleth_http(extra, corr=corr) from extra

    @router.get("/choropleth/palettes")
    def choropleth_palettes(auth: AuthContext = Depends(auth_dep)) -> dict[str, Any]:
        ensure_user(auth)
        return {"ok": True, "palettes": list(PALETTES.keys())}

    @router.post("/choropleth/stats")
    def choropleth_stats(
        body: ChoroplethStatsRequest,
        request: Request,
        auth: AuthContext = Depends(auth_dep),
    ) -> dict[str, Any]:
        ensure_user(auth)
        corr = getattr(request.state, "correlation_id", None) or correlation_id()
        try:
            return field_stats(get_settings(), body.layer.strip(), body.field.strip())
        except Exception as extra:
            raise _choropleth_http(extra, corr=corr) from extra

    @router.post("/choropleth/classify")
    def choropleth_classify(
        body: ChoroplethClassifyRequest,
        request: Request,
        auth: AuthContext = Depends(auth_dep),
    ) -> dict[str, Any]:
        ensure_user(auth)
        corr = getattr(request.state, "correlation_id", None) or correlation_id()
        try:
            data = preview(get_settings(), **body.model_dump())
            classification = data.get("classification") or {}
            return {"ok": True, **classification, "legend": data.get("legend")}
        except Exception as extra:
            raise _choropleth_http(extra, corr=corr) from extra

    @router.post("/choropleth/preview")
    def choropleth_preview(
        body: ChoroplethClassifyRequest,
        request: Request,
        auth: AuthContext = Depends(auth_dep),
    ) -> dict[str, Any]:
        ensure_user(auth)
        corr = getattr(request.state, "correlation_id", None) or correlation_id()
        try:
            return preview(get_settings(), **body.model_dump())
        except Exception as extra:
            raise _choropleth_http(extra, corr=corr) from extra

    @router.post("/choropleth/apply")
    def choropleth_apply(
        body: ChoroplethClassifyRequest,
        request: Request,
        auth: AuthContext = Depends(auth_dep),
    ) -> dict[str, Any]:
        ensure_admin(auth)
        corr = getattr(request.state, "correlation_id", None) or correlation_id()
        try:
            return apply_choropleth(get_settings(), **body.model_dump())
        except Exception as extra:
            raise _choropleth_http(extra, corr=corr) from extra

    @router.post("/choropleth/reset")
    def choropleth_reset(
        body: ChoroplethResetRequest,
        request: Request,
        auth: AuthContext = Depends(auth_dep),
    ) -> dict[str, Any]:
        ensure_admin(auth)
        corr = getattr(request.state, "correlation_id", None) or correlation_id()
        try:
            return reset_choropleth(
                get_settings(),
                body.layer.strip(),
                purge_style=body.purge_style,
                style_name=body.style_name,
            )
        except Exception as extra:
            raise _choropleth_http(extra, corr=corr) from extra

    return router
