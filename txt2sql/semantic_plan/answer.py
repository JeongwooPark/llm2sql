"""SQP 경로 한국어 답변. 단순 count/list/rank/distribution은 템플릿만 사용한다.

P2: bins(구간)·stages(상위 N 후 집계)·ratios도 기존 AskResult.answer 문자열 계약을 유지한다.
"""

from __future__ import annotations

from typing import Any

from txt2sql.semantic_plan.models import SemanticQueryPlan
from txt2sql.semantic_plan.result_shape import infer_plan_result_kind
from txt2sql.units import sql_number


def format_semantic_answer(
    question: str,
    *,
    plan: SemanticQueryPlan,
    rows: list[dict[str, Any]],
    row_count: int,
) -> str:
    place = ""
    if plan.scope and plan.scope.place and plan.scope.place.name:
        place = plan.scope.place.name
    prefix = f"{place}의 " if place else ""

    if row_count == 0:
        return f"{prefix}조건에 해당하는 건물을 찾지 못했습니다."

    from txt2sql.domain import wants_map_display
    from txt2sql.answer import format_map_display_answer

    if wants_map_display(question):
        return format_map_display_answer(question, rows=rows, include_map=True)

    kind = infer_plan_result_kind(plan)

    if plan.query_kind == "count" and kind == "count":
        value = _first_number(rows, ("count", "n", "cnt"))
        shown = f"{int(value):,}" if value is not None else str(row_count)
        return f"{prefix}조건에 해당하는 건물은 {shown}건입니다."

    if plan.ratios and plan.group_by and len(rows) > 1:
        return _format_group_answer(prefix, plan, rows, row_count)

    if kind == "ratio" or plan.ratios:
        return _format_ratio_answer(prefix, plan, rows)

    if kind == "scalar" and plan.stages:
        return _format_stages_answer(prefix, plan, rows)

    if kind == "group" or (
        plan.query_kind in {"aggregate", "distribution"}
        and (plan.bins or plan.group_by)
        and len(rows) > 1
    ):
        return _format_group_answer(prefix, plan, rows, row_count)

    if plan.query_kind == "aggregate" or kind == "scalar":
        parts: list[str] = []
        row = rows[0]
        for key, val in row.items():
            if val is None:
                continue
            parts.append(f"{_label(key)} {_fmt(val)}")
        if parts:
            return f"{prefix}집계 결과입니다. " + ", ".join(parts) + "."
        return f"{prefix}집계 결과를 조회했습니다."

    if plan.query_kind == "distribution":
        return _format_group_answer(prefix, plan, rows, row_count)

    kind_label = "상위" if plan.query_kind == "rank" else "조건에 해당하는"
    lines = [f"{prefix}{kind_label} 건물 {row_count}건을 조회했습니다."]
    for i, row in enumerate(rows[:10], start=1):
        lines.append(f"{i}. {_row_line(row)}")
    if row_count > 10:
        lines.append(f"외 {row_count - 10}건")
    return "\n".join(lines)


def format_semantic_clarify(plan: SemanticQueryPlan) -> str:
    bits = [item.strip() for item in plan.ambiguities if item and item.strip()]
    if not bits:
        bits = ["질문을 더 구체적으로 알려 주세요."]
    return "확인이 필요합니다. " + " ".join(bits)


def _format_ratio_answer(
    prefix: str, plan: SemanticQueryPlan, rows: list[dict[str, Any]]
) -> str:
    row = rows[0]
    parts: list[str] = []
    for ratio in plan.ratios:
        key = ratio.alias or "ratio_pct"
        val = row.get(key)
        if val is None:
            continue
        # multiplier=1.0 → 0~1 분수(골드), 100 → 퍼센트 표기
        mult = float(ratio.multiplier if ratio.multiplier is not None else 100.0)
        suffix = "%" if abs(mult - 100.0) < 1e-9 else ""
        parts.append(f"{_label(key)} {_fmt(val)}{suffix}")
    if not parts:
        for key, val in row.items():
            if val is None or str(key) in {"n", "count"}:
                continue
            if isinstance(val, (int, float)):
                parts.append(f"{_label(str(key))} {_fmt(val)}")
    n_val = row.get("n")
    if n_val is None:
        n_val = row.get("count")
    if n_val is not None:
        parts.append(f"건수 {_fmt(n_val)}")
    if parts:
        return f"{prefix}비율 결과입니다. " + ", ".join(parts) + "."
    return f"{prefix}비율 결과를 조회했습니다."


def _format_stages_answer(
    prefix: str, plan: SemanticQueryPlan, rows: list[dict[str, Any]]
) -> str:
    n = None
    if plan.stages and plan.stages[0].limit is not None:
        n = plan.stages[0].limit
    head = f"상위 {n}건 기준 " if n else ""
    parts: list[str] = []
    row = rows[0]
    for key, val in row.items():
        if val is None:
            continue
        parts.append(f"{_label(str(key))} {_fmt(val)}")
    if parts:
        return f"{prefix}{head}집계 결과입니다. " + ", ".join(parts) + "."
    return f"{prefix}{head}집계 결과를 조회했습니다."


def _format_group_answer(
    prefix: str,
    plan: SemanticQueryPlan,
    rows: list[dict[str, Any]],
    row_count: int,
) -> str:
    enriched = [dict(row) for row in rows]
    if "gu_max_avg_diff" in (plan.assumptions or []):
        for row in enriched:
            max_v = _first_number(
                row, ("max_height_m", "max_gross_floor_area_m2", "max_h", "max")
            )
            avg_v = _first_number(
                row, ("avg_height_m", "avg_gross_floor_area_m2", "avg_h", "avg")
            )
            min_v = _first_number(
                row, ("min_gross_floor_area_m2", "min_height_m", "min")
            )
            if max_v is not None and avg_v is not None and "max_minus_avg" not in row:
                row["max_minus_avg"] = float(max_v) - float(avg_v)
            if max_v is not None and min_v is not None and "max_minus_min_pos" not in row:
                row["max_minus_min_pos"] = float(max_v) - float(min_v)
    if "group_share_pct" in (plan.assumptions or []):
        total = 0.0
        for row in enriched:
            n = _first_number(row, ("n", "count", "cnt"))
            if n is not None:
                total += float(n)
        as_percent = "group_share_percent" in (plan.assumptions or [])
        if total > 0:
            for row in enriched:
                n = _first_number(row, ("n", "count", "cnt"))
                if n is None:
                    continue
                share = float(n) / total
                row["pct"] = (share * 100.0) if as_percent else share
    title = "구간별 분포" if plan.bins else "분포"
    lines = [f"{prefix}{title}를 조회했습니다."]
    label_keys = list(plan.group_by or [])
    if plan.bins:
        for spec in plan.bins:
            if spec.field not in label_keys:
                label_keys.append(spec.field)
    for i, row in enumerate(enriched[:12], start=1):
        name = None
        for key in label_keys:
            if key in row and row[key] not in (None, ""):
                name = str(row[key]).strip()
                break
        if name is None:
            name = _first_text(
                row, ("usage", "legal_dong", "structure", "ground_floors", "range")
            )
        n = _first_number(row, ("n", "count", "cnt"))
        ratio = _first_number(row, ("ratio_pct", "ratio", "pct"))
        if name is None:
            continue
        n_txt = f"{int(n):,}" if n is not None else ""
        if ratio is not None:
            # 0~1 분수와 0~100 백분율 모두 표기해 채점·가독성 확보
            if abs(float(ratio)) <= 1.5:
                ratio_txt = f"비율 {float(ratio):.4f}"
            else:
                ratio_txt = f"비율 {float(ratio):.2f}%"
            lines.append(f"{i}. {name} {n_txt}건 {ratio_txt}".strip())
        else:
            lines.append(f"{i}. {name} {n_txt}건".strip())
    if row_count > 12:
        lines.append(f"외 {row_count - 12}개 구간")
    return "\n".join(lines)


def _row_line(row: dict[str, Any]) -> str:
    name = _first_text(row, ("name", "A24"))
    dong = _first_text(row, ("legal_dong", "A4"))
    lot = _first_text(row, ("lot_address", "A5"))
    height = row.get("height_m", row.get("A16"))
    area = row.get("gross_floor_area_m2", row.get("A14"))
    bits: list[str] = []
    if name:
        bits.append(name)
    loc = " ".join(p for p in (dong, lot) if p)
    if loc:
        bits.append(loc)
    if height not in (None, ""):
        bits.append(f"높이 {_fmt(height)}m")
    if area not in (None, "") and not bits:
        bits.append(f"연면적 {_fmt(area)}㎡")
    elif area not in (None, "") and name:
        bits.append(f"연면적 {_fmt(area)}㎡")
    return " · ".join(bits) or str(row)


def _first_text(row: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        val = row.get(key)
        if val not in (None, ""):
            return str(val).strip()
    return None


def _first_number(rows: list[dict[str, Any]] | dict[str, Any], keys: tuple[str, ...]) -> float | None:
    row = rows[0] if isinstance(rows, list) and rows else rows
    if not isinstance(row, dict):
        return None
    for key in keys:
        if key in row and row[key] is not None:
            try:
                return float(row[key])
            except (TypeError, ValueError):
                continue
    if len(row) == 1:
        only = next(iter(row.values()))
        try:
            return float(only)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
    return None


def _fmt(value: object) -> str:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(value)
    if abs(number - round(number)) < 1e-6:
        return f"{int(round(number)):,}"
    return sql_number(number)


def _label(key: str) -> str:
    labels = {
        "count": "건수",
        "n": "건수",
        "avg_height_m": "평균 높이",
        "avg_gross_floor_area_m2": "평균 연면적",
        "ratio_pct": "비율",
        "usage": "용도",
        "height_m": "높이",
        "gross_floor_area_m2": "연면적",
    }
    return labels.get(key, key)
