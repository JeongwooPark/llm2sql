"""배포 모드·Host/Origin·루프백 검사."""

from __future__ import annotations

import ipaddress
import logging
from collections.abc import Callable, Sequence
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from txt2sql.config import Settings
from txt2sql.security.errors import correlation_id

logger = logging.getLogger(__name__)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def validate_security_settings(settings: Settings) -> None:
    mode = (settings.security_mode or "").strip().lower()
    if mode not in {"local", "token"}:
        raise ValueError(
            f"SECURITY_MODE must be 'local' or 'token' (got {settings.security_mode!r})"
        )
    if mode == "token":
        if not (settings.api_user_token or "").strip():
            raise ValueError("token mode requires API_USER_TOKEN")
        if not (settings.api_admin_token or "").strip():
            raise ValueError("token mode requires API_ADMIN_TOKEN")
        if settings.api_user_token.strip() == settings.api_admin_token.strip():
            raise ValueError("API_USER_TOKEN and API_ADMIN_TOKEN must differ")
        q = (settings.database_url_query or settings.database_url).strip()
        m = (settings.database_url_map or settings.database_url).strip()
        a = (settings.database_url_admin or settings.database_url).strip()
        if len({q, m, a}) < 3:
            raise ValueError(
                "token mode requires distinct DATABASE_URL_QUERY / "
                "DATABASE_URL_MAP / DATABASE_URL_ADMIN"
            )


def is_loopback_host(host: str | None) -> bool:
    raw = (host or "").strip().lower()
    if not raw:
        return False
    # strip port
    if raw.startswith("["):
        raw = raw.split("]", 1)[0].lstrip("[")
    elif ":" in raw and raw.count(":") == 1:
        raw = raw.split(":", 1)[0]
    if raw in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(raw).is_loopback
    except ValueError:
        return False


def client_host(request: Request, settings: Settings) -> str:
    """Never trust X-Forwarded-* unless trusted_proxy is enabled."""
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
    client = request.client
    return (client.host if client else "") or ""


def request_host_header(request: Request) -> str:
    return (request.headers.get("host") or "").strip()


def origin_allowed(origin: str | None, allowed: Sequence[str]) -> bool:
    if not origin:
        return True  # same-origin navigations may omit Origin
    if not allowed:
        return False
    return origin.rstrip("/") in {item.rstrip("/") for item in allowed}


class SecurityGateMiddleware(BaseHTTPMiddleware):
    """local: loopback only. token: Host/Origin allowlist. Sets request.state flags."""

    def __init__(self, app: Any, get_settings: Callable[[], Settings]) -> None:
        super().__init__(app)
        self._get_settings = get_settings

    async def dispatch(self, request: Request, call_next) -> Response:
        settings = self._get_settings()
        corr = correlation_id()
        request.state.correlation_id = corr
        peer = client_host(request, settings)
        loopback = is_loopback_host(peer)
        request.state.client_is_loopback = loopback
        request.state.client_host = peer

        host_hdr = request_host_header(request)
        if settings.security_mode == "local":
            if not loopback:
                logger.warning(
                    "rejected non-loopback client corr=%s peer=%s", corr, peer
                )
                return JSONResponse(
                    {
                        "detail": "로컬 모드에서는 루프백 접근만 허용됩니다.",
                        "code": "forbidden_host",
                        "correlation_id": corr,
                    },
                    status_code=403,
                )
            # Reject unexpected Host to reduce DNS rebinding in local mode.
            # Starlette TestClient uses Host: testserver on loopback peers.
            host_name = host_hdr.split(":", 1)[0].strip().lower()
            allowed_local = _LOOPBACK_HOSTS | {"testserver"}
            if host_name and host_name not in allowed_local:
                return JSONResponse(
                    {
                        "detail": "허용되지 않은 Host입니다.",
                        "code": "forbidden_host",
                        "correlation_id": corr,
                    },
                    status_code=403,
                )
        else:
            allowed_hosts = {
                h.strip().lower()
                for h in (settings.trusted_hosts or ())
                if h and h.strip()
            }
            host_name = host_hdr.split(":", 1)[0].strip().lower()
            if allowed_hosts and host_name not in allowed_hosts:
                return JSONResponse(
                    {
                        "detail": "허용되지 않은 Host입니다.",
                        "code": "forbidden_host",
                        "correlation_id": corr,
                    },
                    status_code=403,
                )
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                origin = request.headers.get("origin")
                allowed = list(settings.allowed_origins or ())
                if origin and not origin_allowed(origin, allowed):
                    return JSONResponse(
                        {
                            "detail": "허용되지 않은 Origin입니다.",
                            "code": "forbidden_origin",
                            "correlation_id": corr,
                        },
                        status_code=403,
                    )

        response = await call_next(request)
        response.headers.setdefault("X-Correlation-ID", corr)
        return response
