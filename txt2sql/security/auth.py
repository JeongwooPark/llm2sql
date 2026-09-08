"""API 토큰 인증·인가."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Literal

from fastapi import Header, Request

from txt2sql.config import Settings
from txt2sql.security.errors import PublicError

Role = Literal["anonymous", "user", "admin"]


class AuthError(PublicError):
    def __init__(self, message: str = "인증이 필요합니다.") -> None:
        super().__init__(message, status_code=401, code="unauthorized")


class PermissionError(PublicError):
    def __init__(self, message: str = "권한이 없습니다.") -> None:
        super().__init__(message, status_code=403, code="forbidden")


@dataclass(frozen=True)
class AuthContext:
    role: Role
    subject: str
    via: str = "none"


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def _token_match(provided: str | None, expected: str) -> bool:
    if not provided or not expected:
        return False
    # compare_digest requires equal-length strings
    a = provided.encode("utf-8")
    b = expected.encode("utf-8")
    if len(a) != len(b):
        # length-mismatch still compare against dummy to reduce timing leak shape
        secrets.compare_digest(a, a)
        return False
    return secrets.compare_digest(a, b)


def resolve_auth(
    settings: Settings,
    *,
    authorization: str | None,
    client_is_loopback: bool,
) -> AuthContext:
    """Resolve caller role from Bearer token and deploy mode."""
    token = _extract_bearer(authorization)
    admin = (settings.api_admin_token or "").strip()
    user = (settings.api_user_token or "").strip()

    if _token_match(token, admin) and admin:
        return AuthContext(role="admin", subject="admin", via="bearer")
    if _token_match(token, user) and user:
        return AuthContext(role="user", subject="user", via="bearer")

    if settings.security_mode == "local" and client_is_loopback:
        # Trusted single-user loopback: implicit admin for local UI.
        return AuthContext(role="admin", subject="local", via="loopback")

    if settings.security_mode == "token":
        if token:
            raise AuthError("유효하지 않은 토큰입니다.")
        raise AuthError()

    # local but non-loopback already rejected by middleware; treat as anonymous
    return AuthContext(role="anonymous", subject="anonymous", via="none")


def require_user(auth: AuthContext) -> AuthContext:
    if auth.role in {"user", "admin"}:
        return auth
    raise AuthError()


def require_admin(auth: AuthContext) -> AuthContext:
    if auth.role == "admin":
        return auth
    if auth.role == "anonymous":
        raise AuthError()
    raise PermissionError()


def auth_dependency_factory(get_settings):
    """FastAPI dependency: returns AuthContext after host/origin middleware."""

    async def _dep(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> AuthContext:
        settings: Settings = get_settings()
        loopback = bool(getattr(request.state, "client_is_loopback", False))
        return resolve_auth(
            settings,
            authorization=authorization,
            client_is_loopback=loopback,
        )

    return _dep
