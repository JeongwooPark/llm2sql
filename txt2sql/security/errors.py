"""공개 오류와 상관관계 ID."""

from __future__ import annotations

import secrets
import uuid
from typing import Any


class PublicError(Exception):
    """사용자에게 노출해도 되는 입력·정책 오류 (4xx)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        code: str = "bad_request",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


class UserInputError(PublicError):
    def __init__(self, message: str, *, code: str = "user_input") -> None:
        super().__init__(message, status_code=400, code=code)


def correlation_id() -> str:
    return uuid.uuid4().hex[:16]


def public_error_message(
    *,
    corr_id: str | None = None,
    fallback: str = "요청을 처리하지 못했습니다.",
) -> str:
    cid = corr_id or correlation_id()
    return f"{fallback} (참조: {cid})"


def safe_detail(exc: BaseException, *, corr_id: str) -> dict[str, Any]:
    if isinstance(exc, PublicError):
        return {
            "detail": exc.message,
            "code": exc.code,
            "correlation_id": corr_id,
        }
    return {
        "detail": public_error_message(corr_id=corr_id),
        "code": "internal_error",
        "correlation_id": corr_id,
    }


def new_opaque_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)
