"""결과 행이 Contract·Plan이 기대하는 형태인지 검사한다.

count 계약이 후속·구간별에서 잘못 남는 경우가 많다. 그때는 실행된 Plan의
query_kind(list/group)를 따른다. 진짜 건수 질의의 다중 숫자 행은 계속 거절한다.

P2: stages(CTE 스칼라)·bins(구간 그룹)·ratios 결과를 기존 AskResult
(ok/answer/rows/table/chart/map) 계약에 맞게 분류·검증한다. AskResult 스키마는
바꾸지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from txt2sql.semantic_plan.models import SemanticQueryPlan

# SQP query_kind → Contract query_kind. aggregate/distribution 은 여러 행 그룹이다.
_PLAN_KIND = {
    "aggregate": "group",
    "distribution": "group",
}
_COUNT_ALIASES = {"n", "cnt", "count", "건수", "total_n"}
_RATIO_ALIASES = {"ratio_pct", "ratio", "pct", "percent", "비율"}
_LIST_ALIASES = {
    "name",
    "a24",
    "legal_dong",
    "lot_address",
    "a4",
    "a5",
    "usage",
    "a9",
}

PlanResultKind = Literal[
    "count", "list", "rank", "group", "ratio", "scalar", "unknown"
]


@dataclass
class ResultVerify:
    ok: bool
    reasons: list[str] = field(default_factory=list)


def infer_plan_result_kind(plan: SemanticQueryPlan | None) -> PlanResultKind:
    """실행 Plan이 기대하는 결과 형상(AskResult rows 계약)."""
    if plan is None:
        return "unknown"
    if plan.ratios:
        return "ratio"
    if plan.bins or (
        plan.query_kind in {"aggregate", "distribution"} and plan.group_by
    ):
        return "group"
    if plan.stages:
        # top-N → outer aggregate 등은 단일 행 스칼라.
        outer_aggs = []
        if len(plan.stages) >= 2:
            outer_aggs = list(plan.stages[1].aggregations or [])
        if outer_aggs or plan.aggregations:
            return "scalar"
        if plan.stages[0].kind == "rank":
            return "rank"
        return "list"
    raw = plan.query_kind
    if raw == "count":
        return "count"
    if raw == "list":
        return "list"
    if raw == "rank":
        return "rank"
    if raw in {"aggregate", "distribution"}:
        if plan.group_by:
            return "group"
        return "scalar"
    return "unknown"


def diagnose_result_shape(
    plan: SemanticQueryPlan,
    rows: list[dict[str, Any]] | None,
) -> list[str]:
    """Plan 기준 shape. Q03은 경고·재계획 신호이지 SQL 문법 오류가 아니다."""
    rows = rows or []
    errors: list[str] = []
    kind = infer_plan_result_kind(plan)

    if kind == "count":
        if len(rows) != 1:
            errors.append("Q03")
        elif rows and not _numeric_values(rows[0]):
            errors.append("Q03")
    elif kind == "rank" and plan.limit and len(rows) > plan.limit:
        errors.append("Q03")
    elif kind == "list" and len(rows) > 5000:
        errors.append("Q03")
    elif kind == "scalar":
        if len(rows) != 1:
            errors.append("Q03")
        elif rows and not _numeric_values(rows[0]):
            errors.append("Q03")
        else:
            aliases = _aggregation_aliases(plan)
            if aliases and rows and not any(a in rows[0] for a in aliases):
                if len(_numeric_values(rows[0])) < 1:
                    errors.append("Q03")
            # Scalar paths that request population size must expose alias `n`.
            wants_n = any(
                (item.alias or "").lower() == "n" or item.function == "count"
                for item in (plan.aggregations or [])
            )
            if wants_n and rows:
                row0 = rows[0]
                has_n = any(str(k).lower() == "n" for k in row0.keys())
                if not has_n and "count" not in {str(k).lower() for k in row0.keys()}:
                    errors.append("Q03")
    elif kind == "ratio":
        if not rows:
            errors.append("Q03")
        elif not _row_has_ratio(rows[0], plan):
            if not _numeric_values(rows[0]):
                errors.append("Q03")
    elif kind == "group":
        if plan.aggregations and rows:
            aliases = _aggregation_aliases(plan)
            row0 = rows[0]
            if aliases and not any(alias in row0 for alias in aliases):
                if len(_numeric_values(row0)) < len(plan.aggregations):
                    errors.append("Q03")
            for key in plan.group_by:
                if key not in row0 and not _bin_label_present(row0, key):
                    errors.append("Q03")
                    break
        elif plan.bins and rows:
            row0 = rows[0]
            for spec in plan.bins:
                if spec.field not in row0 and not _bin_label_present(row0, spec.field):
                    errors.append("Q03")
                    break

    if rows and all(all(v is None for v in row.values()) for row in rows):
        errors.append("Q03")
    return errors


def build_semantic_result_table(
    plan: SemanticQueryPlan | None,
    rows: list[dict[str, Any]] | None,
    *,
    question: str = "",
) -> dict[str, Any] | None:
    """구간·그룹 집계를 AskResult.table 호환 구조로 만든다."""
    if plan is None:
        return None
    rows = list(rows or [])
    kind = infer_plan_result_kind(plan)
    if kind not in {"group", "ratio"} or not rows:
        return None
    if kind == "ratio" and len(rows) == 1:
        return None

    label_keys = list(plan.group_by or [])
    if plan.bins:
        for spec in plan.bins:
            if spec.field not in label_keys:
                label_keys.append(spec.field)
    if not label_keys:
        # 첫 비수치 열을 라벨로.
        for key in rows[0].keys():
            if str(key).lower() not in _COUNT_ALIASES | _RATIO_ALIASES:
                label_keys.append(str(key))
                break
    if not label_keys:
        return None

    count_key = _count_key(rows[0])
    pairs: list[tuple[str, int]] = []
    for row in rows:
        label = _first_present_text(row, label_keys) or "(미상)"
        n = _as_int(row.get(count_key)) if count_key else None
        if n is None:
            nums = _numeric_values(row)
            n = int(nums[0]) if nums else None
        if n is None:
            continue
        pairs.append((label, n))
    if len(pairs) < 1:
        return None
    total = sum(n for _, n in pairs)
    table_rows = [
        {"range": label, "n": n, "pct": round(100.0 * n / total, 1) if total else 0.0}
        for label, n in pairs
    ]
    peak = max(table_rows, key=lambda r: r["n"]) if table_rows else None
    place = ""
    if plan.scope and plan.scope.place and plan.scope.place.name:
        place = plan.scope.place.name
    caption = (
        f"{place} 구간·그룹 집계입니다.".strip()
        if place
        else "구간·그룹 집계입니다."
    )
    if question and ("비율" in question or plan.ratios):
        caption = f"{place} 비율·구성입니다.".strip() if place else "비율·구성입니다."
    return {
        "caption": caption,
        "range_header": "구간" if plan.bins else "구분",
        "count_header": "동 수",
        "share_header": "비율",
        "rows": table_rows,
        "total": total,
        "peak": peak,
    }


def _aggregation_aliases(plan: SemanticQueryPlan) -> set[str]:
    items = list(plan.aggregations or [])
    if plan.stages and len(plan.stages) >= 2:
        items = list(plan.stages[1].aggregations or []) or items
    aliases: set[str] = set()
    for item in items:
        if item.alias:
            aliases.add(item.alias)
        elif item.field:
            aliases.add(f"{item.function}_{item.field}")
        else:
            aliases.add(item.function)
    for ratio in plan.ratios or []:
        if ratio.alias:
            aliases.add(ratio.alias)
    return aliases


def _bin_label_present(row: dict[str, Any], field: str) -> bool:
    lower = {str(k).lower() for k in row}
    return field.lower() in lower or "range" in lower or "label" in lower


def _row_has_ratio(row: dict[str, Any], plan: SemanticQueryPlan) -> bool:
    aliases = {r.alias for r in (plan.ratios or []) if r.alias}
    aliases |= _RATIO_ALIASES
    return any(a in row for a in aliases) or bool(_numeric_values(row))


def _count_key(row: dict[str, Any]) -> str | None:
    for key in row:
        if str(key).lower() in _COUNT_ALIASES:
            return str(key)
    return None


def _first_present_text(row: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return str(row[key]).strip()
        # case-insensitive
        for rk, rv in row.items():
            if str(rk).lower() == key.lower() and rv not in (None, ""):
                return str(rv).strip()
    return None


def _as_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _numeric_values(row: dict[str, Any]) -> list[Any]:
    return [v for v in row.values() if isinstance(v, (int, float)) and v is not None]


def _mapped_plan_kind(plan: SemanticQueryPlan | None) -> str | None:
    if plan is None:
        return None
    inferred = infer_plan_result_kind(plan)
    if inferred == "scalar":
        return "group"
    if inferred == "ratio":
        return "ratio"
    if inferred in {"group", "count", "list", "rank"}:
        return inferred
    raw = getattr(plan, "query_kind", None)
    return _PLAN_KIND.get(raw, raw) if raw else None


def _effective_kind(contract: Any | None, plan: SemanticQueryPlan | None) -> str | None:
    """실행 Plan이 list/group/ratio/scalar이면 계약의 stale count보다 우선한다."""
    plan_kind = _mapped_plan_kind(plan)
    contract_kind = getattr(contract, "query_kind", None) if contract is not None else None
    if plan_kind and plan_kind != "count":
        return plan_kind
    return contract_kind or plan_kind


def _rows_are_grouped_counts(rows: list[dict[str, Any]]) -> bool:
    """구간·연도별처럼 라벨 + 건수 열이 있는 여러 행."""
    if len(rows) < 2:
        return False
    keys = [str(k) for k in rows[0].keys()]
    if len(keys) < 2:
        return False
    lower = {k.lower() for k in keys}
    has_count = bool(lower & _COUNT_ALIASES) or bool(_numeric_values(rows[0]))
    has_label = any(k.lower() not in _COUNT_ALIASES for k in keys)
    return has_count and has_label


def _rows_are_entity_list(rows: list[dict[str, Any]]) -> bool:
    if not rows:
        return False
    return bool({str(k).lower() for k in rows[0].keys()} & _LIST_ALIASES)


def _is_bin_or_group_contract(contract: Any | None) -> bool:
    if contract is None:
        return False
    return bool(getattr(contract, "fixed_bins", False) or getattr(contract, "group_fields", None))


def _contract_allows_list_rows(contract: Any | None) -> bool:
    """목록 SQL인데 계약만 count로 남은 경우(출력 필드·비건수 질의)."""
    if contract is None:
        return False
    return bool(
        getattr(contract, "operation", None) == "list"
        or getattr(contract, "output_fields", None)
        or not getattr(contract, "wants_count", True)
    )


def _contract_has_extra_agg(contract: Any | None) -> bool:
    if contract is None:
        return False
    fns = {
        getattr(item, "function", None)
        for item in (getattr(contract, "aggregation_requests", None) or [])
    }
    return bool(fns - {"count", None})


def verify_result(
    contract: Any | None,
    rows: list[dict[str, Any]] | None,
    *,
    plan: SemanticQueryPlan | None = None,
) -> ResultVerify:
    """실패는 SQP 재계획 신호. Router는 plan=None이라 계약 kind만 본다."""
    rows = rows or []
    kind = _effective_kind(contract, plan)
    if kind == "count":
        return _verify_count(contract, rows, plan)
    if kind == "ratio":
        return _verify_ratio(rows)
    if kind == "rank":
        limit = getattr(contract, "limit", None) if contract is not None else None
        if plan is not None and limit is None:
            limit = plan.limit
            if plan.stages and plan.stages[0].limit is not None:
                limit = plan.stages[0].limit
        if limit is not None and len(rows) > int(limit):
            return ResultVerify(False, ["rank_over_limit"])
        return ResultVerify(True)
    if kind == "list" and len(rows) > 5000:
        return ResultVerify(False, ["list_too_many"])
    if kind == "group":
        # 다중 행 그룹·구간은 허용. 빈 결과도 ok(답변 템플릿이 처리).
        return ResultVerify(True)
    return ResultVerify(True)


def _verify_count(
    contract: Any | None,
    rows: list[dict[str, Any]],
    plan: SemanticQueryPlan | None,
) -> ResultVerify:
    if len(rows) != 1:
        # Router(plan 없음)에서 구간별·목록 SQL이 count 계약으로 들어온 경우만 통과
        mapped = _mapped_plan_kind(plan)
        if _rows_are_grouped_counts(rows) and (
            _is_bin_or_group_contract(contract) or mapped == "group"
        ):
            return ResultVerify(True)
        if _rows_are_entity_list(rows) and _contract_allows_list_rows(contract):
            return ResultVerify(True)
        # stages/bins 계획이 count 계약과 어긋나도 실행 행이 형상에 맞으면 유지
        inferred = infer_plan_result_kind(plan)
        if inferred in {"group", "scalar", "ratio"} and rows:
            return ResultVerify(True)
        return ResultVerify(False, ["count_row_count"])
    nums = _numeric_values(rows[0]) if rows else []
    if not nums:
        return ResultVerify(False, ["count_not_numeric"])
    if len(nums) > 2:
        # 평균+중앙값+건수처럼 한 행 다중 지표. 순수 건수의 3열 오답은 거절.
        if _contract_has_extra_agg(contract) or _mapped_plan_kind(plan) in {
            "group",
            "scalar",
            "ratio",
        }:
            return ResultVerify(True)
        if infer_plan_result_kind(plan) in {"scalar", "ratio", "group"}:
            return ResultVerify(True)
        return ResultVerify(False, ["count_multi_numeric"])
    return ResultVerify(True)


def _verify_ratio(rows: list[dict[str, Any]]) -> ResultVerify:
    if not rows:
        return ResultVerify(False, ["ratio_empty"])
    nums = [v for row in rows for v in _numeric_values(row)]
    if not nums:
        return ResultVerify(False, ["ratio_not_numeric"])
    if not any(0 <= float(v) <= 100 for v in nums) and all(float(v) > 100 for v in nums):
        return ResultVerify(False, ["ratio_out_of_range"])
    return ResultVerify(True)
