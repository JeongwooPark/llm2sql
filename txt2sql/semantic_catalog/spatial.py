"""Spatial operator → PostGIS compiler mapping (Phase 8)."""

from __future__ import annotations

from typing import Literal

SpatialOperator = Literal[
    "WITHIN",
    "CONTAINS",
    "INTERSECTS",
    "TOUCHES",
    "OVERLAPS",
    "CROSSES",
    "DWITHIN",
    "OUTSIDE_DISTANCE",
    "NEAREST",
]

POSTGIS_OPERATOR_MAP: dict[str, str] = {
    "WITHIN": "ST_Within",
    "CONTAINS": "ST_Contains",
    "INTERSECTS": "ST_Intersects",
    "TOUCHES": "ST_Touches",
    "OVERLAPS": "ST_Overlaps",
    "CROSSES": "ST_Crosses",
    "DWITHIN": "ST_DWithin",
    "OUTSIDE_DISTANCE": "NOT ST_DWithin",
    "NEAREST": "ST_Distance",
}


def postgis_function(operator: str) -> str | None:
    """Return PostGIS function name for semantic spatial operator."""
    return POSTGIS_OPERATOR_MAP.get(operator.upper())


def datasets_with_spatial() -> list[str]:
    """Dataset ids that expose geometry for spatial joins."""
    from txt2sql.semantic_catalog.datasets import DATASETS

    return sorted(
        ds_id for ds_id, ds in DATASETS.items() if getattr(ds, "supports_spatial", False)
    )


def spatial_supported(dataset_id: str) -> bool:
    from txt2sql.semantic_catalog.datasets import DATASETS

    ds = DATASETS.get(dataset_id)
    return bool(ds and getattr(ds, "supports_spatial", False))


def compile_distance_predicate(
    operator: str,
    *,
    geom_a: str,
    geom_b: str,
    distance_m: float,
    srid: int = 5186,
) -> str:
    """Compile distance-based spatial predicate."""
    op = operator.upper()
    if op == "DWITHIN":
        fn = postgis_function("DWITHIN")
        return f"{fn}({geom_a}, {geom_b}, {distance_m})"
    if op == "OUTSIDE_DISTANCE":
        fn = postgis_function("DWITHIN")
        return f"NOT {fn}({geom_a}, {geom_b}, {distance_m})"
    if op == "NEAREST":
        return f"ST_Distance({geom_a}, {geom_b})"
    fn = postgis_function(op)
    if fn:
        return f"{fn}({geom_a}, {geom_b})"
    raise ValueError(f"unsupported spatial operator: {operator}")
