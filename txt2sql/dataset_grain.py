"""Dataset / column grain policy (D010 building vs D198 ledger).

MAIN485 계약 (P012 회귀수정 지시서 §4.3):
- D198 coverage가 등록된 구·동에서 usage를 조건·그룹·집계로 쓰면 D198 A25 우선
- detail_usage / usage_class / ledger_kind / complex_building_kind / permit_date → 항상 D198
- D010 공통 metric만 / 공간 핵심(전용 속성 없음) / 미등록·부산 전체 → D010
- 동일 개념이라도 A9와 A25는 다른 grain이며 교환하지 않는다
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from txt2sql.query_ir.models import QueryIR

DatasetGrain = Literal["d010", "d198"]

# NL cues that always require ledger grain (beyond main-usage coverage rule).
_D198_METRIC_CUES = (
    "세부용도",
    "용도분류",
    "건축물종류",
    "대장",
    "표제부",
    "허가일",
    "허가일자",
    "사용승인",
    "건축년",
    "경과",
    "년 이상",
    "년 이하",
)

# Fields that exist only (or primarily) on D198 ledger grain.
_D198_EXCLUSIVE_FIELDS = frozenset(
    {
        "detail_usage",
        "usage_class",
        "ledger_kind",
        "complex_building_kind",
        "permit_date",
    }
)

_D198_TEMPORAL_FIELDS = frozenset({"approval_date", "permit_date", "building_age_years"})

# usage as filter / group / aggregate → coverage-gated D198.
_USAGE_GRAIN_FIELDS = frozenset({"usage"})


def place_has_d198_coverage(
    question: str = "",
    *,
    gu: str | None = None,
    place: str | None = None,
) -> bool:
    """True when the question scope maps to a registered D198 sigungu table."""
    from txt2sql.domain import (
        d198_gu_for_dong,
        d198_table_for_gu,
        extract_gu,
        extract_place,
        is_busan_wide,
    )

    q = (question or "").strip()
    if q and is_busan_wide(q):
        return False
    gu_name = (gu or "").strip() or extract_gu(q)
    if not gu_name:
        place_name = (place or "").strip() or extract_place(q)
        if place_name:
            gu_name = d198_gu_for_dong(place_name, question=q)
    return bool(gu_name and d198_table_for_gu(gu_name))


def needs_d198_building_grain(question: str) -> bool:
    """Ledger grain required by NL cues (detail usage, age, explicit ledger terms)."""
    q = (question or "").strip()
    if not q:
        return False
    from txt2sql.domain import extract_detail_usages

    if extract_detail_usages(q):
        return True
    from txt2sql.domain import extract_usage_classes

    if extract_usage_classes(q):
        return True
    if any(cue in q for cue in _D198_METRIC_CUES):
        return True
    from txt2sql.intent_router import looks_like_age_question

    if looks_like_age_question(q):
        return True
    # Main usage + registered D198 place → ledger (MAIN485 gold contract).
    from txt2sql.domain import extract_usage, extract_usages

    if (extract_usage(q) or extract_usages(q)) and place_has_d198_coverage(q):
        return True
    return False


def simple_building_usage_count(question: str) -> bool:
    """구/동 + 대분류 용도 건수를 D010+A9로 둘지.

    D198 coverage가 있으면 False(→ A25). 미등록·부산 전체는 True(→ A9).
    """
    return not needs_d198_building_grain(question)


def _ir_mentions_usage(ir: QueryIR) -> bool:
    fields: set[str] = set()

    def _walk(preds: list) -> None:
        for p in preds:
            if getattr(p, "field", None):
                fields.add(p.field)
            children = getattr(p, "children", None) or []
            if children:
                _walk(list(children))

    _walk(list(ir.predicates or []))
    for a in ir.aggregations or []:
        if getattr(a, "field", None):
            fields.add(a.field)
    for d in getattr(ir, "dimensions", None) or []:
        if getattr(d, "field", None):
            fields.add(d.field)
    for s in getattr(ir, "select", None) or []:
        if isinstance(s, str):
            fields.add(s)
        elif getattr(s, "field", None):
            fields.add(s.field)
    return bool(fields & _USAGE_GRAIN_FIELDS)


def _scope_names(ir: QueryIR) -> tuple[str | None, str | None]:
    scope = ir.scope
    if scope is None:
        return None, None
    gu = None
    place = None
    kind = getattr(scope, "place_kind", None) or ""
    name = getattr(scope, "place", None)
    if kind in {"gu", "sigungu"} and name:
        gu = str(name)
    elif name:
        place = str(name)
    return gu, place


def query_ir_needs_d198(ir: QueryIR, question: str = "") -> bool:
    """QueryIR slots + coverage → D198 ledger grain (central policy)."""
    from txt2sql.domain import extract_detail_usages, extract_usage, extract_usages, extract_usage_classes
    from txt2sql.intent_router import looks_like_age_question

    fields = {p.field for p in ir.predicates if p.field}

    def _collect(preds: list) -> None:
        for p in preds:
            if getattr(p, "field", None):
                fields.add(p.field)
            children = getattr(p, "children", None) or []
            if children:
                _collect(list(children))

    _collect(list(ir.predicates or []))

    if fields & _D198_EXCLUSIVE_FIELDS:
        return True
    if any(a.field in _D198_EXCLUSIVE_FIELDS for a in ir.aggregations if a.field):
        return True
    if ir.temporal is not None and (
        ir.temporal.field in _D198_TEMPORAL_FIELDS or ir.temporal.age_years is not None
    ):
        return True
    if fields & _D198_TEMPORAL_FIELDS:
        return True
    if any(a.field in _D198_TEMPORAL_FIELDS for a in ir.aggregations if a.field):
        return True

    q = (question or "").strip()
    if q:
        if extract_detail_usages(q):
            return True
        if extract_usage_classes(q):
            return True
        if any(cue in q for cue in _D198_METRIC_CUES):
            return True
        if looks_like_age_question(q):
            return True

    # Main usage (+ optional D010 metrics) in a covered gu/dong → D198 A25.
    usage_mentioned = _ir_mentions_usage(ir) or bool(
        q and (extract_usage(q) or extract_usages(q))
    )
    if usage_mentioned:
        gu, place = _scope_names(ir)
        return place_has_d198_coverage(q, gu=gu, place=place)

    return False


def resolve_dataset_grain(ir: QueryIR, question: str = "") -> DatasetGrain:
    """Single entry point for D010 vs D198 selection — all modules must use this."""
    return "d198" if query_ir_needs_d198(ir, question) else "d010"


def grain_to_assumption(grain: DatasetGrain) -> str:
    return "d198_ledger" if grain == "d198" else "d010_gis"
