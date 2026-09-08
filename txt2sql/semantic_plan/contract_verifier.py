"""Contract span이 Plan 노드에 연결됐는지 검증하고 slot confidence를 계산한다."""

from __future__ import annotations

from dataclasses import dataclass, field

from txt2sql.query_understanding.contract import QueryContract, extract_contract
from txt2sql.semantic_plan.models import PlanConfidence, SemanticQueryPlan
from txt2sql.semantic_plan.predicate_utils import (
    effective_predicate,
    has_field_compare,
    has_op,
    has_operator,
    predicate_fields,
    range_bounds,
)


@dataclass
class ContractVerifyResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    confidence: PlanConfidence = field(default_factory=PlanConfidence)
    hard_fail: bool = False


def _slot(value: float) -> float:
    return max(0.0, min(1.0, value))


def _expression_fields(expr) -> set[str]:
    """Collect field keys referenced by an AggregationSpec.expression tree."""
    if expr is None:
        return set()
    if getattr(expr, "kind", None) == "field" and getattr(expr, "field", None):
        return {str(expr.field)}
    found: set[str] = set()
    found |= _expression_fields(getattr(expr, "left", None))
    found |= _expression_fields(getattr(expr, "right", None))
    return found


def verify_contract(
    question: str,
    plan: SemanticQueryPlan,
    *,
    contract: QueryContract | None = None,
    min_slot: float = 0.85,
) -> ContractVerifyResult:
    contract = contract or extract_contract(question)
    reasons: list[str] = []
    if plan.requires_clarification:
        return ContractVerifyResult(
            ok=False,
            reasons=["clarify"],
            confidence=PlanConfidence(overall=0.0),
            hard_fail=False,
        )

    entity_score = 1.0 if plan.entity in {
        "building",
        "admin_area",
        "basic_zone",
        "industrial_complex",
    } else 0.4
    if contract.places:
        place_ok = bool(plan.scope and plan.scope.place and plan.scope.place.name)
        if not place_ok:
            from txt2sql.domain import is_busan_wide

            place_ok = is_busan_wide(question)
        if not place_ok:
            place_ok = any(
                getattr(rel.target, "entity", None) == "industrial_complex"
                or (
                    rel.target.place is not None
                    and bool((rel.target.place.name or "").strip())
                )
                for rel in plan.spatial_relations
            )
        scope_score = 1.0 if place_ok else 0.0
        if not place_ok:
            reasons.append("P07")
    else:
        scope_score = 1.0

    pred = effective_predicate(plan)
    semantic_pred_fields = predicate_fields(pred)
    for agg in plan.aggregations:
        semantic_pred_fields |= predicate_fields(agg.predicate)
    for ratio in plan.ratios:
        if ratio.numerator_predicate is not None:
            semantic_pred_fields |= predicate_fields(ratio.numerator_predicate)
        if ratio.denominator_predicate is not None:
            semantic_pred_fields |= predicate_fields(ratio.denominator_predicate)
    # 허가↔승인 시차는 assumption SQL로 표현 — 슬롯 커버로 인정
    from txt2sql.domain import assumptions_include_permit_lag

    if assumptions_include_permit_lag(plan.assumptions):
        semantic_pred_fields |= {"permit_date", "approval_date"}
    pred_fields = {item.field for item in plan.filters}
    pred_fields |= semantic_pred_fields
    pred_fields |= {item.field for item in plan.aggregations if item.field}
    # 허가일−사용승인일 등 expression 집계 필드도 fields 슬롯에 포함
    for agg in plan.aggregations:
        pred_fields |= _expression_fields(agg.expression)
    pred_fields |= set(plan.select)
    pred_fields |= set(plan.group_by)
    for ratio in plan.ratios:
        if ratio.numerator_predicate is not None:
            pred_fields |= predicate_fields(ratio.numerator_predicate)
        if ratio.denominator_predicate is not None:
            pred_fields |= predicate_fields(ratio.denominator_predicate)
    metric_fields: set[str] = set()
    for span in contract.metrics:
        field = span.value
        if not field:
            continue
        # 범주형 개체 라벨(아파트·공장 등)은 predicate로 검증하고 fields 슬롯에서는 제외.
        if field in {
            "usage",
            "detail_usage",
            "structure",
            "violation_status",
            "special_land",
        } and str(span.text) not in {"용도", "구조", "세부용도", "위반"}:
            continue
        metric_fields.add(str(field))
    # 건축연령 집계는 approval_date 파생 — 한쪽만 있어도 fields 커버
    if metric_fields & {"building_age_years", "approval_date"}:
        if pred_fields & {"building_age_years", "approval_date"}:
            pred_fields = pred_fields | (metric_fields & {"building_age_years", "approval_date"})
    field_hits = len(metric_fields & pred_fields)
    fields_score = 1.0 if not metric_fields else field_hits / len(metric_fields)

    pred_score = 1.0
    threshold_numbers = [
        span
        for span in contract.numbers
        if span.meta.get("role", "threshold") == "threshold"
        and (
            span.meta.get("operator")
            or any(
                hint in contract.question[span.end : span.end + 10]
                for hint in ("이상", "이하", "초과", "미만", "보다")
            )
        )
    ]
    wanted_atoms: list[tuple[str, str | None, object]] = []
    for span in threshold_numbers:
        field = span.meta.get("field")
        if not field:
            continue
        wanted_atoms.append(
            (str(field), span.meta.get("operator"), getattr(span, "value", None))
        )
    for span in contract.ranges:
        field = span.meta.get("field")
        if field:
            wanted_atoms.append((str(field), "between", span.meta.get("low")))
    # 명시 BinSpec(edges)로 구간을 표현하면 WHERE 임계 필터는 요구하지 않는다.
    bin_fields = {
        str(spec.field)
        for spec in (plan.bins or [])
        if spec.field and (spec.edges or spec.width is not None)
    }
    if bin_fields:
        wanted_atoms = [
            atom for atom in wanted_atoms if atom[0] not in bin_fields
        ]
    wanted_predicate_fields = {field for field, _op, _val in wanted_atoms}
    if wanted_predicate_fields and not wanted_predicate_fields <= semantic_pred_fields:
        pred_score = 0.0
        reasons.append("missing_predicate")
        reasons.append("PREDICATE_DROPPED")
        reasons.append("P03")
    # Operator mismatch: field present with wrong operator is PREDICATE_DROPPED.
    # Ratio / aggregation stage predicates count as plan leaves (conditional ratio).
    from txt2sql.semantic_plan.predicate_utils import walk_predicate

    plan_predicate_roots = [pred] if pred is not None else []
    for agg in plan.aggregations or []:
        if agg.predicate is not None:
            plan_predicate_roots.append(agg.predicate)
    for ratio in plan.ratios or []:
        if ratio.numerator_predicate is not None:
            plan_predicate_roots.append(ratio.numerator_predicate)
        if ratio.denominator_predicate is not None:
            plan_predicate_roots.append(ratio.denominator_predicate)

    plan_leaves = list(plan.filters)
    for field, operator, value in wanted_atoms:
        if operator in {None, "between"}:
            continue
        matched = any(
            item.field == field
            and item.operator == operator
            and (
                value is None
                or item.value == value
                or float(item.value or 0) == float(value or 0)
            )
            for item in plan_leaves
            if item.field
        )
        if matched:
            continue
        value_ok = False
        for root in plan_predicate_roots:
            if root is None:
                continue
            if not (field in predicate_fields(root) and has_operator(root, operator)):
                continue
            if value is None:
                value_ok = True
                break
            for node in walk_predicate(root):
                if (
                    node.op == "cmp"
                    and node.left
                    and node.left.field == field
                    and node.operator == operator
                    and node.right is not None
                    and (
                        node.right.value == value
                        or float(node.right.value or 0) == float(value or 0)
                    )
                ):
                    value_ok = True
                    break
            if value_ok:
                break
        if value_ok:
            continue
        pred_score = 0.0
        if "PREDICATE_DROPPED" not in reasons:
            reasons.append("missing_predicate")
            reasons.append("PREDICATE_DROPPED")
            reasons.append("P03")

    # Categorical metric atoms (usage vs detail_usage are distinct).
    from txt2sql.domain import extract_detail_usages

    detail_hits = set(extract_detail_usages(question) or [])
    for span in contract.metrics:
        field = span.value
        if field not in {"usage", "detail_usage", "structure", "violation_status"}:
            continue
        if str(span.text) in {"용도", "구조", "세부용도", "위반"}:
            continue
        canonical = str(span.meta.get("canonical") or span.text or "").strip()
        if not canonical:
            continue
        present = field in semantic_pred_fields or any(
            item.field == field for item in plan.filters
        )
        # Detail aliases (아파트 등) are correctly bound to detail_usage even when
        # contract metric slot is labeled usage.
        if (
            not present
            and field == "usage"
            and detail_hits
            and (
                "detail_usage" in semantic_pred_fields
                or any(item.field == "detail_usage" for item in plan.filters)
            )
        ):
            present = True
        if not present:
            pred_score = 0.0
            if "PREDICATE_DROPPED" not in reasons:
                reasons.append("PREDICATE_DROPPED")
                reasons.append("P03")
            continue
        # Main-usage contract must not be satisfied by detail_usage alone when
        # the text is a main usage label (not a detail alias).
        if (
            field == "usage"
            and not detail_hits
            and "usage" not in semantic_pred_fields
            and not any(item.field == "usage" for item in plan.filters)
            and "detail_usage" in semantic_pred_fields
        ):
            pred_score = 0.0
            reasons.append("ENTITY_SELECTION_ERROR")
            reasons.append("PREDICATE_DROPPED")
    if any(span.kind == "or" for span in contract.boolean_ops):
        if not has_op(pred, "or"):
            pred_score = 0.0
            reasons.append("BOOLEAN_OR_DROPPED")
            reasons.append("P04")
    if any(span.kind == "not" for span in contract.boolean_ops):
        has_not = has_op(pred, "not") or any(item.operator == "neq" for item in plan.filters)
        if not has_not:
            pred_score = 0.0
            reasons.append("BOOLEAN_NOT_DROPPED")
            reasons.append("P04")
        else:
            # NOT(A OR B) must wrap an OR subtree — flat neq list is not enough.
            for span in contract.boolean_ops:
                if span.kind != "not":
                    continue
                if not (span.meta or {}).get("scopes_or"):
                    continue
                if not has_op(pred, "not"):
                    pred_score = 0.0
                    reasons.append("BOOLEAN_NOT_DROPPED")
                    reasons.append("P04")
                    break
                from txt2sql.semantic_plan.predicate_utils import walk_predicate

                not_wraps_or = False
                if pred is not None:
                    for node in walk_predicate(pred):
                        if node.op == "not" and any(
                            (child.op == "or") for child in (node.args or [])
                        ):
                            not_wraps_or = True
                            break
                if not not_wraps_or:
                    pred_score = 0.0
                    reasons.append("BOOLEAN_NOT_DROPPED")
                    reasons.append("P04")
                    break
    if contract.ranges:
        bin_fields = {
            str(spec.field)
            for spec in (plan.bins or [])
            if spec.field and (spec.edges or spec.width is not None)
        }
        pending_ranges = [
            span
            for span in contract.ranges
            if str(span.meta.get("field") or "") not in bin_fields
        ]
        range_ok = True
        if pending_ranges:
            range_ok = has_operator(pred, "between")
            if not range_ok:
                for span in pending_ranges:
                    field = span.meta.get("field")
                    low, high = range_bounds(pred, field) if field else (None, None)
                    if low is None or high is None:
                        range_ok = False
                        break
                    range_ok = True
        if not range_ok:
            pred_score = min(pred_score, 0.0)
            reasons.append("RANGE_BOUND_DROPPED")
            reasons.append("P03")
    if contract.comparisons:
        has_ff = has_field_compare(pred) or any(item.value_field for item in plan.filters)
        if not has_ff:
            pred_score = 0.0
            reasons.append("PREDICATE_DROPPED")
            reasons.append("P03")

    agg_score = 1.0
    plan_fns = {item.function for item in plan.aggregations}
    if plan.query_kind == "count":
        plan_fns.add("count")
    wanted_aggs = {item.function for item in contract.aggregation_requests}
    if wanted_aggs and not wanted_aggs <= plan_fns:
        agg_score = 0.0
        reasons.append("missing_aggregation")
        reasons.append("P05")
    if contract.aggregations:
        wanted = {span.value for span in contract.aggregations}
        got = {item.function for item in plan.aggregations}
        if not got or not wanted.issubset(got):
            agg_score = 0.0
            reasons.append("P05")
        if contract.groups and not plan.group_by:
            agg_score = 0.0
            reasons.append("P05")
    if contract.group_fields:
        if not set(contract.group_fields) <= set(plan.group_by):
            agg_score = 0.0
            reasons.append("missing_group")
            reasons.append("P05")
    if contract.query_kind == "group" and not plan.group_by:
        agg_score = 0.0
        reasons.append("missing_group")
        reasons.append("P05")
    if contract.query_kind == "scalar" and plan.query_kind in {"count", "list", "rank"}:
        agg_score = 0.0
        reasons.append("aggregation_shape_mismatch")
        reasons.append("P05")

    if contract.order:
        want_dir = contract.order[0].value
        got_dir = plan.order_by[0].direction if plan.order_by else None
        if got_dir != want_dir:
            reasons.append("P06")
            agg_score = min(agg_score, 0.2)
    if contract.order_requests and not plan.order_by:
        reasons.append("missing_order")
        agg_score = min(agg_score, 0.0)
    elif contract.order_requests and contract.order_requests[0].field:
        got_field = plan.order_by[0].field if plan.order_by else None
        if got_field != contract.order_requests[0].field:
            reasons.append("missing_order_field")
            agg_score = min(agg_score, 0.0)

    if contract.limit is not None and plan.limit != contract.limit:
        reasons.append("missing_limit")
        agg_score = min(agg_score, 0.0)

    if plan.query_kind in {"list", "rank"} and contract.output_fields:
        got_out = (
            set(plan.select)
            | {item.field for item in plan.projections}
            | {item.field for item in plan.aggregations if item.field}
        )
        if not set(contract.output_fields) <= got_out:
            reasons.append("missing_output")
            agg_score = min(agg_score, 0.0)

    if contract.ratios:
        if not plan.ratios:
            q = contract.question or ""
            # 구조별·용도별 구성비(전체 대비 백분율)는 group+count로 충족
            composition = bool(plan.group_by) and any(
                a.function == "count" for a in (plan.aggregations or [])
            ) and any(k in q for k in ("백분율", "전체 대비", "구성비", "비중"))
            if not composition:
                reasons.append("missing_ratio")
                agg_score = min(agg_score, 0.0)
        elif any(item.has_denominator for item in contract.ratios) and any(
            item.denominator_predicate is None for item in plan.ratios
        ):
            reasons.append("missing_ratio_denominator")
            agg_score = min(agg_score, 0.0)
        else:
            # Identical numerator and denominator predicates → always 100%.
            for ratio in plan.ratios or []:
                num = ratio.numerator_predicate
                den = ratio.denominator_predicate
                if num is not None and den is not None and num == den:
                    reasons.append("ratio_stage_mismatch")
                    agg_score = min(agg_score, 0.0)
                    break

    spatial_score = 1.0
    if contract.places and plan.spatial_relations:
        spatial_score = 1.0

    slots = {
        "entity": _slot(entity_score),
        "scope": _slot(scope_score),
        "fields": _slot(fields_score),
        "predicates": _slot(pred_score),
        "aggregation": _slot(agg_score),
        "spatial": _slot(spatial_score),
    }
    overall = sum(slots.values()) / len(slots)
    confidence = PlanConfidence(**slots, overall=_slot(overall))
    hard = bool(reasons)
    below = [name for name, val in slots.items() if val < min_slot]
    if below:
        reasons.append("slot_below_threshold:" + ",".join(below))
        hard = True
    ok = not hard
    return ContractVerifyResult(ok=ok, reasons=reasons, confidence=confidence, hard_fail=hard)


def _predicate_fields(pred) -> set[str]:
    found: set[str] = set()
    if pred is None:
        return found
    if pred.left and pred.left.field:
        found.add(pred.left.field)
    if pred.right and pred.right.field:
        found.add(pred.right.field)
    for child in pred.args or []:
        found |= _predicate_fields(child)
    return found
