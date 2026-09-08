"""canonical semantic field → 물리 테이블/컬럼 allowlist.

Physical column letters for D010/D198 MUST come from
`txt2sql.canonical_physical_columns` — do not hardcode divergent maps here.
"""

from __future__ import annotations

from dataclasses import dataclass

from txt2sql.canonical_physical_columns import D010_FIELD_COLUMNS, D198_FIELD_COLUMNS
from txt2sql.dataset_tables import (
    DEFAULT_BASIC_ZONE_TABLE,
    DEFAULT_BUILDING_TABLE,
    basic_zone_coverage_map,
    building_coverage_map,
    resolve_basic_zone_table,
    resolve_building_table,
)
from txt2sql.domain import D198_TABLES
from txt2sql.semantic_plan.models import UnknownSemanticFieldError

# 카탈로그 필드 정의·allowlist 기본값. SQL 생성은 resolve_*() 를 쓴다.
BUILDING_TABLE = DEFAULT_BUILDING_TABLE
ADMIN_TABLE = "BND_ADM_DONG_PG"
BASIC_ZONE_TABLE = DEFAULT_BASIC_ZONE_TABLE
INDUSTRIAL_TABLE = "AL_D060_00_20250804"

_TEXT_OPS = ("eq", "neq", "contains", "in", "not_in", "is_null", "is_not_null")
_NUM_OPS = ("eq", "neq", "gt", "gte", "lt", "lte", "between", "in", "not_in", "is_null", "is_not_null")
_DATE_OPS = ("eq", "neq", "gt", "gte", "lt", "lte", "between", "is_null", "is_not_null")

_ENTITY_ALIASES = {
    "building": "building",
    "admin_area": "admin_area",
    "basic_zone": "basic_zone",
    "industrial_complex": "industrial_complex",
}
_FIELD_ALIASES = {
    "height_m": "height_m",
    "gross_floor_area_m2": "gross_floor_area_m2",
    "building_area_m2": "building_area_m2",
    "site_area_m2": "site_area_m2",
    "ground_floors": "ground_floors",
    "legal_dong": "legal_dong",
    "bjd_cd": "bjd_cd",
    "lot_address": "lot_address",
    "building_coverage_ratio": "building_coverage_ratio",
    "floor_area_ratio": "floor_area_ratio",
    "violation_status": "violation_status",
    "building_dong_name": "building_dong_name",
    "special_land": "special_land",
    "approval_date": "approval_date",
    "permit_date": "permit_date",
    "building_age_years": "building_age_years",
    "detail_usage": "detail_usage",
    "usage_class": "usage_class",
    "basement_floors": "basement_floors",
}


@dataclass(frozen=True)
class SemanticField:
    key: str
    entity: str
    table: str
    column: str
    data_type: str
    unit: str | None = None
    label: str = ""
    allowed_ops: tuple[str, ...] = ()
    sortable: bool = True
    groupable: bool = True
    aggregatable: bool = True


@dataclass(frozen=True)
class SemanticEntity:
    key: str
    default_table: str
    geometry_column: str | None
    id_field: str | None
    label: str


def _text(
    key: str,
    entity: str,
    table: str,
    column: str,
    label: str,
    *,
    groupable: bool = True,
) -> SemanticField:
    return SemanticField(
        key=key,
        entity=entity,
        table=table,
        column=column,
        data_type="text",
        label=label,
        allowed_ops=_TEXT_OPS,
        sortable=True,
        groupable=groupable,
        aggregatable=False,
    )


def _date(
    key: str,
    entity: str,
    table: str,
    column: str,
    label: str,
) -> SemanticField:
    return SemanticField(
        key=key,
        entity=entity,
        table=table,
        column=column,
        data_type="text",
        label=label,
        allowed_ops=_DATE_OPS,
        sortable=True,
        groupable=True,
        aggregatable=False,
    )


def _num(
    key: str,
    entity: str,
    table: str,
    column: str,
    label: str,
    unit: str,
) -> SemanticField:
    return SemanticField(
        key=key,
        entity=entity,
        table=table,
        column=column,
        data_type="number",
        unit=unit,
        label=label,
        allowed_ops=_NUM_OPS,
        sortable=True,
        groupable=True,
        aggregatable=True,
    )


def _d010(key: str) -> str:
    return D010_FIELD_COLUMNS[key]


ENTITIES: dict[str, SemanticEntity] = {
    "building": SemanticEntity(
        key="building",
        default_table=BUILDING_TABLE,
        geometry_column="geometry",
        id_field="id",
        label="건물",
    ),
    "admin_area": SemanticEntity(
        key="admin_area",
        default_table=ADMIN_TABLE,
        geometry_column="geometry",
        id_field="code",
        label="행정구역",
    ),
    "basic_zone": SemanticEntity(
        key="basic_zone",
        default_table=BASIC_ZONE_TABLE,
        geometry_column="geometry",
        id_field="id",
        label="기초구역",
    ),
    "industrial_complex": SemanticEntity(
        key="industrial_complex",
        default_table=INDUSTRIAL_TABLE,
        geometry_column="geometry",
        id_field="id",
        label="산업단지",
    ),
}

BUILDING_FIELDS: dict[str, SemanticField] = {
    "id": _text("id", "building", BUILDING_TABLE, _d010("id"), "건물 식별자", groupable=False),
    "name": _text("name", "building", BUILDING_TABLE, _d010("name"), "건물명"),
    "bjd_cd": _text("bjd_cd", "building", BUILDING_TABLE, _d010("bjd_cd"), "법정동코드"),
    "legal_dong": _text("legal_dong", "building", BUILDING_TABLE, _d010("legal_dong"), "법정동명"),
    "lot_address": _text(
        "lot_address", "building", BUILDING_TABLE, _d010("lot_address"), "지번"
    ),
    "usage": _text("usage", "building", BUILDING_TABLE, _d010("usage"), "용도"),
    "structure": _text("structure", "building", BUILDING_TABLE, _d010("structure"), "구조"),
    "building_area_m2": _num(
        "building_area_m2",
        "building",
        BUILDING_TABLE,
        _d010("building_area_m2"),
        "건축면적",
        "m2",
    ),
    "gross_floor_area_m2": _num(
        "gross_floor_area_m2",
        "building",
        BUILDING_TABLE,
        _d010("gross_floor_area_m2"),
        "연면적",
        "m2",
    ),
    "site_area_m2": _num(
        "site_area_m2", "building", BUILDING_TABLE, _d010("site_area_m2"), "대지면적", "m2"
    ),
    "height_m": _num("height_m", "building", BUILDING_TABLE, _d010("height_m"), "높이", "m"),
    "ground_floors": _num(
        "ground_floors",
        "building",
        BUILDING_TABLE,
        _d010("ground_floors"),
        "지상층수",
        "floor",
    ),
    "basement_floors": _num(
        "basement_floors",
        "building",
        BUILDING_TABLE,
        _d010("basement_floors"),
        "지하층수",
        "floor",
    ),
    "building_coverage_ratio": _num(
        "building_coverage_ratio",
        "building",
        BUILDING_TABLE,
        _d010("building_coverage_ratio"),
        "건폐율",
        "%",
    ),
    "floor_area_ratio": _num(
        "floor_area_ratio",
        "building",
        BUILDING_TABLE,
        _d010("floor_area_ratio"),
        "용적율",
        "%",
    ),
    "violation_status": _text(
        "violation_status",
        "building",
        BUILDING_TABLE,
        _d010("violation_status"),
        "위반건축물여부",
    ),
    "building_dong_name": _text(
        "building_dong_name",
        "building",
        BUILDING_TABLE,
        _d010("building_dong_name"),
        "건물동명",
    ),
    "sigungu_name": _text(
        "sigungu_name", "building", BUILDING_TABLE, _d010("sigungu_name"), "시군구명"
    ),
    "special_land": _text(
        "special_land", "building", BUILDING_TABLE, _d010("special_land"), "특수지구분명"
    ),
    "approval_date": _date(
        "approval_date",
        "building",
        BUILDING_TABLE,
        _d010("approval_date"),
        "사용승인일자",
    ),
    "permit_date": _date(
        "permit_date",
        "building",
        BUILDING_TABLE,
        _d010("permit_date"),
        "허가일자",
    ),
    "building_age_years": _num(
        "building_age_years",
        "building",
        BUILDING_TABLE,
        _d010("building_age_years"),
        "건축경과년수",
        "year",
    ),
    # D198-only slots: column letters are D198's. Compiler requires D198 when used
    # so these never resolve against D010 (A27 on D010 is basement_floors).
    "detail_usage": _text(
        "detail_usage",
        "building",
        BUILDING_TABLE,
        D198_FIELD_COLUMNS["detail_usage"],
        "세부용도",
    ),
    "usage_class": _text(
        "usage_class",
        "building",
        BUILDING_TABLE,
        D198_FIELD_COLUMNS["usage_class"],
        "용도분류",
    ),
    "ledger_kind": _text(
        "ledger_kind",
        "building",
        BUILDING_TABLE,
        D198_FIELD_COLUMNS["ledger_kind"],
        "대장종류",
    ),
    "complex_building_kind": _text(
        "complex_building_kind",
        "building",
        BUILDING_TABLE,
        D198_FIELD_COLUMNS["complex_building_kind"],
        "집합건물구분",
    ),
    "geometry": SemanticField(
        key="geometry",
        entity="building",
        table=BUILDING_TABLE,
        column="geometry",
        data_type="geometry",
        label="건물 공간정보",
        allowed_ops=(),
        sortable=False,
        groupable=False,
        aggregatable=False,
    ),
}

ADMIN_FIELDS: dict[str, SemanticField] = {
    "code": _text("code", "admin_area", ADMIN_TABLE, "ADM_CD", "행정구역코드"),
    "name": _text("name", "admin_area", ADMIN_TABLE, "ADM_NM", "행정동명"),
    "geometry": SemanticField(
        key="geometry",
        entity="admin_area",
        table=ADMIN_TABLE,
        column="geometry",
        data_type="geometry",
        label="행정구역 경계",
        allowed_ops=(),
        sortable=False,
        groupable=False,
        aggregatable=False,
    ),
}

INDUSTRIAL_FIELDS: dict[str, SemanticField] = {
    "id": _text("id", "industrial_complex", INDUSTRIAL_TABLE, "A0", "산업단지 식별자", groupable=False),
    "name": _text("name", "industrial_complex", INDUSTRIAL_TABLE, "A8", "산업단지명"),
    "alt_name": _text("alt_name", "industrial_complex", INDUSTRIAL_TABLE, "A9", "산업단지 별칭"),
    "type": _text("type", "industrial_complex", INDUSTRIAL_TABLE, "A6", "산업단지 유형"),
    "area_m2": _num(
        "area_m2",
        "industrial_complex",
        INDUSTRIAL_TABLE,
        "geometry",
        "산업단지면적",
        "m2",
    ),
    "geometry": SemanticField(
        key="geometry",
        entity="industrial_complex",
        table=INDUSTRIAL_TABLE,
        column="geometry",
        data_type="geometry",
        label="산업단지 공간정보",
        allowed_ops=(),
        sortable=False,
        groupable=False,
        aggregatable=False,
    ),
}

BASIC_ZONE_FIELDS: dict[str, SemanticField] = {
    "id": _text("id", "basic_zone", BASIC_ZONE_TABLE, "BAS_ID", "기초구역ID"),
    "area_m2": _num(
        "area_m2", "basic_zone", BASIC_ZONE_TABLE, "BAS_AR", "기초구역면적", "m2"
    ),
    "gu_name": _text("gu_name", "basic_zone", BASIC_ZONE_TABLE, "SIG_KOR_NM", "시군구명"),
    "move_reason": _text(
        "move_reason", "basic_zone", BASIC_ZONE_TABLE, "MVMN_RESN", "이동사유"
    ),
    "geometry": SemanticField(
        key="geometry",
        entity="basic_zone",
        table=BASIC_ZONE_TABLE,
        column="geometry",
        data_type="geometry",
        label="기초구역 공간정보",
        allowed_ops=(),
        sortable=False,
        groupable=False,
        aggregatable=False,
    ),
}

FIELDS_BY_ENTITY: dict[str, dict[str, SemanticField]] = {
    "building": BUILDING_FIELDS,
    "admin_area": ADMIN_FIELDS,
    "basic_zone": BASIC_ZONE_FIELDS,
    "industrial_complex": INDUSTRIAL_FIELDS,
}

ALLOWED_TABLES = frozenset(
    {BUILDING_TABLE, ADMIN_TABLE, BASIC_ZONE_TABLE, INDUSTRIAL_TABLE, *D198_TABLES}
)
ALLOWED_COLUMNS = frozenset(
    {field.column for fields in FIELDS_BY_ENTITY.values() for field in fields.values()}
    | {
        "A1",  # D198 GIS건물통합식별번호 (id override)
        "A3",
        "A7",
        "A8",
        "A13",
        "A19",
        "A21",
        "A23",
        "A25",
        "A29",
        "A30",
        "A31",
        "A32",
        "A33",
        "A34",
    }
)


def is_allowed_physical_identifier(name: str) -> bool:
    """현재 카탈로그와 런타임 D198/건물·기초구역 커버리지에 등록된 식별자인지 확인한다."""
    if name in ALLOWED_COLUMNS or name in {
        BUILDING_TABLE,
        ADMIN_TABLE,
        BASIC_ZONE_TABLE,
        INDUSTRIAL_TABLE,
        resolve_building_table(),
        resolve_basic_zone_table(),
    }:
        return True
    if name in building_coverage_map().values():
        return True
    if name in basic_zone_coverage_map().values():
        return True
    # D198_TABLES는 DB 커버리지 검색 후 제자리 갱신되므로 정적 frozenset보다
    # 런타임 목록을 기준으로 최신 테이블 버전을 검증해야 한다.
    return name in D198_TABLES


CANONICAL_ALIASES = frozenset(
    {
        "count",
        "n",
        *BUILDING_FIELDS,
        *ADMIN_FIELDS,
        *BASIC_ZONE_FIELDS,
        *INDUSTRIAL_FIELDS,
        *_FIELD_ALIASES,
    }
)

DEFAULT_LIST_SELECT = (
    "name",
    "legal_dong",
    "lot_address",
    "usage",
    "height_m",
    "gross_floor_area_m2",
    "ground_floors",
)

AREA_FIELD_KEYS = frozenset(
    {"building_area_m2", "gross_floor_area_m2", "site_area_m2"}
)


def get_entity(key: str) -> SemanticEntity:
    resolved = _ENTITY_ALIASES.get(key, key)
    entity = ENTITIES.get(resolved)
    if entity is None:
        raise UnknownSemanticFieldError(f"unknown entity: {key}")
    return entity


def get_field(entity: str, key: str) -> SemanticField:
    resolved_entity = _ENTITY_ALIASES.get(entity, entity)
    fields = FIELDS_BY_ENTITY.get(resolved_entity)
    if fields is None:
        raise UnknownSemanticFieldError(f"unknown entity: {entity}")
    resolved_key = _FIELD_ALIASES.get(key, key)
    field = fields.get(resolved_key)
    if field is None:
        raise UnknownSemanticFieldError(f"unknown field: {entity}.{key}")
    return field


get_field = get_field
get_entity = get_entity


def catalog_prompt_text(*, entity: str = "building") -> str:
    """LLM prompt에 넣을 canonical catalog. 물리 컬럼명은 넣지 않는다."""
    fields = FIELDS_BY_ENTITY.get(entity) or BUILDING_FIELDS
    lines = [f"entity: {entity}", "fields:"]
    for key, field in fields.items():
        if key == "geometry":
            continue
        unit = f" {field.unit}" if field.unit else ""
        lines.append(f"- {key} : {field.label}{unit}")
    lines.extend(
        [
            "",
            "supported operators: eq, neq, gt, gte, lt, lte, contains, in, not_in, between",
            "supported spatial relations: covered_by, within, intersects, touches, buffer, nearest, overlap_ratio, within_distance, outside_distance",
            "supported query_kind: count, list, rank, aggregate, distribution",
            "spatial_mode auto: 구/법정동은 주소 속성, 행정전용 동은 경계",
            "spatial_mode boundary: 행정 경계 containment (안에/내부)",
            "within_distance: 동·구 경계로부터 미터 거리. 역·POI 좌표는 불가",
            "건축면적 building_area_m2 / 연면적 gross_floor_area_m2 / 대지면적 site_area_m2 는 서로 다름",
            "층수 → ground_floors",
        ]
    )
    return "\n".join(lines)
