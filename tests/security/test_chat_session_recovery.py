"""채팅 API: 만료·재시작 후 stale session_id 복구."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from txt2sql.config import Settings
from txt2sql.types import AskResult
from txt2sql.webapp.app import create_app


def _local_settings() -> Settings:
    return Settings(
        database_url="postgresql://x:x@localhost/x",
        security_mode="local",
        max_concurrent_asks=2,
    )


def test_chat_recreates_missing_session_instead_of_400() -> None:
    fake = MagicMock()
    fake.ask.return_value = AskResult.from_dict(
        {
            "answer": "사용 가능한 데이터 목록입니다.",
            "route": "meta_table",
            "sql": "",
            "rows": [],
        }
    )
    fake.close = MagicMock()

    with patch("txt2sql.webapp.app.Txt2SqlEngine") as eng_cls:
        eng_cls.from_settings.return_value = fake
        with patch("txt2sql.webapp.app.start_cleanup_scheduler"):
            with TestClient(create_app(settings=_local_settings())) as client:
                stale = "a" * 32
                res = client.post(
                    "/api/chat",
                    json={
                        "question": "사용가능한 데이터는?",
                        "session_id": stale,
                        "include_map": True,
                    },
                )
                assert res.status_code == 200, res.text
                assert "text/event-stream" in res.headers.get("content-type", "")
                body = res.text
                assert "ready" in body
                # 새 session_id가 SSE에 포함되고 stale id는 재사용되지 않음
                assert stale not in body or body.count(stale) == 0
                fake.ask.assert_called()
