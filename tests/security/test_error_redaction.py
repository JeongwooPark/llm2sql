from __future__ import annotations

from txt2sql.observability import mask_mapping, mask_text
from txt2sql.security.errors import PublicError, safe_detail
from txt2sql.security.http import redact_sse_error


def test_mask_bearer_cookie_and_urlencoded() -> None:
    text = mask_text(
        "Authorization: Bearer supersecrettoken "
        "Cookie: sid=abc123 "
        "postgresql://u:%40p%40ss@localhost/db "
        "?password=hidden&token=xyz"
    )
    assert "supersecrettoken" not in text
    assert "abc123" not in text
    assert "%40p%40ss" not in text
    assert "hidden" not in text
    assert "***" in text


def test_mask_nested_and_exception() -> None:
    mapped = mask_mapping(
        {
            "nested": {"authorization": "Bearer tok", "ok": 1},
            "items": [{"token": "t1"}, "postgresql://a:b@h/db"],
        }
    )
    assert mapped["nested"]["authorization"] == "***"
    assert "b@" not in str(mapped)


def test_safe_detail_hides_internal() -> None:
    detail = safe_detail(RuntimeError("postgresql://u:secret@h/db"), corr_id="abc")
    assert "secret" not in detail["detail"]
    assert detail["correlation_id"] == "abc"
    assert detail["code"] == "internal_error"


def test_public_error_passthrough() -> None:
    exc = PublicError("입력 오류", status_code=400, code="user_input")
    detail = safe_detail(exc, corr_id="cid")
    assert detail["detail"] == "입력 오류"
    sse = redact_sse_error(RuntimeError("boom path=/tmp/x"), corr_id="cid2")
    assert "boom" not in sse["message"]
    assert sse["correlation_id"] == "cid2"
