"""GeoServer KorDB catalog listing (temp_* flood / layers timeout)."""

from __future__ import annotations

from txt2sql.config import Settings
from txt2sql.map.geoserver import GeoServerClient


def _client() -> GeoServerClient:
    return GeoServerClient(
        Settings(
            database_url="postgresql://x:x@localhost/x",
            geoserver_url="http://geoserver.test/geoserver",
            geoserver_workspace="korDB",
            geoserver_datastore="KoreaDB",
        )
    )


def test_catalog_layers_uses_featuretypes_and_skips_temp(monkeypatch) -> None:
    client = _client()
    calls: list[str] = []

    def fake_request(method: str, url: str, json_body=None, timeout=None):
        calls.append(url)
        assert "featuretypes.json" in url
        assert "layers.json" not in url
        body = (
            b'{"featureTypes":{"featureType":['
            b'{"name":"AL_D010_26_20250704"},'
            b'{"name":"temp_abc123"},'
            b'{"name":"BND_ADM_DONG_PG"}'
            b"]}}"
        )
        return 200, body

    monkeypatch.setattr(client, "_request", fake_request)
    layers = client.catalog_layers()
    names = [item["name"] for item in layers]
    assert names == ["AL_D010_26_20250704", "BND_ADM_DONG_PG"]
    assert all(item["extent"] == [] for item in layers)
    assert not any("layers.json" in u for u in calls)


def test_list_workspace_layers_falls_back_when_featuretypes_empty(
    monkeypatch,
) -> None:
    client = _client()

    def fake_request(method: str, url: str, json_body=None, timeout=None):
        if "featuretypes.json" in url:
            return 404, b""
        if "layers.json" in url:
            return 200, b'{"layers":{"layer":[{"name":"AL_D060_00_20250804"}]}}'
        return 404, b""

    monkeypatch.setattr(client, "_request", fake_request)
    assert client.list_workspace_layers() == ["AL_D060_00_20250804"]


def test_featuretype_exists_checks_single_resource(monkeypatch) -> None:
    client = _client()

    def fake_request(method: str, url: str, json_body=None, timeout=None):
        assert "featuretypes/AL_D010_26_20250704.json" in url
        return 200, b"{}"

    monkeypatch.setattr(client, "_request", fake_request)
    assert client.featuretype_exists("AL_D010_26_20250704") is True
