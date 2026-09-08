from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from txt2sql.config import Settings
from txt2sql.data.router import create_data_router
from txt2sql.map.router import create_map_router
from txt2sql.security.auth import AuthError, auth_dependency_factory, resolve_auth
from txt2sql.security.deploy import SecurityGateMiddleware, validate_security_settings
from txt2sql.security.headers import SecurityHeadersMiddleware


def _token_settings(**kwargs) -> Settings:
    base = dict(
        database_url="postgresql://q:q@localhost:5432/q",
        database_url_query="postgresql://q:q@localhost:5432/q",
        database_url_map="postgresql://m:m@localhost:5432/m",
        database_url_admin="postgresql://a:a@localhost:5432/a",
        security_mode="token",
        api_user_token="user-token-value-32chars-aaaaaaa",
        api_admin_token="admin-token-value-32chars-bbbbbb",
        trusted_hosts=("127.0.0.1", "localhost", "testserver"),
        allowed_origins=("http://127.0.0.1:8000",),
    )
    base.update(kwargs)
    return Settings(**base)


def test_token_mode_requires_distinct_tokens_and_db_urls() -> None:
    with pytest.raises(ValueError):
        validate_security_settings(
            Settings(
                database_url="postgresql://x:x@localhost/x",
                security_mode="token",
                api_user_token="same",
                api_admin_token="same",
                database_url_query="postgresql://q:q@localhost/q",
                database_url_map="postgresql://m:m@localhost/m",
                database_url_admin="postgresql://a:a@localhost/a",
            )
        )
    with pytest.raises(ValueError):
        validate_security_settings(
            Settings(
                database_url="postgresql://x:x@localhost/x",
                security_mode="token",
                api_user_token="user-token-aaaaaaaaaaaaaaaa",
                api_admin_token="admin-token-bbbbbbbbbbbbbbb",
            )
        )


def test_unknown_security_mode_fails() -> None:
    with pytest.raises(ValueError):
        Settings.from_mapping(
            {
                "DATABASE_URL": "postgresql://x:x@localhost/x",
                "SECURITY_MODE": "open",
            }
        )


def test_bearer_prefix_suffix_case_rejected() -> None:
    settings = _token_settings()
    token = settings.api_admin_token
    for bad in (token[:-1], "x" + token, token.upper()):
        with pytest.raises(AuthError):
            resolve_auth(
                settings,
                authorization=f"Bearer {bad}",
                client_is_loopback=False,
            )


def test_user_cannot_call_admin_upload() -> None:
    settings = _token_settings()
    app = FastAPI()
    app.add_middleware(SecurityGateMiddleware, get_settings=lambda: settings)
    app.include_router(
        create_data_router(
            lambda: settings,
            get_auth=auth_dependency_factory(lambda: settings),
        )
    )
    client = TestClient(app)
    r = client.post(
        "/api/data/upload",
        headers={"Authorization": f"Bearer {settings.api_user_token}"},
        files={"shapefile": ("x.zip", b"PK\x05\x06" + b"\x00" * 18, "application/zip")},
    )
    assert r.status_code == 403


def test_mutating_map_requires_admin() -> None:
    settings = _token_settings()
    app = FastAPI()
    app.add_middleware(SecurityGateMiddleware, get_settings=lambda: settings)
    app.include_router(
        create_map_router(
            lambda: settings,
            get_auth=auth_dependency_factory(lambda: settings),
        )
    )
    client = TestClient(app)
    r = client.delete(
        "/api/map/layer/temp_deadbeef",
        headers={"Authorization": f"Bearer {settings.api_user_token}"},
    )
    assert r.status_code == 403


def test_forbidden_origin_rejected() -> None:
    settings = _token_settings()
    app = FastAPI()
    app.add_middleware(SecurityGateMiddleware, get_settings=lambda: settings)
    app.include_router(
        create_data_router(
            lambda: settings,
            get_auth=auth_dependency_factory(lambda: settings),
        )
    )
    client = TestClient(app)
    r = client.post(
        "/api/data/metadata",
        headers={
            "Authorization": f"Bearer {settings.api_admin_token}",
            "Origin": "https://evil.example",
        },
        json={"table_name": "t"},
    )
    assert r.status_code == 403


def test_local_mode_rejects_forged_proxy_header() -> None:
    settings = Settings(
        database_url="postgresql://x:x@localhost/x",
        security_mode="local",
        trust_proxy_headers=False,
    )
    app = FastAPI()
    app.add_middleware(SecurityGateMiddleware, get_settings=lambda: settings)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    client = TestClient(app)
    r = client.get("/api/health", headers={"X-Forwarded-For": "8.8.8.8"})
    assert r.status_code == 200


def test_security_headers_present() -> None:
    settings = Settings(database_url="postgresql://x:x@localhost/x")
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(SecurityGateMiddleware, get_settings=lambda: settings)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    client = TestClient(app)
    r = client.get("/api/health")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert "Content-Security-Policy" in r.headers
