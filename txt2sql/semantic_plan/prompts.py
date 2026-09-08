"""Semantic Query Plan 생성용 프롬프트. SQL 예시는 넣지 않는다."""

from __future__ import annotations

from typing import Any

from txt2sql.semantic_plan.catalog import catalog_prompt_text

SYSTEM_PROMPT = """You are a semantic query planner for a Korean GIS database.

Your job is NOT to write SQL.
Never output SQL.
Never output physical table names such as AL_D010_26_20250704.
Never output physical column names such as A4, A16, A24.
Never output PostGIS function names such as ST_DWithin.

Convert the Korean user's request into a SemanticQueryPlan JSON object.

Use ONLY the entities, fields, operators, query kinds, and spatial relations
listed in the provided semantic catalog.

If the user's request is ambiguous, do not guess.
Set requires_clarification=true and explain the ambiguity.

If the request cannot be represented by the supported semantic model,
set unsupported_reason.

Normalize common domain aliases:
- 아파트 → usage = 공동주택
- 학교 → usage = 교육연구시설
- 창고 → usage = 창고시설

Bare "면적" without 건축면적/연면적/대지면적 is ambiguous: requires_clarification=true.

Inside/within a dong ("안에", "내부") → spatial_mode=boundary.
Distance from a dong/gu boundary ("N m 이내", "주변") → spatial_relations.within_distance.
Station/POI coordinates are unsupported: requires_clarification=true.

Prefer a `predicate` tree (and/or/not/cmp) for OR/NOT over flat filters alone.
For ratios, always set `ratios` with numerator_predicate; set denominator_predicate when the denominator is not the full population.
When the parent district is known, set place.sido / place.sigungu (and place.code if known).
Use `bins` with edges or width for explicit numeric intervals (prefer bins over width_bucket assumptions).
Use `stages` ONLY for top-N row selection followed by a second aggregate (rank then avg/sum). Do not use stages for ordinary rank/list.

Do not invent fields.
Do not invent units.
Station/POI names without coordinates require clarification; do not guess lon/lat.
Plan joins must use catalog edge_id only (building_in_admin, building_in_basic_zone, building_in_industrial).
Spatial relations: within/covered_by, intersects, touches, buffer, nearest, overlap_ratio. Never emit PostGIS function names.
Do not invent places.

Return JSON only.
"""

_FEW_SHOTS = [
    (
        "해운대구 아파트가 몇 채야?",
        {
            "version": "1.0",
            "query_kind": "count",
            "entity": "building",
            "scope": {
                "place": {"name": "해운대구", "kind": "gu"},
                "spatial_mode": "auto",
            },
            "filters": [
                {"field": "usage", "operator": "eq", "value": "공동주택"}
            ],
            "select": [],
            "aggregations": [],
            "group_by": [],
            "order_by": [],
            "limit": None,
            "spatial_relations": [],
            "requires_clarification": False,
            "ambiguities": [],
            "assumptions": [],
            "unsupported_reason": None,
            "model_confidence": 0.98,
        },
    ),
    (
        "금정구에서 연면적이 가장 큰 건물 5개",
        {
            "version": "1.0",
            "query_kind": "rank",
            "entity": "building",
            "scope": {
                "place": {"name": "금정구", "kind": "gu"},
                "spatial_mode": "auto",
            },
            "filters": [],
            "select": [
                "name",
                "legal_dong",
                "lot_address",
                "gross_floor_area_m2",
            ],
            "aggregations": [],
            "group_by": [],
            "order_by": [
                {
                    "field": "gross_floor_area_m2",
                    "direction": "desc",
                    "nulls": "last",
                }
            ],
            "limit": 5,
            "spatial_relations": [],
            "requires_clarification": False,
            "ambiguities": [],
            "assumptions": [],
            "unsupported_reason": None,
            "model_confidence": 0.98,
        },
    ),
    (
        "해운대구 아파트 중 높이 100m 이상인 건물의 이름과 높이를 보여줘",
        {
            "version": "1.0",
            "query_kind": "list",
            "entity": "building",
            "scope": {
                "place": {"name": "해운대구", "kind": "gu"},
                "spatial_mode": "auto",
            },
            "filters": [
                {"field": "usage", "operator": "eq", "value": "공동주택"},
                {
                    "field": "height_m",
                    "operator": "gte",
                    "value": 100,
                    "unit": "m",
                },
            ],
            "select": ["name", "height_m"],
            "aggregations": [],
            "group_by": [],
            "order_by": [],
            "limit": 100,
            "spatial_relations": [],
            "requires_clarification": False,
            "ambiguities": [],
            "assumptions": [],
            "unsupported_reason": None,
            "model_confidence": 0.95,
        },
    ),
    (
        "연산동 안에 있는 공동주택 중 연면적 상위 10개",
        {
            "version": "1.0",
            "query_kind": "rank",
            "entity": "building",
            "scope": {
                "place": {"name": "연산동", "kind": "admin_dong"},
                "spatial_mode": "boundary",
            },
            "filters": [
                {"field": "usage", "operator": "eq", "value": "공동주택"}
            ],
            "select": ["name", "gross_floor_area_m2", "height_m"],
            "aggregations": [],
            "group_by": [],
            "order_by": [
                {
                    "field": "gross_floor_area_m2",
                    "direction": "desc",
                    "nulls": "last",
                }
            ],
            "limit": 10,
            "spatial_relations": [],
            "requires_clarification": False,
            "ambiguities": [],
            "assumptions": [],
            "unsupported_reason": None,
            "model_confidence": 0.96,
        },
    ),
    (
        "구서동 주변 500m 이내에 있는 공동주택",
        {
            "version": "1.0",
            "query_kind": "list",
            "entity": "building",
            "scope": {
                "place": {"name": "구서동", "kind": "legal_dong"},
                "spatial_mode": "auto",
            },
            "filters": [
                {"field": "usage", "operator": "eq", "value": "공동주택"}
            ],
            "select": ["name", "legal_dong", "lot_address"],
            "aggregations": [],
            "group_by": [],
            "order_by": [],
            "limit": 100,
            "spatial_relations": [
                {
                    "relation": "within_distance",
                    "target": {
                        "entity": None,
                        "place": {"name": "구서동", "kind": "legal_dong"},
                        "longitude": None,
                        "latitude": None,
                    },
                    "distance_m": 500,
                }
            ],
            "requires_clarification": False,
            "ambiguities": [],
            "assumptions": [],
            "unsupported_reason": None,
            "model_confidence": 0.94,
        },
    ),
    (
        "해운대구에서 철근콘크리트 또는 철골구조인 건물 수",
        {
            "version": "1.1",
            "query_kind": "count",
            "entity": "building",
            "scope": {
                "place": {"name": "해운대구", "kind": "gu", "sigungu": None, "sido": "부산광역시"},
                "spatial_mode": "auto",
            },
            "filters": [],
            "predicate": {
                "op": "or",
                "args": [
                    {
                        "op": "cmp",
                        "operator": "contains",
                        "left": {"kind": "field", "field": "structure"},
                        "right": {"kind": "literal", "value": "철근콘크리트"},
                    },
                    {
                        "op": "cmp",
                        "operator": "contains",
                        "left": {"kind": "field", "field": "structure"},
                        "right": {"kind": "literal", "value": "철골"},
                    },
                ],
            },
            "select": [],
            "aggregations": [],
            "group_by": [],
            "order_by": [],
            "limit": None,
            "spatial_relations": [],
            "requires_clarification": False,
            "ambiguities": [],
            "assumptions": [],
            "unsupported_reason": None,
            "model_confidence": 0.96,
        },
    ),
    (
        "영도구 15층 이상 건물 중 공동주택 비율",
        {
            "version": "1.1",
            "query_kind": "aggregate",
            "entity": "building",
            "scope": {
                "place": {"name": "영도구", "kind": "gu"},
                "spatial_mode": "auto",
            },
            "filters": [
                {"field": "ground_floors", "operator": "gte", "value": 15}
            ],
            "ratios": [
                {
                    "numerator_predicate": {
                        "op": "cmp",
                        "operator": "eq",
                        "left": {"kind": "field", "field": "usage"},
                        "right": {"kind": "literal", "value": "공동주택"},
                    },
                    "denominator_predicate": None,
                    "multiplier": 100.0,
                    "alias": "ratio_pct",
                }
            ],
            "select": [],
            "aggregations": [],
            "group_by": [],
            "order_by": [],
            "limit": None,
            "spatial_relations": [],
            "requires_clarification": False,
            "ambiguities": [],
            "assumptions": [],
            "unsupported_reason": None,
            "model_confidence": 0.95,
        },
    ),
    (
        "금정구 건물을 연면적 0·100·200㎡ 구간으로 나눠 건수",
        {
            "version": "1.1",
            "query_kind": "aggregate",
            "entity": "building",
            "scope": {
                "place": {"name": "금정구", "kind": "gu"},
                "spatial_mode": "auto",
            },
            "filters": [],
            "bins": [
                {
                    "field": "gross_floor_area_m2",
                    "edges": [0, 100, 200],
                    "width": None,
                    "labels": ["0_100", "100_200", "200_inf"],
                }
            ],
            "group_by": ["gross_floor_area_m2"],
            "aggregations": [
                {"function": "count", "field": None, "alias": "n"}
            ],
            "select": [],
            "order_by": [
                {
                    "field": "gross_floor_area_m2",
                    "direction": "asc",
                    "nulls": "last",
                }
            ],
            "limit": None,
            "spatial_relations": [],
            "requires_clarification": False,
            "ambiguities": [],
            "assumptions": [],
            "unsupported_reason": None,
            "model_confidence": 0.93,
        },
    ),
    (
        "연제구 연산동 건물 중 연면적 상위 10개의 평균 높이",
        {
            "version": "1.1",
            "query_kind": "aggregate",
            "entity": "building",
            "scope": {
                "place": {
                    "name": "연산동",
                    "kind": "legal_dong",
                    "sido": "부산광역시",
                    "sigungu": "연제구",
                },
                "spatial_mode": "auto",
            },
            "filters": [],
            "stages": [
                {
                    "id": "rank0",
                    "kind": "rank",
                    "select": [
                        "name",
                        "legal_dong",
                        "lot_address",
                        "gross_floor_area_m2",
                        "height_m",
                    ],
                    "order_by": [
                        {
                            "field": "gross_floor_area_m2",
                            "direction": "desc",
                            "nulls": "last",
                        }
                    ],
                    "limit": 10,
                    "aggregations": [],
                    "group_by": [],
                    "filters": [],
                },
                {
                    "id": "agg1",
                    "kind": "aggregate",
                    "aggregations": [
                        {
                            "function": "avg",
                            "field": "height_m",
                            "alias": "avg_height_m",
                        }
                    ],
                    "select": [],
                    "group_by": [],
                    "filters": [],
                    "order_by": [],
                    "limit": None,
                },
            ],
            "select": [],
            "aggregations": [
                {
                    "function": "avg",
                    "field": "height_m",
                    "alias": "avg_height_m",
                }
            ],
            "group_by": [],
            "order_by": [],
            "limit": None,
            "spatial_relations": [],
            "requires_clarification": False,
            "ambiguities": [],
            "assumptions": [],
            "unsupported_reason": None,
            "model_confidence": 0.94,
        },
    ),
]


def build_messages(
    question: str,
    *,
    hints: dict[str, Any] | None = None,
    extra_examples: list[str] | None = None,
) -> list[dict[str, str]]:
    import json

    shots = []
    for q, plan in _FEW_SHOTS:
        shots.append(f"User: {q}\nPlan:\n{json.dumps(plan, ensure_ascii=False, indent=2)}")
    if extra_examples:
        for blob in extra_examples:
            shots.append(f"Signature example Plan:\n{blob}")
    user = [
        catalog_prompt_text(entity="building"),
        "",
        "Examples:",
        "\n\n".join(shots),
        "",
        f"User question:\n{question.strip()}",
    ]
    if hints:
        user.extend(
            [
                "",
                "Deterministic hints (verify, do not blindly copy):",
                json.dumps({"resolved_hints": hints}, ensure_ascii=False, indent=2),
            ]
        )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(user)},
    ]
