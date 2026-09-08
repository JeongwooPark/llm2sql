"""SemanticQueryPlan 의미 검증. 실행 여부는 score가 아니라 status가 결정한다."""

from __future__ import annotations

import re

import psycopg

from txt2sql.domain import (
    AGE_HINTS,
    assumptions_include_permit_lag,
    is_permit_approval_lag_question,
    is_permit_lag_assumption,
    looks_like_age_question,
)
from txt2sql.gazetteer import find_places, load_gazetteer
from txt2sql.semantic_plan.catalog import get_entity, get_field
from txt2sql.semantic_plan.models import (
    ExpressionSpec,
    PlanValidationResult,
    PredicateSpec,
    SemanticQueryPlan,
    UnknownSemanticFieldError,
)

_MAX_FILTERS = 6
_MAX_SPATIAL = 2
_MAX_GROUP = 2
_MAX_AGG = 8
_MAX_ORDER = 3
_MAX_LIMIT = 1000
_V1_ENTITIES = frozenset(
    {"building", "admin_area", "basic_zone", "industrial_complex"}
)
_V1_SPATIAL = frozenset(
    {
        "within",
        "intersects",
        "within_distance",
        "outside_distance",
        "touches",
        "covered_by",
        "buffer",
        "nearest",
        "overlap_ratio",
    }
)


def validate_semantic_plan(
    plan: SemanticQueryPlan,
    question: str,
    *,
    conn: psycopg.Connection | None = None,
    contract=None,
) -> PlanValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    score = 1.0
    status = "ready"

    if plan.unsupported_reason:
        return PlanValidationResult(
            status="fallback",
            score=0.0,
            errors=[plan.unsupported_reason],
            warnings=warnings,
            plan=plan,
        )
    if plan.requires_clarification:
        return PlanValidationResult(
            status="clarify",
            score=max(0.0, score - 0.3),
            errors=list(plan.ambiguities) or ["clarification required"],
            warnings=warnings,
            plan=plan,
        )

    if plan.entity not in _V1_ENTITIES:
        return _fallback(plan, f"unsupported entity: {plan.entity}", score - 0.4)

    try:
        get_entity(plan.entity)
    except UnknownSemanticFieldError as exc:
        return _fallback(plan, str(exc), score - 0.4)

    if looks_like_age_question(question) or (
        any(h in question for h in AGE_HINTS)
        and not is_permit_approval_lag_question(question)
    ):
        from txt2sql.planner.semantic_executor import _parse_percentile_tail

        tail = _parse_percentile_tail(question)
        if tail is not None and tail[1] == "approval_date":
            # 최근 준공 연도 꼬리 평균은 전용 SQL이 승인일을 rank에 사용
            pass
        elif assumptions_include_permit_lag(plan.assumptions):
            # 허가↔승인 시차는 day-gap assumption으로 커버됨
            pass
        else:
            _age_fields = frozenset({"approval_date", "permit_date", "building_age_years"})
            has_approval = any(item.field in _age_fields for item in plan.filters)
            has_age_order = any(item.field in _age_fields for item in plan.order_by)
            has_age_select = any(field in _age_fields for field in plan.select)
            has_age_agg = any(
                (item.field in _age_fields)
                or (item.alias or "").startswith(
                    ("avg_age", "age", "median_days", "std_days", "corr_age")
                )
                or any(
                    f in _age_fields for f in _expression_fields(item.expression)
                )
                for item in plan.aggregations
            )
            has_age_group = any(field in _age_fields for field in plan.group_by)
            has_year_group = any(
                k in question for k in ("구간별", "년대별", "연도별", "s~", "s～")
            )
            from txt2sql.semantic_plan.predicate_utils import walk_predicate

            def _pred_has_age(node) -> bool:
                if node is None:
                    return False
                for leaf in walk_predicate(node):
                    if leaf.left and leaf.left.field in _age_fields:
                        return True
                return False

            has_age_ratio = any(
                _pred_has_age(ratio.numerator_predicate)
                or _pred_has_age(ratio.denominator_predicate)
                for ratio in (plan.ratios or [])
            )
            if not (
                has_approval
                or has_age_order
                or has_age_select
                or has_age_agg
                or has_age_group
                or has_year_group
                or has_age_ratio
            ):
                reason = (
                    "unsupported_coverage: 허가일은 D198만 지원"
                    if ("허가일" in question or "허가일자" in question)
                    and "사용승인" not in question
                    else "unsupported_coverage: building age / 사용승인"
                )
                return _fallback(plan, reason, score - 0.5)

    if (
        "면적" in question
        and "기초구역" not in question
        and "산업단지" not in question
        and not any(
            k in question
            for k in ("연면적", "건축물면적", "건축면적", "건물면적", "대지면적")
        )
    ):
        from txt2sql.place_area_qa import is_place_boundary_area_question

        if not is_place_boundary_area_question(question):
            area_fields = [f.field for f in plan.filters if "area" in f.field]
            agg_areas = [
                a.field
                for a in (plan.aggregations or [])
                if a.field and "area" in a.field
            ]
            if (
                not area_fields
                and not agg_areas
                and not any("area" in s for s in plan.select)
                and plan.entity not in {"industrial_complex", "basic_zone"}
            ):
                clarified = plan.model_copy(
                    update={
                        "requires_clarification": True,
                        "ambiguities": list(plan.ambiguities)
                        + ["면적이 건축면적·연면적·대지면적 중 어떤 것인지 필요합니다"],
                    }
                )
                return PlanValidationResult(
                    status="clarify",
                    score=score - 0.3,
                    errors=list(clarified.ambiguities),
                    warnings=warnings,
                    plan=clarified,
                )

    agg_aliases = {item.alias for item in plan.aggregations if item.alias}
    ratio_aliases = {item.alias for item in plan.ratios if item.alias}
    known_aliases = agg_aliases | ratio_aliases | {"n", "count", "matching_n", "ratio_pct"}
    used_fields = list(plan.select) + list(plan.group_by)
    used_fields.extend(item.field for item in plan.filters)
    used_fields.extend(
        item.field for item in plan.order_by if item.field not in known_aliases
    )
    used_fields.extend(item.field for item in plan.aggregations if item.field)

    for key in used_fields:
        if not key:
            continue
        try:
            field = get_field(plan.entity, key)
        except UnknownSemanticFieldError:
            return _fallback(plan, f"unknown field: {key}", score - 0.4)

        if field.data_type == "geometry":
            return _fallback(plan, f"geometry field cannot be selected: {key}", score - 0.4)

    for spec in plan.filters:
        try:
            field = get_field(plan.entity, spec.field)
        except UnknownSemanticFieldError:
            return _fallback(plan, f"unknown field: {spec.field}", score - 0.4)
        if spec.operator not in field.allowed_ops:
            return _fallback(
                plan,
                f"unsupported operation: {spec.field} {spec.operator}",
                score - 0.4,
            )
        if spec.operator in {"gt", "gte", "lt", "lte", "between"} and field.data_type != "number":
            if spec.field not in {"approval_date", "permit_date"}:
                return _fallback(
                    plan, f"numeric operator on text field: {spec.field}", score - 0.4
                )
        if spec.operator == "contains" and field.data_type != "text":
            return _fallback(
                plan, f"contains on non-text field: {spec.field}", score - 0.4
            )
        if spec.operator == "between" and spec.value2 is None:
            errors.append("between requires value2")
            status = "fallback"
            score -= 0.4

    alias_names = [name for name in list(agg_aliases) + list(ratio_aliases) if name]
    if len(alias_names) != len(set(alias_names)):
        return _fallback(plan, "duplicate alias", score - 0.4)

    for agg in plan.aggregations:
        if agg.function == "percentile" and agg.percentile is None:
            return _fallback(plan, "percentile requires a value", score - 0.4)
        if agg.expression is not None:
            expr_error = _validate_expression(plan.entity, agg.expression)
            if expr_error:
                return _fallback(plan, expr_error, score - 0.4)
        if agg.predicate is not None:
            pred_error = _validate_predicate_fields(plan.entity, agg.predicate)
            if pred_error:
                return _fallback(plan, pred_error, score - 0.4)

    for ratio in plan.ratios:
        if ratio.numerator_predicate is None:
            return _fallback(plan, "ratio numerator required", score - 0.4)
        for pred in (ratio.numerator_predicate, ratio.denominator_predicate):
            if pred is None:
                continue
            pred_error = _validate_predicate_fields(plan.entity, pred)
            if pred_error:
                return _fallback(plan, pred_error, score - 0.4)

    if plan.scope and plan.scope.place and plan.scope.place.name.strip():
        place_status, place_error = _validate_place(plan.scope.place.name)
        inherited = any(
            item in (plan.assumptions or [])
            for item in ("plan_followup_delta", "plan_followup_event")
        )
        from txt2sql.domain import MULTI_GU_DONGS

        unique_dong = plan.scope.place.name not in MULTI_GU_DONGS
        if place_status == "clarify" and not inherited and not unique_dong:
            clarified = plan.model_copy(
                update={
                    "requires_clarification": True,
                    "ambiguities": list(plan.ambiguities) + [place_error],
                }
            )
            return PlanValidationResult(
                status="clarify",
                score=score - 0.3,
                errors=[place_error],
                warnings=warnings,
                plan=clarified,
            )
        if place_status == "fallback" and not inherited:
            return _fallback(plan, place_error, score - 0.35)

    for rel in plan.spatial_relations:
        if rel.relation not in _V1_SPATIAL:
            return _fallback(plan, f"unsupported spatial relation: {rel.relation}", 0.4)
        if rel.relation in {"within_distance", "outside_distance", "buffer"}:
            if rel.distance_m is None or rel.distance_m <= 0:
                return _fallback(plan, "distance_m must be > 0", score - 0.4)
        target = rel.target
        if target.longitude is not None or target.latitude is not None:
            lon = target.longitude
            lat = target.latitude
            if lon is None or lat is None:
                return _fallback(plan, "longitude/latitude both required", score - 0.4)
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                return _fallback(plan, "invalid lon/lat range", score - 0.4)
        elif rel.relation in {
            "within",
            "intersects",
            "within_distance",
            "outside_distance",
            "touches",
            "covered_by",
            "buffer",
            "nearest",
            "overlap_ratio",
        }:
            target_ent = getattr(target, "entity", None)
            if target_ent == "industrial_complex":
                continue
            if target.place is None or not (target.place.name or "").strip():
                return _fallback(plan, "spatial relation needs a place or lon/lat", score - 0.4)

    from txt2sql.semantic_catalog.registry import allowed_edges

    for join in plan.joins:
        if join.edge_id not in allowed_edges():
            return _fallback(plan, f"unknown join edge: {join.edge_id}", 0.3)
        if re.search(r"(?i)\b(select|insert|update|st_[a-z]+)\b", str(join.extra or {})):
            return _fallback(plan, "join extra cannot contain SQL", 0.2)

    from txt2sql.semantic_catalog.linking import retrieve_poi

    followup_plan = any(
        item in (plan.assumptions or [])
        for item in ("plan_followup_delta", "plan_followup_event")
    )
    if not followup_plan:
        poi = retrieve_poi(question)
        if poi.clarify:
            clarified = plan.model_copy(
                update={
                    "requires_clarification": True,
                    "ambiguities": list(plan.ambiguities) + ["ambiguous_poi"],
                }
            )
            return PlanValidationResult(
                status="clarify",
                score=score - 0.4,
                errors=["ambiguous_poi"],
                warnings=warnings,
                plan=clarified,
            )

    if len(plan.filters) > _MAX_FILTERS:
        return _fallback(plan, "too many filters", score - 0.2)
    if len(plan.spatial_relations) > _MAX_SPATIAL:
        return _fallback(plan, "too many spatial relations", score - 0.2)
    if len(plan.group_by) > _MAX_GROUP:
        return _fallback(plan, "too many group_by", score - 0.2)
    if len(plan.aggregations) > _MAX_AGG:
        return _fallback(plan, "too many aggregations", score - 0.2)
    if len(plan.order_by) > _MAX_ORDER:
        return _fallback(plan, "too many order_by", score - 0.2)
    if plan.limit is not None and plan.limit > _MAX_LIMIT:
        return _fallback(plan, "limit exceeds 1000", score - 0.2)

    tracked = [
        item
        for item in plan.assumptions
        if item
        not in {
            "heuristic_plan",
            "plan_followup_delta",
            "plan_followup_event",
            "d198_ledger",
        }
        and not item.startswith("edge_bins:")
        # Deterministic day-gap / list-order assumptions are not quality debt.
        and not is_permit_lag_assumption(item)
        and not item.startswith("group_by_industrial_name")
    ]
    if tracked:
        score -= 0.1 * min(len(tracked), 3)
        warnings.extend(tracked)
    if plan.assumptions:
        warnings.extend(
            item for item in plan.assumptions if item not in tracked
        )
    if plan.ambiguities:
        score -= 0.3
        status = "clarify"

    if errors:
        status = "fallback"

    from txt2sql.semantic_plan.contract_verifier import verify_contract

    verified = verify_contract(question, plan, contract=contract)
    plan = plan.model_copy(update={"slot_confidence": verified.confidence})
    followup_plan = any(
        item in (plan.assumptions or [])
        for item in ("plan_followup_delta", "plan_followup_event")
    )
    semantic_loss = any(
        reason in {"P03", "P04", "P05", "aggregation_shape_mismatch"}
        or reason.startswith("missing_")
        for reason in verified.reasons
    )
    if status == "ready" and verified.hard_fail and (
        not followup_plan or semantic_loss
    ):
        status = "fallback"
        errors.extend(verified.reasons)
        score = min(score, verified.confidence.overall)
    return PlanValidationResult(
        status=status,
        score=max(0.0, min(1.0, score)),
        errors=errors,
        warnings=warnings,
        plan=plan,
    )


def _expression_fields(expr: ExpressionSpec | None) -> list[str]:
    if expr is None:
        return []
    if expr.kind == "field":
        return [expr.field] if expr.field else []
    return _expression_fields(expr.left) + _expression_fields(expr.right)


def _validate_expression(entity: str, expr: ExpressionSpec) -> str | None:
    if expr.kind == "field":
        if not expr.field:
            return "expression field missing"
        try:
            get_field(entity, expr.field)
        except UnknownSemanticFieldError:
            return f"unknown field: {expr.field}"
        return None
    if expr.left is None or expr.right is None:
        return "divide requires denominator" if expr.kind == "divide" else "expression operands required"
    if expr.kind == "divide" and expr.right is None:
        return "divide requires denominator"
    left_error = _validate_expression(entity, expr.left)
    if left_error:
        return left_error
    return _validate_expression(entity, expr.right)


def _predicate_fields(pred: PredicateSpec | None) -> list[str]:
    if pred is None:
        return []
    found: list[str] = []
    if pred.left and pred.left.kind == "field" and pred.left.field:
        found.append(pred.left.field)
    if pred.right and pred.right.kind == "field" and pred.right.field:
        found.append(pred.right.field)
    for arg in pred.args or []:
        found.extend(_predicate_fields(arg))
    return found


def _validate_predicate_fields(entity: str, pred: PredicateSpec) -> str | None:
    for key in _predicate_fields(pred):
        try:
            get_field(entity, key)
        except UnknownSemanticFieldError:
            return f"unknown field: {key}"
    return None


def _fallback(plan: SemanticQueryPlan, reason: str, score: float) -> PlanValidationResult:
    return PlanValidationResult(
        status="fallback",
        score=max(0.0, score),
        errors=[reason],
        warnings=[],
        plan=plan,
    )


def _validate_place(name: str) -> tuple[str, str]:
    text = name.strip()
    if not text or re.search(r"[;\\]|--", text):
        return "fallback", f"unresolved place: {text}"
    gaz = load_gazetteer()
    if not (gaz.legal_dong or gaz.admin_dong or gaz.sigungu):
        return "ready", ""
    hits = find_places(text)
    if text.endswith(("시", "도")) or text in {"부산광역시", "부산시", "부산"}:
        return "ready", ""
    if not hits:
        # 구 단위 등 질문 조각만 온 경우는 허용하고 compiler가 A4 LIKE로 처리
        if text.endswith(("구", "군")):
            return "ready", ""
        return "fallback", f"unresolved place: {text}"
    dongs = [h for h in hits if not h.is_sigungu]
    names = {h.name for h in dongs}
    if len(names) > 1:
        return "clarify", f"ambiguous place: {', '.join(sorted(names))}"
    return "ready", ""
