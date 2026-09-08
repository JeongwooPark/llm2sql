"""검증된 SemanticQueryPlan → PostgreSQL SELECT. LLM을 호출하지 않는다."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from txt2sql.domain import STRUCTURE_ALIASES, d198_table_for_gu
from txt2sql.gazetteer import (
    KIND_SIGUNGU,
    adm_cd_prefix_for_place,
    canonical_sido,
    census_adm_prefix,
    classify_place,
    load_gazetteer,
    sigungu_a3_prefix,
    unique_sigungu_adm_prefix,
    uses_admin_boundary,
)
from txt2sql.canonical_physical_columns import D198_FIELD_COLUMNS
from txt2sql.dataset_tables import resolve_basic_zone_table, resolve_building_table
from txt2sql.place_scope import building_place_predicate
from txt2sql.semantic_plan.catalog import (
    ADMIN_TABLE,
    INDUSTRIAL_TABLE,
    get_entity,
    get_field,
    is_allowed_physical_identifier,
)
from txt2sql.semantic_plan.migrate import validate_predicate
from txt2sql.semantic_plan.models import (
    AggregationSpec,
    ExpressionSpec,
    FilterSpec,
    PredicateSpec,
    SemanticCompileError,
    SemanticQueryPlan,
    UnknownSemanticFieldError,
)
from txt2sql.semantic_plan.predicate_utils import effective_predicate
from txt2sql.query_understanding.temporal import reference_date_sql
from txt2sql.units import sql_number

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_OPS = {
    "eq": "=",
    "neq": "IS DISTINCT FROM",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}


@dataclass
class CompiledSemanticQuery:
    sql: str
    tables: list[str]
    route: str
    semantic_plan: dict
    uses_boundary: bool = False
    extra: dict = field(default_factory=dict)
    params: list[object] = field(default_factory=list)


_ENTITY_ALIAS = {
    "building": "b",
    "admin_area": "a",
    "basic_zone": "z",
    "industrial_complex": "i",
}

# D198 물리 컬럼 — single source: physical_columns.D198_FIELD_COLUMNS.
# D010 A27은 지하층수, D198 A27은 세부용도 (동일 문자라도 테이블이 다름).
D198_BUILDING_COLUMNS = dict(D198_FIELD_COLUMNS)


def compile_semantic_plan(plan: SemanticQueryPlan) -> CompiledSemanticQuery:
    if getattr(plan, "stages", None):
        return _compile_with_stages(plan)
    return _compile_flat_semantic_plan(plan)


def _compile_flat_semantic_plan(plan: SemanticQueryPlan) -> CompiledSemanticQuery:
    entity = get_entity(plan.entity)
    alias = _ENTITY_ALIAS.get(plan.entity, "t")
    col_map = _column_override(plan)
    d198_table = _d198_table_for_plan(plan)
    if d198_table:
        default_table = d198_table
    elif plan.entity == "building":
        default_table = resolve_building_table()
    elif plan.entity == "basic_zone":
        default_table = resolve_basic_zone_table()
    else:
        default_table = entity.default_table
    tables = [default_table]
    joins: list[str] = []
    where: list[str] = []
    uses_boundary = False
    compiled_nodes: list[str] = []

    place = plan.scope.place if plan.scope else None
    spatial_mode = plan.scope.spatial_mode if plan.scope else "auto"

    spatial_places: set[str] = set()
    spatial_order: list[str] = []
    if plan.spatial_relations:
        spatial_boundary, spatial_places = _apply_spatial_relations(
            alias, plan, tables, joins, where, spatial_order
        )
        uses_boundary = uses_boundary or spatial_boundary

    if plan.joins:
        _apply_canonical_joins(alias, plan, tables, joins, where)

    if place and place.name.strip() and place.name.strip() not in spatial_places:
        name = place.name.strip()
        sido_name = canonical_sido(name)
        if place.kind == "sido" or sido_name:
            prefix = census_adm_prefix(sido_name or name)
            if plan.entity == "admin_area" and prefix:
                where.append(
                    f'{alias}."ADM_CD" LIKE {_literal(prefix + "%", "text")}'
                )
            elif plan.entity == "industrial_complex":
                from txt2sql.gazetteer import sido_pnu_prefix

                pnu = sido_pnu_prefix(sido_name or name)
                if pnu:
                    where.append(
                        f'{alias}."A4" LIKE {_literal(str(pnu) + "%", "text")}'
                    )
            name = ""
        if not name:
            pass
        elif plan.entity != "building":
            where.append(_entity_place_sql(alias, plan.entity, name))
            extra = _adm_cd_sql(alias, name, plan)
            if extra and plan.entity == "admin_area":
                where.append(extra)
        else:
            # P012 §8: BND only when spatial_mode requests boundary (or explicit cue).
            # place.kind==admin_dong alone must not force a spatial join.
            if spatial_mode == "boundary":
                want_boundary = True
            elif spatial_mode == "attribute":
                want_boundary = False
            else:
                want_boundary = False
            # 구·군은 BND ADM_NM이 아님 → A3 접두 (전국: sigungu_a3_prefix)
            is_gu = place.kind == "gu" or (
                name.endswith(("구", "군"))
                and KIND_SIGUNGU in classify_place(name)
            )
            if is_gu:
                where.append(_building_place_sql(alias, name, plan=plan))
            elif want_boundary:
                uses_boundary = True
                if ADMIN_TABLE not in tables:
                    tables.append(ADMIN_TABLE)
                joins.append(
                    f"JOIN {_ident(ADMIN_TABLE, physical=True)} adm "
                    f"ON ST_Intersects({alias}.geometry, adm.geometry)"
                )
                where.append(_admin_name_sql("adm", name))
                extra = _adm_cd_sql("adm", name, plan)
                if extra:
                    where.append(extra)
            else:
                where.append(_building_place_sql(alias, name, plan=plan))

    # 동 스코프 + 부가 구 필터 (assumption 또는 PlaceSpec.sigungu/code)
    gu_scope = next(
        (a for a in (plan.assumptions or []) if a.startswith("scope_gu:")),
        None,
    )
    parent_gu = None
    parent_code = None
    if gu_scope and plan.entity == "building":
        parent_gu = gu_scope.split(":", 1)[1]
    elif place and plan.entity == "building" and place.kind in {
        "legal_dong",
        "admin_dong",
        "unknown",
    }:
        parent_gu = place.sigungu
        parent_code = place.code
    if parent_gu or parent_code:
        code = parent_code or (
            sigungu_a3_prefix(parent_gu, sido=_plan_sido_context(plan))
            if parent_gu
            else None
        )
        if code:
            where.append(f'{alias}."A3" LIKE {_literal(code + "%", "text")}')

    params: list[object] = []
    pred = effective_predicate(plan)
    if pred is not None:
        validate_predicate(pred)
        clause, _, pred_params = _predicate_sql(
            alias, plan.entity, pred, col_map=col_map
        )
        where.append(clause)
        params.extend(pred_params)
        compiled_nodes.extend(_predicate_node_ids(pred))

    gap_assump = next(
        (a for a in (plan.assumptions or []) if a.startswith("permit_year_gap:")),
        None,
    )
    if gap_assump and plan.entity == "building":
        n = int(gap_assump.split(":", 1)[1])
        a33 = _semantic_date_col(alias, "permit_date", col_map)
        a34 = _semantic_date_col(alias, "approval_date", col_map)
        y33 = f"LEFT(regexp_replace({a33}::text, '[^0-9]', '', 'g'), 4)::int"
        y34 = f"LEFT(regexp_replace({a34}::text, '[^0-9]', '', 'g'), 4)::int"
        where.append(f"{a33}::text ~ '^[0-9]{{4}}'")
        where.append(f"{a34}::text ~ '^[0-9]{{4}}'")
        where.append(f"ABS({y33} - {y34}) >= {n}")

    # 허가↔승인 시차: list/count는 WHERE, ratio는 SELECT FILTER(분자)에만 비교식
    ratio_mode = bool(plan.ratios)
    day_gte = next(
        (a for a in (plan.assumptions or []) if a.startswith("permit_day_gap_gte:")),
        None,
    )
    day_lte = next(
        (a for a in (plan.assumptions or []) if a.startswith("permit_day_gap_lte:")),
        None,
    )
    if (day_gte or day_lte) and plan.entity == "building":
        a33 = _semantic_date_col(alias, "permit_date", col_map)
        a34 = _semantic_date_col(alias, "approval_date", col_map)
        where.append(f"{a33} ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$'")
        where.append(f"{a34} ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$'")
        if not ratio_mode:
            if day_gte:
                days = int(day_gte.split(":", 1)[1])
                where.append(f"({a34}::date - {a33}::date) >= {days}")
            if day_lte:
                days = int(day_lte.split(":", 1)[1])
                where.append(f"({a34}::date - {a33}::date) <= {days}")

    if "permit_after_approval" in (plan.assumptions or []) and plan.entity == "building":
        a33 = _semantic_date_col(alias, "permit_date", col_map)
        a34 = _semantic_date_col(alias, "approval_date", col_map)
        where.append(f"{a33} ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$'")
        where.append(f"{a34} ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$'")
        where.append(f"{a33}::date > {a34}::date")

    if "permit_approval_year_neq" in (plan.assumptions or []) and plan.entity == "building":
        a33 = _semantic_date_col(alias, "permit_date", col_map)
        a34 = _semantic_date_col(alias, "approval_date", col_map)
        y33 = f"LEFT(regexp_replace({a33}::text, '[^0-9]', '', 'g'), 4)::int"
        y34 = f"LEFT(regexp_replace({a34}::text, '[^0-9]', '', 'g'), 4)::int"
        where.append(f"{a33}::text ~ '^[0-9]{{4}}'")
        where.append(f"{a34}::text ~ '^[0-9]{{4}}'")
        if not ratio_mode:
            where.append(f"{y33} <> {y34}")

    select_sql = _select_sql(alias, plan, col_map=col_map)
    from_sql = f"FROM {_ident(default_table, physical=True)} {alias}"
    if joins:
        from_sql = from_sql + "\n" + "\n".join(joins)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    group_sql = _group_sql(alias, plan, col_map=col_map)
    having_sql = ""
    if plan.having and plan.having.predicate:
        having_clause, _, having_params = _predicate_sql(
            alias, plan.entity, plan.having.predicate, col_map=col_map
        )
        having_sql = f"HAVING {having_clause}"
        params.extend(having_params)
    order_sql = _order_sql(alias, plan, col_map=col_map)
    if spatial_order:
        extra_order = ", ".join(spatial_order)
        order_sql = f"{order_sql}, {extra_order}" if order_sql else f"ORDER BY {extra_order}"
    limit_sql = f"LIMIT {int(plan.limit)}" if plan.limit else ""

    parts = [select_sql, from_sql]
    if where_sql:
        parts.append(where_sql)
    if group_sql:
        parts.append(group_sql)
    if having_sql:
        parts.append(having_sql)
    if order_sql:
        parts.append(order_sql)
    if limit_sql:
        parts.append(limit_sql)
    sql = "\n".join(parts) + ";"

    _assert_safe_sql(sql)
    _assert_table_columns(sql, plan.entity, tables)
    extra = dict(plan.model_dump().get("extra") or {})
    extra["compile_trace"] = {
        "entity": plan.entity,
        "predicate_nodes": compiled_nodes,
        "aggregations": [item.function for item in plan.aggregations],
        "group_fields": list(plan.group_by),
        "spatial_relations": [item.relation for item in plan.spatial_relations],
        "filters_merged": bool(plan.filters) and plan.predicate is not None,
    }
    if plan.spatial_relations:
        extra.setdefault("distinct_policy", "identity_if_spatial_join")
    return CompiledSemanticQuery(
        sql=sql,
        tables=tables,
        route=f"semantic_plan_{plan.query_kind}",
        semantic_plan=plan.model_dump(),
        uses_boundary=uses_boundary,
        extra=extra,
        params=params,
    )


def _compile_with_stages(plan: SemanticQueryPlan) -> CompiledSemanticQuery:
    """Compile multi-stage plans as WITH CTEs (e.g. top-N then outer aggregate).

    Contract:
    - Common place/filters/spatial live on stage 0 (population).
    - Outer plan.ratios FILTER only ratio predicates (population already in CTE).
    - Empty stages never reach here (caller checks).
    """
    stages = list(plan.stages or [])
    if not stages:
        return _compile_flat_semantic_plan(plan)

    stage0 = stages[0]
    inner = plan.model_copy(deep=True)
    inner.stages = []
    inner.bins = list(plan.bins or [])
    if stage0.filters:
        inner.filters = list(stage0.filters) + list(inner.filters or [])
    if stage0.predicate is not None:
        inner.predicate = stage0.predicate
    if stage0.select:
        inner.select = list(stage0.select)
    if stage0.order_by:
        inner.order_by = list(stage0.order_by)
    if stage0.limit is not None:
        inner.limit = stage0.limit
    if stage0.group_by:
        inner.group_by = list(stage0.group_by)

    # Stage 0 is the row population (rank/filter); strip outer aggs/ratios.
    inner.aggregations = []
    inner.ratios = []
    if stage0.kind == "aggregate" and stage0.aggregations:
        inner.aggregations = list(stage0.aggregations)
        inner.query_kind = "aggregate"
        if stage0.group_by:
            inner.group_by = list(stage0.group_by)
    elif stage0.kind == "rank":
        inner.query_kind = "rank"
        if not inner.select and stage0.order_by:
            metric = stage0.order_by[0].field
            inner.select = ["name", "legal_dong", "lot_address", metric]
    else:
        inner.query_kind = "list" if inner.select else "rank"

    compiled_inner = _compile_flat_semantic_plan(inner)
    if len(stages) == 1 and not plan.ratios:
        return compiled_inner

    inner_sql = compiled_inner.sql.rstrip().rstrip(";")
    cte = "stage_0"
    outer_alias = "s0"
    pieces: list[str] = []
    params: list[object] = list(compiled_inner.params or [])

    # Remaining stages after 0: currently one outer aggregate (or ratios).
    outer_aggs: list[AggregationSpec] = []
    if len(stages) >= 2:
        outer_aggs = list(stages[1].aggregations or [])
    if not outer_aggs and plan.aggregations and len(stages) >= 2:
        outer_aggs = list(plan.aggregations)

    for agg in outer_aggs:
        out_alias = agg.alias or (
            agg.function if not agg.field else f"{agg.function}_{agg.field}"
        )
        if not _IDENT_RE.fullmatch(out_alias):
            raise SemanticCompileError(f"invalid aggregation alias: {out_alias}")
        if agg.function == "count" and not agg.field:
            pieces.append(f'COUNT(*) AS {_ident(out_alias)}')
            continue
        if not agg.field:
            raise SemanticCompileError("stage aggregate requires field")
        col = f"{outer_alias}.{_ident(agg.field)}"
        fn = agg.function.upper()
        if agg.function == "percentile":
            pct = float(agg.percentile if agg.percentile is not None else 0.5)
            pieces.append(
                f"percentile_cont({sql_number(pct)}) WITHIN GROUP "
                f"(ORDER BY {col}) AS {_ident(out_alias)}"
            )
        elif agg.expression is not None:
            raise SemanticCompileError("stage expression aggregates not supported")
        else:
            pieces.append(f"{fn}({col}::float8) AS {_ident(out_alias)}")

    # Ratios over stage-0 population: common WHERE already applied in CTE.
    # CTE columns are semantic aliases from stage0 SELECT — use identity col_map.
    if plan.ratios:
        from txt2sql.semantic_plan.predicate_utils import predicate_fields

        identity_map: dict[str, str] = {}
        for key in inner.select or []:
            identity_map[key] = key
        for ratio in plan.ratios:
            for pred in (
                ratio.numerator_predicate,
                ratio.denominator_predicate,
            ):
                for node in predicate_fields(pred):
                    identity_map[node] = node
        # Ensure stage0 projected ratio fields (recompile if missing).
        needed = [k for k in identity_map if k not in (inner.select or [])]
        if needed:
            inner.select = list(inner.select or []) + needed
            compiled_inner = _compile_flat_semantic_plan(inner)
            inner_sql = compiled_inner.sql.rstrip().rstrip(";")
            params = list(compiled_inner.params or [])
        ratio_plan = plan.model_copy(deep=True)
        ratio_plan.stages = []
        ratio_plan.filters = []
        ratio_plan.predicate = None
        for ratio in plan.ratios:
            pieces.append(
                _ratio_sql(outer_alias, ratio_plan, ratio, col_map=identity_map)
            )

    if not pieces:
        raise SemanticCompileError("stages compile produced empty outer SELECT")

    sql = (
        f"WITH {_ident(cte)} AS (\n{inner_sql}\n)\n"
        f"SELECT {', '.join(pieces)}\n"
        f"FROM {_ident(cte)} {outer_alias};"
    )
    _assert_safe_sql(sql)
    tables = list(compiled_inner.tables)
    _assert_table_columns(sql, plan.entity, tables)
    extra = dict(compiled_inner.extra or {})
    prior_trace = dict(extra.get("compile_trace") or {})
    prior_aggs = list(prior_trace.get("aggregations") or [])
    prior_aggs.extend(agg.function for agg in outer_aggs)
    extra["compile_trace"] = {
        **prior_trace,
        "aggregations": prior_aggs,
        "stages": [s.kind for s in stages],
        "cte": True,
    }
    return CompiledSemanticQuery(
        sql=sql,
        tables=tables,
        route=f"semantic_plan_{plan.query_kind}",
        semantic_plan=plan.model_dump(),
        uses_boundary=compiled_inner.uses_boundary,
        extra=extra,
        params=params,
    )


def _field_col(
    alias: str,
    entity: str,
    key: str,
    col_map: dict[str, str] | None = None,
):
    field = get_field(entity, key)
    column = (col_map or {}).get(key) or field.column
    # Identity col_map (CTE semantic aliases) must not require physical allowlist.
    physical = not (col_map is not None and col_map.get(key) == key)
    return field, _col(alias, column, physical=physical)


def _select_sql(
    alias: str,
    plan: SemanticQueryPlan,
    col_map: dict[str, str] | None = None,
) -> str:
    if plan.query_kind == "count" and not plan.ratios:
        spatial_join = bool(plan.spatial_relations)
        industrial_join = any(
            getattr(rel.target, "entity", None) == "industrial_complex"
            for rel in plan.spatial_relations
        )
        if industrial_join or (plan.entity == "building" and spatial_join):
            pk = {
                "building": "A1",
                "basic_zone": "BAS_ID",
                "industrial_complex": "A0",
            }.get(plan.entity, "A0")
            return f'SELECT COUNT(DISTINCT {alias}."{pk}") AS "count"'
        return 'SELECT COUNT(*) AS "count"'
    if plan.query_kind in {"aggregate", "distribution"} or plan.ratios:
        pieces: list[str] = []
        if "group_by_industrial_name" in (plan.assumptions or []):
            pieces.append(
                'COALESCE(NULLIF(TRIM(ind."A8"), \'\'), NULLIF(TRIM(ind."A9"), \'\')) '
                'AS "park"'
            )
        for key in plan.group_by:
            if "group_by_industrial_name" in (plan.assumptions or []):
                continue
            _field, col = _field_col(alias, plan.entity, key, col_map)
            if key == "approval_date" and "approval_decade" in (plan.assumptions or []):
                pieces.append(f"{_approval_decade_expr(col)} AS {_ident('decade')}")
            elif key == "approval_date" and "approval_year_group" in (
                plan.assumptions or []
            ):
                pieces.append(
                    f"({_approval_year_expr(col)})::int AS {_ident('y')}"
                )
            elif _bin_expr_for(plan, key, col) is not None:
                pieces.append(
                    f"{_bin_expr_for(plan, key, col)} AS {_ident(key)}"
                )
            elif key == "sigungu_name" and plan.entity == "building":
                pieces.append(f"{_sigungu_name_sql(alias)} AS {_ident(key)}")
            else:
                pieces.append(f"{col} AS {_ident(key)}")
        for ratio in plan.ratios:
            pieces.append(_ratio_sql(alias, plan, ratio, col_map=col_map))
        aggs = list(plan.aggregations)
        if not aggs and not plan.ratios and plan.query_kind == "distribution":
            pieces.append('COUNT(*) AS "n"')
        for agg in aggs:
            pieces.append(_agg_sql(alias, plan.entity, agg, col_map=col_map, plan=plan))
        # 스칼라 avg/sum/min/max 는 gold가 n을 함께 요구하는 경우가 많음
        has_count = any(
            (a.function or "").lower() == "count" or (a.alias or "") in {"n", "count"}
            for a in aggs
        )
        if (
            aggs
            and not has_count
            and not plan.group_by
            and not plan.ratios
            and plan.query_kind == "aggregate"
        ):
            # 상관계수 단독 집계도 FILTER 모집단 n을 붙인다
            if any((a.function or "").lower() == "corr" for a in aggs):
                pieces.append(
                    _agg_sql(
                        alias,
                        plan.entity,
                        AggregationSpec(function="count", field=None, alias="n"),
                        col_map=col_map,
                        plan=plan,
                    )
                )
            else:
                pieces.append('COUNT(*) AS "n"')
        if not pieces:
            raise SemanticCompileError("aggregate/distribution needs aggregations")
        return "SELECT " + ",\n       ".join(pieces)

    keys = list(plan.select)
    if not keys:
        if plan.entity == "admin_area":
            keys = ["name"]
        elif plan.entity == "basic_zone":
            keys = ["id", "gu_name"]
        elif plan.entity == "industrial_complex":
            keys = ["name"]
        else:
            keys = ["name", "legal_dong", "lot_address"]
    pieces = []
    for key in keys:
        field, expr = _field_col(alias, plan.entity, key, col_map)
        if field.data_type == "number":
            expr = f"{expr}::float8"
        pieces.append(f"{expr} AS {_ident(key)}")
    return "SELECT " + ",\n       ".join(pieces)


def _compile_expression(
    alias: str,
    entity: str,
    expr: ExpressionSpec,
    col_map: dict[str, str] | None = None,
) -> str:
    if expr.kind == "field":
        if not expr.field:
            raise SemanticCompileError("expression field missing")
        field, col = _field_col(alias, entity, expr.field, col_map)
        if field.data_type == "number":
            return f"{col}::float8"
        if expr.field in {"approval_date", "permit_date"}:
            return f"{col}::date"
        return col
    if expr.left is None or expr.right is None:
        raise SemanticCompileError("expression operands required")
    left = _compile_expression(alias, entity, expr.left, col_map)
    right = _compile_expression(alias, entity, expr.right, col_map)
    if expr.kind == "divide":
        return f"({left} / NULLIF({right}, 0))"
    if expr.kind == "multiply":
        return f"({left} * {right})"
    if expr.kind == "add":
        return f"({left} + {right})"
    if expr.kind == "subtract":
        # date - date → integer days (허가→승인 소요일)
        if "::date" in left and "::date" in right:
            return f"(({left}) - ({right}))"
        return f"({left} - {right})"
    raise SemanticCompileError(f"unsupported expression kind: {expr.kind}")


def _permit_lag_ratio_filter(
    alias: str,
    plan: SemanticQueryPlan,
    col_map: dict[str, str] | None = None,
) -> str | None:
    """비율 질의의 FILTER(WHERE …)용 허가↔승인 시차 조건."""
    a33 = _semantic_date_col(alias, "permit_date", col_map)
    a34 = _semantic_date_col(alias, "approval_date", col_map)
    day_lte = next(
        (a for a in (plan.assumptions or []) if a.startswith("permit_day_gap_lte:")),
        None,
    )
    if day_lte:
        days = int(day_lte.split(":", 1)[1])
        return f"({a34}::date - {a33}::date) <= {days}"
    day_gte = next(
        (a for a in (plan.assumptions or []) if a.startswith("permit_day_gap_gte:")),
        None,
    )
    if day_gte:
        days = int(day_gte.split(":", 1)[1])
        return f"({a34}::date - {a33}::date) >= {days}"
    if "permit_approval_year_neq" in (plan.assumptions or []):
        y33 = f"LEFT(regexp_replace({a33}::text, '[^0-9]', '', 'g'), 4)::int"
        y34 = f"LEFT(regexp_replace({a34}::text, '[^0-9]', '', 'g'), 4)::int"
        return f"{y33} <> {y34}"
    return None


def _ratio_sql(
    alias: str,
    plan: SemanticQueryPlan,
    ratio,
    col_map: dict[str, str] | None = None,
) -> str:
    from txt2sql.semantic_plan.predicate_utils import walk_predicate

    lag = _permit_lag_ratio_filter(alias, plan, col_map=col_map)
    if lag is not None:
        num = lag
    else:
        num, _, _ = _predicate_sql(
            alias, plan.entity, ratio.numerator_predicate, col_map=col_map
        )
    num_fields: set[str] = set()
    if ratio.numerator_predicate is not None:
        for node in walk_predicate(ratio.numerator_predicate):
            if node.left and node.left.field:
                num_fields.add(node.left.field)
    if ratio.denominator_predicate is not None:
        den, _, _ = _predicate_sql(
            alias, plan.entity, ratio.denominator_predicate, col_map=col_map
        )
        den_count = f"COUNT(*) FILTER (WHERE {den})"
    elif num_fields & {"approval_date", "building_age_years", "permit_date"}:
        # 사용승인 기반 비율: 분모는 유효 일자 건수 (골드 grain)
        date_field = (
            "permit_date" if "permit_date" in num_fields else "approval_date"
        )
        date_col = _semantic_date_col(alias, date_field, col_map)
        den_count = (
            f"COUNT(*) FILTER (WHERE {date_col}::text "
            f"~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$')"
        )
    else:
        den_count = "COUNT(*)"
    out = ratio.alias or "ratio_pct"
    if not _IDENT_RE.fullmatch(out):
        raise SemanticCompileError(f"invalid ratio alias: {out}")
    return (
        f"{sql_number(float(ratio.multiplier))} * COUNT(*) FILTER "
        f"(WHERE {num})::float8 / NULLIF({den_count}, 0) AS {_ident(out)}"
    )


def _agg_sql(
    alias: str,
    entity: str,
    agg,
    col_map: dict[str, str] | None = None,
    *,
    plan: SemanticQueryPlan | None = None,
) -> str:
    function = agg.function
    field_key = agg.field
    out = agg.alias or (f"{function}_{field_key}" if field_key else function)
    if not _IDENT_RE.fullmatch(out):
        raise SemanticCompileError(f"invalid aggregation alias: {out}")
    filter_parts: list[str] = []
    if getattr(agg, "filter_field", None) and function != "corr":
        extra, _ = _filter_sql(
            alias,
            entity,
            FilterSpec(
                field=agg.filter_field,
                operator=agg.filter_operator or "eq",
                value=agg.filter_value,
            ),
            col_map=col_map,
        )
        filter_parts.append(extra)
    if agg.predicate is not None:
        clause, _, _ = _predicate_sql(alias, entity, agg.predicate, col_map=col_map)
        filter_parts.append(clause)
    filter_sql = f" FILTER (WHERE {' AND '.join(filter_parts)})" if filter_parts else ""
    if agg.expression is not None:
        expr = _compile_expression(alias, entity, agg.expression, col_map)
        # 허가일−승인일 일수: 양쪽 ISO date 필요
        if (
            agg.expression.kind == "subtract"
            and agg.expression.left
            and agg.expression.right
            and {agg.expression.left.field, agg.expression.right.field}
            <= {"approval_date", "permit_date"}
        ):
            for date_field in ("permit_date", "approval_date"):
                _f, dcol = _field_col(alias, entity, date_field, col_map)
                filter_parts.append(
                    f"({dcol}::text ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$')"
                )
            filter_sql = (
                f" FILTER (WHERE {' AND '.join(filter_parts)})" if filter_parts else ""
            )
    elif function == "count" and not field_key:
        industrial = bool(
            plan
            and any(
                getattr(rel.target, "entity", None) == "industrial_complex"
                for rel in (plan.spatial_relations or [])
            )
        )
        if industrial and entity == "building":
            return f'COUNT(DISTINCT {alias}."A1"){filter_sql} AS {_ident(out)}'
        # 상관계수와 함께 쓰는 n은 CORR FILTER와 동일 모집단
        if plan is not None:
            corr_peer = next(
                (
                    peer
                    for peer in (plan.aggregations or [])
                    if (peer.function or "").lower() == "corr"
                    and peer.field
                    and peer.filter_field
                ),
                None,
            )
            if corr_peer is not None:
                _xf, x_col = _field_col(alias, entity, str(corr_peer.field), col_map)
                _yf, y_col = _field_col(
                    alias, entity, str(corr_peer.filter_field), col_map
                )
                if corr_peer.field == "building_age_years":
                    ref = _ref_sql()
                    x_ok = (
                        f"({x_col}::text ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$')"
                    )
                else:
                    x_ok = f"{x_col}::float8 IS NOT NULL"
                y_ok = f"{y_col}::float8 IS NOT NULL"
                return (
                    f"COUNT(*) FILTER (WHERE {x_ok} AND {y_ok}) "
                    f"AS {_ident(out)}"
                )
        return f"COUNT(*){filter_sql} AS {_ident(out)}"
    elif not field_key:
        raise SemanticCompileError(f"{function} requires a field")
    else:
        field, col = _field_col(alias, entity, field_key, col_map)
        if not field.aggregatable:
            raise SemanticCompileError(f"field is not aggregatable: {field_key}")
        if field_key == "building_age_years":
            # Gold grain: fractional years via day delta / 365.25 (ISO dates).
            ref = _ref_sql()
            expr = f"(({ref}::date - {col}::date)::float8 / 365.25)"
            iso = f"({col}::text ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$')"
            filter_parts = [iso, *filter_parts]
            filter_sql = (
                f" FILTER (WHERE {' AND '.join(filter_parts)})" if filter_parts else ""
            )
        elif entity == "industrial_complex" and field_key == "area_m2":
            expr = f"ST_Area({alias}.geometry::geography)"
        else:
            expr = f"{col}::float8"
    if function == "corr":
        # field = X, filter_field = Y (no FILTER clause for corr partner).
        if not field_key or not getattr(agg, "filter_field", None):
            raise SemanticCompileError("corr requires field and filter_field")
        x_field, x_col = _field_col(alias, entity, field_key, col_map)
        y_field, y_col = _field_col(alias, entity, str(agg.filter_field), col_map)
        if field_key == "building_age_years":
            ref = _ref_sql()
            x_expr = f"(({ref}::date - {x_col}::date)::float8 / 365.25)"
            x_ok = f"({x_col}::text ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$')"
        else:
            x_expr = f"{x_col}::float8"
            x_ok = f"{x_expr} IS NOT NULL"
        y_expr = f"{y_col}::float8"
        y_ok = f"{y_expr} IS NOT NULL"
        return (
            f"CORR({x_expr}, {y_expr}) "
            f"FILTER (WHERE {x_ok} AND {y_ok}) AS {_ident(out)}"
        )
    if function == "percentile":
        if agg.percentile is None:
            raise SemanticCompileError("percentile requires a value")
        return (
            f"PERCENTILE_CONT({sql_number(float(agg.percentile))}) "
            f"WITHIN GROUP (ORDER BY {expr}){filter_sql} AS {_ident(out)}"
        )
    if function == "median":
        return (
            f"PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {expr})"
            f"{filter_sql} AS {_ident(out)}"
        )
    if function == "stddev":
        return f"STDDEV_POP({expr}){filter_sql} AS {_ident(out)}"
    if function == "variance":
        return f"VAR_POP({expr}){filter_sql} AS {_ident(out)}"
    fn = {"avg": "AVG", "sum": "SUM", "min": "MIN", "max": "MAX", "count": "COUNT"}
    sql_fn = fn.get(function)
    if sql_fn is None:
        raise SemanticCompileError(f"unsupported aggregation: {function}")
    return f"{sql_fn}({expr}){filter_sql} AS {_ident(out)}"


def _group_sql(
    alias: str, plan: SemanticQueryPlan, col_map: dict[str, str] | None = None
) -> str:
    if "group_by_industrial_name" in (plan.assumptions or []):
        return 'GROUP BY COALESCE(NULLIF(TRIM(ind."A8"), \'\'), NULLIF(TRIM(ind."A9"), \'\'))'
    if not plan.group_by:
        return ""
    cols = []
    for key in plan.group_by:
        _field, col = _field_col(alias, plan.entity, key, col_map)
        if key == "approval_date" and "approval_decade" in (plan.assumptions or []):
            cols.append(_approval_decade_expr(col))
        elif key == "approval_date" and "approval_year_group" in (
            plan.assumptions or []
        ):
            cols.append(f"({_approval_year_expr(col)})::int")
        elif _bin_expr_for(plan, key, col) is not None:
            cols.append(_bin_expr_for(plan, key, col))
        elif key == "sigungu_name" and plan.entity == "building":
            cols.append(_sigungu_name_sql(alias))
        else:
            cols.append(col)
    return "GROUP BY " + ", ".join(cols)


def _order_sql(
    alias: str, plan: SemanticQueryPlan, col_map: dict[str, str] | None = None
) -> str:
    day_gap_order = next(
        (
            a
            for a in (plan.assumptions or [])
            if a.startswith("order_by_day_gap:")
        ),
        None,
    )
    if day_gap_order and plan.entity == "building":
        direction = "DESC" if day_gap_order.endswith(":desc") else "ASC"
        a33 = _semantic_date_col(alias, "permit_date", col_map)
        a34 = _semantic_date_col(alias, "approval_date", col_map)
        gap = f"({a34}::date - {a33}::date)"
        return f"ORDER BY {gap} {direction} NULLS LAST"
    if not plan.order_by:
        return ""
    bits = []
    agg_aliases = {item.alias for item in plan.aggregations if item.alias}
    agg_aliases.update(item.alias for item in plan.ratios if item.alias)
    # 비그룹 집계·count 에서 원 컬럼 ORDER BY 는 GroupingError
    scalar_agg = (
        plan.query_kind in {"aggregate", "distribution", "count"}
        and not plan.group_by
        and (plan.aggregations or plan.ratios or plan.query_kind == "count")
    )
    for item in plan.order_by:
        if item.field in agg_aliases or item.field in {"count", "n", "park"}:
            expr = _ident(item.field)
        elif scalar_agg:
            # count/비그룹 집계: 원컬럼 ORDER BY 는 생략 (GroupingError 방지)
            continue
        else:
            field, expr = _field_col(alias, plan.entity, item.field, col_map)
            if item.field == "approval_date" and "approval_decade" in (plan.assumptions or []):
                expr = _approval_decade_expr(
                    _field_col(alias, plan.entity, "approval_date", col_map)[1]
                )
            elif _bin_expr_for(plan, item.field, expr) is not None:
                expr = _bin_expr_for(plan, item.field, expr)
            elif field.data_type == "number":
                expr = f"{expr}::float8"
        direction = "DESC" if item.direction == "desc" else "ASC"
        nulls = "NULLS FIRST" if item.nulls == "first" else "NULLS LAST"
        bits.append(f"{expr} {direction} {nulls}")
    if not bits:
        return ""
    return "ORDER BY " + ", ".join(bits)


def _operand_sql(
    alias: str,
    entity: str,
    operand,
    col_map: dict[str, str] | None = None,
) -> tuple[str, bool, list[object]]:
    from txt2sql.semantic_plan.models import OperandSpec

    if operand is None:
        raise SemanticCompileError("missing operand")
    if operand.kind == "field":
        if not operand.field:
            raise SemanticCompileError("field operand missing name")
        field, col = _field_col(alias, entity, operand.field, col_map)
        expr = f"{col}::float8" if field.data_type == "number" else col
        if operand.scale is not None:
            scale = float(operand.scale)
            if scale <= 0 or scale > 1000:
                raise SemanticCompileError(f"invalid operand scale: {scale}")
            expr = f"({expr} * {scale:g})"
        return expr, operand.field == "height_m", []
    field_type = "number" if isinstance(operand.value, (int, float)) else "text"
    lit = _literal(operand.value, field_type)
    return lit, False, [operand.value]


def _predicate_sql(
    alias: str,
    entity: str,
    pred: PredicateSpec,
    col_map: dict[str, str] | None = None,
) -> tuple[str, bool, list[object]]:
    if pred.op == "and":
        parts = [
            _predicate_sql(alias, entity, child, col_map=col_map)
            for child in (pred.args or [])
        ]
        sql = "(" + " AND ".join(item[0] for item in parts) + ")"
        return sql, any(item[1] for item in parts), [p for item in parts for p in item[2]]
    if pred.op == "or":
        parts = [
            _predicate_sql(alias, entity, child, col_map=col_map)
            for child in (pred.args or [])
        ]
        sql = "(" + " OR ".join(item[0] for item in parts) + ")"
        return sql, any(item[1] for item in parts), [p for item in parts for p in item[2]]
    if pred.op == "not":
        if not pred.args:
            raise SemanticCompileError("not predicate missing args")
        inner, height_used, params = _predicate_sql(
            alias, entity, pred.args[0], col_map=col_map
        )
        return f"(NOT {inner})", height_used, params
    if pred.op != "cmp" or pred.operator is None:
        raise SemanticCompileError(f"unsupported predicate op: {pred.op}")
    if pred.left and pred.left.field in {"special_land", "structure"}:
        spec = FilterSpec(
            field=pred.left.field,
            operator=pred.operator,
            value=pred.right.value if pred.right else None,
        )
        sql, height_used = _filter_sql(alias, entity, spec, col_map=col_map)
        return sql, height_used, []
    if pred.left and pred.left.field in {"approval_date", "permit_date"}:
        raw = pred.right.value if pred.right else None
        value2 = None
        value = raw
        if pred.operator == "between":
            if isinstance(raw, (list, tuple)) and len(raw) >= 2:
                value, value2 = raw[0], raw[1]
            else:
                raise SemanticCompileError("between requires [low, high] literal")
        field_name = pred.left.field
        spec = FilterSpec(
            field=field_name,
            operator=pred.operator,
            value=value,
            value2=value2,
        )
        _field, col = _field_col(alias, entity, field_name, col_map)
        return _approval_date_sql(col, spec), False, []
    if pred.left and pred.left.field == "building_age_years":
        raw = pred.right.value if pred.right else None
        value2 = None
        value = raw
        if pred.operator == "between":
            if isinstance(raw, (list, tuple)) and len(raw) >= 2:
                value, value2 = raw[0], raw[1]
            else:
                raise SemanticCompileError("between requires [low, high] literal")
        spec = FilterSpec(
            field="building_age_years",
            operator=pred.operator,
            value=value,
            value2=value2,
        )
        _field, col = _field_col(alias, entity, "building_age_years", col_map)
        return _building_age_sql(col, spec), False, []
    if pred.operator in {"is_null", "is_not_null"}:
        left, height_used, params = _operand_sql(
            alias, entity, pred.left, col_map=col_map
        )
        sql = f"{left} IS NULL" if pred.operator == "is_null" else f"{left} IS NOT NULL"
        return sql, height_used, params
    if pred.operator == "between":
        left, height_used, params = _operand_sql(
            alias, entity, pred.left, col_map=col_map
        )
        raw = pred.right.value if pred.right else None
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            low, high = raw[0], raw[1]
        else:
            raise SemanticCompileError("between requires [low, high] literal")
        params.extend([low, high])
        return (
            f"{left} BETWEEN {_literal(low, 'number')} AND {_literal(high, 'number')}",
            height_used,
            params,
        )
    if pred.operator in {"in", "not_in"}:
        left, height_used, params = _operand_sql(
            alias, entity, pred.left, col_map=col_map
        )
        values = pred.right.value if pred.right else []
        if not isinstance(values, (list, tuple)):
            values = [values]
        lits = ", ".join(_literal(v, "text") for v in values)
        params.extend(list(values))
        kw = "IN" if pred.operator == "in" else "NOT IN"
        return f"{left} {kw} ({lits})", height_used, params
    left_sql, h1, p1 = _operand_sql(alias, entity, pred.left, col_map=col_map)
    right_sql, h2, p2 = _operand_sql(alias, entity, pred.right, col_map=col_map)
    op = _OPS.get(pred.operator)
    if op is None:
        if pred.operator == "neq":
            op = "IS DISTINCT FROM"
        elif pred.operator == "contains":
            raw = str(pred.right.value if pred.right else "")
            field_name = pred.left.field if pred.left else None
            if field_name == "legal_dong" and raw.endswith(("동", "가", "리", "읍", "면")):
                return (
                    f"({left_sql} LIKE {_literal(f'% {raw}', 'text')} OR "
                    f"{left_sql} = {_literal(raw, 'text')})",
                    h1 or h2,
                    p1 + p2,
                )
            return f"{left_sql} ILIKE {_literal('%' + raw + '%', 'text')}", h1 or h2, p1 + p2
        else:
            raise SemanticCompileError(f"unknown operator: {pred.operator}")
    return f"{left_sql} {op} {right_sql}", h1 or h2, p1 + p2


def _filter_sql(
    alias: str,
    entity: str,
    spec: FilterSpec,
    col_map: dict[str, str] | None = None,
) -> tuple[str, bool]:
    try:
        field, col = _field_col(alias, entity, spec.field, col_map)
    except UnknownSemanticFieldError as exc:
        raise SemanticCompileError(str(exc)) from exc
    height_used = spec.field == "height_m"
    if spec.field in {"approval_date", "permit_date"}:
        return _approval_date_sql(col, spec), height_used
    if spec.field == "building_age_years":
        return _building_age_sql(col, spec), height_used
    if spec.operator == "is_null":
        return f"{col} IS NULL", height_used
    if spec.operator == "is_not_null":
        return f"{col} IS NOT NULL", height_used
    if spec.operator == "not_in":
        values = spec.value if isinstance(spec.value, (list, tuple)) else [spec.value]
        lits = ", ".join(_literal(v, field.data_type) for v in values)
        return f"{col} NOT IN ({lits})", height_used
    if spec.operator == "in":
        values = spec.value if isinstance(spec.value, (list, tuple)) else [spec.value]
        lits = ", ".join(_literal(v, field.data_type) for v in values)
        return f"{col} IN ({lits})", height_used
    if spec.field == "special_land" and not col_map:
        a6 = _col(alias, "A6")
        a7 = _col(alias, "A7")
        raw = str(spec.value or "").strip()
        if raw in {"산", "산지"}:
            inner = f"({a6}::text = '2' OR TRIM(COALESCE({a7}::text, '')) = '산')"
        elif raw in {"일반", "일반지번"}:
            inner = (
                f"({a6}::text = '1' OR TRIM(COALESCE({a7}::text, '')) IN "
                f"('일반', '일반지번'))"
            )
        elif raw in {"가지번", "가지"}:
            inner = (
                f"({a6}::text IN ('3', '4') OR COALESCE({a7}::text, '') ILIKE '%가지%')"
            )
        elif raw in {"블럭지번", "블록지번", "블럭", "블록"}:
            inner = (
                f"({a6}::text IN ('5', '6', '7', '8') OR "
                f"COALESCE({a7}::text, '') ILIKE '%블럭%' OR "
                f"COALESCE({a7}::text, '') ILIKE '%블록%')"
            )
        else:
            inner = f"TRIM(COALESCE({a7}::text, '')) = {_literal(raw, 'text')}"
        if spec.operator == "neq":
            return f"(NOT {inner})", height_used
        return inner, height_used
    if spec.operator == "contains":
        pattern = spec.value
        if spec.field == "structure" and isinstance(pattern, str):
            mapped = STRUCTURE_ALIASES.get(pattern)
            if mapped:
                return f"{col} ILIKE {_literal(mapped, 'text')}", height_used
        if spec.field == "legal_dong" and isinstance(pattern, str):
            # Avoid '%서동%' matching 구서동 — match token boundary.
            name = pattern.strip()
            if name.endswith(("동", "가", "리", "읍", "면")):
                return (
                    f"({col} LIKE {_literal(f'% {name}', 'text')} OR "
                    f"{col} = {_literal(name, 'text')})",
                    height_used,
                )
        return f"{col} ILIKE {_literal(f'%{pattern}%', 'text')}", height_used
    if spec.field == "structure" and spec.operator == "neq" and isinstance(spec.value, str):
        mapped = STRUCTURE_ALIASES.get(spec.value) or f"%{spec.value}%"
        return f"NOT ({col} ILIKE {_literal(mapped, 'text')})", height_used
    if spec.operator == "between":
        left = _literal(spec.value, field.data_type)
        right = _literal(spec.value2, field.data_type)
        expr = f"{col}::float8" if field.data_type == "number" else col
        return f"{expr} BETWEEN {left} AND {right}", height_used
    if spec.value_field:
        other, right = _field_col(alias, entity, spec.value_field, col_map)
        left = f"{col}::float8" if field.data_type == "number" else col
        if other.data_type == "number":
            right = f"{right}::float8"
        if spec.value_scale is not None:
            scale = float(spec.value_scale)
            if scale <= 0 or scale > 1000:
                raise SemanticCompileError(f"invalid value_scale: {scale}")
            right = f"({right} * {scale:g})"
        op = _OPS.get(spec.operator)
        if op is None:
            raise SemanticCompileError(f"unknown operator: {spec.operator}")
        return f"{left} {op} {right}", height_used
    if spec.field == "structure" and spec.operator == "eq" and isinstance(spec.value, str):
        raw = spec.value.strip()
        if raw.endswith("구조"):
            return f"{col} = {_literal(raw, 'text')}", height_used
        mapped = STRUCTURE_ALIASES.get(raw)
        if mapped:
            return f"{col} ILIKE {_literal(mapped, 'text')}", height_used
    op = _OPS.get(spec.operator)
    if op is None:
        raise SemanticCompileError(f"unknown operator: {spec.operator}")
    expr = f"{col}::float8" if field.data_type == "number" else col
    return f"{expr} {op} {_literal(spec.value, field.data_type)}", height_used


def _apply_spatial_relations(
    alias: str,
    plan: SemanticQueryPlan,
    tables: list[str],
    joins: list[str],
    where: list[str],
    spatial_order: list[str],
) -> tuple[bool, set[str]]:
    """spatial_relations → JOIN/WHERE. 물리 함수명은 compiler 정책만 고른다."""
    from txt2sql.semantic_plan.spatial_policy import resolve_spatial_policy

    uses_boundary = False
    used_places: set[str] = set()
    admin_alias_used = False
    for index, rel in enumerate(plan.spatial_relations):
        policy = resolve_spatial_policy(rel.relation)
        target = rel.target
        place_name = (
            target.place.name.strip() if target.place and target.place.name else None
        )
        target_entity = getattr(target, "entity", None) or "admin_area"
        if policy.kind == "predicate":
            target_entity = getattr(target, "entity", None) or "admin_area"
            if not place_name and target_entity != "industrial_complex":
                raise SemanticCompileError(f"{rel.relation} requires a place target")
            if place_name:
                used_places.add(place_name)
            if target_entity == "industrial_complex":
                d_alias = "ind" if index == 0 else f"ind{index}"
            else:
                d_alias = "a" if not admin_alias_used else f"a{index}"
                admin_alias_used = True
            table, name_clause = _spatial_target_sql(
                target_entity, d_alias, place_name or "", plan
            )
            if table not in tables:
                tables.append(table)
            if target_entity == "industrial_complex":
                # Aggregate/count: JOIN so multi-park overlaps weight AVG; COUNT uses DISTINCT.
                # List/rank: EXISTS to avoid duplicate primary rows.
                use_join = plan.query_kind in {
                    "aggregate",
                    "distribution",
                    "count",
                } or bool(plan.aggregations or plan.ratios or plan.group_by)
                sido_prefix = None
                if plan.scope and plan.scope.place:
                    from txt2sql.gazetteer import sido_pnu_prefix

                    sido_name = canonical_sido(plan.scope.place.name) or (
                        plan.scope.place.name
                        if plan.scope.place.kind == "sido"
                        else plan.scope.place.sido
                    )
                    if sido_name:
                        sido_prefix = sido_pnu_prefix(sido_name)
                extra_bits: list[str] = []
                if name_clause and name_clause != "TRUE":
                    extra_bits.append(name_clause)
                elif sido_prefix:
                    extra_bits.append(
                        f'{d_alias}."A4" LIKE '
                        f"{_literal(str(sido_prefix) + '%', 'text')}"
                    )
                if use_join:
                    joins.append(
                        f"JOIN {_ident(table, physical=True)} {d_alias} "
                        f"ON {policy.postgis_fn}({alias}.geometry, {d_alias}.geometry)"
                    )
                    where.extend(extra_bits)
                else:
                    exists = (
                        f"EXISTS (SELECT 1 FROM {_ident(table, physical=True)} {d_alias} "
                        f"WHERE {policy.postgis_fn}({alias}.geometry, {d_alias}.geometry)"
                    )
                    for bit in extra_bits:
                        exists += f" AND {bit}"
                    exists += ")"
                    where.append(exists)
                uses_boundary = True
                continue
            else:
                joins.append(
                    f"JOIN {_ident(table, physical=True)} {d_alias} "
                    f"ON {policy.postgis_fn}({alias}.geometry, {d_alias}.geometry)"
                )
                if name_clause and name_clause != "TRUE":
                    where.append(name_clause)
            uses_boundary = True
            continue
        if policy.kind in {"distance", "distance_outside"}:
            if rel.distance_m is None or rel.distance_m <= 0:
                raise SemanticCompileError("distance_m must be > 0")
            meters = sql_number(float(rel.distance_m))
            expand = sql_number(max(0.0015, float(rel.distance_m) / 111000.0 * 1.5))
            z_alias = "z" if index == 0 else f"z{index}"
            if place_name:
                used_places.add(place_name)
                if ADMIN_TABLE not in tables:
                    tables.append(ADMIN_TABLE)
                pred = _admin_place_pred("d", place_name, plan)
                joins.append(
                    "CROSS JOIN (\n"
                    "  SELECT ST_Union(d.geometry) AS geom\n"
                    f"  FROM {_ident(ADMIN_TABLE, physical=True)} d\n"
                    f"  WHERE {pred}\n"
                    f") {z_alias}"
                )
                where.append(f"{z_alias}.geom IS NOT NULL")
                if policy.kind == "distance_outside":
                    # 「N m 밖에」= 경계로부터 N m 버퍼 밖 전체 (골드 NOT ST_DWithin)
                    where.append(
                        "NOT ST_DWithin("
                        f"{alias}.geometry::geography, "
                        f"{_distance_origin_sql(z_alias, plan)}::geography, "
                        f"{meters})"
                    )
                else:
                    where.append(f"{alias}.geometry && ST_Expand({z_alias}.geom, {expand})")
                    where.append(
                        "ST_DWithin("
                        f"{alias}.geometry::geography, "
                        f"{_distance_origin_sql(z_alias, plan)}::geography, "
                        f"{meters})"
                    )
                uses_boundary = True
                continue
            if target.longitude is not None and target.latitude is not None:
                lon = sql_number(float(target.longitude))
                lat = sql_number(float(target.latitude))
                point = f"ST_SetSRID(ST_MakePoint({lon}, {lat}), 4326)"
                where.append(
                    "ST_DWithin("
                    f"{alias}.geometry::geography, "
                    f"{point}::geography, "
                    f"{meters})"
                )
                continue
            raise SemanticCompileError("within_distance needs a place or lon/lat")
        if policy.kind == "nearest":
            if not place_name:
                raise SemanticCompileError("nearest requires a place target")
            used_places.add(place_name)
            z_alias = "z" if index == 0 else f"z{index}"
            if ADMIN_TABLE not in tables:
                tables.append(ADMIN_TABLE)
            pred = _admin_place_pred("d", place_name, plan)
            joins.append(
                "CROSS JOIN (\n"
                "  SELECT ST_Union(d.geometry) AS geom\n"
                f"  FROM {_ident(ADMIN_TABLE, physical=True)} d\n"
                f"  WHERE {pred}\n"
                f") {z_alias}"
            )
            where.append(f"{z_alias}.geom IS NOT NULL")
            spatial_order.append(f"ST_Distance({alias}.geometry, {z_alias}.geom)")
            uses_boundary = True
            continue
        if policy.kind == "ratio":
            if not place_name:
                raise SemanticCompileError("overlap_ratio requires a place target")
            used_places.add(place_name)
            d_alias = "a" if not admin_alias_used else f"a{index}"
            admin_alias_used = True
            if ADMIN_TABLE not in tables:
                tables.append(ADMIN_TABLE)
            ratio = rel.min_ratio if rel.min_ratio is not None else 0.5
            joins.append(
                f"JOIN {_ident(ADMIN_TABLE, physical=True)} {d_alias} "
                f"ON ST_Intersects({alias}.geometry, {d_alias}.geometry)"
            )
            where.append(_admin_name_sql(d_alias, place_name))
            extra = _adm_cd_sql(d_alias, place_name, plan)
            if extra:
                where.append(extra)
            where.append(
                "ST_Area(ST_Intersection("
                f"{alias}.geometry, {d_alias}.geometry)) "
                f"/ NULLIF(ST_Area({alias}.geometry), 0) >= {sql_number(float(ratio))}"
            )
            uses_boundary = True
            continue
        raise SemanticCompileError(f"unsupported spatial relation: {rel.relation}")
    return uses_boundary, used_places


def _apply_canonical_joins(
    alias: str,
    plan: SemanticQueryPlan,
    tables: list[str],
    joins: list[str],
    where: list[str],
) -> None:
    from txt2sql.semantic_catalog.registry import get_edge

    sql_pat = re.compile(r"(?i)\b(select|insert|update|delete|st_[a-z]+)\b")
    for spec in plan.joins:
        extra = spec.extra or {}
        if sql_pat.search(str(extra)):
            raise SemanticCompileError("join extra cannot contain SQL")
        try:
            edge = get_edge(spec.edge_id)
        except KeyError as exc:
            raise SemanticCompileError(f"unknown join edge: {spec.edge_id}") from exc
        if edge.edge_id == "building_in_admin":
            if ADMIN_TABLE not in tables:
                tables.append(ADMIN_TABLE)
            if not any(ADMIN_TABLE in item for item in joins):
                joins.append(
                    f"JOIN {_ident(ADMIN_TABLE, physical=True)} adm "
                    f"ON ST_Intersects({alias}.geometry, adm.geometry)"
                )
        elif edge.edge_id == "building_in_basic_zone":
            bas = resolve_basic_zone_table()
            if bas not in tables:
                tables.append(bas)
            if not any(bas in item for item in joins):
                joins.append(
                    f"JOIN {_ident(bas, physical=True)} bas "
                    f"ON ST_Intersects({alias}.geometry, bas.geometry)"
                )
        elif edge.edge_id == "building_in_industrial":
            if INDUSTRIAL_TABLE not in tables:
                tables.append(INDUSTRIAL_TABLE)
            use_join = plan.query_kind in {
                "aggregate",
                "distribution",
                "count",
            } or bool(plan.aggregations or plan.ratios or plan.group_by)
            if use_join:
                if not any(INDUSTRIAL_TABLE in item for item in joins):
                    joins.append(
                        f"JOIN {_ident(INDUSTRIAL_TABLE, physical=True)} ind "
                        f"ON ST_Intersects({alias}.geometry, ind.geometry)"
                    )
            else:
                if not any("EXISTS" in item and "ind" in item for item in where):
                    where.append(
                        f'EXISTS (SELECT 1 FROM {_ident(INDUSTRIAL_TABLE, physical=True)} ind '
                        f"WHERE ST_Intersects({alias}.geometry, ind.geometry))"
                    )
        else:
            raise SemanticCompileError(f"uncompiled join edge: {edge.edge_id}")


def _sigungu_name_sql(alias: str) -> str:
    """A3 접두 → 시군구명. gazetteer sigungu_pnu_prefix 만 사용 (BUSAN_GU_CODES CASE 금지)."""
    mapping = load_gazetteer().sigungu_pnu_prefix
    if not mapping:
        return "NULL"
    # 긴 코드 우선(접두 충돌 방지). 동명 구는 대표 PNU 하나만 있다.
    items = sorted(mapping.items(), key=lambda kv: (-len(kv[1]), kv[1], kv[0]))
    whens = " ".join(
        f"WHEN {alias}.\"A3\" LIKE {_literal(code + '%', 'text')} THEN {_literal(name, 'text')}"
        for name, code in items
        if code.isdigit()
    )
    return f"(CASE {whens} ELSE NULL END)"


def _distance_origin_sql(z_alias: str, plan: SemanticQueryPlan) -> str:
    if "distance_from_centroid" in (plan.assumptions or []):
        return f"ST_Centroid({z_alias}.geom)"
    return f"{z_alias}.geom"


def _adm_cd_sql(alias: str, place_name: str | None, plan=None) -> str | None:
    sido = None
    gu = None
    if plan is not None and plan.scope and plan.scope.place:
        spec = plan.scope.place
        if getattr(spec, "sido", None):
            sido = spec.sido
        if getattr(spec, "sigungu", None):
            gu = spec.sigungu
        if getattr(spec, "code", None) and place_name and spec.kind in {
            "gu",
            "sigungu",
        }:
            return f'{alias}."ADM_CD" LIKE {_literal(spec.code + "%", "text")}'
        if spec.kind == "sido" and not sido:
            sido = spec.name
        elif spec.kind in {"gu", "sigungu"} and not gu:
            gu = spec.name
    prefix = adm_cd_prefix_for_place(place_name, sido=sido, gu=gu)
    if not prefix:
        return None
    return f'{alias}."ADM_CD" LIKE {_literal(prefix + "%", "text")}'


def _admin_place_pred(alias: str, place_name: str, plan=None) -> str:
    pred = _admin_name_sql(alias, place_name)
    extra = _adm_cd_sql(alias, place_name, plan)
    if extra:
        return f"{pred} AND {extra}"
    return pred


def _plan_sido_context(plan=None) -> str | None:
    if plan is None or not plan.scope or not plan.scope.place:
        return None
    spec = plan.scope.place
    if getattr(spec, "sido", None):
        return spec.sido
    if spec.kind == "sido":
        return spec.name
    return None


def _building_place_sql(alias: str, place: str, plan=None) -> str:
    """구=A3 접두, 법정동=A4. place_scope 정책과 동일."""
    return building_place_predicate(
        place,
        alias=alias,
        sido=_plan_sido_context(plan),
    )


# 레거시 별칭
def _a4_place_sql(alias: str, place: str, plan=None) -> str:
    return _building_place_sql(alias, place, plan=plan)


def _admin_name_sql(alias: str, place: str) -> str:
    col = f'{alias}."ADM_NM"'
    if re.fullmatch(r"[가-힣]+\d+동", place):
        return f"{col} = {_literal(place, 'text')}"
    stem = place[:-1] if place.endswith("동") else place
    if not re.fullmatch(r"[가-힣0-9]+", stem):
        return f"{col} = {_literal(place, 'text')}"
    return (
        f"({col} = {_literal(place, 'text')} OR "
        f"{col} ~ {_literal('^' + stem + '[0-9]+동$', 'text')})"
    )


def _entity_place_sql(alias: str, entity: str, place: str, plan=None) -> str:
    if entity == "admin_area":
        return _admin_name_sql(alias, place)
    if entity == "basic_zone":
        return f'{alias}."SIG_KOR_NM" LIKE {_literal("%" + place + "%", "text")}'
    if entity == "industrial_complex":
        if not (place or "").strip():
            return "TRUE"
        parts = [p.strip() for p in re.split(r"[·･、,/|]", place) if p.strip()]
        if len(parts) >= 2:
            clauses = []
            for part in parts:
                pat = _literal("%" + part + "%", "text")
                clauses.append(
                    f'({alias}."A8" ILIKE {pat} OR {alias}."A9" ILIKE {pat})'
                )
            return "(" + " OR ".join(clauses) + ")"
        pat = _literal("%" + place + "%", "text")
        return f'({alias}."A8" ILIKE {pat} OR {alias}."A9" ILIKE {pat})'
    return _building_place_sql(alias, place, plan=plan)


def _spatial_target_sql(
    entity: str, alias: str, place: str, plan=None
) -> tuple[str, str]:
    if entity == "basic_zone":
        return resolve_basic_zone_table(), _entity_place_sql(
            alias, entity, place, plan=plan
        )
    if entity == "industrial_complex":
        return INDUSTRIAL_TABLE, _entity_place_sql(alias, entity, place, plan=plan)
    if entity == "building":
        return resolve_building_table(), _building_place_sql(alias, place, plan=plan)
    clause = _admin_name_sql(alias, place)
    extra = _adm_cd_sql(alias, place, plan)
    if extra:
        clause = f"{clause} AND {extra}"
    return ADMIN_TABLE, clause


def _semantic_date_col(
    alias: str,
    field: str,
    col_map: dict[str, str] | None = None,
) -> str:
    """permit_date / approval_date → catalog/col_map 물리 컬럼."""
    _field, col = _field_col(alias, "building", field, col_map)
    return col


def _predicate_node_ids(pred: PredicateSpec) -> list[str]:
    from txt2sql.semantic_plan.predicate_utils import walk_predicate

    out: list[str] = []
    for index, node in enumerate(walk_predicate(pred)):
        if node.op == "cmp" and node.left and node.left.field:
            out.append(f"{node.left.field}:{node.operator}:{index}")
        else:
            out.append(f"{node.op}:{index}")
    return out


def _plan_uses_d198_slots(plan: SemanticQueryPlan) -> bool:
    if "d010_gis" in (plan.assumptions or []):
        return False
    if "d198_ledger" in (plan.assumptions or []):
        return True
    fields = {item.field for item in plan.filters}
    from txt2sql.semantic_plan.predicate_utils import walk_predicate

    pred = effective_predicate(plan)
    if pred is not None:
        for node in walk_predicate(pred):
            if node.op == "cmp" and node.left and node.left.field:
                fields.add(node.left.field)
    d198_exclusive = {
        "detail_usage",
        "usage_class",
        "ledger_kind",
        "complex_building_kind",
        "permit_date",
    }
    d010_native = {
        "ground_floors",
        "basement_floors",
        "height_m",
        "gross_floor_area_m2",
        "building_area_m2",
        "site_area_m2",
        "building_coverage_ratio",
        "floor_area_ratio",
        "structure",
        "usage",
        "violation_status",
        "special_land",
    }
    temporal_fields = {"approval_date", "building_age_years"}
    if (
        plan.query_kind == "count"
        and fields <= {"usage_class"}
        and "d198_ledger" not in (plan.assumptions or [])
    ):
        return False
    if fields & d198_exclusive:
        return True
    from txt2sql.domain import assumptions_include_permit_lag

    # Temporal + D010-native metrics → stay on D010 (A13/A26), not D198 ledger columns.
    if (
        fields & temporal_fields
        and fields & d010_native
        and not assumptions_include_permit_lag(plan.assumptions)
    ):
        return False
    if fields & temporal_fields:
        return True
    if assumptions_include_permit_lag(plan.assumptions):
        return True
    for ratio in plan.ratios or []:
        for pred in (ratio.numerator_predicate, ratio.denominator_predicate):
            if pred is None:
                continue
            for node in walk_predicate(pred):
                if node.op == "cmp" and node.left and node.left.field:
                    if node.left.field in d198_exclusive | temporal_fields:
                        return True
                    if node.right and node.right.field in d198_exclusive | temporal_fields:
                        return True
    return False


def _d198_table_for_plan(plan: SemanticQueryPlan) -> str | None:
    if plan.entity != "building" or not _plan_uses_d198_slots(plan):
        return None
    place = plan.scope.place.name.strip() if plan.scope and plan.scope.place else ""
    gu = place if place.endswith(("구", "군")) else None
    if gu is None and place:
        from txt2sql.domain import d198_gu_for_dong

        gu = d198_gu_for_dong(place)
    table = d198_table_for_gu(gu) if gu else None
    if table is None:
        # Temporal on uncovered district: fall back to D010 date column (no raise).
        if any(
            f.field in {"approval_date", "building_age_years"}
            for f in plan.filters
        ) and not any(
            f.field
            in {
                "detail_usage",
                "usage_class",
                "ledger_kind",
                "complex_building_kind",
                "permit_date",
            }
            for f in plan.filters
        ):
            return None
        # also allow day-gap without exclusive filters to resolve table via dong→gu
        from txt2sql.domain import assumptions_include_permit_lag

        if assumptions_include_permit_lag(plan.assumptions):
            return None
        raise SemanticCompileError(
            "detail_usage/usage_class require a D198-covered district"
        )
    return table


def _column_override(plan: SemanticQueryPlan) -> dict[str, str]:
    if _d198_table_for_plan(plan):
        return dict(D198_BUILDING_COLUMNS)
    return {}


def _col(alias: str, column: str, *, physical: bool = True) -> str:
    return f"{alias}.{_ident(column, physical=physical)}"


_SQL_WORDS = frozenset(
    {
        "select",
        "from",
        "where",
        "join",
        "insert",
        "update",
        "delete",
        "drop",
        "table",
        "limit",
        "order",
        "group",
    }
)


def _ident(name: str, *, physical: bool = False) -> str:
    if not _IDENT_RE.fullmatch(name) or name.lower() in _SQL_WORDS:
        raise SemanticCompileError(f"invalid identifier: {name}")
    if physical and not is_allowed_physical_identifier(name):
        raise SemanticCompileError(f"rejected physical identifier: {name}")
    return '"' + name + '"'


def _literal(value: object, data_type: str) -> str:
    if value is None:
        return "NULL"
    if data_type == "number":
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise SemanticCompileError(f"invalid number literal: {value!r}") from exc
        if number != number or number in (float("inf"), float("-inf")):
            raise SemanticCompileError("invalid number literal")
        return sql_number(number)
    text = str(value)
    return "'" + text.replace("'", "''") + "'"


def _compiler_reference_date() -> str | date | None:
    try:
        from txt2sql.config import load_settings

        return load_settings().reference_date
    except Exception:
        return "2026-08-27"


def _ref_sql() -> str:
    return reference_date_sql(_compiler_reference_date())


def _approval_year_expr(col: str) -> str:
    return f"LEFT(regexp_replace({col}::text, '[^0-9]', '', 'g'), 4)"


def _building_age_expr(col: str) -> str:
    """Age in years via AGE/EXTRACT when ISO date, else year subtraction."""
    year_expr = _approval_year_expr(col)
    ref = _ref_sql()
    return (
        f"(CASE WHEN {col}::text ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}' "
        f"THEN EXTRACT(YEAR FROM AGE({ref}, {col}::date))::int "
        f"ELSE (EXTRACT(YEAR FROM {ref})::int - ({year_expr})::int) END)"
    )


def _building_age_sql(col: str, spec: FilterSpec) -> str:
    age = _building_age_expr(col)
    valid = f"({col}::text ~ '^[0-9]{{4}}')"
    op = _OPS.get(spec.operator)
    if spec.operator == "between":
        lo, hi = spec.value, spec.value2
        return f"{valid} AND {age} BETWEEN {int(float(lo))} AND {int(float(hi))}"
    if op is None:
        raise SemanticCompileError(f"unknown operator: {spec.operator}")
    return f"{valid} AND {age} {op} {int(float(spec.value))}"


def _approval_decade_expr(col: str) -> str:
    return f"(({_approval_year_expr(col)})::int / 10 * 10)"


def _bin_expr_for(plan: SemanticQueryPlan, key: str, col: str) -> str | None:
    # Prefer explicit BinSpec over legacy width_bucket assumptions.
    for spec in getattr(plan, "bins", None) or []:
        if spec.field != key:
            continue
        if spec.edges and len(spec.edges) >= 2:
            return _edges_bin_case(col, list(spec.edges), list(spec.labels or []))
        if spec.width is not None and float(spec.width) > 0:
            w = sql_number(float(spec.width))
            return f"(FLOOR(({col})::float8 / {w}) * {w})"
    for item in plan.assumptions or []:
        if not item.startswith("width_bucket:"):
            continue
        parts = item.split(":")
        if len(parts) < 3 or parts[1] != key:
            continue
        try:
            width = float(parts[2])
        except ValueError:
            continue
        if width <= 0:
            continue
        w = sql_number(width)
        return f"(FLOOR(({col})::float8 / {w}) * {w})"
    return None


def _edges_bin_case(col: str, edges: list[float], labels: list[str]) -> str:
    """Cutpoint bins. 2 edges → (-inf,e0) / [e0,e1] / (e1,+inf). Else left-closed intervals."""
    sorted_edges = sorted(float(e) for e in edges)
    if len(sorted_edges) < 2:
        return f"({col})::float8"
    # Explicit 3-way split used by「미만 / 중간 / 초과」questions.
    if len(sorted_edges) == 2 and len(labels) >= 3:
        lo, hi = sorted_edges[0], sorted_edges[1]
        return (
            "(CASE "
            f"WHEN ({col})::float8 < {sql_number(lo)} THEN {_literal(labels[0], 'text')} "
            f"WHEN ({col})::float8 <= {sql_number(hi)} THEN {_literal(labels[1], 'text')} "
            f"ELSE {_literal(labels[2], 'text')} END)"
        )
    branches: list[str] = []
    for i in range(len(sorted_edges) - 1):
        lo = sorted_edges[i]
        hi = sorted_edges[i + 1]
        label = labels[i] if i < len(labels) else f"{lo:g}_{hi:g}"
        lit = _literal(label, "text")
        branches.append(
            f"WHEN ({col})::float8 >= {sql_number(lo)} "
            f"AND ({col})::float8 < {sql_number(hi)} THEN {lit}"
        )
    last_lo = sorted_edges[-1]
    last_label = (
        labels[len(sorted_edges) - 1]
        if len(labels) >= len(sorted_edges)
        else f"{last_lo:g}_inf"
    )
    branches.append(
        f"WHEN ({col})::float8 >= {sql_number(last_lo)} "
        f"THEN {_literal(last_label, 'text')}"
    )
    return "(CASE " + " ".join(branches) + " ELSE NULL END)"


def _approval_date_sql(col: str, spec: FilterSpec) -> str:
    """사용승인일 텍스트 컬럼을 연도 또는 날짜로 비교한다."""
    if spec.operator == "is_null":
        return f"{col} IS NULL"
    if spec.operator == "is_not_null":
        # 「기록된」→ ISO 날짜가 있는 행 (골드 grain과 동일)
        return f"{col}::text ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$'"
    year_expr = _approval_year_expr(col)
    valid = f"({col}::text ~ '^[0-9]{{4}}')"
    op = _OPS.get(spec.operator)
    if spec.operator == "between":
        lo, hi = spec.value, spec.value2
        return f"{valid} AND {year_expr}::int BETWEEN {int(lo)} AND {int(hi)}"
    raw = spec.value
    if isinstance(raw, str) and raw.startswith("rel_years:"):
        years = int(raw.split(":", 1)[1])
        if years < 1 or years > 200:
            raise SemanticCompileError(f"invalid rel_years: {raw}")
        if op is None:
            raise SemanticCompileError(f"unknown operator: {spec.operator}")
        date_ok = f"{col}::text ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$'"
        return (
            f"{date_ok} AND {col}::date {op} "
            f"({_ref_sql()} - INTERVAL '{years} years')"
        )
    if isinstance(raw, str) and re.match(r"^\d{4}-\d{2}-\d{2}", raw):
        year = int(raw[:4])
        iso = raw[:10]
        date_ok = f"{col}::text ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}'"
        year_ok = f"{col}::text ~ '^[0-9]{{4}}$'"
        if op is None:
            raise SemanticCompileError(f"unknown operator: {spec.operator}")
        return (
            f"{valid} AND ("
            f"({date_ok} AND {col}::date {op} DATE '{iso}') OR "
            f"({year_ok} AND {year_expr}::int {op} {year})"
            f")"
        )
    if op is None:
        raise SemanticCompileError(f"unknown operator: {spec.operator}")
    return f"{valid} AND {year_expr}::int {op} {int(float(raw))}"


def _assert_safe_sql(sql: str) -> str:
    lower = " ".join(sql.lower().split())
    if not (lower.startswith("select") or lower.startswith("with")):
        raise SemanticCompileError("compiled SQL must be SELECT or WITH")
    for word in ("insert", "update", "delete", "drop", "alter", "truncate"):
        if f" {word} " in f" {lower} ":
            raise SemanticCompileError(f"forbidden keyword: {word}")
    if "select *" in lower:
        raise SemanticCompileError("SELECT * is not allowed")
    return sql


def _assert_table_columns(sql: str, entity: str, tables: list[str]) -> str:
    """D010에 D198 전용 컬럼(A33/A34/A30)을 붙이지 않는다."""
    from txt2sql.sql_d010_guard import rewrite_d198_columns_on_d010, uses_d010_only

    using_d010 = entity == "building" or any("AL_D010" in (t or "") for t in tables)
    using_d198 = any("AL_D198" in (t or "") for t in tables)
    if using_d010 and not using_d198:
        sql = rewrite_d198_columns_on_d010(sql)
        if uses_d010_only(sql) and re.search(r'"(A33|A34|A30)"', sql):
            raise SemanticCompileError("D198 columns cannot be used on D010")
    return sql
