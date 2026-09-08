"""FastAPI용 공개 오류 → HTTPException 변환."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from txt2sql.observability import mask_text
from txt2sql.security.errors import PublicError, correlation_id, safe_detail

logger = logging.getLogger(__name__)


def http_from_public(exc: PublicError, *, corr_id: str | None = None) -> HTTPException:
    cid = corr_id or correlation_id()
    return HTTPException(
        status_code=exc.status_code,
        detail={
            "detail": exc.message,
            "code": exc.code,
            "correlation_id": cid,
        },
    )


def raise_public(exc: PublicError, *, corr_id: str | None = None) -> None:
    raise http_from_public(exc, corr_id=corr_id)


async def public_error_handler(request: Request, exc: PublicError) -> JSONResponse:
    corr = getattr(request.state, "correlation_id", None) or correlation_id()
    return JSONResponse(status_code=exc.status_code, content=safe_detail(exc, corr_id=corr))


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Do not swallow FastAPI/Starlette HTTP errors.
    if isinstance(exc, (HTTPException, StarletteHTTPException)):
        raise exc
    corr = getattr(request.state, "correlation_id", None) or correlation_id()
    logger.exception(
        "unhandled error corr=%s path=%s err=%s",
        corr,
        request.url.path,
        mask_text(f"{type(exc).__name__}: {exc}"),
    )
    return JSONResponse(
        status_code=500,
        content=safe_detail(exc, corr_id=corr),
    )


def redact_sse_error(exc: BaseException, *, corr_id: str) -> dict[str, Any]:
    if isinstance(exc, PublicError):
        return {
            "type": "error",
            "message": exc.message,
            "code": exc.code,
            "correlation_id": corr_id,
        }
    return {
        "type": "error",
        "message": f"요청을 처리하지 못했습니다. (참조: {corr_id})",
        "code": "internal_error",
        "correlation_id": corr_id,
    }
