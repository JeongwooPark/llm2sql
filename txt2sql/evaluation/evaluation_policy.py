"""EvaluationPolicy — metric-specific comparators with semantic context guards.

Tolerance is NEVER applied globally. Before any tolerance comparison,
metric / unit / scope / grain must align between gold and prediction context.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Literal

ComparatorKind = Literal[
    "integer_exact",
    "scalar_float",
    "ratio",
    "distance_m",
    "area_m2",
    "geometry",
]

NUM_RE = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+\.\d+|-?\d+")


@dataclass(frozen=True)
class SemanticEvalContext:
    """Semantic context required before tolerance may apply."""

    metric: str  # count | count_distinct | avg | sum | min | max | ratio | distance | area
    unit: str | None = None  # None | count | m2 | m | pct | coord
    scope: str | None = None  # canonical place if detectable
    grain: str | None = None  # building | admin_dong | legal_dong | sigungu | ...
    distinct: bool = False


@dataclass(frozen=True)
class ComparatorSpec:
    kind: ComparatorKind
    abs_tol: float = 0.0
    rel_tol: float = 0.0


# Metric-specific comparator defaults (not global).
COMPARATOR_DEFAULTS: dict[ComparatorKind, ComparatorSpec] = {
    "integer_exact": ComparatorSpec(kind="integer_exact"),
    "scalar_float": ComparatorSpec(kind="scalar_float", abs_tol=0.01, rel_tol=0.001),
    "ratio": ComparatorSpec(kind="ratio", abs_tol=0.01, rel_tol=0.005),
    "distance_m": ComparatorSpec(kind="distance_m", abs_tol=0.5, rel_tol=0.001),
    "area_m2": ComparatorSpec(kind="area_m2", abs_tol=0.1, rel_tol=0.001),
    "geometry": ComparatorSpec(kind="geometry", abs_tol=1e-6, rel_tol=1e-6),
}


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        m = re.search(r"[-+]?\d*\.?\d+", text)
        return float(m.group()) if m else None


def _is_integer_value(value: float) -> bool:
    return abs(value - round(value)) < 1e-6 and abs(value) < 1e15


def _as_int(value: float) -> int | None:
    if _is_integer_value(value):
        return int(round(value))
    return None


def parse_numbers(text: str) -> list[float]:
    """Extract floats.

    Prefer key=value RHS only for metric-style aliases (p25=, resi_h=, var_area=).
    Group golds like ``gu=…, n=1,288`` keep full NUM_RE so rank indices still match.
    """
    text = text or ""
    metric_kv = re.findall(
        r"(?:p\d+|pctl\w*|percentile\w*|var_\w*|resi_\w*|com_\w*|ind_\w*|"
        r"avg_\w*|sum_\w*|min_\w*|max_\w*|median_\w*|std\w*|diff|ratio\w*|pct\w*)"
        r"\s*=\s*(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+\.\d+|-?\d+)",
        text,
        flags=re.IGNORECASE,
    )
    if metric_kv:
        out: list[float] = []
        for m in metric_kv:
            v = _to_float(m)
            if v is not None:
                out.append(v)
        return out
    out = []
    for m in NUM_RE.findall(text):
        v = _to_float(m)
        if v is not None:
            out.append(v)
    return out


def _detect_scope(text: str) -> str | None:
    raw = text or ""
    for m in re.finditer(r"[\w가-힣]+(?:구|군|동|시|도)", raw):
        token = m.group(0)
        end = m.end()
        # 「기초구역」 안의 '기초구' 오탐 방지
        if token.endswith("구") and end < len(raw) and raw[end] == "역":
            continue
        if len(token) >= 2:
            return token
    return None


def _detect_unit(text: str, *, metric: str) -> str | None:
    t = text or ""
    if metric in {"count", "count_distinct"}:
        return "count"
    # 면적 집계·분위수·분산: 질문의 「25%」는 비율 단위가 아님
    areaish = (
        "면적" in t
        or "연면적" in t
        or "㎡" in t
        or "m2" in t.lower()
        or "gfa" in t.lower()
        or "var_area" in t.lower()
        or re.search(r"\bp\d+\s*=", t.lower()) is not None
    )
    if metric in {"area", "avg", "sum", "min", "max", "std", "median", "scalar_float"} and areaish:
        if not any(k in t for k in ("비율", "퍼센트", "몇%", "몇 프로")):
            return "m2"
    # pctl/percentile 별칭에 포함된 'pct' 부분문자열 오탐 방지
    if re.search(r"(?<![a-z])pct(?![a-z_])", t.lower()) or re.search(
        r"(?<!\d)%", t
    ) or "비율" in t or "퍼센트" in t:
        if "%" in t and not any(k in t for k in ("비율", "퍼센트", "몇%", "pct")):
            if areaish or "분위" in t or "pctl" in t.lower() or "percentile" in t.lower():
                return "m2" if areaish or "분위" in t or "pctl" in t.lower() else None
        if "pctl" in t.lower() or "percentile" in t.lower():
            return "m2" if areaish or metric == "area" else None
        return "pct"
    if "㎡" in t or "m2" in t.lower() or "연면적" in t or "면적" in t:
        return "m2"
    if re.search(r"\d+\s*m\b", t, re.I) or "거리" in t or "반경" in t:
        return "m"
    if "좌표" in t or "경위도" in t or "위도" in t or "경도" in t:
        return "coord"
    return None


def _metric_from_gold_text(gold: str) -> str | None:
    """Parse gold field keys (avg_h=, pct=, …) before question heuristics."""
    g = (gold or "").lower()
    if re.search(r"\bsum_", g):
        return "sum"
    # max_h/min_h 는 rank 답변(scalar_float)과 맞추고, 면적 min/max만 엄격 metric
    if re.search(r"\bmin_(?:area|building)", g):
        return "min"
    if re.search(r"\bmax_(?:area|building)", g):
        return "max"
    if re.search(r"\bmedian_", g):
        return "median"
    if re.search(r"\bstd_", g):
        return "std"
    if re.search(r"\bavg_", g):
        return "avg"
    if re.search(r"\bcorr_", g):
        return "corr"
    if re.search(r"\bvar_", g) or re.search(r"\bp\d+\b", g) or "pctl" in g:
        return "area"
    if re.search(r"_ratio\b", g):
        return "ratio"
    if re.search(r"\bpct\b", g) or g.strip().startswith("pct"):
        return "ratio"
    if re.search(r"\bcut=", g):
        return "avg"
    return None


def _unit_from_gold_text(gold: str, *, metric: str) -> str | None:
    g = (gold or "").lower()
    if metric == "ratio" or re.search(r"\bpct\b", g) or re.search(r"_ratio\b", g):
        return "pct" if "%" in gold or re.search(r"\bpct\b", g) else None
    if (
        "avg_h" in g
        or "median_h" in g
        or "std_h" in g
        or "max_h" in g
        or "min_h" in g
        or "height" in g
        or "높이" in g
    ):
        return "m"
    if (
        "avg_gfa" in g
        or "sum_gfa" in g
        or "gfa" in g
        or "연면적" in g
        or "min_area" in g
        or "max_area" in g
        or "avg_area" in g
        or "sum_area" in g
        or "sum_ar" in g
        or re.search(r"\b(?:min|max|avg|sum)_.*area", g)
    ):
        return "m2"
    if metric in {"count", "count_distinct"}:
        return "count"
    return None


def _metric_from_answer_text(answer: str) -> str | None:
    a = (answer or "").lower()
    if re.search(r"\bcorr_", a) or "상관계수" in (answer or ""):
        return "corr"
    if re.search(r"\bsum_", a):
        return "sum"
    if re.search(r"\bmin_(?:area|building)", a):
        return "min"
    if re.search(r"\bmax_(?:area|building)", a):
        return "max"
    if "variance_" in a or re.search(r"\bvar_(?:area|building)", a):
        return "area"
    if "percentile_" in a or "pctl_" in a:
        return "area"
    if re.search(r"median_(?:height|h|days)", a):
        return "median"
    if re.search(r"std(?:dev)?_(?:height|h|days)", a):
        return "std"
    if re.search(r"avg_(?:height|gross_floor_area|building|age|area|ground)", a):
        return "avg"
    if "ratio_pct" in a or re.search(r"\bpct\b", a):
        return "ratio"
    if re.search(r"\bavg_", a):
        return "avg"
    return None


def _unit_from_answer_text(answer: str, *, metric: str) -> str | None:
    a = (answer or "").lower()
    if metric == "ratio" or "ratio_pct" in a:
        return "pct"
    if any(tok in a for tok in ("avg_height", "median_height", "std_height", "height_m")):
        return "m"
    if any(
        tok in a
        for tok in (
            "sum_gross_floor_area",
            "avg_gross_floor_area",
            "gfa",
            "variance_",
            "percentile_",
            "building_area",
            "area_m2",
            "min_building_area",
            "min_area",
            "sum_area",
            "avg_area",
        )
    ):
        return "m2"
    return _detect_unit(answer, metric=metric)


def _metric_from_sql(sql: str | None, *, kind: str) -> str | None:
    upper = (sql or "").upper()
    if not upper:
        return None
    if "COUNT(DISTINCT" in upper:
        return "count_distinct"
    # Scalar answers often SELECT avg(...) and count(*) together — prefer aggregate intent.
    if kind == "scalar":
        for token, metric in (
            ("CORR(", "corr"),
            ("VAR_POP", "area"),
            ("VARIANCE", "area"),
            ("VAR_SAMP", "area"),
            ("PERCENTILE", "area"),
            ("STDDEV", "std"),
            ("AVG(", "avg"),
            ("SUM(", "sum"),
            ("MIN(", "min"),
            ("MAX(", "max"),
        ):
            if token in upper:
                return metric
        if "RATIO" in upper or " AS \"RATIO_PCT\"" in upper:
            return "ratio"
    if kind == "count" or (
        "COUNT(" in upper
        and not any(
            tok in upper
            for tok in (
                "CORR(",
                "AVG(",
                "SUM(",
                "MIN(",
                "MAX(",
                "VAR_POP",
                "VARIANCE",
                "PERCENTILE",
                "STDDEV",
            )
        )
    ):
        return "count"
    if "AVG(" in upper:
        return "avg"
    return None


def _context_from_rows(
    rows: list[dict[str, Any]] | None, *, kind: str
) -> tuple[str | None, str | None]:
    if not rows:
        return None, None
    keys: set[str] = set()
    for row in rows:
        keys.update(str(k).lower() for k in row.keys())
    metric: str | None = None
    grain: str | None = None
    if "avg_h" in keys or "avg_height_m" in keys or any(k.startswith("avg_") for k in keys):
        metric = "avg"
    if any(k.startswith("corr_") for k in keys):
        metric = "corr"
    if "legal_dong" in keys or "bjd" in keys:
        grain = "legal_dong"
    elif "admin_dong" in keys:
        grain = "admin_dong"
    elif kind == "scalar" and "n" in keys and grain is None:
        grain = "group"
    return metric, grain


def infer_gold_context(*, kind: str, gold: str, question: str = "") -> SemanticEvalContext:
    """Infer semantic evaluation context from gold + question."""
    blob = f"{gold} {question}"
    metric = "unknown"
    distinct = False
    grain: str | None = None

    if kind == "count":
        metric = "count"
        grain = "building"
        if "행정동" in blob and "별" in blob:
            grain = "admin_dong"
            if "서로 다른" in blob or "distinct" in blob.lower():
                metric = "count_distinct"
                distinct = True
        elif "법정동" in blob:
            grain = "legal_dong"
    elif kind == "scalar":
        metric = _metric_from_gold_text(gold) or "scalar_float"
        if "avg_h" in gold or "avg_h=" in gold.lower():
            metric = "avg"
        if "n=" in gold and "bjd=" in gold:
            metric = "avg" if "avg" in gold.lower() else "count"
            grain = "legal_dong"
        if metric == "corr":
            pass  # 상관계수: 질문의 「연면적」 등으로 area로 덮지 않음
        elif metric == "scalar_float":
            if re.search(r"\bpct\b", gold.lower()) or gold.strip().lower().startswith("pct"):
                metric = "ratio"
            elif "평균" in question or "avg" in gold.lower():
                metric = "avg"
            elif "거리" in question or re.search(r"\d+\s*m\b", question, re.I):
                metric = "distance"
            elif "㎡" in gold or "연면적" in gold or "면적" in question:
                metric = "area"
            elif "좌표" in question or "경위도" in question:
                metric = "geometry"
    elif kind == "group":
        metric = "count"
        grain = "group"
    else:
        metric = kind

    unit = _unit_from_gold_text(gold, metric=metric) or _detect_unit(gold, metric=metric)
    if metric == "corr":
        unit = None
    elif unit is None and metric not in {"avg", "scalar_float"}:
        unit = _detect_unit(blob, metric=metric)
    scope: str | None = None
    bjd_m = re.search(r"bjd=([^;/]+)", gold)
    if bjd_m:
        scope = bjd_m.group(1).strip()
    if scope is None:
        scope = _detect_scope(question) or _detect_scope(gold)
    return SemanticEvalContext(
        metric=metric,
        unit=unit,
        scope=scope,
        grain=grain,
        distinct=distinct,
    )


def infer_context_from_query_ir(query_ir: dict[str, Any] | None) -> SemanticEvalContext | None:
    """Build evaluation context from canonical QueryIR when available."""
    if not query_ir:
        return None
    task = str(query_ir.get("task") or "")
    aggs = query_ir.get("aggregations") or []
    dims = query_ir.get("dimensions") or []
    metric = task
    distinct = False
    grain: str | None = None
    if aggs:
        fn = str(aggs[0].get("function") or "count")
        metric = fn
        distinct = bool(aggs[0].get("distinct"))
        g = aggs[0].get("grain")
        if isinstance(g, dict) and g.get("entity"):
            grain = str(g["entity"])
    if dims and not grain:
        grain = str(dims[0].get("field") or "")
    scope_place = None
    scope = query_ir.get("scope")
    if isinstance(scope, dict):
        scope_place = scope.get("place")
    return SemanticEvalContext(
        metric=metric,
        unit=aggs[0].get("unit") if aggs else None,
        scope=str(scope_place) if scope_place else None,
        grain=grain,
        distinct=distinct,
    )


def infer_pred_context(
    *,
    kind: str,
    answer: str,
    rows: list[dict[str, Any]] | None,
    sql: str | None = None,
    question: str = "",
    query_ir: dict[str, Any] | None = None,
) -> SemanticEvalContext | None:
    """Infer predicted semantic context from engine output."""
    from_ir = infer_context_from_query_ir(query_ir)
    row_metric, row_grain = _context_from_rows(rows, kind=kind)
    scope = _detect_scope(answer) or _detect_scope(question)
    metric = row_metric or _metric_from_answer_text(answer) or _metric_from_sql(sql, kind=kind)
    if metric is None:
        if from_ir is not None:
            return from_ir
        return infer_gold_context(kind=kind, gold="", question=question)

    distinct = metric == "count_distinct"
    unit = _unit_from_answer_text(answer, metric=metric)
    if metric == "corr":
        unit = None
    elif unit is None:
        unit = _detect_unit(answer, metric=metric)
    if metric in {"count", "count_distinct"} and unit is None:
        unit = "count"
    if metric == "avg" and unit is None and (
        row_grain == "legal_dong" or "avg_h" in (answer or "").lower()
    ):
        unit = "m"

    grain: str | None = row_grain or (from_ir.grain if from_ir else None)
    if kind == "count" and grain is None:
        grain = infer_gold_context(kind=kind, gold="", question=question).grain

    return SemanticEvalContext(
        metric=metric,
        unit=unit or (from_ir.unit if from_ir else None),
        scope=scope or (from_ir.scope if from_ir else None),
        grain=grain,
        distinct=distinct or (from_ir.distinct if from_ir else False),
    )


def contexts_align(gold: SemanticEvalContext, pred: SemanticEvalContext | None) -> bool:
    """All four semantic dimensions must match before tolerance applies."""
    if pred is None:
        return False
    if gold.metric != pred.metric:
        return False
    if gold.distinct != pred.distinct:
        return False
    if gold.unit is not None and pred.unit is not None and gold.unit != pred.unit:
        return False
    if gold.grain is not None and pred.grain is not None and gold.grain != pred.grain:
        return False
    if gold.scope is not None and pred.scope is not None and gold.scope != pred.scope:
        g_scope, p_scope = gold.scope, pred.scope
        if g_scope in p_scope or p_scope in g_scope:
            return True
        # 답변 place prefix가 상위 구·군이고 gold는 동 단위인 경우
        if g_scope.endswith(("동", "가", "리")) and p_scope.endswith(("구", "군", "시")):
            return True
        if p_scope.endswith(("동", "가", "리")) and g_scope.endswith(("구", "군", "시")):
            return True
        if gold.grain in {"legal_dong", "admin_dong", "group"}:
            return True
        return False
    return True


def comparator_for_context(ctx: SemanticEvalContext) -> ComparatorSpec:
    """Map semantic context to comparator — never one global tolerance."""
    m = ctx.metric
    if m in {"count", "count_distinct"}:
        return COMPARATOR_DEFAULTS["integer_exact"]
    if m in {"min", "max"} and ctx.unit == "count":
        return COMPARATOR_DEFAULTS["integer_exact"]
    if m == "ratio":
        return COMPARATOR_DEFAULTS["ratio"]
    if m == "distance":
        return COMPARATOR_DEFAULTS["distance_m"]
    if m == "area":
        return COMPARATOR_DEFAULTS["area_m2"]
    if m == "geometry":
        return COMPARATOR_DEFAULTS["geometry"]
    if m == "avg":
        return COMPARATOR_DEFAULTS["scalar_float"]
    if m in {"sum", "min", "max", "median", "std", "scalar_float", "corr"}:
        return COMPARATOR_DEFAULTS["scalar_float"]
    return COMPARATOR_DEFAULTS["scalar_float"]


def compare_values(got: Any, expected: Any, spec: ComparatorSpec) -> bool:
    """Compare two values using metric-specific comparator."""
    g = _to_float(got)
    e = _to_float(expected)
    if g is None or e is None:
        return str(got).strip() == str(expected).strip()

    if spec.kind == "integer_exact":
        gi, ei = _as_int(g), _as_int(e)
        if gi is not None and ei is not None:
            return gi == ei
        return g == e

    if spec.kind in {"scalar_float", "ratio", "distance_m", "area_m2", "geometry"}:
        return math.isclose(g, e, rel_tol=spec.rel_tol, abs_tol=spec.abs_tol)

    return g == e


def compare_with_policy(
    got: Any,
    expected: Any,
    *,
    gold_ctx: SemanticEvalContext,
    pred_ctx: SemanticEvalContext | None,
) -> tuple[bool, str]:
    """Compare values only if semantic contexts align; otherwise exact fail."""
    if not contexts_align(gold_ctx, pred_ctx):
        return False, "semantic-context-mismatch"
    spec = comparator_for_context(gold_ctx)
    if compare_values(got, expected, spec):
        return True, f"policy-{spec.kind}"
    return False, f"policy-mismatch-{spec.kind}"


def match_numbers_in_haystack(
    hay: list[float],
    targets: list[float],
    *,
    gold_ctx: SemanticEvalContext,
    pred_ctx: SemanticEvalContext | None,
) -> tuple[bool, int, str]:
    """Match gold numbers in prediction haystack under evaluation policy."""
    if not targets:
        return False, 0, "no-targets"

    spec = comparator_for_context(gold_ctx)

    # integer_exact: no tolerance — safe to match without full context alignment
    if spec.kind == "integer_exact":
        hits = sum(1 for target in targets if any(compare_values(x, target, spec) for x in hay))
        ok = hits >= len(targets)
        reason = f"policy-{spec.kind} {hits}/{len(targets)}"
        if not ok:
            reason = f"policy-mismatch-{spec.kind} hits={hits} gold={targets[:4]}"
        return ok, hits, reason

    # tolerance comparators: require semantic context alignment first
    if not contexts_align(gold_ctx, pred_ctx):
        return False, 0, "semantic-context-mismatch"

    hits = 0
    for target in targets:
        if any(compare_values(x, target, spec) for x in hay):
            hits += 1

    need = max(1, min(2, len(targets) // 2 + 1))
    if len(targets) == 1:
        need = 1

    ok = hits >= need
    reason = f"policy-{spec.kind} {hits}/{len(targets)}"
    if not ok:
        reason = f"policy-mismatch-{spec.kind} hits={hits} gold={targets[:4]}"
    return ok, hits, reason
