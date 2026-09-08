"""Adapters between QueryContract / SemanticQueryPlan and canonical QueryIR."""

from __future__ import annotations

from typing import Any

from txt2sql.query_ir.models import (
    AggregationIR,
    ComparisonIR,
    DimensionIR,
    EntityName,
    InteractionIR,
    MeasureIR,
    OrderingIR,
    PredicateIR,
    ProvenanceIR,
    ProvenanceSpan,
    QueryIR,
    ScopeIR,
    SpatialIR,
    TemporalIR,
    UnresolvedIR,
)
from txt2sql.query_ir.normalize import assert_no_physical_names, normalize_query_ir, normalize_task, result_shape_for_task


def _span_prov(span: Any) -> ProvenanceSpan:
    return ProvenanceSpan(
        text=getattr(span, "text", "") or "",
        start=getattr(span, "start", None),
        end=getattr(span, "end", None),
        kind=getattr(span, "kind", None),
        value=getattr(span, "value", None),
    )


def _entity_from_contract(contract: Any) -> EntityName:
    datasets = [str(x).lower() for x in (getattr(contract, "datasets", None) or [])]
    joined = " ".join(datasets)
    q = str(getattr(contract, "question", "") or "")
    if "기초구역" in q or "basic" in joined or "bas_" in joined:
        return "basic_zone"
    if "basic" in joined or "bas" in joined:
        return "basic_zone"
    if "industrial" in joined:
        return "industrial_complex"
    if "admin" in joined:
        return "admin_area"
    return "building"


def _rel_to_op(rel: str | None, *, bound: str) -> str:
    token = (rel or "").strip()
    if bound == "low":
        if token in {"초과"}:
            return "gt"
        return "gte"
    if token in {"미만"}:
        return "lt"
    return "lte"


def _coalesce_or_predicates(contract: Any, predicates: list[PredicateIR]) -> list[PredicateIR]:
    """동일 필드 eq predicate가 OR로 연결된 경우 logical_group으로 묶는다."""
    has_or = any(
        getattr(span, "kind", None) == "or"
        for span in getattr(contract, "boolean_ops", []) or []
    )
    if not has_or:
        return predicates
    grouped: dict[str, list[PredicateIR]] = {}
    rest: list[PredicateIR] = []
    for pred in predicates:
        if (
            pred.field
            and pred.operator == "eq"
            and pred.field in {"usage", "detail_usage", "structure"}
            and not pred.negated
            and not pred.children
        ):
            grouped.setdefault(pred.field, []).append(pred)
        else:
            rest.append(pred)
    merged = list(rest)
    for items in grouped.values():
        if len(items) >= 2:
            merged.append(PredicateIR(logical_group="or", children=items))
        else:
            merged.extend(items)
    return merged


def _apply_not_from_contract(contract: Any, predicates: list[PredicateIR]) -> list[PredicateIR]:
    """Mark OR groups / categorical leaves negated when contract NOT scopes them."""
    not_spans = [
        span
        for span in getattr(contract, "boolean_ops", []) or []
        if getattr(span, "kind", None) == "not"
    ]
    if not not_spans:
        return predicates
    scopes_or = any((getattr(span, "meta", None) or {}).get("scopes_or") for span in not_spans)
    operands: set[str] = set()
    for span in not_spans:
        meta = getattr(span, "meta", None) or {}
        for item in meta.get("operands") or []:
            operands.add(str(item))
    if not scopes_or and not operands:
        return predicates

    out: list[PredicateIR] = []
    for pred in predicates:
        if pred.logical_group == "or" and scopes_or:
            out.append(pred.model_copy(update={"negated": True}))
            continue
        if (
            pred.operator == "eq"
            and pred.field in {"usage", "detail_usage", "structure"}
            and pred.value is not None
            and (
                str(pred.value) in operands
                or any(op in str(pred.value) or str(pred.value) in op for op in operands)
            )
        ):
            out.append(pred.model_copy(update={"negated": True}))
            continue
        out.append(pred)
    return out


def _coalesce_union_predicates(contract: Any, predicates: list[PredicateIR]) -> list[PredicateIR]:
    """「공장과 창고를 합친」처럼 union count 의도면 OR/IN으로 묶는다."""
    q = getattr(contract, "question", "") or ""
    if not any(k in q for k in ("합친", "합계", "더한", "합산")):
        return predicates
    grouped: dict[str, list[PredicateIR]] = {}
    rest: list[PredicateIR] = []
    for pred in predicates:
        if (
            pred.field
            and pred.operator == "eq"
            and pred.field in {"usage", "detail_usage"}
            and not pred.negated
        ):
            grouped.setdefault(pred.field, []).append(pred)
        else:
            rest.append(pred)
    merged = list(rest)
    for items in grouped.values():
        if len(items) >= 2:
            merged.append(PredicateIR(logical_group="or", children=items))
        else:
            merged.extend(items)
    return merged


def contract_to_query_ir(contract: Any) -> QueryIR:
    places = list(getattr(contract, "places", None) or [])
    scope = None
    if places:
        p0 = places[0]
        scope = ScopeIR(
            place=str(getattr(p0, "value", None) or getattr(p0, "text", "") or None),
            place_kind=None,
            spatial_mode="auto",
        )

    predicates: list[PredicateIR] = []
    for span in getattr(contract, "ranges", None) or []:
        meta = getattr(span, "meta", None) or {}
        lo_rel = meta.get("lo_rel")
        hi_rel = meta.get("hi_rel")
        field = meta.get("field") or getattr(span, "value", None)
        low = meta.get("low", getattr(span, "value", None))
        high = meta.get("high")
        unit = meta.get("unit")
        exclusive = (lo_rel == "초과") or (hi_rel == "미만")
        if exclusive and low is not None and high is not None:
            predicates.append(
                PredicateIR(
                    field=field,
                    operator=_rel_to_op(lo_rel, bound="low"),
                    value=low,
                    unit=unit,
                    provenance=_span_prov(span),
                )
            )
            predicates.append(
                PredicateIR(
                    field=field,
                    operator=_rel_to_op(hi_rel, bound="high"),
                    value=high,
                    unit=unit,
                    provenance=_span_prov(span),
                )
            )
        else:
            predicates.append(
                PredicateIR(
                    field=field,
                    operator=meta.get("operator") or "between",
                    value=low,
                    value2=high,
                    unit=unit,
                    provenance=_span_prov(span),
                )
            )
    for span in getattr(contract, "comparisons", None) or []:
        meta = getattr(span, "meta", None) or {}
        value = getattr(span, "value", None)
        left = meta.get("field") or meta.get("left")
        operator = meta.get("operator") or meta.get("op") or "eq"
        right_field = meta.get("right_field") or meta.get("value_field")
        cmp_value = meta.get("value")
        if isinstance(value, dict):
            left = value.get("left") or left
            operator = value.get("op") or operator
            right_field = value.get("right") or right_field
        # Map Korean metric labels to fields when still present.
        from txt2sql.query_understanding import operators as ops

        if isinstance(left, str) and left in ops.METRIC_MAP:
            left = ops.METRIC_MAP[left]
        if isinstance(right_field, str) and right_field in ops.METRIC_MAP:
            right_field = ops.METRIC_MAP[right_field]
        predicates.append(
            PredicateIR(
                field=left,
                operator=operator,
                value=cmp_value,
                value_field=right_field,
                provenance=_span_prov(span),
            )
        )
    for span in getattr(contract, "numbers", None) or []:
        meta = getattr(span, "meta", None) or {}
        role = meta.get("role", "threshold")
        if role != "threshold":
            continue
        if meta.get("field") or meta.get("operator"):
            predicates.append(
                PredicateIR(
                    field=meta.get("field"),
                    operator=meta.get("operator") or "gte",
                    value=getattr(span, "value", None),
                    unit=meta.get("unit"),
                    provenance=_span_prov(span),
                )
            )

    measures: list[MeasureIR] = []
    for span in getattr(contract, "metrics", None) or []:
        concept = str(getattr(span, "value", None) or getattr(span, "text", "") or "")
        if concept:
            measures.append(MeasureIR(concept=concept, provenance=_span_prov(span)))
        # Categorical metric labels (e.g. 단독주택→usage) become equality predicates.
        text = str(getattr(span, "text", "") or "").strip()
        field = str(getattr(span, "value", "") or "").strip()
        if (
            text
            and field in {"usage", "detail_usage", "structure", "violation_status"}
            and text not in {"용도", "구조", "세부용도", "높이", "연면적", "건폐율", "용적률"}
        ):
            if not any(p.field == field and p.value == text for p in predicates):
                predicates.append(
                    PredicateIR(
                        field=field,
                        operator="eq",
                        value=text,
                        provenance=_span_prov(span),
                    )
                )

    predicates = _coalesce_or_predicates(contract, predicates)
    predicates = _coalesce_union_predicates(contract, predicates)
    predicates = _apply_not_from_contract(contract, predicates)

    aggregations: list[AggregationIR] = []
    for req in getattr(contract, "aggregation_requests", None) or []:
        aggregations.append(
            AggregationIR(
                function=getattr(req, "function", "count"),
                field=getattr(req, "field", None),
                percentile=getattr(req, "percentile", None),
            )
        )
    for req in getattr(contract, "percentile_requests", None) or []:
        aggregations.append(
            AggregationIR(
                function="percentile",
                field=getattr(req, "field", None),
                percentile=getattr(req, "percentile", None),
            )
        )
    for req in getattr(contract, "ratios", None) or []:
        aggregations.append(
            AggregationIR(function="ratio", has_denominator=bool(getattr(req, "has_denominator", False)))
        )
    for req in getattr(contract, "derived_metrics", None) or []:
        aggregations.append(
            AggregationIR(
                function="derived",
                derived_kind=getattr(req, "kind", "divide"),
                left=getattr(req, "left", None),
                right=getattr(req, "right", None),
            )
        )

    dimensions = [
        DimensionIR(field=str(f), bins=bool(getattr(contract, "fixed_bins", False)))
        for f in (getattr(contract, "group_fields", None) or [])
        if f
    ]
    ordering = [
        OrderingIR(field=getattr(o, "field", None), direction=getattr(o, "direction", "desc") or "desc")
        for o in (getattr(contract, "order_requests", None) or [])
    ]
    unresolved = [
        UnresolvedIR(code="UNRESOLVED_SPAN", message=getattr(s, "text", ""), span=_span_prov(s))
        for s in (getattr(contract, "unresolved_spans", None) or [])
    ]
    # Multi-place: keep first in scope; surface extras so they are not silently dropped.
    if len(places) > 1:
        for extra in places[1:]:
            unresolved.append(
                UnresolvedIR(
                    code="EXTRA_PLACE",
                    message=str(getattr(extra, "value", None) or getattr(extra, "text", "") or ""),
                    span=_span_prov(extra),
                )
            )

    temporal = TemporalIR() if getattr(contract, "wants_temporal", False) else None
    spatial: list[SpatialIR] = []
    if getattr(contract, "wants_spatial", False):
        distance = next(
            (
                float(getattr(n, "value"))
                for n in getattr(contract, "numbers", []) or []
                if (getattr(n, "meta", None) or {}).get("role") == "distance"
            ),
            None,
        )
        spatial.append(SpatialIR(relation="within", distance_m=distance))

    # Strip physical dataset names from contract.datasets — keep only as unresolved/provenance note
    legacy_datasets = list(getattr(contract, "datasets", None) or [])
    safe_hints: dict[str, Any] = {
        "wants_basement": bool(getattr(contract, "wants_basement", False)),
        "wants_count": bool(getattr(contract, "wants_count", False)),
        "coverage_ratio": getattr(contract, "coverage_ratio", None),
        "complexity": getattr(contract, "complexity", None),
        "operation": getattr(contract, "operation", None),
    }
    # Do not copy physical table names into IR; record count only
    if legacy_datasets:
        safe_hints["legacy_dataset_hint_count"] = len(legacy_datasets)
    if len(places) > 1:
        safe_hints["extra_places"] = [
            str(getattr(p, "value", None) or getattr(p, "text", "") or "")
            for p in places[1:]
        ]

    task = normalize_task(getattr(contract, "query_kind", None) or getattr(contract, "operation", None))
    if not aggregations and dimensions and (
        getattr(contract, "wants_count", False) or task in {"group", "distribution"}
    ):
        aggregations.append(AggregationIR(function="count"))
    if "basic_zone" in legacy_datasets or any("bas" in str(d).lower() for d in legacy_datasets):
        safe_hints["basic_zone"] = True
    ir = QueryIR(
        task=task,
        entity=_entity_from_contract(contract),
        scope=scope,
        predicates=predicates,
        temporal=temporal,
        spatial=spatial,
        measures=measures,
        aggregations=aggregations,
        dimensions=dimensions,
        comparisons=[],
        ordering=ordering,
        limit=getattr(contract, "limit", None),
        outputs=list(getattr(contract, "output_fields", None) or []),
        result_shape=result_shape_for_task(task),  # type: ignore[arg-type]
        interaction=InteractionIR(),
        unresolved=unresolved,
        provenance=ProvenanceIR(
            source_text=getattr(contract, "question", "") or "",
            source="contract",
            legacy_hints=safe_hints,
        ),
    )
    return normalize_query_ir(ir)


def _predicate_from_filter(filt: Any) -> PredicateIR:
    return PredicateIR(
        field=getattr(filt, "field", None),
        operator=getattr(filt, "operator", None),
        value=getattr(filt, "value", None),
        value2=getattr(filt, "value2", None),
        unit=getattr(filt, "unit", None),
        value_field=getattr(filt, "value_field", None),
    )


def _predicate_from_spec(pred: Any) -> PredicateIR | None:
    if pred is None:
        return None
    op = getattr(pred, "op", None)
    if op in {"and", "or"}:
        children = [_predicate_from_spec(c) for c in (getattr(pred, "args", None) or [])]
        return PredicateIR(
            logical_group=op,
            children=[c for c in children if c is not None],
        )
    if op == "not":
        args = getattr(pred, "args", None) or []
        child = _predicate_from_spec(args[0]) if args else None
        if child is None:
            return PredicateIR(negated=True)
        child.negated = True
        return child
    left = getattr(pred, "left", None)
    right = getattr(pred, "right", None)
    return PredicateIR(
        field=getattr(left, "field", None) if left is not None else None,
        operator=getattr(pred, "operator", None),
        value=getattr(right, "value", None) if right is not None else None,
        value_field=getattr(right, "field", None) if right is not None else None,
        unit=getattr(right, "unit", None) if right is not None else None,
    )


def plan_to_query_ir(plan: Any) -> QueryIR:
    scope = None
    plan_scope = getattr(plan, "scope", None)
    if plan_scope is not None:
        place = getattr(plan_scope, "place", None)
        scope = ScopeIR(
            place=getattr(place, "name", None) if place is not None else None,
            place_kind=getattr(place, "kind", None) if place is not None else None,
            spatial_mode=getattr(plan_scope, "spatial_mode", "auto") or "auto",
        )

    predicates = [_predicate_from_filter(f) for f in (getattr(plan, "filters", None) or [])]
    root_pred = _predicate_from_spec(getattr(plan, "predicate", None))
    if root_pred is not None:
        predicates.append(root_pred)

    aggregations = [
        AggregationIR(
            function=getattr(a, "function", "count"),
            field=getattr(a, "field", None),
            alias=getattr(a, "alias", None),
            percentile=getattr(a, "percentile", None),
        )
        for a in (getattr(plan, "aggregations", None) or [])
    ]
    for ratio in getattr(plan, "ratios", None) or []:
        aggregations.append(
            AggregationIR(
                function="ratio",
                alias=getattr(ratio, "alias", "ratio_pct"),
                has_denominator=getattr(ratio, "denominator_predicate", None) is not None,
            )
        )

    measures = [
        MeasureIR(concept=str(p.field), alias=getattr(p, "alias", None))
        for p in (getattr(plan, "projections", None) or [])
        if getattr(p, "field", None)
    ]
    for field in getattr(plan, "select", None) or []:
        if field and all(m.concept != field for m in measures):
            measures.append(MeasureIR(concept=str(field)))

    dimensions = [DimensionIR(field=str(g)) for g in (getattr(plan, "group_by", None) or [])]
    ordering = [
        OrderingIR(field=getattr(o, "field", None), direction=getattr(o, "direction", "asc") or "asc")
        for o in (getattr(plan, "order_by", None) or [])
    ]
    spatial = [
        SpatialIR(
            relation=getattr(s, "relation", None),
            target_place=getattr(getattr(getattr(s, "target", None), "place", None), "name", None),
            target_entity=getattr(getattr(s, "target", None), "entity", None),
            distance_m=getattr(s, "distance_m", None),
            min_ratio=getattr(s, "min_ratio", None),
            longitude=getattr(getattr(s, "target", None), "longitude", None),
            latitude=getattr(getattr(s, "target", None), "latitude", None),
        )
        for s in (getattr(plan, "spatial_relations", None) or [])
    ]

    unresolved: list[UnresolvedIR] = []
    if getattr(plan, "requires_clarification", False):
        unresolved.append(UnresolvedIR(code="CLARIFY_REQUIRED", message="requires_clarification"))
    for amb in getattr(plan, "ambiguities", None) or []:
        unresolved.append(UnresolvedIR(code="AMBIGUITY", message=str(amb)))
    if getattr(plan, "unsupported_reason", None):
        unresolved.append(
            UnresolvedIR(code="UNSUPPORTED", message=str(getattr(plan, "unsupported_reason")))
        )

    entity = getattr(plan, "entity", "building") or "building"
    if entity not in {"building", "admin_area", "basic_zone", "industrial_complex"}:
        entity = "unknown"
    task = normalize_task(getattr(plan, "query_kind", None))
    ir = QueryIR(
        task=task,
        entity=entity,  # type: ignore[arg-type]
        scope=scope,
        predicates=predicates,
        temporal=None,
        spatial=spatial,
        measures=measures,
        aggregations=aggregations,
        dimensions=dimensions,
        comparisons=[],
        ordering=ordering,
        limit=getattr(plan, "limit", None),
        outputs=list(getattr(plan, "select", None) or []),
        result_shape=result_shape_for_task(task),  # type: ignore[arg-type]
        interaction=InteractionIR(),
        unresolved=unresolved,
        provenance=ProvenanceIR(
            source="semantic_plan",
            confidence=getattr(plan, "model_confidence", None),
            notes=list(getattr(plan, "assumptions", None) or []),
        ),
    )
    return normalize_query_ir(ir)


def _predicate_ir_to_spec(pred: PredicateIR) -> Any | None:
    from txt2sql.semantic_plan.models import FilterSpec, OperandSpec, PredicateSpec

    if pred.logical_group in {"and", "or"} and pred.children:
        args = [_predicate_ir_to_spec(child) for child in pred.children]
        args = [item for item in args if item is not None]
        if not args:
            return None
        if len(args) == 1:
            node = args[0]
        else:
            node = PredicateSpec(op=pred.logical_group, args=args)
        if pred.negated:
            return PredicateSpec(op="not", args=[node])
        return node
    if not pred.field or not pred.operator:
        return None
    filt = FilterSpec(
        field=pred.field,
        operator=pred.operator,  # type: ignore[arg-type]
        value=pred.value,
        value2=pred.value2,
        unit=pred.unit,
        value_field=pred.value_field,
    )
    from txt2sql.semantic_plan.migrate import filter_to_predicate

    node = filter_to_predicate(filt)
    if pred.negated:
        return PredicateSpec(op="not", args=[node])
    return node


def query_ir_to_semantic_plan(ir: QueryIR) -> Any:
    """Best-effort reverse adapter to legacy SemanticQueryPlan."""
    from txt2sql.semantic_plan.models import (
        AggregationSpec,
        FilterSpec,
        OrderSpec,
        PlaceSpec,
        PredicateSpec,
        ProjectionSpec,
        ScopeSpec,
        SemanticQueryPlan,
        SpatialRelationSpec,
        SpatialTargetSpec,
    )

    assert_no_physical_names(ir.model_dump())

    kind_map = {
        "count": "count",
        "list": "list",
        "rank": "rank",
        "aggregate": "aggregate",
        "group": "distribution",
        "distribution": "distribution",
        "ratio": "aggregate",
        "compare": "aggregate",
        "meta": "count",
        "unknown": "count",
    }
    query_kind = kind_map.get(ir.task, "count")
    entity = ir.entity if ir.entity != "unknown" else "building"

    scope = None
    if ir.scope and ir.scope.place:
        scope = ScopeSpec(
            place=PlaceSpec(name=ir.scope.place, kind=(ir.scope.place_kind or "unknown")),  # type: ignore[arg-type]
            spatial_mode=ir.scope.spatial_mode,
        )

    filters: list[FilterSpec] = []
    root_predicate: PredicateSpec | None = None
    for pred in ir.predicates:
        if pred.children or pred.logical_group:
            converted = _predicate_ir_to_spec(pred)
            if converted is not None:
                if root_predicate is None:
                    root_predicate = converted
                else:
                    root_predicate = PredicateSpec(
                        op="and",
                        args=[root_predicate, converted],
                    )
            continue
        if not pred.field or not pred.operator:
            continue
        filters.append(
            FilterSpec(
                field=pred.field,
                operator=pred.operator,  # type: ignore[arg-type]
                value=pred.value,
                value2=pred.value2,
                unit=pred.unit,
                value_field=pred.value_field,
            )
        )

    # TemporalIR → FilterSpec when not already present as a predicate.
    if ir.temporal is not None:
        t = ir.temporal
        field = t.field or "approval_date"
        if t.age_years is not None and t.operator:
            already = any(
                f.field == "building_age_years"
                and f.operator == t.operator
                and f.value == t.age_years
                for f in filters
            )
            if not already:
                filters.append(
                    FilterSpec(
                        field="building_age_years",
                        operator=t.operator,  # type: ignore[arg-type]
                        value=t.age_years,
                    )
                )
        elif t.operator and t.value is not None:
            already = any(
                f.field == field and f.operator == t.operator and f.value == t.value
                for f in filters
            )
            if not already:
                filters.append(
                    FilterSpec(
                        field=field,
                        operator=t.operator,  # type: ignore[arg-type]
                        value=t.value,
                        value2=t.value2,
                    )
                )

    aggregations = [
        AggregationSpec(
            function=a.function if a.function not in {"ratio", "derived"} else "count",  # type: ignore[arg-type]
            field=a.field,
            alias=a.alias,
            percentile=a.percentile,
        )
        for a in ir.aggregations
        if a.function not in {"ratio", "derived"}
    ]
    projections = [ProjectionSpec(field=m.concept, alias=m.alias) for m in ir.measures]
    order_by = [
        OrderSpec(field=o.field or "count", direction=o.direction) for o in ir.ordering if o.field or True
    ]
    spatial_relations = []
    for s in ir.spatial:
        if not s.relation:
            continue
        target_place = s.target_place
        if not target_place and ir.scope and ir.scope.place:
            # SpatialFilter without explicit target inherits scope place.
            target_place = ir.scope.place
        spatial_relations.append(
            SpatialRelationSpec(
                relation=s.relation,  # type: ignore[arg-type]
                target=SpatialTargetSpec(
                    entity=s.target_entity if s.target_entity in {"building", "admin_area", "basic_zone", "industrial_complex"} else None,  # type: ignore[arg-type]
                    place=PlaceSpec(name=target_place) if target_place else None,
                    longitude=s.longitude,
                    latitude=s.latitude,
                ),
                distance_m=s.distance_m,
                min_ratio=s.min_ratio,
            )
        )

    return SemanticQueryPlan(
        query_kind=query_kind,  # type: ignore[arg-type]
        entity=entity,  # type: ignore[arg-type]
        scope=scope,
        predicate=root_predicate,
        filters=filters,
        select=list(ir.outputs) or [m.concept for m in ir.measures],
        projections=projections,
        aggregations=aggregations,
        group_by=[d.field for d in ir.dimensions],
        order_by=[o for o in order_by if o.field],
        limit=ir.limit,
        spatial_relations=spatial_relations,
        requires_clarification=any(u.code.startswith("CLARIFY") for u in ir.unresolved),
        ambiguities=[u.message for u in ir.unresolved if u.code == "AMBIGUITY"],
        unsupported_reason=next((u.message for u in ir.unresolved if u.code == "UNSUPPORTED"), None),
        model_confidence=ir.provenance.confidence or 0.5,
        assumptions=list(ir.provenance.notes),
    )
