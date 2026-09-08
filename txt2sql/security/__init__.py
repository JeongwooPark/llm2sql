"""보안 경계: 배포 모드, 인증, 세션, SQL AST, 업로드, 오류 적색화."""

from __future__ import annotations

from txt2sql.security.auth import (
    AuthContext,
    AuthError,
    PermissionError,
    require_admin,
    require_user,
    resolve_auth,
)
from txt2sql.security.errors import (
    PublicError,
    UserInputError,
    correlation_id,
    public_error_message,
)
from txt2sql.security.http import http_from_public
from txt2sql.security.sessions import SessionStore

__all__ = [
    "AuthContext",
    "AuthError",
    "PermissionError",
    "PublicError",
    "SessionStore",
    "UserInputError",
    "correlation_id",
    "ensure_admin",
    "ensure_user",
    "http_from_public",
    "public_error_message",
    "require_admin",
    "require_user",
    "resolve_auth",
]


def ensure_user(auth: AuthContext) -> AuthContext:
    try:
        return require_user(auth)
    except PublicError as exc:
        raise http_from_public(exc) from exc


def ensure_admin(auth: AuthContext) -> AuthContext:
    try:
        return require_admin(auth)
    except PublicError as exc:
        raise http_from_public(exc) from exc
