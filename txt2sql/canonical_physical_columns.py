"""Canonical physical column maps shared by Binding and Compiler.

Single source of truth for concept/field → physical column on D010 / D198.
Derived from docs/kordb_catalog.json (AL_D010 / AL_D198). Do not redefine
these mappings in semantic_plan.catalog or datasets.CONCEPT_PHYSICAL_FIELDS.

Lives at txt2sql top-level (not under semantic_catalog) to avoid circular
imports with semantic_plan.catalog ↔ semantic_catalog.registry.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# D010 (AL_D010_*) — GIS 건물통합정보
# ---------------------------------------------------------------------------
# Keyed by SQP semantic field name (compiler allowlist keys).
D010_FIELD_COLUMNS: dict[str, str] = {
    "id": "A0",
    "bjd_cd": "A3",
    "sigungu_name": "A3",
    "legal_dong": "A4",
    "lot_address": "A5",
    "special_land": "A7",
    "usage": "A9",
    "structure": "A11",
    "building_area_m2": "A12",
    "approval_date": "A13",
    # permit_date is not a reliable D010 slot; prefer D198 A33.
    "permit_date": "A13",
    "gross_floor_area_m2": "A14",
    "site_area_m2": "A15",
    "height_m": "A16",
    "building_coverage_ratio": "A17",
    "floor_area_ratio": "A18",
    "violation_status": "A20",
    "name": "A24",
    "building_dong_name": "A25",
    "ground_floors": "A26",
    "basement_floors": "A27",
    # building_age_years is derived from approval_date year on D010 (sparse).
    "building_age_years": "A13",
}

# ---------------------------------------------------------------------------
# D198 (AL_D198_*) — 용도별건물 (구 단위). Column numbers differ from D010.
# ---------------------------------------------------------------------------
D198_FIELD_COLUMNS: dict[str, str] = {
    "id": "A1",
    "name": "A13",
    "legal_dong": "A4",
    "lot_address": "A7",
    "usage": "A25",
    "structure": "A23",
    "building_area_m2": "A18",
    "gross_floor_area_m2": "A19",
    "site_area_m2": "A17",
    "height_m": "A30",
    "ground_floors": "A31",
    "basement_floors": "A32",
    "building_coverage_ratio": "A21",
    "floor_area_ratio": "A20",
    "building_dong_name": "A14",
    "special_land": "A6",
    "approval_date": "A34",
    "permit_date": "A33",
    "detail_usage": "A27",
    "usage_class": "A29",
    # 집합건물구분 (D198 A10). ledger_kind(A12 대장종류)와 구분.
    "complex_building_kind": "A10",
    "ledger_kind": "A12",
    "building_age_years": "A34",
}

# Concept key → (dataset_id → physical column). Binding uses this exclusively.
# detail_usage / permit_date / usage_class / ledger_kind: D198 only.
CONCEPT_PHYSICAL_FIELDS: dict[str, dict[str, str]] = {
    "building.height": {"building_gis_d010": D010_FIELD_COLUMNS["height_m"]},
    "building.usage": {
        "building_gis_d010": D010_FIELD_COLUMNS["usage"],
        "building_attr_d198": D198_FIELD_COLUMNS["usage"],
    },
    "building.detail_usage": {
        "building_attr_d198": D198_FIELD_COLUMNS["detail_usage"],
    },
    "building.structure": {
        "building_gis_d010": D010_FIELD_COLUMNS["structure"],
        "building_attr_d198": D198_FIELD_COLUMNS["structure"],
    },
    "building.approval_date": {
        "building_gis_d010": D010_FIELD_COLUMNS["approval_date"],
        "building_attr_d198": D198_FIELD_COLUMNS["approval_date"],
    },
    "building.permit_date": {
        "building_attr_d198": D198_FIELD_COLUMNS["permit_date"],
    },
    "building.floor_area_ratio": {
        "building_gis_d010": D010_FIELD_COLUMNS["floor_area_ratio"],
    },
    "building.building_coverage_ratio": {
        "building_gis_d010": D010_FIELD_COLUMNS["building_coverage_ratio"],
    },
    "building.gross_floor_area": {
        "building_gis_d010": D010_FIELD_COLUMNS["gross_floor_area_m2"],
        "building_attr_d198": D198_FIELD_COLUMNS["gross_floor_area_m2"],
    },
    "building.ground_floors": {
        "building_gis_d010": D010_FIELD_COLUMNS["ground_floors"],
        "building_attr_d198": D198_FIELD_COLUMNS["ground_floors"],
    },
    "building.basement_floors": {
        "building_gis_d010": D010_FIELD_COLUMNS["basement_floors"],
    },
    "building.age": {
        "building_gis_d010": D010_FIELD_COLUMNS["building_age_years"],
        "building_attr_d198": D198_FIELD_COLUMNS["building_age_years"],
    },
    "admin.sigungu": {
        "building_gis_d010": D010_FIELD_COLUMNS["sigungu_name"],
        "admin_boundary": "SIGUNGU_NM",
    },
    "admin.legal_dong": {
        "building_gis_d010": D010_FIELD_COLUMNS["legal_dong"],
        "admin_boundary": "DONG_NM",
    },
}

# Concept → default SQP field key (for cross-checks).
CONCEPT_TO_FIELD: dict[str, str] = {
    "building.height": "height_m",
    "building.usage": "usage",
    "building.detail_usage": "detail_usage",
    "building.structure": "structure",
    "building.approval_date": "approval_date",
    "building.permit_date": "permit_date",
    "building.floor_area_ratio": "floor_area_ratio",
    "building.building_coverage_ratio": "building_coverage_ratio",
    "building.gross_floor_area": "gross_floor_area_m2",
    "building.ground_floors": "ground_floors",
    "building.basement_floors": "basement_floors",
    "building.age": "building_age_years",
    "admin.sigungu": "sigungu_name",
    "admin.legal_dong": "legal_dong",
}
