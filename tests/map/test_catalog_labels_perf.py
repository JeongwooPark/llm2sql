"""KorDB catalog labeling must not scan all temp_* geometry tables."""

from __future__ import annotations

from txt2sql.config import Settings
from txt2sql.map.labels import _catalog_estimated_extents, label_catalog_layers


def test_catalog_estimated_extents_requires_names() -> None:
    settings = Settings(database_url="postgresql://x:x@localhost/x")
    assert _catalog_estimated_extents(settings) == {}
    assert _catalog_estimated_extents(settings, names=["temp_abc"]) == {}


def test_label_catalog_layers_scopes_extent_lookup(monkeypatch) -> None:
    settings = Settings(database_url="postgresql://x:x@localhost/x")
    seen: dict[str, object] = {}

    class FakeIndex:
        @classmethod
        def load(cls, _settings):
            return cls()

        def table_title(self, name: str) -> str:
            return name

        def resolve_table(self, name: str) -> str:
            return name

        def fields_for(self, name: str) -> dict[str, str]:
            return {}

    def fake_geom_types(_settings, *, names=None):
        seen["geom_names"] = names
        return {"AL_D010_26_20250704": "MULTIPOLYGON"}

    def fake_extents(_settings, *, names=None):
        seen["extent_names"] = names
        return {"AL_D010_26_20250704": [129.0, 35.0, 129.2, 35.2]}

    monkeypatch.setattr("txt2sql.map.labels.MetaIndex", FakeIndex)
    monkeypatch.setattr("txt2sql.map.labels._catalog_geom_types", fake_geom_types)
    monkeypatch.setattr("txt2sql.map.labels._catalog_estimated_extents", fake_extents)

    layers = label_catalog_layers(
        settings,
        [
            {
                "name": "AL_D010_26_20250704",
                "qualified": "korDB:AL_D010_26_20250704",
                "wms_url": "http://x/wms",
                "wfs_url": "http://x/wfs",
                "extent": [],
            }
        ],
    )
    assert seen["geom_names"] == ["AL_D010_26_20250704"]
    assert seen["extent_names"] == ["AL_D010_26_20250704"]
    assert len(layers[0]["extent"]) == 4
    assert layers[0]["extent"][0] <= 129.0
    assert layers[0]["extent"][2] >= 129.2
