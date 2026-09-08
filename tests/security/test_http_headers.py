from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from txt2sql.config import Settings
from txt2sql.security.deploy import SecurityGateMiddleware
from txt2sql.security.headers import (
    SecurityHeadersMiddleware,
    build_content_security_policy,
    geoserver_csp_origin,
)


def test_http_security_headers() -> None:
    settings = Settings(database_url="postgresql://x:x@localhost/x")
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(SecurityGateMiddleware, get_settings=lambda: settings)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_geoserver_csp_origin_from_url() -> None:
    assert geoserver_csp_origin("http://192.168.0.19:8080/geoserver") == (
        "http://192.168.0.19:8080"
    )
    assert geoserver_csp_origin("https://maps.example.com/geoserver/") == (
        "https://maps.example.com"
    )
    assert geoserver_csp_origin("") is None


def test_csp_allows_configured_http_geoserver_for_wms() -> None:
    """http GeoServer WMS는 img-src/connect-src에 origin이 있어야 지도에 보인다."""
    settings = Settings(
        database_url="postgresql://x:x@localhost/x",
        geoserver_url="http://192.168.0.19:8080/geoserver",
    )
    csp = build_content_security_policy(settings)
    assert "http://192.168.0.19:8080" in csp
    assert "img-src" in csp and "http://192.168.0.19:8080" in csp.split("img-src")[1]
    assert "connect-src" in csp and "http://192.168.0.19:8080" in csp.split("connect-src")[1]


def test_middleware_injects_geoserver_origin_into_csp() -> None:
    settings = Settings(
        database_url="postgresql://x:x@localhost/x",
        geoserver_url="http://192.168.0.19:8080/geoserver",
    )
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware, get_settings=lambda: settings)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200
    csp = r.headers["Content-Security-Policy"]
    assert "http://192.168.0.19:8080" in csp
