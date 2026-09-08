"""보안 HTTP 응답 헤더."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from txt2sql.config import Settings


def geoserver_csp_origin(geoserver_url: str) -> str | None:
    """GeoServer base URL → CSP origin (scheme://host[:port]).

    로컬/사설망 GeoServer는 흔히 http://host:8080 이다. img-src에 https:만
    있으면 ImageWMS 타일이 브라우저에서 차단되어 레이어는 등록되지만 지도에
    도형이 보이지 않는다.
    """
    raw = (geoserver_url or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def build_content_security_policy(settings: Settings | None = None) -> str:
    """지도 WMS/WFS에 필요한 GeoServer origin을 포함한 CSP."""
    img_src = ["'self'", "data:", "blob:", "https:"]
    connect_src = ["'self'", "http://127.0.0.1:*", "http://localhost:*"]
    origin = None
    if settings is not None:
        origin = geoserver_csp_origin(settings.geoserver_url)
    if origin:
        if origin not in img_src:
            img_src.append(origin)
        if origin not in connect_src:
            connect_src.append(origin)
    return (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
        f"img-src {' '.join(img_src)}; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        f"connect-src {' '.join(connect_src)}; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'"
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, get_settings: Callable[[], Settings] | None = None) -> None:
        super().__init__(app)
        self._get_settings = get_settings

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=()",
        )
        settings: Settings | None = None
        if self._get_settings is not None:
            try:
                settings = self._get_settings()
            except Exception:
                settings = None
        response.headers.setdefault(
            "Content-Security-Policy",
            build_content_security_policy(settings),
        )
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response
