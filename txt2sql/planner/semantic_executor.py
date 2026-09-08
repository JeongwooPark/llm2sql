"""Execute Logical/Physical plans through deterministic compiler (semantic-v2)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Literal

import psycopg

from txt2sql.canonical_physical_columns import D010_FIELD_COLUMNS, D198_FIELD_COLUMNS
from txt2sql.compiler.safety import validate_compiled_sql
from txt2sql.db import execute_query
from txt2sql.planner.executor_adapter import ExecutionPlanBundle
from txt2sql.planner.logical import LogicalPlan
from txt2sql.planner.physical import PhysicalPlan, reject_partial_execution
from txt2sql.query_ir.adapters import query_ir_to_semantic_plan
from txt2sql.query_ir.models import PredicateIR, QueryIR, ScopeIR, TemporalIR
from txt2sql.semantic_plan.catalog import get_entity
from txt2sql.semantic_plan.compiler import compile_semantic_plan
from txt2sql.semantic_plan.models import AggregationSpec, OrderSpec, SemanticQueryPlan

V2FailureCode = Literal["GATE", "COMPILE", "VALIDATE", "EXECUTE", "PARTIAL"]


@dataclass(frozen=True)
class SemanticV2Failure:
    code: V2FailureCode
    message: str
    physical_strategy: str | None = None
    logical_status: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def refine_query_ir_for_compile(ir: QueryIR, *, question: str | None = None) -> QueryIR:
    """Fix common IR slot issues before compile (operator-level, not Q-ID)."""
    data = ir.model_copy(deep=True)
    # Prefer aggregatable numeric measure as AVG/SUM/... field when multiple metrics exist.
    numericish = {
        "height_m",
        "gross_floor_area_m2",
        "building_area_m2",
        "site_area_m2",
        "ground_floors",
        "basement_floors",
        "building_coverage_ratio",
        "floor_area_ratio",
        "building.gross_floor_area",
        "building.height",
        "building.building_coverage_ratio",
        "building.floor_area_ratio",
        "building.ground_floors",
        "building.basement_floors",
    }
    concept_to_field = {
        "building.gross_floor_area": "gross_floor_area_m2",
        "building.height": "height_m",
        "building.building_coverage_ratio": "building_coverage_ratio",
        "building.floor_area_ratio": "floor_area_ratio",
        "building.ground_floors": "ground_floors",
        "building.basement_floors": "basement_floors",
        "building.usage": "usage",
        "building.detail_usage": "detail_usage",
        "building.approval_date": "approval_date",
        "building.permit_date": "permit_date",
    }
    measure_fields: list[str] = []
    for m in data.measures:
        f = concept_to_field.get(m.concept, m.concept)
        measure_fields.append(f)

    categorical = {"usage", "structure", "detail_usage", "violation_status", "special_land"}
    for agg in data.aggregations:
        if agg.function in {"avg", "sum", "min", "max", "median", "stddev", "percentile"}:
            field = agg.field
            if field in categorical or field is None or field not in numericish:
                pick = next((f for f in measure_fields if f in numericish), None)
                if pick:
                    agg.field = pick
            for f in measure_fields:
                if f in categorical and not any(p.field == f for p in data.predicates):
                    for m in data.measures:
                        mapped = concept_to_field.get(m.concept, m.concept)
                        if mapped == f and m.provenance and m.provenance.text:
                            text = m.provenance.text
                            if text not in {"용도", "구조", "높이", "연면적", "건폐율", "용적률"}:
                                data.predicates.append(
                                    PredicateIR(field=f, operator="eq", value=text)
                                )
                            break
    for m in data.measures:
        if m.concept in concept_to_field:
            m.concept = concept_to_field[m.concept]

    # Enrich temporal from NL; replace conflicting date predicates from contract/operators.
    if question and (data.temporal is not None or _looks_temporal(question)):
        from txt2sql.query_understanding.temporal import parse_temporal_filters

        tfilters = parse_temporal_filters(question)
        if tfilters:
            date_fields = {"approval_date", "permit_date", "building_age_years"}
            data.predicates = [p for p in data.predicates if p.field not in date_fields]
            data.temporal = TemporalIR(
                field=tfilters[0].field,
                operator=tfilters[0].operator,
                value=tfilters[0].value,
                value2=tfilters[0].value2,
            )
            for tf in tfilters:
                data.predicates.append(
                    PredicateIR(
                        field=tf.field,
                        operator=tf.operator,
                        value=tf.value,
                        value2=tf.value2,
                        unit=tf.unit,
                    )
                )

    _enrich_d198_from_nl(data, question)

    return data


# D198 A27 (detail_usage) vs A25 (main usage) — gold-aligned
D198_A27_TERMS = frozenset(
    {
        "아파트",
        "오피스텔",
        "다세대주택",
        "다가구주택",
        "단독주택",
        "연립주택",
        "일반음식점",
        "근린생활시설",
    }
)


def _enrich_d198_from_nl(data: QueryIR, question: str | None) -> None:
    """Map NL usage terms to D198 filters (aggregate scalars only)."""
    if not question or data.task != "aggregate":
        return
    from txt2sql.domain import (
        extract_detail_usages,
        extract_place,
        extract_usage_classes,
    )

    details = extract_detail_usages(question)
    if details:
        # Detail usage wins: drop main-usage predicates that would AND with A25.
        data.predicates = [
            p for p in data.predicates if p.field not in {"usage", "detail_usage"}
        ]
        for term in details:
            field = "detail_usage" if term in D198_A27_TERMS else "usage"
            if not any(
                p.field == field and str(p.value) == term for p in data.predicates
            ):
                data.predicates.append(
                    PredicateIR(field=field, operator="eq", value=term)
                )

    classes = extract_usage_classes(question)
    if len(classes) == 1:
        data.predicates = [p for p in data.predicates if p.field != "usage_class"]
        term = classes[0]
        if not any(
            p.field == "usage_class" and str(p.value) == term for p in data.predicates
        ):
            data.predicates.append(
                PredicateIR(field="usage_class", operator="eq", value=term)
            )
    # len>=2: compare/OR 는 SP FILTER·group 경로에 맡기고 AND 하지 않음

    place = extract_place(question) or (data.scope.place if data.scope else None)
    if place and str(place).endswith(("동", "가", "리")):
        data.predicates = [p for p in data.predicates if p.field != "legal_dong"]
        if data.scope is None:
            data.scope = ScopeIR(place=str(place))
        elif not data.scope.place:
            data.scope = data.scope.model_copy(update={"place": str(place)})


def _parse_percentile_tail(question: str) -> tuple[float, str, str, str] | None:
    """상위/오래된/하위 N% … 평균 X → (percentile_cont, rank_field, agg_field, side).

    side: ``high`` = rank >= cut (최근/상위), ``low`` = rank <= cut (가장 오래된·하위·빠른).
    Also supports 「최근 준공 상위 N% … 평균」→ rank by approval year.
    """
    q = question or ""
    oldest = bool(
        re.search(r"가장\s*오래된|제일\s*오래된|오래된\s*건물", q)
    )
    low_tail = oldest or bool(
        re.search(
            r"하위\s*\d|준공연도가?\s*빠른|사용승인(?:연도|일)?가?\s*빠른|"
            r"빠른\s*하위|연도가?\s*빠른\s*하위",
            q,
        )
    )
    m = re.search(r"(?:상위|하위)\s*(\d+(?:\.\d+)?)\s*%\s*", q)
    if m is None:
        # 「최근 준공 5%」「가장 오래된 건물 1%」「준공연도가 빠른 하위 10%」
        m = re.search(
            r"(?:최근\s*준공|최근준공|최근\s*사용승인|가장\s*오래된|제일\s*오래된|"
            r"오래된\s*건물|준공연도가?\s*빠른|사용승인(?:연도|일)?가?\s*빠른|"
            r"하위)\s*(?:된\s*)?(?:건물\s*)?(?:상위\s*|하위\s*)?(\d+(?:\.\d+)?)\s*%",
            q,
        )
    if m is None:
        m = re.search(r"(?:건물\s*)?(\d+(?:\.\d+)?)\s*%\s*의\s*평균", q)
        if m and not low_tail and "최근" not in q and "상위" not in q:
            m = None
    if not m or "평균" not in q:
        return None
    top_pct = float(m.group(1))
    if top_pct <= 0 or top_pct >= 100:
        return None

    # 최근 준공/사용승인 연도 꼬리 또는 가장 오래된·하위·빠른 연도 꼬리
    year_tail = low_tail or any(
        k in q
        for k in (
            "최근 준공",
            "최근준공",
            "최근 사용승인",
            "준공 상위",
            "사용승인 상위",
            "준공연도",
            "사용승인연도",
        )
    )
    if year_tail:
        rank_field = "approval_date"
        agg_field = None
        if any(k in q for k in ("평균 건축물면적", "평균 건축면적", "평균 건물면적")):
            agg_field = "building_area_m2"
        elif "평균 연면적" in q:
            agg_field = "gross_floor_area_m2"
        elif "평균 높이" in q or ("평균" in q and "높이" in q):
            agg_field = "height_m"
        elif any(k in q for k in ("평균 지상층", "평균 층", "지상층수")):
            agg_field = "ground_floors"
        if agg_field is None:
            return None
        if low_tail:
            return round(top_pct / 100.0, 6), rank_field, agg_field, "low"
        return round(1.0 - top_pct / 100.0, 6), rank_field, agg_field, "high"

    pct = round(1.0 - top_pct / 100.0, 6)
    rank_field = (
        "height_m"
        if "높이" in q and "연면적" not in q.split("평균")[0]
        else "gross_floor_area_m2"
        if "연면적" in q.split("평균")[0]
        else None
    )
    if "높이" in q and "연면적" in q and "상위" in q:
        before_avg = q.split("평균", 1)[0]
        rank_field = "height_m" if "높이" in before_avg else "gross_floor_area_m2"
        if "높이" in before_avg and "연면적" in before_avg:
            rank_field = (
                "height_m"
                if before_avg.index("높이") < before_avg.index("연면적")
                else "gross_floor_area_m2"
            )

    agg_field = None
    if "평균 연면적" in q or "평균 건축면적" in q:
        agg_field = "gross_floor_area_m2"
    elif any(k in q for k in ("평균 건축물면적", "평균 건물면적")):
        agg_field = "building_area_m2"
    elif any(k in q for k in ("평균 지상층", "평균 층", "지상층수")):
        agg_field = "ground_floors"
    elif "평균 높이" in q:
        agg_field = "height_m"

    if rank_field is None or agg_field is None or rank_field == agg_field:
        return None
    if low_tail and not year_tail:
        return round(top_pct / 100.0, 6), rank_field, agg_field, "low"
    return pct, rank_field, agg_field, "high"


def _compile_percentile_tail_sql(
    question: str,
    *,
    pct: float,
    rank_field: str,
    agg_field: str,
    plan: SemanticQueryPlan,
    side: str = "high",
) -> str:
    """Percentile-threshold tail aggregate (Q191/Q192/Q298/Q334-class)."""
    use_d198 = (
        "d198_ledger" in (plan.assumptions or [])
        or rank_field in {"approval_date", "permit_date", "building_age_years"}
        or any(
            f.field in {"detail_usage", "usage", "usage_class", "approval_date"}
            for f in plan.filters
        )
    )
    col_map = D198_FIELD_COLUMNS if use_d198 else D010_FIELD_COLUMNS
    if rank_field == "approval_date":
        rank_col = col_map.get("approval_date") or col_map.get("building_age_years")
    else:
        rank_col = col_map[rank_field]
    agg_col = col_map[agg_field]

    if use_d198:
        from txt2sql.semantic_plan.compiler import _d198_table_for_plan

        table = _d198_table_for_plan(plan) or get_entity("building").default_table
    else:
        table = get_entity("building").default_table

    if rank_field == "approval_date":
        # Gold: year from approval date text; recent tail = high years
        rank_expr = (
            f"LEFT(regexp_replace(b.\"{rank_col}\"::text, '[^0-9]', '', 'g'), 4)::int"
        )
        rank_null = f"b.\"{rank_col}\"::text ~ '^[0-9]{{4}}'"
    else:
        rank_expr = f'NULLIF(TRIM(b."{rank_col}"::text), \'\')::float8'
        rank_null = f"{rank_expr} IS NOT NULL"
    agg_expr = f'NULLIF(TRIM(b."{agg_col}"::text), \'\')::float8'
    pct_sql = f"{pct:g}"

    where_parts = [
        rank_null,
        f"{agg_expr} IS NOT NULL",
    ]
    if rank_field == "height_m" and not use_d198:
        where_parts.insert(0, f"{rank_expr} > 0")
        where_parts.append(f"{rank_expr} <= 500")
    elif rank_field == "gross_floor_area_m2" and not use_d198:
        where_parts.insert(0, f"{rank_expr} > 0")

    for filt in plan.filters:
        if filt.field == "usage" and filt.operator == "eq":
            col = "A25" if use_d198 else "A9"
            where_parts.append(f'b."{col}" = {_sql_lit(filt.value)}')
        elif filt.field == "detail_usage" and filt.operator == "eq":
            where_parts.append(f'b."A27" = {_sql_lit(filt.value)}')
        elif filt.field == "legal_dong" and filt.operator == "contains":
            where_parts.append(
                f'(b."A4" LIKE {_sql_lit("% " + str(filt.value))} '
                f'OR b."A4" = {_sql_lit(filt.value)})'
            )

    # Gu scope from plan (A3 prefix)
    if plan.scope and plan.scope.place and plan.scope.place.kind == "gu":
        from txt2sql.gazetteer import sigungu_a3_prefix

        prefix = sigungu_a3_prefix(plan.scope.place.name)
        if prefix:
            where_parts.append(f'b."A3" LIKE {_sql_lit(prefix + "%")}')

    where_sql = " AND ".join(where_parts)
    rank_alias = (
        "y"
        if rank_field == "approval_date"
        else "gfa"
        if rank_field == "gross_floor_area_m2"
        else "h"
    )
    agg_alias = "fl" if agg_field == "ground_floors" else "v"
    avg_alias = f"avg_{agg_field}"
    cmp = "<=" if side == "low" else ">="

    return f"""WITH base AS (
  SELECT {rank_expr} AS {rank_alias}, {agg_expr} AS {agg_alias}
  FROM "{table}" b
  WHERE {where_sql}
), p AS (
  SELECT PERCENTILE_CONT({pct_sql}) WITHIN GROUP (ORDER BY {rank_alias}) AS cut FROM base
)
SELECT AVG({agg_alias}) AS "{avg_alias}", COUNT(*)::bigint AS "n", (SELECT cut FROM p) AS "cut"
FROM base, p WHERE {rank_alias} {cmp} p.cut"""


def _sql_lit(value: object) -> str:
    if value is None:
        return "NULL"
    text = str(value).replace("'", "''")
    return f"'{text}'"


def _append_aggregate_count(plan: SemanticQueryPlan) -> None:
    if plan.query_kind != "aggregate" or not plan.aggregations:
        return
    if any(a.function == "count" for a in plan.aggregations):
        return
    plan.aggregations.append(AggregationSpec(function="count", alias="n"))


def _looks_temporal(question: str) -> bool:
    q = question or ""
    needles = ("년", "년대", "경과", "사용승인", "허가일", "건축년", "지어진")
    return any(n in q for n in needles)


def physical_to_dataset_assumptions(
    physical: PhysicalPlan | None,
    logical: LogicalPlan | None = None,
) -> list[str]:
    """Map PhysicalPlan strategy/bindings to SQP assumptions (compiler input)."""
    if physical is None:
        return []
    datasets = set()
    if logical is not None:
        datasets = {b.dataset for b in logical.bindings}
    elif physical.logical is not None:
        datasets = {b.dataset for b in physical.logical.bindings}

    if physical.strategy == "D198_EXECUTOR" or "building_attr_d198" in datasets:
        return ["d198_ledger"]
    if physical.strategy == "D010_EXECUTOR":
        return ["d010_gis"]
    return []


def _apply_dataset_assumptions(
    plan: SemanticQueryPlan,
    *,
    physical: PhysicalPlan | None,
    logical: LogicalPlan | None,
    refined: QueryIR,
    question: str | None = None,
) -> None:
    """Apply D010/D198 grain from the central policy only.

    MAIN485 §4.3: router / QueryIR physical / SQP / compiler share one grain
    decision. A PhysicalPlan hint must not override coverage+usage → D198
    (or exclusive ledger fields) with D010 — that was the Phase6→P012 −15
    mass regression (A25→A9).
    """
    from txt2sql.dataset_grain import grain_to_assumption, resolve_dataset_grain

    # physical/logical kept for call-site compatibility; grain is not taken from them.
    _ = (physical, logical)
    notes = [
        n for n in (plan.assumptions or []) if n not in {"d010_gis", "d198_ledger"}
    ]
    grain = resolve_dataset_grain(refined, question or "")
    notes.append(grain_to_assumption(grain))
    plan.assumptions = notes


def _enrich_place_scope(plan: SemanticQueryPlan, question: str | None) -> None:
    """PlaceScopePolicy v1.0 — BND/A4/A3 from gazetteer, not suffix heuristics."""
    if not question or not plan.scope or not plan.scope.place:
        return
    from txt2sql.gazetteer import question_needs_admin_boundary
    from txt2sql.semantic_catalog.place_scope import (
        PlaceEntity,
        PlaceScopeContext,
        resolve_place_scope,
    )

    place = plan.scope.place
    name = place.name.strip()
    if not name:
        return
    place_type = place.kind if place.kind != "unknown" else None
    prefer_admin = question_needs_admin_boundary(question) or any(
        k in question for k in ("행정동", "경계", "내부", "이내")
    )
    prefer_legal = (not prefer_admin) and any(
        k in question for k in ("법정동", "건물", "건축물", "평균", "높이", "연면적")
    )
    binding = resolve_place_scope(
        PlaceEntity(
            name=name,
            place_type=place_type,
            sido=place.sido,
            sigungu=place.sigungu,
        ),
        context=PlaceScopeContext(
            question=question,
            default_sido=place.sido or "부산광역시",
            prefer_admin=prefer_admin,
            prefer_legal=prefer_legal,
        ),
    )
    if binding.semantic_type == "ADMIN_DONG":
        place.kind = "admin_dong"
        plan.scope.spatial_mode = "boundary"
    elif binding.semantic_type == "LEGAL_DONG":
        place.kind = "legal_dong"
        plan.scope.spatial_mode = "auto"
    elif binding.semantic_type == "SIGUNGU":
        place.kind = "gu"
    elif binding.semantic_type == "SIDO":
        place.kind = "sido"
    # Preserve parent admin context for compiler homonym disambiguation.
    if binding.sido:
        place.sido = binding.sido
    if binding.sigungu:
        place.sigungu = binding.sigungu
    if binding.code:
        place.code = binding.code


def build_sqp(
    ir: QueryIR,
    *,
    question: str | None = None,
    physical: PhysicalPlan | None = None,
    logical: LogicalPlan | None = None,
) -> SemanticQueryPlan:
    refined = refine_query_ir_for_compile(ir, question=question)
    plan = query_ir_to_semantic_plan(refined)
    _enrich_place_scope(plan, question)
    _apply_dataset_assumptions(
        plan, physical=physical, logical=logical or (physical.logical if physical else None), refined=refined, question=question
    )
    # Ensure group dimensions present
    if refined.dimensions and not plan.group_by:
        plan.group_by = [d.field for d in refined.dimensions]
    # Stable secondary order for group/rank
    if plan.query_kind in {"distribution", "rank"} and plan.group_by:
        primary = "count"
        if plan.aggregations:
            a0 = plan.aggregations[0]
            if a0.alias:
                primary = a0.alias
            elif a0.function == "count":
                primary = "count"
            elif a0.function and a0.field:
                primary = f"{a0.function}_{a0.field}"
                a0.alias = primary
        if not plan.order_by:
            plan.order_by = [
                OrderSpec(field=primary, direction="desc"),
                OrderSpec(field=plan.group_by[0], direction="asc"),
            ]
        elif len(plan.order_by) == 1 and plan.group_by:
            key = plan.group_by[0]
            if all(o.field != key for o in plan.order_by):
                plan.order_by = list(plan.order_by) + [OrderSpec(field=key, direction="asc")]
            # Replace bogus ORDER BY count when no count agg exists
            if plan.order_by[0].field == "count" and primary != "count":
                plan.order_by[0] = OrderSpec(field=primary, direction="desc")
    # Rank/list-top: unify Sort+Limit defaults
    if plan.query_kind == "rank":
        if not plan.order_by and plan.aggregations:
            alias = plan.aggregations[0].alias or plan.aggregations[0].field or "count"
            plan.order_by = [OrderSpec(field=alias, direction="desc")]
        if plan.limit is None:
            plan.limit = 10
        if not plan.select and refined.outputs:
            plan.select = list(refined.outputs)
    if plan.query_kind == "list" and plan.limit is None and refined.limit:
        plan.limit = refined.limit
    _append_aggregate_count(plan)
    return plan


def compile_sql_from_bundle(
    bundle: ExecutionPlanBundle, *, question: str | None = None
) -> tuple[str, dict[str, Any]]:
    reject_partial_execution(bundle.physical)
    plan = build_sqp(
        bundle.query_ir,
        question=question,
        physical=bundle.physical,
        logical=bundle.logical,
    )
    if question:
        tail = _parse_percentile_tail(question)
        if tail is not None and bundle.query_ir.task == "aggregate":
            pct, rank_field, agg_field, side = tail
            sql = _compile_percentile_tail_sql(
                question,
                pct=pct,
                rank_field=rank_field,
                agg_field=agg_field,
                plan=plan,
                side=side,
            )
            table = get_entity("building").default_table
            if "d198_ledger" in (plan.assumptions or []):
                from txt2sql.semantic_plan.compiler import _d198_table_for_plan

                table = _d198_table_for_plan(plan) or table
            return sql, {
                "tables": [table],
                "route": "semantic_v2",
                "semantic_plan": plan.model_dump(),
                "assumptions": list(plan.assumptions or []) + ["percentile_tail"],
                "physical_strategy": bundle.physical.strategy,
            }
    compiled = compile_semantic_plan(plan)
    from txt2sql.query_contract import verify_query_contract

    contract_errors = verify_query_contract(
        plan, compiled, question=question or ""
    )
    if contract_errors and question:
        from txt2sql.query_understanding.contract import extract_contract
        from txt2sql.semantic_plan.plan_repair import (
            is_repairable,
            repair_plan_from_contract,
        )

        if is_repairable(contract_errors):
            bound = extract_contract(question)
            plan = repair_plan_from_contract(
                plan, bound, contract_errors, question
            )
            compiled = compile_semantic_plan(plan)
            contract_errors = verify_query_contract(
                plan, compiled, question=question
            )
    sql = compiled.sql if hasattr(compiled, "sql") else str(compiled)
    meta = {
        "tables": getattr(compiled, "tables", []),
        "route": getattr(compiled, "route", "semantic_v2"),
        "semantic_plan": getattr(compiled, "semantic_plan", plan.model_dump()),
        "assumptions": list(plan.assumptions or []),
        "physical_strategy": bundle.physical.strategy,
        "compiled": compiled,
        "contract_errors": contract_errors,
    }
    return sql, meta


def execution_trace(
    bundle: ExecutionPlanBundle,
    *,
    execution_source: str,
    compiler_source: str,
    fallback_source: str | None = None,
) -> dict[str, Any]:
    return {
        "query_ir_task": bundle.query_ir.task,
        "logical_status": bundle.logical.status,
        "physical_strategy": bundle.physical.strategy,
        "execution_source": execution_source,
        "compiler_source": compiler_source,
        "fallback_source": fallback_source,
        "reason_codes": list(bundle.logical.reason_codes),
    }


def should_try_semantic_v2(bundle: ExecutionPlanBundle) -> bool:
    if bundle.logical.status != "READY":
        return False
    if bundle.physical.partial:
        return False
    source = bundle.query_ir.provenance.source_text or ""
    from txt2sql.count_routes import PRIORITY_COUNT_INTENTS, match_priority_count_route
    from txt2sql.domain import extract_detail_usages, extract_usage_classes
    from txt2sql.intent_router import try_route

    # 명시 구간 분할(미만/~ /초과로 나눠)은 SQP BinSpec 경로 우선
    if any(k in source for k in ("나눠", "나누어", "나눠서", "구간별", "크기별")):
        return False
    # 세부용도·용도분류·구별 파생차이는 SQP grain/derived 경로 우선
    if extract_detail_usages(source) or extract_usage_classes(source):
        return False
    if any(k in source for k in ("용도분류별", "건물용도분류별", "주요용도별")):
        return False
    if any(k in source for k in ("각 구", "구·군")) and any(
        k in source for k in ("차이", "최대", "평균", "두 번째", "두번째")
    ):
        return False
    if "준공연도별" in source and any(k in source for k in ("건폐율", "용적률", "용적율")):
        return False
    if any(k in source for k in ("허가된", "허가일", "허가 후")) and any(
        k in source for k in ("이후", "이후", "부터", "년")
    ):
        # 허가 시점 목록은 D198 permit_date SQP 우선
        if any(k in source for k in ("보여", "찾아", "목록")):
            return False
    if match_priority_count_route(source) is not None:
        return False
    legacy = try_route(source)
    if legacy is not None and legacy.intent in PRIORITY_COUNT_INTENTS:
        return False
    # Keep Fast Path simple count/list on legacy router.
    if bundle.physical.strategy in {"FAST_SIMPLE_COUNT", "FAST_THRESHOLD"}:
        return False
    ir = bundle.query_ir
    # Unstable / high-regression operators stay on legacy/SQP until compilers mature.
    if ir.task in {"meta", "list", "rank", "compare", "unknown"}:
        return False
    # SpatialFilter→PostGIS still available via SQP/legacy; v2 ownership gated carefully.
    if ir.spatial:
        return False

    hints = ir.provenance.legacy_hints or {}
    source = ir.provenance.source_text or ""

    has_temporal = ir.temporal is not None and (
        ir.temporal.field is not None or ir.temporal.age_years is not None
    )
    has_temporal = has_temporal or any(
        p.field in {"approval_date", "permit_date", "building_age_years"}
        for p in ir.predicates
    )

    if ir.task == "aggregate":
        # Bare "면적 평균" is often clarify/meta — do not force AVG(gross_floor_area).
        if "면적" in source and not any(
            tok in source
            for tok in ("연면적", "건축물면적", "건축면적", "대지면적", "부지면적")
        ):
            return False
        # Multi-percentile / variance: SQP aliases + VAR_POP are more stable than v2.
        if any(
            k in source
            for k in ("분위수", "분위", "분산", "표준편차", "중앙값", "상관", "상관계수")
        ):
            return False
        if "일수" in source and any(k in source for k in ("중앙값", "표준편차")):
            return False
        if len(re.findall(r"\d+(?:\.\d+)?\s*%", source)) >= 2 and "비율" not in source:
            return False
        # 주거용·상업용 평균 비교는 SQP FILTER AVG 경로
        from txt2sql.domain import extract_usage_classes

        usage_classes = extract_usage_classes(source)
        if len(usage_classes) >= 2 and (
            any(k in source for k in ("비교", "차이"))
            or re.search(
                r"(주거용|상업용|공업용|문교사회용).{0,6}(과|와).{0,6}"
                r"(주거용|상업용|공업용|문교사회용)",
                source,
            )
        ):
            return False
        # 산업단지 면적 집계·기초구역∩행정동은 SQP
        if "산업단지" in source and "면적" in source and "건물" not in source:
            return False
        if "기초구역" in source and any(k in source for k in ("겹치", "교차")):
            return False
        return bool(ir.aggregations)
    if ir.task in {"group", "distribution"}:
        # Simple group+count/avg with explicit dimensions (no spatial).
        if not ir.dimensions:
            return False
        if any(a.function == "ratio" for a in ir.aggregations):
            return False
        return bool(ir.aggregations) or hints.get("wants_count") is True
    if ir.task == "ratio":
        return False  # ratio still unstable on v2
    if ir.task == "count":
        # Avoid list-shaped questions mis-tagged as count (e.g. "보여줘", "찾아줘").
        if not hints.get("wants_count"):
            return False
        list_shaped = any(k in source for k in ("보여", "찾아", "목록", "리스트"))
        count_shaped = any(
            k in source for k in ("몇", "개수", "건수", "채", "수를", "수가", "개야")
        )
        if list_shaped and not count_shaped:
            return False
        if has_temporal:
            return True
        # Multi-predicate attribute counts (e.g. coverage + FAR thresholds).
        if len(ir.predicates) >= 2:
            return True
        return False
    return False


def _v2_fail(
    bundle: ExecutionPlanBundle,
    code: V2FailureCode,
    message: str,
) -> dict[str, Any]:
    failure = SemanticV2Failure(
        code=code,
        message=message,
        physical_strategy=bundle.physical.strategy,
        logical_status=bundle.logical.status,
    )
    return {
        "ok": False,
        "v2_failure": failure.as_dict(),
        "v2_failure_code": failure.code,
        "fallback_source": "legacy_after_semantic_v2",
        **execution_trace(
            bundle,
            execution_source="semantic_v2",
            compiler_source="deterministic_compiler_v2",
            fallback_source="legacy_after_semantic_v2",
        ),
    }


def try_execute_semantic_v2(
    question: str,
    bundle: ExecutionPlanBundle,
    *,
    conn: psycopg.Connection,
    default_limit: int = 100,
) -> dict[str, Any] | None:
    """Compile+execute via semantic-v2.

    Returns:
      - success dict with ok=True
      - failure dict with ok=False + v2_failure_code (typed)
      - None only when the gate declines (caller should not treat as hard fail)
    """
    if not should_try_semantic_v2(bundle):
        return None
    if bundle.physical.partial:
        return _v2_fail(bundle, "PARTIAL", "partial coverage forbidden")
    try:
        sql, meta = compile_sql_from_bundle(bundle, question=question)
    except Exception as exc:  # noqa: BLE001
        return _v2_fail(bundle, "COMPILE", f"{type(exc).__name__}: {exc}")
    if meta.get("contract_errors"):
        return None
    try:
        validate_compiled_sql(sql, question=question, conn=None)
    except Exception as exc:  # noqa: BLE001
        return _v2_fail(bundle, "VALIDATE", f"{type(exc).__name__}: {exc}")
    try:
        rows = execute_query(conn, sql, default_limit=default_limit)
    except Exception as exc:  # noqa: BLE001
        return _v2_fail(bundle, "EXECUTE", f"{type(exc).__name__}: {exc}")
    return {
        "ok": True,
        "sql": sql,
        "rows": rows,
        "row_count": len(rows),
        "tables": meta.get("tables"),
        "route": "semantic_v2",
        "answer": None,
        "v2_failure_code": None,
        "fallback_source": None,
        **execution_trace(
            bundle,
            execution_source="semantic_v2",
            compiler_source="deterministic_compiler_v2",
            fallback_source=None,
        ),
    }
