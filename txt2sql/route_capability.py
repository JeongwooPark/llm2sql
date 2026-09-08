"""레거시 라우트가 Query Contract를 필드 단위로 100% 지원할 때만 실행한다."""

from __future__ import annotations

from dataclasses import dataclass, field

from txt2sql.query_understanding.contract import QueryContract, extract_contract


@dataclass(frozen=True)
class RouteCapability:
    route: str
    entities: frozenset[str] = field(default_factory=lambda: frozenset({"building"}))
    supports_or: bool = False
    supports_not: bool = False
    supports_between: bool = False
    supports_field_compare: bool = False
    supports_multiple_predicates: bool = False
    supports_group_by: bool = False
    supported_group_fields: frozenset[str] = field(default_factory=frozenset)
    supports_multiple_aggregates: bool = False
    supported_aggregations: frozenset[str] = field(default_factory=frozenset)
    supports_spatial: bool = False
    supports_rank: bool = False
    supports_top_n: bool = False
    supports_output_projection: bool = False
    supports_ratio: bool = False
    supports_conditional_ratio: bool = False
    supports_multiple_ratios: bool = False
    supports_percentile: bool = False
    supports_derived_metric: bool = False
    supports_fixed_bins: bool = False
    supports_basement: bool = False
    supports_temporal: bool = False
    supported_fields: frozenset[str] = field(default_factory=frozenset)


_THRESHOLD_FIELDS = frozenset(
    {
        "height_m",
        "gross_floor_area_m2",
        "building_area_m2",
        "site_area_m2",
        "ground_floors",
        "basement_floors",
        "building_coverage_ratio",
        "floor_area_ratio",
    }
)
_PLACE_FIELDS = frozenset({"legal_dong", "sigungu_name", "name"})
_COUNT_ONLY = frozenset({"count"})
_BASIC_AGGS = frozenset({"count", "avg", "sum", "min", "max", "median"})
_ALL_AGGS = _BASIC_AGGS | frozenset({"stddev", "percentile"})
_ALL_GROUPS = frozenset(
    {"usage", "structure", "legal_dong", "sigungu_name", "ground_floors"}
)
_BUILDING_ONLY = _THRESHOLD_FIELDS | {
    "usage",
    "structure",
    "violation_status",
    "special_land",
}

SIMPLE_COUNT = RouteCapability(
    route="legacy_simple_count",
    supports_multiple_predicates=False,
    supported_aggregations=_COUNT_ONLY,
    supported_fields=_PLACE_FIELDS,
)
USAGE_COUNT = RouteCapability(
    route="building_usage_count",
    supported_aggregations=_COUNT_ONLY,
    supported_fields=_PLACE_FIELDS | {"usage"},
)
STRUCTURE_COUNT = RouteCapability(
    route="building_structure_count",
    supports_multiple_predicates=True,
    supported_aggregations=_COUNT_ONLY,
    supported_fields=_PLACE_FIELDS | {"structure", "violation_status", "usage"},
)
SPECIAL_LAND_COUNT = RouteCapability(
    route="building_special_land_count",
    supported_aggregations=_COUNT_ONLY,
    supported_fields=_PLACE_FIELDS | {"special_land"},
)
THRESHOLD_COUNT = RouteCapability(
    route="building_threshold_count",
    supports_between=True,
    supports_multiple_predicates=True,
    supported_aggregations=_COUNT_ONLY,
    supported_fields=_THRESHOLD_FIELDS | {"usage"} | _PLACE_FIELDS,
)
THRESHOLD_LIST = RouteCapability(
    route="building_threshold_list",
    supports_between=True,
    supports_multiple_predicates=True,
    supports_output_projection=True,
    supports_rank=True,
    supports_top_n=True,
    supported_aggregations=_COUNT_ONLY,
    supported_fields=_THRESHOLD_FIELDS | {"usage"} | _PLACE_FIELDS,
)
STRUCTURE_LIST = RouteCapability(
    route="building_structure_list",
    supports_output_projection=True,
    supports_rank=True,
    supports_top_n=True,
    supported_aggregations=_COUNT_ONLY,
    supported_fields=_PLACE_FIELDS | {"structure"},
)
SPECIAL_LAND_LIST = RouteCapability(
    route="building_special_land_list",
    supports_output_projection=True,
    supports_rank=True,
    supports_top_n=True,
    supported_aggregations=_COUNT_ONLY,
    supported_fields=_PLACE_FIELDS | {"special_land", "lot_address"},
)
PROFILE = RouteCapability(
    route="building_profile",
    supported_aggregations=frozenset(),
    supported_fields=_PLACE_FIELDS | {"usage"},
)
RANK = RouteCapability(
    route="building_rank",
    supports_rank=True,
    supports_top_n=True,
    supports_output_projection=True,
    supported_fields=frozenset(
        {
            "height_m",
            "gross_floor_area_m2",
            "building_area_m2",
            "site_area_m2",
            "ground_floors",
            "name",
            "legal_dong",
            "lot_address",
            "usage",
        }
    ),
)
NAME_LOOKUP = RouteCapability(
    route="building_name_lookup",
    supports_output_projection=True,
    supported_fields=_PLACE_FIELDS | {"lot_address", "usage", "height_m", "ground_floors"},
)
INDUSTRIAL = RouteCapability(
    route="industrial",
    entities=frozenset({"industrial_complex", "building"}),
    supports_spatial=True,
    supports_group_by=True,
    supported_group_fields=frozenset({"name", "industrial_complex", "sigungu_name"}),
    supports_rank=True,
    supports_top_n=True,
    supports_output_projection=True,
    supported_aggregations=_BASIC_AGGS,
    supported_fields=_PLACE_FIELDS
    | _THRESHOLD_FIELDS
    | {"usage", "name", "area_m2", "gross_floor_area_m2"},
)
INDUSTRIAL_ADMIN_GROUP = RouteCapability(
    route="industrial_admin_sig_group",
    entities=frozenset({"industrial_complex", "admin_area"}),
    supports_spatial=True,
    supports_group_by=True,
    supported_group_fields=frozenset({"sigungu_name", "adm_sig", "admin_sigungu"}),
    supported_aggregations=frozenset({"count"}),
    supported_fields=_PLACE_FIELDS,
)
D010 = RouteCapability(
    route="d010",
    supports_between=True,
    supports_multiple_predicates=True,
    supports_rank=True,
    supports_top_n=True,
    supports_output_projection=True,
    supported_aggregations=_COUNT_ONLY,
    supports_temporal=True,
    supported_fields=_THRESHOLD_FIELDS
    | {
        "violation_status",
        "special_land",
        "building_dong_name",
        "lot_address",
        "approval_date",
    }
    | _PLACE_FIELDS,
)
D198 = RouteCapability(
    route="d198",
    supports_between=True,
    supports_multiple_predicates=True,
    supports_group_by=True,
    supported_group_fields=frozenset({"legal_dong", "sigungu_name", "usage", "detail_usage"}),
    supports_rank=True,
    supports_top_n=True,
    supports_fixed_bins=True,
    supports_output_projection=True,
    supported_aggregations=_COUNT_ONLY,
    supports_temporal=True,
    supported_fields=_THRESHOLD_FIELDS
    | {
        "usage",
        "detail_usage",
        "usage_class",
        "approval_date",
        "permit_date",
        "ledger_kind",
        "complex_building_kind",
    }
    | _PLACE_FIELDS,
)
BAS = RouteCapability(
    route="bas",
    entities=frozenset({"basic_zone"}),
    supports_group_by=True,
    supported_group_fields=frozenset({"legal_dong", "sigungu_name", "zone_name"}),
    supports_rank=True,
    supports_top_n=True,
    supports_output_projection=True,
    supported_aggregations=_COUNT_ONLY,
    supported_fields=frozenset({"area_m2", "legal_dong", "sigungu_name", "zone_name", "zone_code"}),
)
SPATIAL = RouteCapability(
    route="spatial",
    entities=frozenset({"building", "basic_zone"}),
    supports_spatial=True,
    supports_group_by=True,
    supported_group_fields=frozenset({"basic_zone", "bas_id"}),
    supported_aggregations=_COUNT_ONLY,
    supports_output_projection=True,
    supported_fields=_PLACE_FIELDS | {"usage", "basic_zone", "bas_id"},
)
PLAN_ROUTE = RouteCapability(
    route="semantic_plan",
    entities=frozenset(
        {"building", "admin_area", "basic_zone", "industrial_complex"}
    ),
    supports_or=True,
    supports_not=True,
    supports_between=True,
    supports_field_compare=True,
    supports_multiple_predicates=True,
    supports_group_by=True,
    supported_group_fields=_ALL_GROUPS,
    supports_multiple_aggregates=True,
    supported_aggregations=_ALL_AGGS,
    supports_spatial=True,
    supports_rank=True,
    supports_top_n=True,
    supports_output_projection=True,
    supports_ratio=True,
    supports_conditional_ratio=True,
    supports_multiple_ratios=True,
    supports_percentile=True,
    supports_derived_metric=True,
    supports_fixed_bins=True,
    supports_basement=True,
    supports_temporal=True,
)


def capability_for(route: str) -> RouteCapability:
    intent = (route or "").strip()
    from txt2sql.count_routes import PRIORITY_COUNT_INTENTS

    if intent in PRIORITY_COUNT_INTENTS or intent in {
        "building_industrial_bas_overlap",
        "followup_count_display",
    }:
        return PLAN_ROUTE
    if intent == "semantic_plan" or intent.startswith("semantic_plan"):
        return PLAN_ROUTE
    if intent == "building_profile" or intent == "building_profile_compare":
        return PROFILE
    if intent.startswith("building_rank_"):
        return RANK
    if intent == "building_name_lookup":
        return NAME_LOOKUP
    if intent == "bas_admin_overlap":
        return SPATIAL
    if intent == "industrial_count_by_sigungu":
        return INDUSTRIAL_ADMIN_GROUP
    if intent == "industrial_admin_sig_group":
        return INDUSTRIAL_ADMIN_GROUP
    if intent.startswith("industrial_") or intent == "buildings_in_industrial":
        return INDUSTRIAL
    if intent == "dual_gu_approval_year_median":
        return PLAN_ROUTE
    if intent == "sigungu_site_avg_vs_city" or intent == "sigungu_avg_ground_floors":
        return RouteCapability(
            route=intent,
            supports_group_by=True,
            supported_group_fields=frozenset({"sigungu_name"}),
            supported_aggregations=_BASIC_AGGS,
            supports_output_projection=True,
            supported_fields=_THRESHOLD_FIELDS | _PLACE_FIELDS,
        )
    if intent.startswith("d198_") or intent.startswith("building_age"):
        return D198
    if intent.startswith("d010_attr") or intent.startswith("d010_"):
        return D010
    if intent.startswith("bas_") or intent.startswith("bas_area"):
        return BAS
    if (
        intent.startswith("spatial_")
        or intent.startswith("place_buffer")
        or intent.startswith("buffer_")
        or intent in {
            "building_in_dong_spatial",
            "building_in_dong_spatial_list",
        }
    ):
        return SPATIAL
    if intent == "building_rank_compare":
        return RANK
    if intent == "building_usage_count" or intent == "building_admin_dong_usage_count":
        return USAGE_COUNT
    if intent == "building_structure_count":
        return STRUCTURE_COUNT
    if intent == "building_special_land_count":
        return SPECIAL_LAND_COUNT
    if intent in {
        "building_place_count",
    }:
        return SIMPLE_COUNT
    if intent in {
        "building_height_count",
        "building_floor_count",
        "building_attr_count",
        "building_area_threshold_count",
    }:
        return THRESHOLD_COUNT
    if intent in {
        "building_area_threshold_list",
        "building_height_threshold_list",
        "building_floor_threshold_list",
        "building_attr_list",
        "building_area_topn",
        "building_area_top1_value",
    }:
        return THRESHOLD_LIST
    if intent in {
        "building_structure_list",
    }:
        return STRUCTURE_LIST
    if intent in {
        "building_special_land_list",
    }:
        return SPECIAL_LAND_LIST
    return SIMPLE_COUNT


def route_cost(intent: str) -> int:
    name = (intent or "").strip()
    rules: list[tuple[str, int]] = [
        ("building_place_count", 10),
        ("building_usage_count", 12),
        ("building_structure_count", 12),
        ("building_special_land_count", 12),
        ("building_admin_dong_usage_count", 14),
        ("building_height_count", 20),
        ("building_floor_count", 20),
        ("building_attr_count", 20),
        ("building_area_threshold_count", 20),
        ("building_area_threshold_list", 25),
        ("building_height_threshold_list", 25),
        ("building_floor_threshold_list", 25),
        ("building_name_lookup", 28),
        ("building_rank_", 30),
        ("industrial_", 45),
        ("buildings_in_industrial", 45),
        ("building_profile", 40),
        ("d010_attr", 50),
        ("d198_", 55),
        ("building_age", 55),
        ("bas_", 60),
        ("spatial_", 70),
        ("place_buffer", 70),
        ("buffer_", 70),
        # Physical executor cost after LogicalPlan is fixed — not a
        # competing "last resort" against understanding (v2).
        ("semantic_plan", 90),
    ]
    for prefix, cost in rules:
        if name == prefix or name.startswith(prefix):
            return cost
    return 80


def _constraint_fields(contract: QueryContract) -> set[str]:
    fields: set[str] = set()
    for span in contract.metrics:
        if span.value:
            fields.add(str(span.value))
        field = span.meta.get("field")
        if field:
            fields.add(str(field))
    for span in contract.numbers + contract.ranges:
        field = span.meta.get("field")
        if field:
            fields.add(str(field))
    return fields


def missing_requirements(capability: RouteCapability, contract: QueryContract) -> list[str]:
    missing: list[str] = []
    if any(span.kind == "or" for span in contract.boolean_ops) and not capability.supports_or:
        missing.append("or")
    if any(span.kind == "not" for span in contract.boolean_ops) and not capability.supports_not:
        missing.append("not")
    if contract.ranges and not capability.supports_between:
        missing.append("between")
    if contract.comparisons and not capability.supports_field_compare:
        missing.append("field_compare")
    pred_fields = _constraint_fields(contract)
    n_pred = len(pred_fields) + len(contract.comparisons)
    if n_pred >= 2 and not capability.supports_multiple_predicates:
        missing.append("multiple_predicates")
    needed_aggs = {item.function for item in contract.aggregation_requests}
    if len(needed_aggs) >= 2 and not capability.supports_multiple_aggregates:
        missing.append("multiple_aggregates")
    unsupported = needed_aggs - set(capability.supported_aggregations)
    if unsupported:
        missing.append("unsupported_aggregation")
        missing.extend(f"aggregation:{fn}" for fn in sorted(unsupported))
    group_fields = list(contract.group_fields)
    if group_fields:
        if not capability.supports_group_by:
            missing.append("group_by")
            missing.extend(f"group_by:{item}" for item in group_fields)
        elif capability.supported_group_fields and not set(group_fields) <= set(
            capability.supported_group_fields
        ):
            missing.append("unsupported_group_field")
    requires_rank = contract.operation in {"rank", "group_rank"} or bool(
        contract.order_requests
    )
    if requires_rank and not capability.supports_rank:
        missing.append("rank")
    if contract.limit is not None and requires_rank and not capability.supports_top_n:
        missing.append("top_n")
    metric_vals = {str(span.value) for span in contract.metrics if span.value}
    projection_needed = [
        field
        for field in contract.output_fields
        if field not in metric_vals or contract.operation in {"list", "rank", "group_rank"}
    ]
    if projection_needed and not capability.supports_output_projection:
        missing.append("output_projection")
    if contract.ratios and not capability.supports_ratio:
        missing.append("ratio")
    if any(item.has_denominator for item in contract.ratios) and not capability.supports_conditional_ratio:
        missing.append("conditional_ratio")
    if len(contract.ratios) >= 2 and not capability.supports_multiple_ratios:
        missing.append("multiple_ratios")
    if contract.percentile_requests and not capability.supports_percentile:
        missing.append("percentile")
    if contract.derived_metrics and not capability.supports_derived_metric:
        missing.append("derived_metric")
    if contract.fixed_bins and not capability.supports_fixed_bins:
        missing.append("fixed_bins")
    if contract.wants_spatial and not capability.supports_spatial:
        missing.append("spatial")
    if (
        contract.wants_basement or "basement_floors" in pred_fields
    ) and not capability.supports_basement:
        missing.append("basement")
    if contract.wants_temporal and not capability.supports_temporal:
        missing.append("temporal")
    threshold_used = pred_fields & _THRESHOLD_FIELDS
    if (
        capability.route in {"building_threshold_count", "building_threshold_list"}
        and len(threshold_used) >= 2
    ):
        missing.append("multiple_threshold_fields")
    if (
        threshold_used
        and capability.route in {"legacy_simple_count", "building_threshold_count"}
        and not contract.wants_count
    ):
        missing.append("explicit_count")
    if capability.entities == frozenset({"basic_zone"}) and pred_fields & _BUILDING_ONLY:
        missing.append("unsupported_field")
    if capability.supported_fields:
        extra = (pred_fields | set(contract.output_fields)) - set(capability.supported_fields)
        extra -= {"legal_dong", "sigungu_name", "name"}
        if extra:
            missing.append("unsupported_field")
            missing.extend(f"field:{item}" for item in sorted(extra))
    return list(dict.fromkeys(missing))


def missing_slots(capability: RouteCapability, contract: QueryContract) -> list[str]:
    return missing_requirements(capability, contract)


def contract_is_complete(contract: QueryContract) -> bool:
    return contract.is_sufficient()


def fully_supports(capability: RouteCapability, contract: QueryContract) -> bool:
    return len(missing_requirements(capability, contract)) == 0


def legacy_route_eligible(route: str, contract: QueryContract) -> bool:
    intent = (route or "").strip()
    cap = capability_for(intent)
    # Spatial templates are self-contained; dong-number tokens must not block them.
    if cap.supports_spatial and intent.startswith("spatial_"):
        return len(missing_requirements(cap, contract)) == 0
    if not contract_is_complete(contract):
        missing = missing_requirements(cap, contract)
        if not (contract.wants_count and not missing):
            return False
    return len(missing_requirements(cap, contract)) == 0


def route_allowed(intent: str, contract: QueryContract) -> bool:
    return legacy_route_eligible(intent, contract)


def pick_eligible_routed(routed_options: list, contract: QueryContract):
    """완전 커버 후보 중 최저비용 RoutedQuery."""
    eligible = []
    seen: set[str] = set()
    for routed in routed_options:
        if routed is None:
            continue
        intent = getattr(routed, "intent", None)
        if not intent or intent in seen:
            continue
        seen.add(intent)
        if legacy_route_eligible(intent, contract):
            eligible.append(routed)
    if not eligible:
        return None
    return min(eligible, key=lambda item: route_cost(item.intent))


def detect_route_candidates(
    question: str,
    contract: QueryContract | None = None,
    *,
    conn=None,
) -> list[str]:
    from txt2sql.intent_router import try_route
    from txt2sql.profile_qa import is_profile_question
    from txt2sql.route_dispatch import match_route

    match = match_route(question, conn=conn, contract=contract)
    candidates: list[str] = []
    if match.early is not None:
        candidates.append(match.early.intent)
    if is_profile_question(question):
        candidates.append("building_profile")
    deferred = match.deferred
    if deferred is None and match.early is None:
        deferred = try_route(question, conn=conn)
    if deferred is not None and deferred.intent not in candidates:
        candidates.append(deferred.intent)
    return candidates


def select_execution_path(
    question: str,
    *,
    conn=None,
    contract: QueryContract | None = None,
) -> str:
    """Contract → 완전 커버 최저비용 경로, 없으면 semantic_plan."""
    from txt2sql.intent_router import try_route
    from txt2sql.route_dispatch import match_route

    contract = contract or extract_contract(question)
    if not contract_is_complete(contract):
        return "semantic_plan"
    match = match_route(question, conn=conn, contract=contract)
    options = []
    if match.early is not None:
        options.append(match.early)
    deferred = match.deferred
    if deferred is None and match.early is None:
        deferred = try_route(question, conn=conn)
    if deferred is not None:
        options.append(deferred)
    picked = pick_eligible_routed(options, contract)
    if picked is not None:
        return picked.intent
    return "semantic_plan"
