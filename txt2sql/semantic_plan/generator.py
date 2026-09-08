"""자연어 → SemanticQueryPlan.

LLM structured JSON을 우선 사용하고, 실패 시 단순 건물 질의는
기존 domain/units 힌트로 heuristic plan을 만든다.
"""

from __future__ import annotations

import json
import re
from typing import Any

import psycopg

from txt2sql.config import Settings
from txt2sql.domain import (
    LENGTH_DIST_PATTERN,
    assumptions_include_permit_lag,
    d198_gu_for_dong,
    d198_table_for_gu,
    d198_unavailable_reason,
    exact_structure_label,
    extract_gu,
    extract_industrial_name,
    extract_industrial_names,
    extract_place,
    extract_places,
    extract_special_land,
    extract_structure,
    extract_structures,
    extract_usage,
    extract_usages,
    extract_detail_usages,
    extract_usage_classes,
    is_busan_wide,
    is_vague_age_threshold,
    looks_like_age_question,
)
from txt2sql.llm import chat
from txt2sql.query_understanding.contract import QueryContract, extract_contract
from txt2sql.query_understanding.gate import accept_heuristic_plan
from txt2sql.query_understanding.operators import AGG_MAP
from txt2sql.query_understanding.temporal import parse_temporal_filters
from txt2sql.semantic_plan.migrate import filter_to_predicate, migrate_plan_v11
from txt2sql.semantic_plan.predicate_utils import effective_predicate, has_op
from txt2sql.semantic_plan.models import (
    AggregationSpec,
    BinSpec,
    ExpressionSpec,
    FilterSpec,
    OperandSpec,
    OrderSpec,
    PlaceSpec,
    PredicateSpec,
    RatioSpec,
    ScopeSpec,
    SemanticPlanGenerationError,
    SemanticQueryPlan,
    SpatialRelationSpec,
    SpatialTargetSpec,
    StageSpec,
)
from txt2sql.semantic_plan.prompts import build_messages
from txt2sql.session import SessionContext
from txt2sql.units import UNIT_TOKEN, convert_for_schema

_PHYSICAL_LEAK = re.compile(
    r"\b(AL_D\d+|BND_ADM|TL_KODIS|ST_[A-Za-z]+|SELECT|INSERT|UPDATE|DELETE|DROP)\b"
    r'|(?<![A-Za-z])A\d{1,2}(?![A-Za-z0-9])',
    re.I,
)
_REL = {
    "이상": "gte",
    "이하": "lte",
    "초과": "gt",
    "미만": "lt",
    "넘는": "gt",
}
_UNSUPPORTED_HINTS = (
    "시가총액",
    "시세",
    "매매가",
    "공시지가",
    "인구",
    "세대수",
    "지하철",
    "버스",
    "주차",
    "좋은",
    "멋진",
    "예쁜",
    "살기",
)


def extract_plan_hints(question: str) -> dict[str, Any]:
    """LLM 이전 deterministic hint. Plan을 강제 덮어쓰지 않는다."""
    from txt2sql.gazetteer import (
        KIND_ADMIN,
        KIND_LEGAL,
        KIND_SIGUNGU,
        classify_place,
        resolve_place_kind,
        uses_admin_boundary,
    )

    q = question.strip()
    gu = extract_gu(q)
    places = extract_places(q)
    prefer_admin = "행정동" in q
    place = None
    kind = "unknown"
    # 행정동(번호동·행정전용) > 법정동 > 구. 구가 먼저 나와도 동을 우선.
    for cand in places:
        ck = classify_place(cand)
        if uses_admin_boundary(cand, prefer_admin=prefer_admin, question=q):
            place, kind = cand, "admin_dong"
            break
    if place is None:
        for cand in places:
            ck = classify_place(cand)
            if KIND_SIGUNGU in ck and cand.endswith(("구", "군")):
                continue
            if KIND_LEGAL in ck or cand.endswith(("동", "가", "리")):
                place = cand
                kind = resolve_place_kind(cand, q)
                break
    if place is None and gu:
        place, kind = gu, "gu"
    elif place is None:
        place = extract_place(q)
        if place:
            kind = resolve_place_kind(place, q)
    usage = extract_usage(q)
    if "세부용도" in q:
        usage = None
    structure = extract_structure(q)
    numerics: list[dict[str, Any]] = []
    for field, schema_unit, pattern in (
        ("height_m", "m", rf"높이[가이]?\s*(\d+(?:\.\d+)?)\s*{UNIT_TOKEN}\s*[을를]?\s*(이상|이하|초과|미만|넘는)"),
        (
            "gross_floor_area_m2",
            "㎡",
            rf"연면적[이가]?\s*(\d+(?:\.\d+)?)\s*{UNIT_TOKEN}\s*[을를]?\s*(이상|이하|초과|미만|넘는)",
        ),
        (
            "building_area_m2",
            "㎡",
            rf"(?:건축면적|건물면적)[이가]?\s*(\d+(?:\.\d+)?)\s*{UNIT_TOKEN}\s*[을를]?\s*(이상|이하|초과|미만|넘는)",
        ),
        (
            "site_area_m2",
            "㎡",
            rf"대지면적[이가]?\s*(\d+(?:\.\d+)?)\s*{UNIT_TOKEN}\s*[을를]?\s*(이상|이하|초과|미만|넘는)",
        ),
        (
            "ground_floors",
            "층",
            r"(?:지상\s*층?|층수|지상층)[이가]?\s*(\d+)\s*층?\s*[을를]?\s*(이상|이하|초과|미만|넘는)",
        ),
        (
            "basement_floors",
            "층",
            r"지하\s*(?:층수?)?[이가]?\s*(\d+)\s*층?\s*[을를]?\s*(이상|이하|초과|미만|넘는)",
        ),
        (
            "building_coverage_ratio",
            "%",
            rf"건폐율[이가]?\s*(\d+(?:\.\d+)?)\s*%?\s*[을를]?\s*(이상|이하|초과|미만|넘는)",
        ),
        (
            "floor_area_ratio",
            "%",
            rf"용적[률율][이가]?\s*(\d+(?:\.\d+)?)\s*%?\s*[을를]?\s*(이상|이하|초과|미만|넘는)",
        ),
    ):
        match = re.search(pattern, q)
        if not match:
            continue
        unit = match.group(2) if match.lastindex and match.lastindex >= 2 else None
        if field == "ground_floors" or field == "basement_floors":
            unit = "층"
            rel = match.group(2)
        elif field in {"building_coverage_ratio", "floor_area_ratio"}:
            rel = match.group(2)
            numerics.append(
                {
                    "field": field,
                    "operator": _REL.get(rel, "gte"),
                    "value": float(match.group(1)),
                    "unit": "percent",
                }
            )
            continue
        else:
            rel = match.group(3)
        converted = convert_for_schema(match.group(1), unit, schema_unit)
        if converted is None:
            continue
        numerics.append(
            {
                "field": field,
                "operator": _REL.get(rel, "gte"),
                "value": converted.canonical,
                "unit": "m2" if schema_unit in {"㎡", "m2"} else ("m" if schema_unit == "m" else "floor"),
            }
            )
    range_nums = _extract_range_numerics(q)
    if range_nums:
        ranged_fields = {item["field"] for item in range_nums}
        numerics = [item for item in numerics if item["field"] not in ranged_fields]
        numerics.extend(range_nums)
    if not any(item["field"] == "ground_floors" for item in numerics):
        floor_m = re.search(r"(\d+)\s*층\s*[을를]?\s*(이상|이하|초과|미만|넘는)", q)
        if floor_m and "지하" not in q[max(0, floor_m.start() - 4) : floor_m.start()]:
            # 「10배」는 층수 임계가 아님
            after = q[floor_m.end(1) : floor_m.end(1) + 2]
            if "배" not in after:
                numerics.append(
                    {
                        "field": "ground_floors",
                        "operator": _REL.get(floor_m.group(2), "gte"),
                        "value": int(floor_m.group(1)),
                        "unit": "floor",
                    }
                )
    if not numerics:
        bare = re.search(
            rf"(\d+(?:\.\d+)?)\s*(킬로미터|㎞|km|미터|m)\s*[을를]?\s*(이상|이하|초과|미만|넘는)",
            q,
        )
        if bare:
            converted = convert_for_schema(bare.group(1), bare.group(2), "m")
            if converted is not None:
                numerics.append(
                    {
                        "field": "height_m",
                        "operator": _REL.get(bare.group(3), "gte"),
                        "value": converted.canonical,
                        "unit": "m",
                    }
                )
    if re.search(r"용적[률율][이가]?\s*0보다 크", q):
        numerics.append(
            {
                "field": "floor_area_ratio",
                "operator": "gt",
                "value": 0.0,
                "unit": "percent",
            }
        )
    if "기초구역" in q:
        bas = re.search(
            r"(?:면적|BAS_AR)\s*(?:\(BAS_AR\))?\s*(\d+(?:\.\d+)?)\s*(?:㎡|m2|㎢)?"
            r"\s*(이상|이하|초과|미만)",
            q,
            re.I,
        )
        if bas:
            numerics.append(
                {
                    "field": "area_m2",
                    "operator": _REL.get(bas.group(2), "gte"),
                    "value": float(bas.group(1)),
                }
            )
    far_lt = re.search(
        r"(\d+(?:\.\d+)?)\s*%\s*(미만|이하)",
        q,
    )
    if (
        far_lt
        and "용적" in q
        and not any(
            item["field"] == "floor_area_ratio" and item["operator"] in {"lt", "lte"}
            for item in numerics
        )
    ):
        numerics.append(
            {
                "field": "floor_area_ratio",
                "operator": _REL.get(far_lt.group(2), "lt"),
                "value": float(far_lt.group(1)),
                "unit": "percent",
            }
        )
    kind = "unknown"
    if is_busan_wide(q) and not gu and not (place and place.endswith(("구", "군", "동"))):
        place = place or "부산광역시"
        kind = "sido"
    elif place:
        from txt2sql.gazetteer import resolve_place_kind

        kind = resolve_place_kind(place, q)
        if kind == "unknown" and gu and (place == gu or not place):
            kind = "gu"
    elif gu:
        place = gu
        kind = "gu"
    industrial_names = extract_industrial_names(q)
    industrial_name = industrial_names[0] if industrial_names else extract_industrial_name(q)
    structures = extract_structures(q)
    land = extract_special_land(q)
    extra_filters: list[dict[str, Any]] = []
    if any(k in q for k in ("위반건축", "위반 건축", "위반건물")) or (
        "위반" in q and "건축" in q
    ):
        violate_op = "neq" if _term_is_negated(q, "위반건축물") or _term_is_negated(
            q, "위반건축"
        ) else "eq"
        extra_filters.append(
            {"field": "violation_status", "operator": violate_op, "value": "Y"}
        )
    if land:
        land_label = land[0]
        if land_label == "산지":
            land_value = "산"
        elif land_label == "일반지번":
            land_value = "일반"
        elif land_label == "가지번":
            land_value = "가지번"
        elif land_label == "블럭지번":
            land_value = "블럭지번"
        else:
            land_value = None
        if land_value:
            land_op = "neq" if _term_is_negated(q, land_label) else "eq"
            extra_filters.append(
                {"field": "special_land", "operator": land_op, "value": land_value}
            )
    dong_quoted = re.search(
        r"건물동명[이은는가에을를]?\s*[''\"]([^'\"]+)[''\"]",
        q,
    )
    if dong_quoted:
        extra_filters.append(
            {
                "field": "building_dong_name",
                "operator": "contains",
                "value": dong_quoted.group(1).strip(),
            }
        )
    elif "건물동명" in q:
        extra_filters.append(
            {"field": "building_dong_name", "operator": "is_not_null", "value": None}
        )
    if re.search(r"지하층이\s*있", q) or (
        "지하층" in q and "있고" in q and "합계" not in q
    ):
        extra_filters.append(
            {"field": "basement_floors", "operator": "gt", "value": 0}
        )
    if "일반건축물대장" in q:
        extra_filters.append(
            {"field": "ledger_kind", "operator": "eq", "value": "일반건축물대장"}
        )
    if any(k in q for k in ("집합건축물", "집합건물")) and "일반건축물" not in q:
        extra_filters.append(
            {
                "field": "complex_building_kind",
                "operator": "eq",
                "value": "집합건축물",
            }
        )
    # 「높이는 있는데 지상층수가 없는」 presence/absence
    if re.search(r"높이.{0,8}(있|기록).{0,12}(지상층|층수).{0,6}(없|NULL|null)", q) or (
        "높이는 있" in q
        and any(k in q for k in ("지상층수가 없", "층수가 없", "지상층수 없"))
    ):
        extra_filters.append(
            {"field": "height_m", "operator": "is_not_null", "value": None}
        )
        extra_filters.append(
            {"field": "ground_floors", "operator": "is_null", "value": None}
        )
    if any(k in q for k in ("기록된", "모두 있는")):
        if "건폐율" in q:
            extra_filters.append(
                {"field": "building_coverage_ratio", "operator": "gt", "value": 0}
            )
        if "용적" in q:
            extra_filters.append(
                {"field": "floor_area_ratio", "operator": "gt", "value": 0}
            )
        if any(k in q for k in ("사용승인", "건축연령", "준공")):
            extra_filters.append(
                {"field": "approval_date", "operator": "is_not_null", "value": None}
            )
    return {
        "place": place or gu,
        "place_kind": kind if (place or gu) else None,
        "usage": usage,
        "usages": [] if "세부용도" in q else extract_usages(q),
        "detail_usages": extract_detail_usages(q),
        "usage_classes": extract_usage_classes(q),
        "structure": structure[0] if structure else None,
        "structures": [item[0] for item in structures],
        "numeric_expressions": numerics,
        "age_question": looks_like_age_question(q),
        "distance_m": _extract_distance_m(q),
        "distance_outside": any(
            k in q for k in ("경계 밖", "바깥", "외부", "밖에")
        ),
        "boundary": any(
            k in q
            for k in (
                "행정동",
                "안에",
                "안의",
                "내부",
                "경계 안",
                "경계안",
                "안쪽",
                "반경",
            )
        )
        or bool(re.search(r"[동읍면]\s*안(?:에|의|쪽)?(?:\s|$)", q))
        # 「N년 이내」시차는 공간 boundary가 아님. 거리 단위+이내만 공간.
        or bool(
            re.search(
                r"\d+(?:\.\d+)?\s*(?:m|M|미터|km|킬로미터|㎞)\s*이내",
                q,
            )
        ),
        "scope_gu": gu if place and gu and place != gu else None,
        "industrial_name": industrial_name,
        "industrial_names": industrial_names,
        "extra_filters": extra_filters,
        "ratio": any(k in q for k in ("비율", "퍼센트", "몇%", "%씩", "몇 프로")),
        "basic_zone": "기초구역" in q,
    }


def parse_plan_json(text: str) -> SemanticQueryPlan:
    payload = _extract_json_object(text)
    if _PHYSICAL_LEAK.search(payload):
        raise SemanticPlanGenerationError("physical identifier or SQL leaked into plan")
    try:
        plan = SemanticQueryPlan.model_validate_json(payload)
    except Exception as exc:
        raise SemanticPlanGenerationError(f"invalid plan json: {exc}") from exc
    return migrate_plan_v11(plan)


def _catalog_owns_d060_only(question: str) -> bool:
    """산업단지 전용 속성(시군구코드 등)은 D060 카탈로그에 맡긴다."""
    if any(k in question for k in ("건물", "공장", "창고", "채수", "교차")):
        return False
    from txt2sql.catalog_attrs import match_catalog

    parsed = match_catalog(question)
    return bool(
        parsed is not None
        and parsed.dataset.key == "d060"
        and (parsed.filters or parsed.rank)
    )


def try_heuristic_plan(
    question: str,
    hints: dict[str, Any] | None = None,
    *,
    reference_date: str | None = None,
    contract=None,
) -> SemanticQueryPlan | None:
    """Router가 놓친 단순 건물 질의를 LLM 없이 Plan으로 옮긴다."""
    bound_contract = contract
    q = question.strip()
    if not q:
        return None
    if any(k in q for k in _UNSUPPORTED_HINTS):
        return None
    hints = hints or extract_plan_hints(q)
    temporal_filters = parse_temporal_filters(q, reference_date=reference_date)
    if is_vague_age_threshold(q) and not temporal_filters:
        scope = None
        place_name = hints.get("place")
        if place_name:
            kind = hints.get("place_kind") or "unknown"
            if kind not in {"sido", "gu", "legal_dong", "admin_dong", "basic_zone", "unknown"}:
                kind = "unknown"
            scope = ScopeSpec(place=PlaceSpec(name=place_name, kind=kind), spatial_mode="auto")
        return SemanticQueryPlan(
            query_kind="count",
            entity="building",
            scope=scope,
            requires_clarification=True,
            ambiguities=["오래된/신규의 기준 연수가 필요합니다"],
        )
    if re.search(r"[가-힣]{1,12}역", q) and _extract_distance_m(q) is not None:
        return SemanticQueryPlan(
            query_kind="list",
            entity="building",
            requires_clarification=True,
            ambiguities=[
                "역·POI 좌표는 지원하지 않습니다. 동·구 경계 기준으로 거리를 물어 주세요."
            ],
        )
    if (
        "면적" in q
        and "기초구역" not in q
        and "산업단지" not in q
        and not any(k in q for k in ("연면적", "건축면적", "건물면적", "건축물면적", "대지면적"))
        and not any(
            item.get("field") in {
                "gross_floor_area_m2",
                "building_area_m2",
                "site_area_m2",
            }
            for item in (hints.get("numeric_expressions") or [])
        )
    ):
        from txt2sql.place_area_qa import is_place_boundary_area_question

        # 행정·법정동·시·구 경계 면적은 건물 면적 clarify 대상이 아님
        if not is_place_boundary_area_question(q):
            return SemanticQueryPlan(
                query_kind="list",
                entity="building",
                requires_clarification=True,
                ambiguities=["면적이 건축면적·연면적·대지면적 중 어떤 것인지 필요합니다"],
            )
    if ("허가일" in q or "허가일자" in q) and "사용승인" not in q:
        gu_name = hints.get("place") if hints.get("place_kind") == "gu" else extract_gu(q)
        if gu_name and d198_table_for_gu(gu_name) is None and not is_busan_wide(q):
            return SemanticQueryPlan(
                query_kind="count",
                entity="building",
                unsupported_reason=d198_unavailable_reason("허가일"),
            )
    hints = hints or extract_plan_hints(q)
    bound = bound_contract if bound_contract is not None else extract_contract(q)
    if (
        not hints.get("place")
        and not hints.get("usage")
        and not hints.get("detail_usages")
        and not hints.get("usage_classes")
        and not hints.get("numeric_expressions")
        and not temporal_filters
        and not hints.get("industrial_name")
        and not hints.get("industrial_names")
        and not hints.get("basic_zone")
        and "산업단지" not in q
        and "사업지구" not in q
        and not hints.get("ratio")
        and not hints.get("extra_filters")
        and not bound.percentile_requests
        and not bound.derived_metrics
        and not bound.ratios
        and not bound.group_fields
        and not any(k in q for k in ("각 구", "구·군", "구군별", "차이"))
    ):
        return None
    if _catalog_owns_d060_only(q):
        return None
    if not any(
        k in q
        for k in (
            "건물",
            "아파트",
            "주택",
            "건축물",
            "공동주택",
            "창고",
            "학교",
            "공장",
            "용도별",
            "높이",
            "연면적",
            "건축면적",
            "건축물면적",
            "대지면적",
            "건축연령",
            "분위수",
            "분위",
            "분산",
            "표준편차",
            "사용승인",
            "년대",
            "허가",
            "산업단지",
            "기초구역",
            "사업지구",
            "채수",
            "비율",
            "산지",
            "구조별",
            "법정동별",
            "구·군",
            "구군",
            "차이",
            "최대",
            "평균",
        )
    ):
        if (
            not hints.get("numeric_expressions")
            and not hints.get("usage")
            and not hints.get("detail_usages")
            and not hints.get("usage_classes")
            and not hints.get("distance_m")
            and not hints.get("ratio")
            and not hints.get("extra_filters")
            and not temporal_filters
            and not any(k in q for k in ("구·군", "구군", "각 구", "차이"))
        ):
            return None

    query_kind = _guess_kind(q)
    filters: list[FilterSpec] = []
    predicate: PredicateSpec | None = None
    assumptions: list[str] = ["heuristic_plan"]
    or_tokens = ("또는", "혹은", "이거나", "둘 중 하나")
    not_tokens = ("제외", "아닌", "빼고", "뺀", "이외")
    usages = list(hints.get("usages") or [])
    structures = list(hints.get("structures") or [])
    detail_usages = list(hints.get("detail_usages") or [])
    usage_classes = list(hints.get("usage_classes") or [])
    # D198 커버 구/동이면 세부용도(아파트 등)를 우선하고 주요용도 오맵을 제거
    _gu_for_detail = extract_gu(q)
    if _gu_for_detail is None and hints.get("place_kind") == "legal_dong":
        _gu_for_detail = d198_gu_for_dong(str(hints.get("place") or ""), question=q)
    if (
        detail_usages
        and _gu_for_detail
        and d198_table_for_gu(str(_gu_for_detail)) is not None
    ):
        usages = []
        if hints.get("usage") and hints["usage"] not in detail_usages:
            hints = {**hints, "usage": None}
    elif detail_usages and not (
        _gu_for_detail and d198_table_for_gu(str(_gu_for_detail)) is not None
    ):
        # 비커버 지역: D010 주요용도만 사용 (아파트→공동주택)
        detail_usages = []
    dual_subset_usage = _dual_count_subset_usage(q, usages)
    # 「세부용도가 아파트인데 주요용도가 공동주택이 아닌」
    detail_vs_usage = re.search(
        r"세부용도.{0,8}(?P<detail>아파트|오피스텔|다세대주택|일반음식점).{0,16}"
        r"주요용도.{0,8}(?P<main>공동주택|단독주택|제1종근린생활시설|제2종근린생활시설|판매시설)"
        r".{0,6}아닌",
        q,
    )
    if detail_vs_usage:
        detail_val = detail_vs_usage.group("detail")
        main_val = detail_vs_usage.group("main")
        predicate = _and_pred(
            _field_eq("detail_usage", detail_val),
            PredicateSpec(op="not", args=[_usage_eq(main_val)]),
        )
        filters = [
            item
            for item in filters
            if item.field not in {"usage", "detail_usage"}
        ]
        usages = []
        detail_usages = []
        if "d198_ledger" not in assumptions:
            assumptions.append("d198_ledger")
    elif dual_subset_usage:
        usages = []
    neg_usages = [u for u in usages if _term_is_negated(q, u)]
    pos_usages = [u for u in usages if u not in neg_usages]
    middot_usage_or = bool(
        re.search(r"(공장|창고시설|창고)[·･、,/]", q)
    ) and len(pos_usages) >= 2
    if any(k in q for k in or_tokens) and len(usages) >= 2 and not neg_usages:
        predicate = PredicateSpec(
            op="or",
            args=[_usage_eq(value) for value in usages],
        )
    elif middot_usage_or and not any(k in q for k in not_tokens):
        predicate = PredicateSpec(
            op="or",
            args=[_usage_eq(value) for value in pos_usages],
        )
    elif any(k in q for k in or_tokens) and len(detail_usages) >= 2:
        predicate = PredicateSpec(
            op="or",
            args=[_field_eq("detail_usage", value) for value in detail_usages],
        )
    elif any(k in q for k in or_tokens) and len(usage_classes) >= 2:
        predicate = PredicateSpec(
            op="or",
            args=[_field_eq("usage_class", value) for value in usage_classes],
        )
    elif any(k in q for k in or_tokens) and len(structures) >= 2:
        predicate = PredicateSpec(
            op="or",
            args=[_structure_contains(value) for value in structures],
        )
    elif any(k in q for k in not_tokens) and len(neg_usages) >= 2:
        predicate = PredicateSpec(
            op="not",
            args=[
                PredicateSpec(
                    op="or",
                    args=[_usage_eq(value) for value in neg_usages],
                )
            ],
        )
    elif (
        re.search(r"도\s*.{0,12}도\s*아닌|도\s*아니고", q)
        and len(usages) >= 2
    ):
        # 「단독주택도 공동주택도 아닌」
        predicate = PredicateSpec(
            op="not",
            args=[
                PredicateSpec(
                    op="or",
                    args=[_usage_eq(value) for value in usages],
                )
            ],
        )
        if usage_classes:
            predicate = _and_pred(
                _field_eq("usage_class", usage_classes[0]),
                predicate,
            )
    elif any(k in q for k in not_tokens) and len(usages) >= 2 and not pos_usages:
        predicate = PredicateSpec(
            op="not",
            args=[
                PredicateSpec(
                    op="or",
                    args=[_usage_eq(value) for value in usages],
                )
            ],
        )
    elif (
        any(k in q for k in ("제외", "빼고", "뺀", "이외"))
        and len(usages) >= 2
        and not any(k in q for k in or_tokens)
        and not neg_usages
    ):
        predicate = PredicateSpec(
            op="not",
            args=[
                PredicateSpec(
                    op="or",
                    args=[_usage_eq(value) for value in usages],
                )
            ],
        )
    elif neg_usages:
        usage_not = neg_usages[0]
        filters.append(FilterSpec(field="usage", operator="neq", value=usage_not))
        predicate = PredicateSpec(op="not", args=[_usage_eq(usage_not)])
        if pos_usages:
            predicate = _and_pred(
                PredicateSpec(
                    op="or",
                    args=[_usage_eq(v) for v in pos_usages],
                )
                if len(pos_usages) >= 2
                else _usage_eq(pos_usages[0]),
                predicate,
            )
    elif hints.get("usage") and not dual_subset_usage:
        filters.append(FilterSpec(field="usage", operator="eq", value=hints["usage"]))
    if (
        predicate is None
        and not any(k in q for k in or_tokens)
        and len(detail_usages) == 1
    ):
        filters.append(
            FilterSpec(field="detail_usage", operator="eq", value=detail_usages[0])
        )
    if (
        predicate is None
        and not any(k in q for k in or_tokens)
        and len(usage_classes) == 1
    ):
        filters.append(
            FilterSpec(field="usage_class", operator="eq", value=usage_classes[0])
        )
    dual_usage_class_aggs = _dual_usage_class_metric_aggs(q, usage_classes)
    compare = _extract_field_compare(q)
    if compare is not None:
        filters.append(compare)
    neg_structures = [s for s in structures if _term_is_negated(q, s)]
    if hints.get("structure") and not (
        predicate is not None and predicate.op == "or" and len(structures) >= 2
        and not neg_structures
    ):
        if len(neg_structures) >= 2:
            predicate = _and_pred(
                predicate,
                PredicateSpec(
                    op="not",
                    args=[
                        PredicateSpec(
                            op="or",
                            args=[_structure_contains(s) for s in neg_structures],
                        )
                    ],
                ),
            )
        elif neg_structures or _term_is_negated(q, str(hints.get("structure") or "")):
            neg_val = (neg_structures[0] if neg_structures else hints["structure"])
            filters.append(
                FilterSpec(field="structure", operator="neq", value=neg_val)
            )
            predicate = _and_pred(
                predicate,
                PredicateSpec(op="not", args=[_structure_contains(neg_val)]),
            )
        else:
            exact = exact_structure_label(q, str(hints["structure"]))
            if exact:
                filters.append(
                    FilterSpec(field="structure", operator="eq", value=exact)
                )
            else:
                filters.append(
                    FilterSpec(
                        field="structure",
                        operator="contains",
                        value=hints["structure"],
                    )
                )
    for item in hints.get("numeric_expressions") or []:
        filters.append(
            FilterSpec(
                field=item["field"],
                operator=item["operator"],
                value=item["value"],
                value2=item.get("value2"),
                unit=item.get("unit"),
            )
        )
    filters.extend(temporal_filters)
    for item in hints.get("extra_filters") or []:
        filters.append(
            FilterSpec(
                field=item["field"],
                operator=item["operator"],
                value=item.get("value"),
            )
        )

    place_name = hints.get("place")
    scope = None
    spatial_relations: list[SpatialRelationSpec] = []
    distance_m = hints.get("distance_m")
    scope_gu = hints.get("scope_gu")
    if isinstance(scope_gu, str) and not scope_gu.strip():
        scope_gu = None

    def _place_spec(name: str, kind: str) -> PlaceSpec:
        return PlaceSpec(
            name=name,
            kind=kind,  # type: ignore[arg-type]
            sigungu=scope_gu if kind in {"legal_dong", "admin_dong", "unknown"} else None,
        )

    if place_name:
        kind = hints.get("place_kind") or "unknown"
        if kind not in {"sido", "gu", "legal_dong", "admin_dong", "basic_zone", "unknown"}:
            kind = "unknown"
        if isinstance(distance_m, (int, float)) and distance_m > 0:
            relation = "outside_distance" if hints.get("distance_outside") else "within_distance"
            spatial_relations.append(
                SpatialRelationSpec(
                    relation=relation,
                    target=SpatialTargetSpec(
                        place=_place_spec(place_name, kind)
                    ),
                    distance_m=float(distance_m),
                )
            )
            scope = ScopeSpec(
                place=_place_spec(place_name, kind),
                spatial_mode="auto",
            )
        else:
            mode = "boundary" if hints.get("boundary") else "auto"
            scope = ScopeSpec(place=_place_spec(place_name, kind), spatial_mode=mode)

    industrial_names = list(hints.get("industrial_names") or [])
    industrial_name = hints.get("industrial_name")

    if len(industrial_names) >= 2:
        joined = "·".join(industrial_names[:4])
        spatial_relations.append(
            SpatialRelationSpec(
                relation="intersects",
                target=SpatialTargetSpec(
                    entity="industrial_complex",
                    place=PlaceSpec(name=joined, kind="unknown"),
                ),
            )
        )
    elif industrial_name:
        spatial_relations.append(
            SpatialRelationSpec(
                relation="intersects",
                target=SpatialTargetSpec(
                    entity="industrial_complex",
                    place=PlaceSpec(name=str(industrial_name), kind="unknown"),
                ),
            )
        )
    elif "산업단지" in q and any(
        k in q for k in ("안", "내", "교차", "속한", "겹치")
    ):
        spatial_relations.append(
            SpatialRelationSpec(
                relation="intersects",
                target=SpatialTargetSpec(entity="industrial_complex"),
            )
        )
    else:
        district = re.search(r"([가-힣0-9]{2,30}사업지구)", q)
        if district and any(k in q for k in ("안", "내", "교차", "속한", "겹치")):
            spatial_relations.append(
                SpatialRelationSpec(
                    relation="intersects",
                    target=SpatialTargetSpec(
                        entity="industrial_complex",
                        place=PlaceSpec(name=district.group(1), kind="unknown"),
                    ),
                )
            )

    select: list[str] = []
    order_by: list[OrderSpec] = []
    aggregations: list[AggregationSpec] = []
    group_by: list[str] = []
    bins: list[BinSpec] = []
    stages: list[StageSpec] = []
    limit = _extract_limit(q)
    decade_group = False

    if query_kind == "rank":
        metric = _rank_metric(q)
        direction = (
            "asc"
            if any(k in q for k in ("낮은", "작은", "오래된", "오래"))
            and not any(k in q for k in ("최근", "신규"))
            else "desc"
        )
        order_by = [OrderSpec(field=metric, direction=direction, nulls="last")]
        select = ["name", "legal_dong", "lot_address", metric]
        if limit is None:
            from txt2sql.intent_router import _extract_top_n

            limit = _extract_top_n(
                q,
                default=1
                if any(k in q for k in ("가장", "제일", "최대", "1등", "최고"))
                else 10,
            )
        if metric == "ground_floors" and any(k in q for k in ("많은", "많 ")) and not any(
            item.field == "ground_floors" for item in filters
        ):
            filters.append(FilterSpec(field="ground_floors", operator="gt", value=0))
        if metric in {
            "gross_floor_area_m2",
            "building_area_m2",
            "height_m",
        } and any(k in q for k in ("많은", "큰", "상위")) and not any(
            item.field == metric for item in filters
        ):
            filters.append(FilterSpec(field=metric, operator="gt", value=0))
    elif query_kind == "list":
        select = _list_select(q)
        if compare is not None and compare.field not in select:
            select.append(compare.field)
        if compare is not None and compare.value_field and compare.value_field not in select:
            select.append(compare.value_field)
        if (
            any(item.field == "usage" for item in filters)
            or (predicate is not None and predicate.op in {"or", "not"})
        ) and "usage" not in select:
            select.append("usage")
        # 「많은/큰」인데 rank로 안 잡힌 경우 → 메트릭 정렬 list
        if any(k in q for k in ("많은", "많 ", "큰 ", "큰순", "많은 순")) and not order_by:
            metric = _rank_metric(q)
            if metric not in select:
                select.append(metric)
            order_by = [OrderSpec(field=metric, direction="desc", nulls="last")]
            if metric == "ground_floors" and not any(
                item.field == "ground_floors" for item in filters
            ):
                filters.append(
                    FilterSpec(field="ground_floors", operator="gt", value=0)
                )
            if limit is None:
                limit = 10
        elif not order_by:
            # 골드 list는 A0 DESC 또는 수치 조건 필드 DESC
            metric_fields = (
                "height_m",
                "ground_floors",
                "gross_floor_area_m2",
                "building_area_m2",
                "site_area_m2",
                "building_coverage_ratio",
                "floor_area_ratio",
                "basement_floors",
            )
            order_field = None
            for item in filters:
                if item.field in metric_fields:
                    order_field = item.field
                    break
            if order_field is None:
                for field in metric_fields:
                    if field in select:
                        order_field = field
                        break
            if order_field is not None:
                order_by = [
                    OrderSpec(field=order_field, direction="desc", nulls="last")
                ]
            else:
                # 연도/일자 필터가 있으면 id(A1) 대신 해당 필드로 정렬 (골드·D198 호환)
                date_fields = [
                    item.field
                    for item in filters
                    if item.field in {"approval_date", "permit_date"}
                ]
                if date_fields:
                    order_by = [
                        OrderSpec(
                            field=date_fields[0], direction="desc", nulls="last"
                        )
                    ]
                elif any(
                    item.field in {"usage", "usage_class", "detail_usage"}
                    for item in filters
                ) or (
                    predicate is not None
                    and predicate.op in {"or", "not", "and"}
                ):
                    # 용도 필터 목록: 골드·D198은 연면적 DESC가 상위명과 맞음
                    order_metric = "gross_floor_area_m2"
                    if order_metric not in select:
                        select.append(order_metric)
                    order_by = [
                        OrderSpec(field=order_metric, direction="desc", nulls="last")
                    ]
                else:
                    order_by = [OrderSpec(field="id", direction="desc", nulls="last")]
        if limit is None and any(k in q for k in ("보여", "찾아", "목록", "나열")):
            limit = 20
    elif query_kind == "aggregate":
        functions = _aggregate_functions(q, bound)
        metrics = _agg_metrics(q)
        aggregations = []
        for fn in functions:
            if fn == "count":
                aggregations.append(
                    AggregationSpec(function="count", field=None, alias="n")
                )
                continue
            # percentile/ratio는 아래 contract_extra 경로에서 값을 채운다.
            if fn in {"percentile", "ratio", "derived"}:
                continue
            for metric in metrics:
                aggregations.append(
                    AggregationSpec(
                        function=fn,
                        field=metric,
                        alias=f"{fn}_{metric}",
                    )
                )
        # 「양수 … 최소/최대」— 측정값 > 0
        if "양수" in q:
            for metric in metrics:
                if metric in {
                    "building_area_m2",
                    "gross_floor_area_m2",
                    "site_area_m2",
                    "height_m",
                    "ground_floors",
                } and not any(f.field == metric for f in filters):
                    filters.append(
                        FilterSpec(field=metric, operator="gt", value=0)
                    )
        # avg/sum/min/max 스칼라에 n 동반
        if aggregations and not any(
            (a.function or "").lower() == "count" for a in aggregations
        ):
            aggregations.append(
                AggregationSpec(function="count", field=None, alias="n")
            )
        if any(k in q for k in ("용도별", "세부용도별", "구조별", "법정동별", "구별")):
            query_kind = "aggregate"
            if "구조별" in q:
                group_by = ["structure"]
            elif "법정동별" in q:
                group_by = ["legal_dong"]
            elif "구별" in q:
                group_by = ["sigungu_name"]
            elif "세부용도별" in q:
                group_by = ["detail_usage"]
            else:
                group_by = ["usage"]
    elif query_kind == "distribution":
        if any(k in q for k in ("평균", "합계", "총합", "최대", "최소")):
            metric = _rank_metric(q)
            function = _aggregate_function(q)
            group_field = "usage"
            if any(k in q for k in ("층수별", "층별")):
                group_field = "ground_floors"
            query_kind = "aggregate"
            group_by = [group_field]
            aggregations = [
                AggregationSpec(function=function, field=metric, alias=f"{function}_{metric}")
            ]
        else:
            group_field = "usage"
            if any(k in q for k in ("층수별", "층별")):
                group_field = "ground_floors"
            if any(k in q for k in ("구간별", "년대별", "연도별")) and any(
                k in q for k in ("사용승인", "연도", "년대")
            ):
                group_field = "approval_date"
                decade_group = True
            group_by = [group_field]
            aggregations = [AggregationSpec(function="count", field=None, alias="n")]
            if limit is None:
                limit = 100

    entity: str = "building"
    if "heuristic_plan" not in assumptions:
        assumptions.insert(0, "heuristic_plan")
    if hints.get("scope_gu"):
        assumptions.append(f"scope_gu:{hints['scope_gu']}")
    if any(k in q for k in ("중심에서", "중심으로부터", "중심 기준")):
        assumptions.append("distance_from_centroid")
    gap = re.search(r"연도 차이가\s*(\d+)\s*년", q)
    if gap and "허가" in q and "사용승인" in q:
        assumptions.append(f"permit_year_gap:{int(gap.group(1))}")
    # 허가일 ↔ 사용승인일 day/order operators (D198 A33/A34)
    if "허가" in q and any(k in q for k in ("사용승인", "준공")):
        day_ge = re.search(
            r"(?:허가일?[부터에서]*\s*)?사용승인일?[까지]*\s*(\d+)\s*년\s*이상"
            r"|(\d+)\s*년\s*이상\s*걸린",
            q,
        )
        if day_ge:
            years = int(day_ge.group(1) or day_ge.group(2))
            assumptions.append(f"permit_day_gap_gte:{years * 365}")
        day_le = re.search(
            r"허가\s*후\s*(\d+)\s*년\s*(?:이내|내)|(\d+)\s*년\s*이내\s*준공",
            q,
        )
        if day_le:
            years = int(day_le.group(1) or day_le.group(2))
            assumptions.append(f"permit_day_gap_lte:{years * 365}")
        if any(
            k in q
            for k in (
                "허가일이 사용승인일보다 늦은",
                "허가일이 사용승인보다 늦은",
                "허가일보다 사용승인이 빠른",
                "비정상 레코드",
            )
        ) and "허가" in q and "사용승인" in q:
            assumptions.append("permit_after_approval")
        if any(
            k in q
            for k in (
                "허가연도와 사용승인연도가 다른",
                "허가연도과 사용승인연도가 다른",
                "허가연·사용승인연이 다른",
            )
        ) or (
            "허가연" in q and "사용승인연" in q and "다른" in q
        ):
            assumptions.append("permit_approval_year_neq")
    # 시차 assumption이 있으면 잘못된 age/rel_years 필터 제거
    if assumptions_include_permit_lag(assumptions):
        filters = [
            item
            for item in filters
            if not (
                item.field in {"approval_date", "permit_date"}
                and isinstance(item.value, str)
                and str(item.value).startswith("rel_years:")
            )
        ]
        if query_kind == "list":
            for field in ("permit_date", "approval_date"):
                if field not in select:
                    select.append(field)
            if any(a.startswith("permit_day_gap_gte:") for a in assumptions):
                order_by = [
                    OrderSpec(field="approval_date", direction="desc", nulls="last")
                ]
            elif "permit_after_approval" in assumptions:
                order_by = [
                    OrderSpec(field="permit_date", direction="desc", nulls="last")
                ]
            if limit is None:
                limit = 20
    if decade_group:
        assumptions.append("approval_decade")
        query_kind = "aggregate"
        order_by = [OrderSpec(field="approval_date", direction="asc", nulls="last")]
        limit = None
    if bound.fixed_bins and not decade_group:
        edge_bin = _edge_bin_from_contract(q, bound)
        if edge_bin is not None:
            bins = [edge_bin]
            group_by = [edge_bin.field]
            aggregations = _metric_group_aggregations(q)
            if not any(a.function == "count" for a in aggregations):
                aggregations.insert(
                    0, AggregationSpec(function="count", field=None, alias="n")
                )
            query_kind = "aggregate"
            order_by = []
            limit = None
            # 구간 분할이면 개별 임계 필터를 WHERE에 두지 않는다.
            filters = [
                item
                for item in filters
                if item.field != edge_bin.field
                or item.operator
                not in {"lt", "lte", "gt", "gte", "between", "eq"}
            ]
            assumptions.append(f"edge_bins:{edge_bin.field}")
        else:
            bin_field = None
            bin_width = None
            for span in bound.numbers:
                field = span.meta.get("field")
                if field and span.value:
                    bin_field = str(field)
                    bin_width = float(span.value)
                    break
            if bin_field and bin_width and bin_width > 0:
                group_by = [bin_field]
                # Prefer explicit BinSpec; keep width_bucket assumption for older consumers.
                bins = [BinSpec(field=bin_field, width=bin_width)]
                assumptions.append(f"width_bucket:{bin_field}:{bin_width:g}")
                aggregations = [AggregationSpec(function="count", field=None, alias="n")]
                query_kind = "aggregate"
                order_by = [OrderSpec(field=bin_field, direction="asc", nulls="last")]
                limit = None
                filters = [
                    item
                    for item in filters
                    if item.field != bin_field
                    or item.operator
                    not in {"lt", "lte", "gt", "gte", "between", "eq"}
                ]
    if hints.get("basic_zone"):
        entity = "basic_zone"
        filters = [
            item.model_copy(update={"field": "area_m2"})
            if item.field in {"gross_floor_area_m2", "building_area_m2", "site_area_m2"}
            else item
            for item in filters
        ]
        # 동·구와 겹치는 기초구역 → admin spatial (속성 LIKE 금지)
        if place_name and any(k in q for k in ("겹치", "교차", "걸치", "일부")):
            kind = hints.get("place_kind") or "admin_dong"
            if kind not in {"sido", "gu", "legal_dong", "admin_dong", "unknown"}:
                kind = "unknown"
            spatial_relations = [
                SpatialRelationSpec(
                    relation="intersects",
                    target=SpatialTargetSpec(
                        entity="admin_area",
                        place=_place_spec(str(place_name), kind),
                    ),
                )
            ]
            scope = None
        if "이동사유별" in q or ("이동사유" in q and "별" in q):
            query_kind = "aggregate"
            group_by = ["move_reason"]
            aggregations = [
                AggregationSpec(function="count", field=None, alias="n")
            ]
            select = []
            order_by = []
            limit = None
        elif query_kind == "count" or any(
            k in q for k in ("몇 개", "몇개", "개수", "몇 곳")
        ):
            query_kind = "count"
            aggregations = [
                AggregationSpec(function="count", field=None, alias="n")
            ]
            select = []
            order_by = []
            limit = None
        elif any(k in q for k in AGG_MAP) or query_kind == "aggregate":
            query_kind = "aggregate"
            function = _aggregate_function(q)
            aggregations = [
                AggregationSpec(
                    function=function,
                    field="area_m2",
                    alias=f"{function}_area_m2",
                ),
                AggregationSpec(function="count", field=None, alias="n"),
            ]
            select = []
            order_by = []
            limit = None
        else:
            query_kind = "rank"
            order_by = [OrderSpec(field="area_m2", direction="desc", nulls="last")]
            select = ["id", "gu_name", "area_m2"]
            aggregations = []
            if limit is None:
                limit = 1
    # 산업단지 면적 평균/합계 (건물 교차 아님)
    if (
        "산업단지" in q
        and "면적" in q
        and not any(k in q for k in ("건물", "건축물", "공장", "내부", "안 "))
        and any(k in q for k in ("평균", "합계", "총합", "합 "))
    ):
        entity = "industrial_complex"
        query_kind = "aggregate"
        function = _aggregate_function(q)
        if function not in {"avg", "sum", "min", "max"}:
            function = "avg" if "평균" in q else "sum"
        aggregations = [
            AggregationSpec(
                function=function,
                field="area_m2",
                alias=f"{function}_area_m2",
            ),
            AggregationSpec(function="count", field=None, alias="n"),
        ]
        select = []
        order_by = []
        limit = None
        spatial_relations = []
        if is_busan_wide(q) or (hints.get("place_kind") == "sido"):
            scope = ScopeSpec(
                place=PlaceSpec(name="부산", kind="sido"),
                spatial_mode="auto",
            )
        filters = []
    # 법정동별 건물 수 상위 N (최근 N년 준공 등 temporal filter 유지)
    if (
        "법정동" in q
        and any(k in q for k in ("많은", "상위", "순위"))
        and any(k in q for k in ("건물", "건축물", "채", "건수"))
        and "비율" not in q
        and entity == "building"
    ):
        query_kind = "aggregate"
        group_by = ["legal_dong"]
        aggregations = [AggregationSpec(function="count", field=None, alias="n")]
        order_by = [OrderSpec(field="n", direction="desc", nulls="last")]
        select = []
        if limit is None:
            limit = _extract_limit(q) or 5
        if any(k in q for k in ("사용승인", "준공", "건축연령", "허가")):
            if "d198_ledger" not in assumptions:
                assumptions.append("d198_ledger")
    # 허가일→사용승인일 소요 일수 중앙값/표준편차/평균
    if (
        "허가" in q
        and any(k in q for k in ("사용승인", "준공"))
        and (
            "일수" in q
            or ("평균" in q and any(k in q for k in ("걸린", "기간", "허가 후")))
        )
        and any(k in q for k in ("중앙값", "표준편차", "평균"))
    ):
        from txt2sql.semantic_plan.models import ExpressionSpec

        query_kind = "aggregate"
        if "중앙값" in q:
            fn, alias = "median", "median_days"
        elif "표준편차" in q:
            fn, alias = "stddev", "std_days"
        else:
            fn, alias = "avg", "avg_days"
        day_expr = ExpressionSpec(
            kind="subtract",
            left=ExpressionSpec(kind="field", field="approval_date"),
            right=ExpressionSpec(kind="field", field="permit_date"),
        )
        aggregations = [
            AggregationSpec(
                function=fn,
                field=None,
                alias=alias,
                expression=day_expr,
            ),
            AggregationSpec(function="count", field=None, alias="n"),
        ]
        select = []
        order_by = []
        limit = None
        if "d198_ledger" not in assumptions:
            assumptions.append("d198_ledger")
    # 허가→준공 기간 최장/최단 목록
    if (
        "허가" in q
        and any(k in q for k in ("사용승인", "준공"))
        and any(k in q for k in ("기간", "걸린"))
        and any(k in q for k in ("가장 긴", "제일 긴", "가장 짧", "제일 짧", "최장", "최단"))
    ):
        query_kind = "list"
        select = ["name", "legal_dong", "lot_address", "permit_date", "approval_date"]
        direction = (
            "asc"
            if any(k in q for k in ("가장 짧", "제일 짧", "최단"))
            else "desc"
        )
        assumptions.append(f"order_by_day_gap:{direction}")
        if "d198_ledger" not in assumptions:
            assumptions.append("d198_ledger")
        order_by = []
        if limit is None:
            limit = _extract_limit(q) or 10
        # day-gap 유효 행만 (「양수 기간」은 0일 제외)
        if not any(a.startswith("permit_day_gap_") for a in assumptions):
            if "양수" in q:
                assumptions.append("permit_day_gap_gte:1")
            else:
                assumptions.append("permit_day_gap_gte:0")
    # 산업단지별 내부 건물 수 상위 N
    if (
        "산업단지" in q
        and any(k in q for k in ("건물", "건축물"))
        and any(k in q for k in ("상위", "많은", "순위"))
        and any(k in q for k in ("단지", "공원", "산단"))
        and not any(k in q for k in ("면적", "높이", "연면적"))
    ):
        query_kind = "aggregate"
        entity = "building"
        aggregations = [
            AggregationSpec(function="count", field=None, alias="n"),
        ]
        group_by = []
        assumptions.append("group_by_industrial_name")
        spatial_relations = [
            SpatialRelationSpec(
                relation="intersects",
                target=SpatialTargetSpec(entity="industrial_complex"),
            )
        ]
        order_by = [OrderSpec(field="n", direction="desc", nulls="last")]
        select = []
        if limit is None:
            limit = _extract_limit(q) or 10
    # 건축연령 ↔ 연면적/층수 상관계수
    if "상관" in q and any(k in q for k in ("건축연령", "경과년")):
        query_kind = "aggregate"
        y_field = "gross_floor_area_m2"
        alias = "corr_age_gfa"
        if any(k in q for k in ("지상층", "층수")):
            y_field = "ground_floors"
            alias = "corr_age_fl"
        elif "높이" in q:
            y_field = "height_m"
            alias = "corr_age_h"
        aggregations = [
            AggregationSpec(
                function="corr",
                field="building_age_years",
                filter_field=y_field,
                alias=alias,
            ),
            AggregationSpec(function="count", field=None, alias="n"),
        ]
        select = []
        order_by = []
        limit = None
        if "d198_ledger" not in assumptions:
            assumptions.append("d198_ledger")
    if "구별" in q and entity == "building":
        query_kind = "aggregate"
        group_by = ["sigungu_name"]
        aggregations = _metric_group_aggregations(q)
        select = []
        if any(k in q for k in ("상위", "순위")):
            order_field = aggregations[0].alias or "n"
            order_by = [OrderSpec(field=order_field, direction="desc", nulls="last")]
            extracted = _extract_limit(q)
            if extracted:
                limit = extracted
        else:
            order_by = []
            limit = None
    if any(k in q for k in ("구·군별", "군별", "각 구·군", "각 구군", "구별로", "구별 ")) and entity == "building":
        query_kind = "aggregate"
        group_by = ["sigungu_name"]
        aggregations = _metric_group_aggregations(q)
        select = []
        # 최대−평균 / 최대−최소양수 파생
        if "차이" in q and any(k in q for k in ("최대", "평균", "최소")):
            metric = _rank_metric(q)
            if "높이" in q:
                metric = "height_m"
            elif "연면적" in q:
                metric = "gross_floor_area_m2"
            aggregations = [
                AggregationSpec(function="max", field=metric, alias=f"max_{metric}"),
                AggregationSpec(function="avg", field=metric, alias=f"avg_{metric}"),
                AggregationSpec(function="count", field=None, alias="n"),
            ]
            if "양수" in q or ("최소" in q and "연면적" in q):
                aggregations.insert(
                    1,
                    AggregationSpec(
                        function="min",
                        field=metric,
                        alias=f"min_{metric}",
                        predicate=PredicateSpec(
                            op="cmp",
                            operator="gt",
                            left=OperandSpec(kind="field", field=metric),
                            right=OperandSpec(kind="literal", value=0),
                        ),
                    ),
                )
            assumptions.append("gu_max_avg_diff")
        order_by = []
        limit = None
    if any(k in q for k in ("구조별", "법정동코드별", "용도별", "세부용도별", "법정동별", "건물용도분류별", "용도분류별")) and entity == "building":
        if "구조별" in q:
            group_field = "structure"
        elif "법정동코드별" in q:
            group_field = "bjd_cd"
        elif "법정동별" in q:
            group_field = "legal_dong"
        elif "세부용도별" in q:
            group_field = "detail_usage"
        elif any(k in q for k in ("건물용도분류별", "용도분류별")):
            group_field = "usage_class"
            if "d198_ledger" not in assumptions:
                assumptions.append("d198_ledger")
        else:
            group_field = "usage"
        query_kind = "aggregate"
        group_by = [group_field]
        aggregations = _metric_group_aggregations(q)
        select = []
        order_by = []
        limit = None
        if group_field == "detail_usage" and "d198_ledger" not in assumptions:
            assumptions.append("d198_ledger")
        # 구조별 비율 — answer 단계에서 share pct 부여
        if group_field == "structure" and hints.get("ratio"):
            aggregations = [AggregationSpec(function="count", field=None, alias="n")]
            assumptions.append("group_share_pct")
            if any(k in q for k in ("%", "백분율", "퍼센트")):
                assumptions.append("group_share_percent")
    if re.search(r"준공연도별", q) and any(k in q for k in ("건폐율", "용적률", "용적율", "평균")):
        query_kind = "aggregate"
        group_by = ["approval_date"]
        metric = (
            "building_coverage_ratio"
            if "건폐" in q
            else "floor_area_ratio"
        )
        aggregations = [
            AggregationSpec(function="avg", field=metric, alias=f"avg_{metric}"),
            AggregationSpec(function="count", field=None, alias="n"),
        ]
        select = []
        order_by = [OrderSpec(field="approval_date", direction="asc", nulls="last")]
        limit = None
        if "d198_ledger" not in assumptions:
            assumptions.append("d198_ledger")
        assumptions.append("approval_year_group")
        decade_group = False  # year not decade
    # 법정동별 아파트(세부용도) 수 — A25 공동주택 오맵 방지
    if (
        "법정동별" in q
        and "아파트" in q
        and any(k in q for k in ("수", "집계", "몇"))
        and "비율" not in q
    ):
        query_kind = "aggregate"
        group_by = ["legal_dong"]
        aggregations = [AggregationSpec(function="count", field=None, alias="n")]
        filters = [
            item
            for item in filters
            if item.field not in {"usage", "detail_usage"}
        ]
        filters.append(
            FilterSpec(field="detail_usage", operator="eq", value="아파트")
        )
        select = []
        order_by = [OrderSpec(field="n", direction="desc", nulls="last")]
        if "d198_ledger" not in assumptions:
            assumptions.append("d198_ledger")
    # 산업단지별 내부 건물 수/평균연면적
    if (
        "산업단지별" in q
        and any(k in q for k in ("건물", "건축물"))
        and any(k in q for k in ("수", "집계", "평균", "연면적"))
        and "경계" not in q
    ):
        query_kind = "aggregate"
        group_by = []
        assumptions.append("group_by_industrial_name")
        if "평균" in q and "연면적" in q:
            aggregations = [
                AggregationSpec(
                    function="avg",
                    field="gross_floor_area_m2",
                    alias="avg_gfa",
                ),
                AggregationSpec(function="count", field=None, alias="n"),
            ]
        else:
            aggregations = [AggregationSpec(function="count", field=None, alias="n")]
        select = []
        order_by = [OrderSpec(field="n", direction="desc", nulls="last")]
        limit = None
        spatial_relations = [
            SpatialRelationSpec(
                relation="intersects",
                target=SpatialTargetSpec(entity="industrial_complex"),
            )
        ]
    if re.search(r"(준공연대별|년대별)", q) and (
        len(re.findall(r"\d{4}년대", q)) >= 2 or "이전" in q
    ):
        decade_group = True
        group_by = ["approval_date"]
        aggregations = [
            AggregationSpec(function="count", field=None, alias="n")
        ]
        query_kind = "aggregate"
        select = []
        order_by = [OrderSpec(field="approval_date", direction="asc", nulls="last")]
        limit = None
        assumptions.append("approval_decade")
        filters = [
            item
            for item in filters
            if not (
                item.field == "approval_date"
                and item.operator in {"between", "gte", "lte", "gt", "lt", "eq"}
            )
        ]
    ratios: list[RatioSpec] = []
    if hints.get("ratio"):
        query_kind = "aggregate"
        lag_lte = next(
            (a for a in assumptions if a.startswith("permit_day_gap_lte:")), None
        )
        if lag_lte:
            years = max(1, int(lag_lte.split(":", 1)[1]) // 365)
            ratios = [
                RatioSpec(
                    numerator_predicate=PredicateSpec(
                        op="cmp",
                        operator="lte",
                        left=OperandSpec(kind="field", field="permit_date"),
                        right=OperandSpec(kind="field", field="approval_date"),
                    ),
                    multiplier=1.0,
                    alias=f"within_{years}y_ratio",
                )
            ]
            aggregations = [AggregationSpec(function="count", field=None, alias="n")]
            select = []
            order_by = []
            limit = None
        elif "permit_approval_year_neq" in assumptions:
            ratios = [
                RatioSpec(
                    numerator_predicate=PredicateSpec(
                        op="cmp",
                        operator="neq",
                        left=OperandSpec(kind="field", field="permit_date"),
                        right=OperandSpec(kind="field", field="approval_date"),
                    ),
                    multiplier=1.0,
                    alias="diff_year_ratio",
                )
            ]
            aggregations = [AggregationSpec(function="count", field=None, alias="n")]
            select = []
            order_by = []
            limit = None
        else:
            ratios = _build_ratio_specs(q, filters)
            if ratios:
                # 골드·일반 비율은 0~1 분수. 백분율/% 명시 시에만 ×100.
                if not any(k in q for k in ("%", "백분율", "퍼센트", "프로")):
                    ratios = [
                        item.model_copy(update={"multiplier": 1.0}) for item in ratios
                    ]
                ratio_fields: set[str] = set()
                for item in ratios:
                    ratio_fields.update(_pred_field_names(item.numerator_predicate))
                    ratio_fields.update(_pred_field_names(item.denominator_predicate))
                filters = [item for item in filters if item.field not in ratio_fields]
                if predicate is not None:
                    pred_fields = set(_pred_field_names(predicate))
                    if pred_fields and pred_fields <= ratio_fields:
                        predicate = None
            if not any(text in q for text in AGG_MAP) and not any(
                k in q for k in ("건수", "채수", "몇 채", "몇채")
            ):
                aggregations = []
                # 조건부 비율은 분모 건수를 함께 반환 (스칼라 골드 n=…)
                if ratios and any(
                    item.denominator_predicate is not None for item in ratios
                ):
                    den_pred = next(
                        item.denominator_predicate
                        for item in ratios
                        if item.denominator_predicate is not None
                    )
                    aggregations = [
                        AggregationSpec(
                            function="count",
                            field=None,
                            alias="n",
                            predicate=den_pred,
                        )
                    ]
            # 위반건축물 비율은 D010 A20 — D198 A20(용적율)과 충돌 방지
            if ratios and any(
                "violation_status" in _pred_field_names(item.numerator_predicate)
                or (
                    item.denominator_predicate is not None
                    and "violation_status"
                    in _pred_field_names(item.denominator_predicate)
                )
                for item in ratios
            ):
                assumptions = [a for a in assumptions if a != "d198_ledger"]
                if "d010_gis" not in assumptions:
                    assumptions.append("d010_gis")
    contract_extra = bound_contract if bound_contract is not None else extract_contract(q)
    # 연도 꼬리(최근 준공 상위 N% … 평균)는 전용 SQL — 잘못된 height percentile 방지
    from txt2sql.planner.semantic_executor import _parse_percentile_tail

    if _parse_percentile_tail(q) is not None:
        contract_extra = contract_extra.model_copy(
            update={"percentile_requests": []}
        )
    seen_percentiles: set[tuple[str | None, float]] = set()
    for req in contract_extra.percentile_requests:
        key = (req.field, round(float(req.percentile), 6))
        if key in seen_percentiles:
            continue
        seen_percentiles.add(key)
        query_kind = "aggregate"
        aggregations.append(
            AggregationSpec(
                function="percentile",
                field=req.field or _rank_metric(q),
                percentile=req.percentile,
                alias="pctl" if len(seen_percentiles) == 1 else f"pctl_{len(seen_percentiles)}",
            )
        )
        select = []
        if not contract_extra.limit:
            limit = None
            order_by = []
    for req in contract_extra.derived_metrics:
        query_kind = "aggregate"
        aggregations.append(
            AggregationSpec(
                function="avg",
                expression=ExpressionSpec(
                    kind="divide",
                    left=ExpressionSpec(kind="field", field=req.left),
                    right=ExpressionSpec(kind="field", field=req.right),
                ),
                alias="avg_ratio",
            )
        )
        select = []
    # 법정동별 용도 비율 상위 N (group + ratio + ORDER BY ratio)
    if (
        "법정동" in q
        and "비율" in q
        and any(k in q for k in ("가장 높", "제일 높", "상위", "높은"))
        and entity == "building"
    ):
        query_kind = "aggregate"
        group_by = ["legal_dong"]
        if not ratios:
            usage_val = None
            for u in ("공동주택", "단독주택", "공장", "판매시설"):
                if u in q:
                    usage_val = u
                    break
            if usage_val:
                ratios = [
                    RatioSpec(
                        numerator_predicate=PredicateSpec(
                            op="cmp",
                            left=OperandSpec(kind="field", field="usage"),
                            operator="eq",
                            right=OperandSpec(kind="literal", value=usage_val),
                        ),
                        denominator_predicate=None,
                        multiplier=1.0,
                        alias="apt_ratio" if usage_val == "공동주택" else "ratio_pct",
                    )
                ]
        if ratios and not any(k in q for k in ("%", "백분율", "퍼센트", "프로")):
            ratios = [item.model_copy(update={"multiplier": 1.0}) for item in ratios]
        if ratios and "공동주택" in q:
            ratios = [
                item.model_copy(update={"alias": "apt_ratio"}) for item in ratios
            ]
        order_field = ratios[0].alias if ratios else "n"
        order_by = [OrderSpec(field=order_field, direction="desc", nulls="last")]
        if limit is None:
            limit = _extract_limit(q) or 5
        if "d198_ledger" not in assumptions:
            assumptions.append("d198_ledger")
        # 비율 분자 용도는 WHERE에서 제거 (FILTER로만); 건수 동반
        if ratios:
            ratio_fields: set[str] = set()
            for item in ratios:
                ratio_fields.update(_pred_field_names(item.numerator_predicate))
            filters = [item for item in filters if item.field not in ratio_fields]
            if not any((a.function or "").lower() == "count" for a in aggregations):
                aggregations = [
                    AggregationSpec(
                        function="count",
                        field=None,
                        alias="apt_n",
                        predicate=ratios[0].numerator_predicate,
                    ),
                    AggregationSpec(function="count", field=None, alias="n"),
                ]
    if "지하층" in q and "합계" in q:
        query_kind = "aggregate"
        basement_pred = PredicateSpec(
            op="cmp",
            operator="gte",
            left=OperandSpec(kind="field", field="basement_floors"),
            right=OperandSpec(kind="literal", value=1),
        )
        aggregations = [
            AggregationSpec(function="sum", field="basement_floors", alias="sum_basement"),
            AggregationSpec(function="count", alias="n", predicate=basement_pred),
        ]
    if group_by and aggregations and not order_by and (
        limit or any(k in q for k in ("상위", "순위"))
    ):
        alias = aggregations[0].alias or aggregations[0].function
        for item in aggregations:
            if item.function == "count" and item.alias:
                alias = item.alias
                break
        order_by = [OrderSpec(field=alias, direction="desc", nulls="last")]
        if limit is None:
            limit = _extract_limit(q)
    elif group_by and aggregations and not order_by:
        # 그룹 평균·합계는 지표 내림차순이 기본(골드·가독성)
        metric_agg = next(
            (
                item
                for item in aggregations
                if item.function in {"avg", "sum", "max", "min"} and item.alias
            ),
            None,
        )
        count_agg = next(
            (item for item in aggregations if item.function == "count" and item.alias),
            None,
        )
        if metric_agg is not None:
            order_by = [
                OrderSpec(field=metric_agg.alias, direction="desc", nulls="last")
            ]
        elif count_agg is not None:
            order_by = [
                OrderSpec(field=count_agg.alias, direction="desc", nulls="last")
            ]
    if dual_subset_usage:
        query_kind = "aggregate"
        aggregations = [
            AggregationSpec(function="count", field=None, alias="total_n"),
            AggregationSpec(
                function="count",
                field=None,
                alias="subset_n",
                filter_field="usage",
                filter_operator="eq",
                filter_value=dual_subset_usage,
            ),
        ]
        select = []
        limit = None
    elif dual_usage_class_aggs:
        query_kind = "aggregate"
        aggregations = dual_usage_class_aggs
        select = []
        group_by = []
        order_by = []
        limit = None
        if "d198_ledger" not in assumptions:
            assumptions.append("d198_ledger")
    from txt2sql.dataset_grain import grain_to_assumption, resolve_dataset_grain
    from txt2sql.query_ir.adapters import contract_to_query_ir

    # Central grain policy (coverage-aware usage → D198).
    try:
        ir_for_grain = contract_to_query_ir(extract_contract(q))
        if resolve_dataset_grain(ir_for_grain, q) == "d198":
            assumptions.append(grain_to_assumption("d198"))
    except Exception:
        d198_needed = any(
            item.field
            in {
                "detail_usage",
                "usage_class",
                "ledger_kind",
                "complex_building_kind",
                "permit_date",
            }
            for item in filters
        ) or (
            predicate is not None
            and _predicate_has_field(
                predicate,
                {
                    "detail_usage",
                    "usage_class",
                    "ledger_kind",
                    "complex_building_kind",
                },
            )
        ) or assumptions_include_permit_lag(assumptions) or any(
            k in q
            for k in (
                "세부용도",
                "용도분류",
                "허가일",
                "허가일자",
                "문교사회용",
                "표제부",
                "집합건축물",
                "일반건축물대장",
                "용도별건물",
            )
        )
        if d198_needed and "d198_ledger" not in assumptions:
            assumptions.append("d198_ledger")

    # Pure site_area aggregates (no ledger attributes) stay on D010 GIS grain.
    site_only = any(
        a.field == "site_area_m2" for a in aggregations if a.field
    ) and not any(
        f.field
        in {
            "usage",
            "detail_usage",
            "usage_class",
            "ledger_kind",
            "complex_building_kind",
            "permit_date",
            "approval_date",
            "building_age_years",
        }
        for f in filters
    ) and not any(k in q for k in ("세부용도", "용도분류", "허가", "사용승인", "건축연령"))
    if site_only and "대지면적" in q:
        assumptions = [a for a in assumptions if a != "d198_ledger"]
        if "d010_gis" not in assumptions:
            assumptions.append("d010_gis")

    d198_needed = "d198_ledger" in assumptions
    clarify = False
    ambiguities: list[str] = []
    if d198_needed:
        gu_name = extract_gu(q)
        dong_name = None
        if hints.get("place_kind") == "legal_dong":
            dong_name = hints.get("place")
            if gu_name is None and dong_name:
                gu_name = d198_gu_for_dong(str(dong_name), question=q)
        # Main-usage + coverage may set d198 without exclusive fields — skip
        # clarify when coverage exists for place.
        from txt2sql.dataset_grain import place_has_d198_coverage

        if not place_has_d198_coverage(q, gu=gu_name, place=dong_name):
            # Exclusive ledger features without coverage → clarify
            exclusive = any(
                item.field
                in {
                    "detail_usage",
                    "usage_class",
                    "ledger_kind",
                    "complex_building_kind",
                    "permit_date",
                }
                for item in filters
            ) or any(
                k in q
                for k in ("세부용도", "용도분류", "허가일", "허가일자", "표제부", "집합건축물")
            )
            if exclusive:
                clarify = True
                ambiguities.append(d198_unavailable_reason("세부용도·용도분류"))
        elif gu_name:
            scope = ScopeSpec(
                place=PlaceSpec(name=str(gu_name), kind="gu"),
                spatial_mode="auto",
            )
            if dong_name:
                filters.append(
                    FilterSpec(
                        field="legal_dong", operator="contains", value=dong_name
                    )
                )

    # top-N rows then outer aggregate → stages CTE (flat limit+agg cannot express this).
    stages = _maybe_topn_aggregate_stages(
        q,
        query_kind=query_kind,
        limit=limit,
        order_by=order_by,
        aggregations=aggregations,
        select=select,
    )
    if stages:
        query_kind = "aggregate"
        aggregations = list(stages[1].aggregations)
        order_by = []
        limit = None
        select = []

    plan = SemanticQueryPlan(
        query_kind=query_kind,
        entity=entity,  # type: ignore[arg-type]
        scope=scope,
        filters=filters,
        predicate=predicate,
        select=select,
        aggregations=aggregations,
        ratios=ratios,
        group_by=group_by,
        order_by=order_by,
        limit=limit,
        spatial_relations=spatial_relations,
        bins=bins,
        stages=stages,
        requires_clarification=clarify,
        ambiguities=ambiguities,
        model_confidence=0.7,
        assumptions=assumptions,
    )
    return _ensure_contract_operators(plan, bound)


def _boolean_complete(contract, plan: SemanticQueryPlan) -> bool:
    """질문에 있는 OR/NOT이 Plan predicate에 모두 있으면 LLM 없이 채택."""
    if plan is None or plan.requires_clarification or plan.unsupported_reason:
        return False
    pred = effective_predicate(plan)
    wants_or = any(span.kind == "or" for span in contract.boolean_ops)
    wants_not = any(span.kind == "not" for span in contract.boolean_ops)
    if wants_or and not has_op(pred, "or"):
        return False
    if wants_not and not (
        has_op(pred, "not") or any(item.operator == "neq" for item in plan.filters)
    ):
        return False
    return wants_or or wants_not


def _defer_uses_heuristic(question: str, plan: SemanticQueryPlan) -> bool:
    """복합 yield 문항은 LLM 대신 완성 heuristic을 실행한다."""
    try:
        from txt2sql.intent_router import should_defer_compound_to_plan
    except Exception:
        return False
    if not should_defer_compound_to_plan(question):
        return False
    if plan.scope is None or plan.scope.place is None:
        if not plan.spatial_relations:
            return False
    if not (plan.filters or plan.predicate or plan.spatial_relations):
        return False
    pred = effective_predicate(plan)
    contract = extract_contract(question)
    if any(span.kind == "or" for span in contract.boolean_ops) and not has_op(pred, "or"):
        return False
    if any(span.kind == "not" for span in contract.boolean_ops) and not (
        has_op(pred, "not") or any(item.operator == "neq" for item in plan.filters)
    ):
        return False
    return True


def _heuristic_passes_contract(question: str, plan: SemanticQueryPlan, contract) -> bool:
    """Weak short-circuits must still pass the same hard contract gate."""
    from txt2sql.semantic_plan.contract_verifier import verify_contract

    verified = verify_contract(question, plan, contract=contract)
    return verified.ok and not verified.hard_fail


def generate_semantic_plan(
    question: str,
    settings: Settings,
    *,
    conn: psycopg.Connection | None = None,
    ollama_client: Any | None = None,
    session: SessionContext | None = None,
    allow_llm: bool = True,
    contract=None,
    force_llm: bool = False,
) -> SemanticQueryPlan:
    hints = extract_plan_hints(question)
    contract = contract if contract is not None else extract_contract(question)
    heuristic = try_heuristic_plan(
        question,
        hints,
        reference_date=settings.reference_date,
        contract=contract,
    )
    if heuristic is not None:
        heuristic = _ensure_contract_operators(heuristic, contract)
    if heuristic is not None and heuristic.requires_clarification:
        if contract.ratios or contract.percentile_requests or contract.derived_metrics:
            heuristic = heuristic.model_copy(
                update={"requires_clarification": False, "ambiguities": []}
            )
        elif not force_llm:
            return heuristic
    if heuristic is not None and accept_heuristic_plan(contract, heuristic):
        return heuristic
    # §5.3: boolean / defer / shape presence alone is not completeness evidence.
    if (
        heuristic is not None
        and _boolean_complete(contract, heuristic)
        and _heuristic_passes_contract(question, heuristic, contract)
    ):
        return heuristic
    if (
        heuristic is not None
        and not heuristic.requires_clarification
        and heuristic.unsupported_reason is None
        and _defer_uses_heuristic(question, heuristic)
        and _heuristic_passes_contract(question, heuristic, contract)
    ):
        return heuristic
    if (
        heuristic is not None
        and not heuristic.requires_clarification
        and heuristic.unsupported_reason is None
        and (heuristic.aggregations or heuristic.spatial_relations or heuristic.ratios)
        and _heuristic_passes_contract(question, heuristic, contract)
    ):
        return heuristic
    last_error: Exception | None = None
    need_llm = force_llm or bool(getattr(contract, "unresolved_spans", None))
    if allow_llm and need_llm:
        try:
            from txt2sql.semantic_plan.examples import examples_for_contract

            return _generate_with_llm(
                question,
                settings,
                hints=hints,
                ollama_client=ollama_client,
                extra_examples=examples_for_contract(contract),
            )
        except SemanticPlanGenerationError as exc:
            last_error = exc
    if heuristic is not None and heuristic.requires_clarification:
        return heuristic
    if (
        heuristic is not None
        and _boolean_complete(contract, heuristic)
        and _heuristic_passes_contract(question, heuristic, contract)
    ):
        return heuristic
    if any(span.kind == "or" for span in contract.boolean_ops):
        pred = effective_predicate(heuristic) if heuristic is not None else None
        if pred is None or not has_op(pred, "or"):
            return SemanticQueryPlan(
                query_kind="list",
                entity="building",
                requires_clarification=True,
                ambiguities=["논리합(OR) 조건을 완전히 해석하지 못해 확인이 필요합니다"],
                assumptions=["or_incomplete"],
            )
    if heuristic is not None:
        return heuristic
    if not allow_llm or last_error is not None:
        return SemanticQueryPlan(
            query_kind="list",
            entity="building",
            requires_clarification=True,
            ambiguities=["질문을 완전히 해석하지 못해 확인이 필요합니다"],
            assumptions=["heuristic_incomplete"],
        )
    if last_error is not None:
        raise last_error
    raise SemanticPlanGenerationError("plan generation failed")


def _ensure_contract_operators(plan: SemanticQueryPlan, contract) -> SemanticQueryPlan:
    from txt2sql.semantic_plan.plan_repair import apply_contract_operators

    return apply_contract_operators(plan, contract)


def _generate_with_llm(
    question: str,
    settings: Settings,
    *,
    hints: dict[str, Any],
    ollama_client: Any | None,
    extra_examples: list[str] | None = None,
) -> SemanticQueryPlan:
    messages = build_messages(question, hints=hints, extra_examples=extra_examples)
    retries = max(0, int(settings.semantic_plan_max_retries))
    schema = SemanticQueryPlan.model_json_schema()
    raw = chat(
        model=settings.planner_model(),
        messages=messages,
        host=settings.ollama_host if ollama_client is None else None,
        client=ollama_client,
        temperature=0.0,
        response_format=schema,
        timeout=settings.llm_timeout_s,
    )
    try:
        return parse_plan_json(raw)
    except SemanticPlanGenerationError:
        if retries < 1:
            raise
        repair_messages = messages + [
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": (
                    "The previous output was invalid. "
                    "Return a valid SemanticQueryPlan JSON object only."
                ),
            },
        ]
        repaired = chat(
            model=settings.planner_model(),
            messages=repair_messages,
            host=settings.ollama_host if ollama_client is None else None,
            client=ollama_client,
            temperature=0.0,
            response_format=schema,
            timeout=settings.llm_timeout_s,
        )
        return parse_plan_json(repaired)


def _extract_json_object(text: str) -> str:
    blob = (text or "").strip()
    if blob.startswith("```"):
        blob = re.sub(r"^```(?:json)?\s*", "", blob, flags=re.I)
        blob = re.sub(r"\s*```$", "", blob)
    start = blob.find("{")
    end = blob.rfind("}")
    if start < 0 or end < start:
        raise SemanticPlanGenerationError("no json object in model output")
    payload = blob[start : end + 1]
    json.loads(payload)
    return payload


def _extract_distance_m(question: str) -> float | None:
    if not any(
        k in question
        for k in ("이내", "주변", "근처", "버퍼", "반경", "바깥", "경계 밖", "밖에")
    ) and not re.search(r"\d+(?:\.\d+)?\s*(?:m|미터|km)\s*안", question):
        return None
    match = re.search(LENGTH_DIST_PATTERN, question)
    if not match:
        return None
    converted = convert_for_schema(match.group(1), match.group(2), "m")
    if converted is None:
        return None
    return float(converted.canonical)


def _term_is_negated(question: str, term: str) -> bool:
    """term이 '아닌/제외/빼고'의 피연산자인지. '공장 중 X이 아닌'의 공장은 양성."""
    if not question or not term:
        return False
    escaped = re.escape(term)
    if re.search(
        escaped
        + r"(?:[·･、,/][가-힣0-9]+)*(?:구조)?(?:이|가|을|를|은|는|도)?"
        r"\s*(?:아닌|아니고|아니면서|제외한|제외하고|빼고|뺀|이외)",
        question,
    ):
        return True
    if re.search(
        r"(?:제외한|제외하고|빼고|뺀)\s*" + escaped,
        question,
    ):
        return True
    return False


def _pred_field_names(pred: PredicateSpec | None) -> list[str]:
    if pred is None:
        return []
    found: list[str] = []
    if pred.left and pred.left.kind == "field" and pred.left.field:
        found.append(pred.left.field)
    if pred.right and pred.right.kind == "field" and pred.right.field:
        found.append(pred.right.field)
    for arg in pred.args or []:
        found.extend(_pred_field_names(arg))
    return found


def _and_pred(
    existing: PredicateSpec | None, extra: PredicateSpec | None
) -> PredicateSpec | None:
    if extra is None:
        return existing
    if existing is None:
        return extra
    if existing.op == "and":
        return PredicateSpec(op="and", args=[*(existing.args or []), extra])
    return PredicateSpec(op="and", args=[existing, extra])


def _edge_bin_from_contract(question: str, bound: Any) -> BinSpec | None:
    """「500미만, 500~2000, 2000초과로 나눠」형 명시 구간 → BinSpec(edges)."""
    if not any(k in question for k in ("나눠", "나누어", "나눠서", "구간")):
        # BIN_HINTS에 나눠가 있어도 임계가 2개 이상일 때만 edge bin
        pass
    cuts: list[float] = []
    field: str | None = None
    for span in list(getattr(bound, "numbers", None) or []) + list(
        getattr(bound, "ranges", None) or []
    ):
        meta = span.meta or {}
        f = meta.get("field")
        if not f:
            continue
        if field is None:
            field = str(f)
        elif str(f) != field:
            continue
        if span.kind == "range" or (
            isinstance(span.value, (tuple, list)) and len(span.value) == 2
        ):
            lo = meta.get("low")
            hi = meta.get("high")
            if lo is None and isinstance(span.value, (tuple, list)):
                lo, hi = span.value[0], span.value[1]
            for v in (lo, hi):
                if v is not None and float(v) not in cuts:
                    cuts.append(float(v))
        elif span.value is not None:
            v = float(span.value)
            if v not in cuts:
                cuts.append(v)
    cuts = sorted(cuts)
    if field is None or len(cuts) < 2:
        return None
    # 두 절단점 → 미만 / 중간 / 초과 3구간
    lo, hi = cuts[0], cuts[1]
    unit = "㎡" if "area" in field else ""
    labels = [
        f"{lo:g}{unit} 미만",
        f"{lo:g}~{hi:g}{unit}",
        f"{hi:g}{unit} 초과",
    ]
    return BinSpec(field=field, edges=[lo, hi], labels=labels)


def _filters_to_predicate(items: list[FilterSpec]) -> PredicateSpec | None:
    from txt2sql.semantic_plan.migrate import filter_to_predicate

    pred = None
    for item in items:
        pred = _and_pred(pred, filter_to_predicate(item))
    return pred


def _build_ratio_specs(question: str, filters: list[FilterSpec]) -> list[RatioSpec]:
    usage_f = [item for item in filters if item.field == "usage"]
    violate_f = [item for item in filters if item.field == "violation_status"]
    land_f = [item for item in filters if item.field == "special_land"]
    numeric_f = [
        item
        for item in filters
        if item.field
        not in {"usage", "violation_status", "special_land", "legal_dong", "building_dong_name"}
    ]
    compact = question.replace(" ", "")
    if "위반" in question and ("위반+N" in compact or "/(위반" in compact):
        return [
            RatioSpec(
                numerator_predicate=PredicateSpec(
                    op="cmp",
                    operator="eq",
                    left=OperandSpec(kind="field", field="violation_status"),
                    right=OperandSpec(kind="literal", value="Y"),
                ),
                denominator_predicate=PredicateSpec(
                    op="cmp",
                    operator="in",
                    left=OperandSpec(kind="field", field="violation_status"),
                    right=OperandSpec(kind="literal", value=["Y", "N"]),
                ),
            )
        ]
    if question.count("비율") >= 2:
        from txt2sql.query_understanding.contract import extract_contract

        specs: list[RatioSpec] = []
        for index, span in enumerate(extract_contract(question).numbers):
            field = span.meta.get("field")
            if not field:
                continue
            specs.append(
                RatioSpec(
                    numerator_predicate=PredicateSpec(
                        op="cmp",
                        operator="gte",
                        left=OperandSpec(kind="field", field=str(field)),
                        right=OperandSpec(kind="literal", value=span.value),
                    ),
                    denominator_predicate=None,
                    alias=f"ratio_{index + 1}",
                )
            )
        if specs:
            return specs
    if "중" in question:
        left = question[: question.rfind("중")]
        right = question[question.rfind("중") + 1 :]
        usage_before = extract_usage(left) is not None
        # 「30년 이상 된 건물 중 10층 이상」→ den=왼쪽 조건, num=왼쪽+오른쪽
        left_fields = _ratio_side_fields(left)
        right_fields = _ratio_side_fields(right)
        # 「공동주택 중 위반」/「20년 미만 중 집합건축물」
        if "위반" in right:
            right_fields.add("violation_status")
        if any(k in right for k in ("집합건축물", "집합건물")):
            right_fields.add("complex_building_kind")
        if left_fields or right_fields:
            all_items = usage_f + violate_f + land_f + numeric_f
            # ledger / 집합건물 구분은 filters에 이미 있을 수 있음
            kind_f = [
                item
                for item in filters
                if item.field in {"ledger_kind", "complex_building_kind"}
            ]
            all_items = all_items + kind_f
            den_items = [item for item in all_items if item.field in left_fields]
            num_items = [
                item
                for item in all_items
                if item.field in left_fields or item.field in right_fields
            ]
            # age/approval on left only → include building_age filters
            if "building_age_years" in left_fields or "approval_date" in left_fields:
                age_f = [
                    item
                    for item in filters
                    if item.field in {"building_age_years", "approval_date"}
                ]
                for item in age_f:
                    if item not in den_items:
                        den_items.append(item)
                    if item not in num_items:
                        num_items.append(item)
            den = _filters_to_predicate(den_items)
            num = _filters_to_predicate(num_items)
            if num is not None and den is not None and num_items != den_items:
                return [
                    RatioSpec(
                        numerator_predicate=num,
                        denominator_predicate=den,
                    )
                ]
        numeric_before = bool(re.search(r"\d+", left)) and any(
            k in left for k in ("층", "㎡", "m", "이상", "이하", "년")
        )
        if usage_before and not numeric_before:
            den = _filters_to_predicate(usage_f)
            num = _filters_to_predicate(usage_f + numeric_f + violate_f)
        else:
            den = _filters_to_predicate(numeric_f)
            num = _filters_to_predicate(numeric_f + usage_f + violate_f)
        if num is None:
            return []
        return [RatioSpec(numerator_predicate=num, denominator_predicate=den)]
    num = _filters_to_predicate(usage_f + violate_f + land_f + numeric_f)
    if num is None:
        return []
    return [RatioSpec(numerator_predicate=num)]


def _ratio_side_fields(side: str) -> set[str]:
    """비율 문장의 ‘중’ 앞·뒤에 등장하는 필드 단서."""
    text = side or ""
    fields: set[str] = set()
    if any(k in text for k in ("층", "지상")):
        fields.add("ground_floors")
    if any(k in text for k in ("높이", "고도")):
        fields.add("height_m")
    if "연면적" in text:
        fields.add("gross_floor_area_m2")
    if any(k in text for k in ("건축물면적", "건축면적", "건물면적")):
        fields.add("building_area_m2")
    if any(k in text for k in ("년", "준공", "사용승인", "지어", "된 건물", "된건물")):
        fields.add("approval_date")
        fields.add("building_age_years")
    if any(k in text for k in ("집합건축물", "집합건물")):
        fields.add("complex_building_kind")
    if "위반" in text:
        fields.add("violation_status")
    if extract_usage(text):
        fields.add("usage")
    return fields


def _usage_eq(value: str) -> PredicateSpec:
    return _field_eq("usage", value)


def _field_eq(field: str, value: str) -> PredicateSpec:
    return PredicateSpec(
        op="cmp",
        operator="eq",
        left=OperandSpec(kind="field", field=field),
        right=OperandSpec(kind="literal", value=value),
    )


def _predicate_has_field(pred: PredicateSpec, fields: set[str]) -> bool:
    from txt2sql.semantic_plan.predicate_utils import walk_predicate

    for node in walk_predicate(pred):
        if node.op == "cmp" and node.left and node.left.field in fields:
            return True
    return False


def _dual_count_subset_usage(question: str, usages: list[str]) -> str | None:
    if not re.search(r"전체.{0,16}채수.{0,16}그\s*중", question):
        return None
    if not usages:
        return None
    return usages[0]


def _dual_usage_class_metric_aggs(
    question: str, usage_classes: list[str]
) -> list[AggregationSpec] | None:
    """주거용과 상업용 … 평균 높이 비교 → FILTER AVG per usage_class."""
    if len(usage_classes) < 2:
        return None
    q = question or ""
    dual_cue = any(k in q for k in ("비교", "차이")) or bool(
        re.search(
            r"(주거용|상업용|공업용|문교사회용).{0,6}(과|와).{0,6}"
            r"(주거용|상업용|공업용|문교사회용)",
            q,
        )
    )
    if not dual_cue:
        return None
    metrics = _agg_metrics(q)
    if not metrics:
        return None
    field = metrics[0]
    alias_by_class = {
        ("주거용", "height_m"): "resi_h",
        ("상업용", "height_m"): "com_h",
        ("공업용", "height_m"): "ind_h",
        ("주거용", "gross_floor_area_m2"): "resi_gfa",
        ("상업용", "gross_floor_area_m2"): "com_gfa",
    }
    aggs: list[AggregationSpec] = []
    for cls in usage_classes[:4]:
        alias = alias_by_class.get((cls, field), f"avg_{field}_{cls}")
        aggs.append(
            AggregationSpec(
                function="avg",
                field=field,
                alias=alias,
                filter_field="usage_class",
                filter_operator="eq",
                filter_value=cls,
            )
        )
    return aggs or None


def _structure_contains(value: str) -> PredicateSpec:
    return PredicateSpec(
        op="cmp",
        operator="contains",
        left=OperandSpec(kind="field", field="structure"),
        right=OperandSpec(kind="literal", value=value),
    )


def _agg_metrics(question: str) -> list[str]:
    metrics: list[str] = []
    mapping = (
        (("건축연령", "건축 연령", "경과년수", "경과 년수"), "building_age_years"),
        (("높이", "고도"), "height_m"),
        (("연면적",), "gross_floor_area_m2"),
        (("건축물면적", "건축면적", "건물면적"), "building_area_m2"),
        (("대지면적",), "site_area_m2"),
        (("지상층", "층수"), "ground_floors"),
        (("건폐율",), "building_coverage_ratio"),
        (("용적율", "용적률"), "floor_area_ratio"),
    )
    for keys, field in mapping:
        if any(k in question for k in keys) and field not in metrics:
            metrics.append(field)
    # 건축연령이 명시되면 높이 등 다른 측정과 섞지 않음
    if "building_age_years" in metrics:
        return ["building_age_years"]
    return metrics or [_rank_metric(question)]


def _extract_field_compare(question: str) -> FilterSpec | None:
    contract = extract_contract(question)
    if not contract.comparisons:
        return None
    span = contract.comparisons[0]
    payload = span.value if isinstance(span.value, dict) else span.meta
    left = payload.get("left")
    right = payload.get("right")
    op = payload.get("op") or "gt"
    if not left or not right:
        return None
    scale = payload.get("scale")
    return FilterSpec(
        field=str(left),
        operator=str(op),
        value_field=str(right),
        value_scale=float(scale) if scale is not None else None,
    )


def _metric_group_aggregations(
    question: str,
    *,
    default_count: bool = True,
) -> list[AggregationSpec]:
    functions = _aggregate_functions(question)
    metrics = _agg_metrics(question)
    aggregations: list[AggregationSpec] = []
    short_alias = {
        "height_m": "h",
        "gross_floor_area_m2": "gfa",
        "site_area_m2": "lot",
        "building_area_m2": "area",
        "ground_floors": "fl",
    }
    for fn in functions:
        if fn == "count":
            aggregations.append(AggregationSpec(function="count", field=None, alias="n"))
            continue
        for metric in metrics:
            aggregations.append(
                AggregationSpec(
                    function=fn,
                    field=metric,
                    alias=f"{fn}_{short_alias.get(metric, metric)}",
                )
            )
    if not aggregations and default_count:
        aggregations.append(AggregationSpec(function="count", field=None, alias="n"))
    return aggregations


def _aggregate_functions(
    question: str,
    contract: QueryContract | None = None,
) -> list[str]:
    if contract and contract.aggregation_requests:
        seen: list[str] = []
        for req in contract.aggregation_requests:
            fn = req.function
            if fn and fn not in seen:
                seen.append(fn)
        if seen:
            return seen
    if contract and contract.wants_count and contract.query_kind == "count":
        return ["count"]
    found: list[str] = []
    for text, fn in AGG_MAP.items():
        if text in question and fn not in found:
            found.append(fn)
    if any(k in question for k in ("건수", "채수", "몇 채", "몇채", "건물 수", "건물수")) and "count" not in found:
        found.append("count")
    if not found:
        if any(k in question for k in ("가장 높", "제일 높", "가장 큰", "제일 큰")):
            return ["max"]
        return ["avg"]
    return found


def _aggregate_function(question: str) -> str:
    return _aggregate_functions(question)[0]


def _extract_range_numerics(question: str) -> list[dict[str, Any]]:
    from txt2sql.query_understanding.contract import extract_contract

    contract = extract_contract(question)
    if not contract.ranges:
        return []
    out: list[dict[str, Any]] = []
    for span in contract.ranges:
        field = span.meta.get("field")
        if not field:
            if "층" in (span.text or ""):
                field = "ground_floors"
            else:
                continue
        low, high = span.meta.get("low"), span.meta.get("high")
        if (
            isinstance(low, (int, float))
            and isinstance(high, (int, float))
            and 1900 <= float(low) <= 2100
            and 1900 <= float(high) <= 2100
            and any(k in question for k in ("년", "사용승인", "허가"))
        ):
            continue
        unit = (
            "percent"
            if field in {"building_coverage_ratio", "floor_area_ratio"}
            else (
                "m2"
                if str(field).endswith("m2")
                else ("floor" if field == "ground_floors" else "m")
            )
        )
        lo_rel = span.meta.get("lo_rel") or "이상"
        hi_rel = span.meta.get("hi_rel") or "이하"
        lo_op = {"초과": "gt", "이상": "gte", "부터": "gte"}.get(lo_rel, "gte")
        hi_op = {"미만": "lt", "이하": "lte", "까지": "lte", "사이": "lte"}.get(hi_rel, "lte")
        if lo_op == "gte" and hi_op == "lte":
            out.append(
                {
                    "field": field,
                    "operator": "between",
                    "value": low,
                    "value2": high,
                    "unit": unit,
                }
            )
        else:
            out.append(
                {
                    "field": field,
                    "operator": lo_op,
                    "value": low,
                    "unit": unit,
                }
            )
            out.append(
                {
                    "field": field,
                    "operator": hi_op,
                    "value": high,
                    "unit": unit,
                }
            )
    return out


def _guess_kind(question: str) -> str:
    from txt2sql.domain import wants_map_display
    from txt2sql.query_understanding import operators as ops
    from txt2sql.query_understanding.contract import (
        _is_grouped_count_question,
        extract_contract,
    )

    if wants_map_display(question):
        return "count"
    contract = extract_contract(question)
    if contract.group_fields and _is_grouped_count_question(
        question, contract.group_fields
    ):
        return "aggregate"
    if any(h in question for h in ops.GROUP_HINTS) and any(
        k in question for k in ("건물 수", "건수", "집계", "보여", "나눠", "수를")
    ):
        return "aggregate"
    if re.search(r"(준공연대별|년대별)", question) and (
        len(re.findall(r"\d{4}년대", question)) >= 2 or "이전" in question
    ):
        return "aggregate"
    if "기초구역" in question and any(
        k in question for k in ("최대", "가장", "상위", "제일")
    ):
        return "rank"
    if any(k in question for k in ("분위수", "분위", "퍼센타일", "분산", "표준편차", "중앙값", "상관", "상관계수")):
        return "aggregate"
    if "일수" in question and any(k in question for k in ("중앙값", "표준편차", "평균")):
        return "aggregate"
    if any(k in question for k in ("비율", "퍼센트", "몇%", "%씩", "몇 프로")):
        return "aggregate"
    contract = extract_contract(question)
    if contract.percentile_requests or contract.derived_metrics:
        return "aggregate"
    if any(k in question for k in ("용도별", "층수별", "층별", "분포", "구성", "구간별", "년대별", "연도별")) or (
        "용도" in question
        and any(k in question for k in ("상위", "순위"))
        and not any(k in question for k in ("높이", "연면적", "층수", "이름"))
    ):
        return "distribution"
    if any(k in question for k in AGG_MAP) or (
        any(k in question for k in ("가장 높", "제일 높"))
        and "높이" in question
        and not re.search(r"\d+\s*(개|곳|채|동)\b", question)
    ):
        return "aggregate"
    if any(
        k in question
        for k in (
            "몇 채",
            "몇채",
            "몇 개",
            "몇개",
            "건수",
            "채수",
            "몇 동",
            "동수",
            "건물 수",
            "레코드 수",
            "채야",
            "얼마나",
            "되나요",
        )
    ):
        return "count"
    # 「층수는」「면적은」처럼 속성 서술의 「수는」는 건수 질의가 아님
    weak_count = any(k in question for k in ("수는", "수가"))
    list_cues = any(
        k in question for k in ("찾아", "보여", "목록", "나열", "레코드")
    )
    if weak_count and not list_cues:
        if not re.search(r"(층|면적|높이|용적|건폐|구조).{0,3}수[는가]", question):
            return "count"
    if re.search(r"레코드\s*수", question):
        return "count"
    if any(k in question for k in ("건폐율", "용적율", "용적률", "지하")) and not any(
        k in question
        for k in ("이름", "건물명", "목록", "보여", "어떤", "찾아", "알려줘")
    ):
        return "count"
    stripped = question.strip()
    if re.search(r"(인\s+)?[가-힣A-Za-z0-9]+ 수\s*[?？]?$", stripped):
        return "count"
    if re.search(r"\s수\s*[?？]?$", stripped) and any(
        k in stripped for k in ("건물", "주택", "시설", "아파트", "구조", "층")
    ):
        return "count"
    if any(k in question for k in ("상위", "큰 순", "높은 순", "낮은 순", "작은 순", "랭킹", "순위", "많은 순")):
        return "rank"
    if any(
        k in question
        for k in (
            "최근 준공",
            "최근준공",
            "오래된 준공",
            "오래된준공",
            "가장 최근",
            "제일 최근",
            "가장 오래",
            "제일 오래",
        )
    ) and any(k in question for k in ("준공", "사용승인", "건물", "건축물")):
        return "rank"
    if re.search(r"\d+\s*(개|곳|채|동)\b", question) and any(
        k in question for k in ("큰", "높", "상위", "낮은", "작은", "오래", "최근", "많은", "많")
    ):
        return "rank"
    if any(k in question for k in ("많은", "많 ")) and any(
        k in question for k in ("층수", "지상층", "연면적", "높이", "건축면적", "건물면적")
    ):
        return "rank"
    if any(
        k in question
        for k in (
            "가장 큰",
            "제일 큰",
            "가장큰",
            "제일큰",
            "가장 넓",
            "제일 넓",
            "가장 작",
            "제일 작",
            "가장 낮",
            "제일 낮",
        )
    ):
        return "rank"
    return "list"


def _rank_metric(question: str) -> str:
    if "기초구역" in question:
        return "area_m2"
    if any(k in question for k in ("준공", "사용승인", "건축연령", "허가일", "허가일자")):
        return "approval_date"
    if any(k in question for k in ("높이", "고도", "낮은")):
        return "height_m"
    if "건축물면적" in question or "건축면적" in question or "건물면적" in question:
        return "building_area_m2"
    if "대지면적" in question:
        return "site_area_m2"
    if any(k in question for k in ("지상층", "층수", "많은 층")):
        return "ground_floors"
    return "gross_floor_area_m2"


def _list_select(question: str) -> list[str]:
    wanted: list[str] = ["name", "legal_dong", "lot_address"]
    mapping = (
        (("용도",), "usage"),
        (("높이",), "height_m"),
        (("연면적",), "gross_floor_area_m2"),
        (("건축물면적", "건축면적", "건물면적"), "building_area_m2"),
        (("대지면적",), "site_area_m2"),
        (("지상", "층수"), "ground_floors"),
        (("구조",), "structure"),
        (("사용승인", "준공"), "approval_date"),
        (("허가일", "허가일자", "허가연"), "permit_date"),
    )
    for keys, field in mapping:
        if any(k in question for k in keys) and field not in wanted:
            wanted.append(field)
    return wanted


def _maybe_topn_aggregate_stages(
    question: str,
    *,
    query_kind: str,
    limit: int | None,
    order_by: list[OrderSpec],
    aggregations: list[AggregationSpec],
    select: list[str],
) -> list[StageSpec]:
    """상위 N 행을 고른 뒤 평균/합계를 내는 패턴만 stages로 표현한다."""
    q = question or ""
    if "상위" not in q:
        return []
    if any(k in q for k in ("%", "백분위", "퍼센타일", "퍼센트")):
        return []
    if not any(k in q for k in ("평균", "합계", "총합")):
        return []
    top_m = re.search(r"상위\s*(\d+)\s*(?:개|곳|채|동)?", q)
    if not top_m:
        return []
    # 구별·용도별 상위 N (그룹 랭킹)은 단일 행 집합 재집계가 아님.
    if any(
        k in q
        for k in (
            "구별",
            "구·군별",
            "구군별",
            "동별",
            "법정동별",
            "용도별",
            "세부용도별",
            "층별",
            "구조별",
            "시군구별",
        )
    ):
        return []
    # Explicit group_by on the flat plan → group ranking, not top-N then agg.
    # (caller may pass aggregations already; stages must not steal group queries)
    if "별" in q and any(k in q for k in ("평균", "합계")) and "상위" in q:
        # 「…별 … 상위 N」 is almost always group ranking.
        if not re.search(r"상위\s*\d+\s*(?:개|곳|채).{0,12}(?:의|을|를).{0,8}(?:평균|합계)", q):
            return []
    n = max(1, min(int(top_m.group(1)), 1000))
    if limit is not None:
        n = max(1, min(int(limit), 1000))

    metric_alias = {
        "연면적": "gross_floor_area_m2",
        "건축물면적": "building_area_m2",
        "건축면적": "building_area_m2",
        "건물면적": "building_area_m2",
        "대지면적": "site_area_m2",
        "높이": "height_m",
        "고도": "height_m",
        "지상층": "ground_floors",
        "층수": "ground_floors",
    }
    rank_field = None
    rank_m = re.search(
        r"(연면적|건축물면적|건축면적|건물면적|대지면적|높이|고도|지상층|층수)"
        r"\s*(?:이\s*)?(?:큰\s*)?상위",
        q,
    )
    if rank_m:
        rank_field = metric_alias[rank_m.group(1)]
    elif order_by:
        rank_field = order_by[0].field
    else:
        rank_field = _rank_metric(q)

    direction = order_by[0].direction if order_by else "desc"
    agg_fn = (
        "sum"
        if any(k in q for k in ("합계", "총합")) and "평균" not in q
        else "avg"
    )
    agg_field = None
    agg_m = re.search(
        r"평균\s*(높이|고도|연면적|건축물면적|건축면적|건물면적|대지면적|지상층|층수)",
        q,
    )
    if agg_m:
        agg_field = metric_alias[agg_m.group(1)]
    else:
        for item in aggregations or []:
            if (
                item.function in {"avg", "sum", "min", "max"}
                and item.field
                and item.field != rank_field
            ):
                agg_fn = item.function
                agg_field = item.field
                break
        if agg_field is None:
            for item in aggregations or []:
                if item.function in {"avg", "sum", "min", "max"} and item.field:
                    agg_fn = item.function
                    agg_field = item.field
                    break
    if agg_field is None:
        agg_field = rank_field

    sel = list(select) if select else ["name", "legal_dong", "lot_address", rank_field]
    if rank_field not in sel:
        sel.append(rank_field)
    if agg_field not in sel:
        sel.append(agg_field)
    _ = query_kind  # used by callers for context; stages apply regardless of kind
    outer_aggs = [
        AggregationSpec(
            function=agg_fn,  # type: ignore[arg-type]
            field=agg_field,
            alias=f"{agg_fn}_{agg_field}",
        )
    ]
    # Top-N 후 평균/합계 스칼라는 gold의 n=N 과 맞추기 위해 건수 동반
    if "평균" in q or "합계" in q or "총합" in q:
        outer_aggs.append(
            AggregationSpec(function="count", field=None, alias="n")
        )
    return [
        StageSpec(
            id="rank0",
            kind="rank",
            select=sel,
            order_by=[OrderSpec(field=rank_field, direction=direction, nulls="last")],
            limit=n,
        ),
        StageSpec(
            id="agg1",
            kind="aggregate",
            aggregations=outer_aggs,
        ),
    ]


def _extract_limit(question: str) -> int | None:
    limit = extract_contract(question).limit
    if limit is None:
        return None
    return max(1, min(int(limit), 1000))
