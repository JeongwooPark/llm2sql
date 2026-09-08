"""한국어 질문 → Query Contract. 원문 character span을 보존한다."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from txt2sql.query_understanding import operators as ops
from txt2sql.query_understanding.ambiguity import conflicting_ranges, unresolved_content_spans
from txt2sql.query_understanding.complexity import complexity_score
from txt2sql.query_understanding.spans import Span, dedupe_nested, find_all


class AggregationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    function: str
    field: str | None = None
    percentile: float | None = None


class RatioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    has_denominator: bool = False


class DerivedMetricRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = "divide"
    left: str
    right: str


class PercentileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    percentile: float
    field: str | None = None


class OrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: str = "desc"
    field: str | None = None


class ContractCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_complete: bool = True
    predicate_complete: bool = True
    aggregation_complete: bool = True
    grouping_complete: bool = True
    output_complete: bool = True
    ordering_complete: bool = True

    def all_ok(self) -> bool:
        return (
            self.entity_complete
            and self.predicate_complete
            and self.aggregation_complete
            and self.grouping_complete
            and self.output_complete
            and self.ordering_complete
        )


class QueryContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    places: list[Span] = Field(default_factory=list)
    metrics: list[Span] = Field(default_factory=list)
    numbers: list[Span] = Field(default_factory=list)
    boolean_ops: list[Span] = Field(default_factory=list)
    aggregations: list[Span] = Field(default_factory=list)
    order: list[Span] = Field(default_factory=list)
    limits: list[Span] = Field(default_factory=list)
    outputs: list[Span] = Field(default_factory=list)
    comparisons: list[Span] = Field(default_factory=list)
    ranges: list[Span] = Field(default_factory=list)
    groups: list[Span] = Field(default_factory=list)
    unresolved_spans: list[Span] = Field(default_factory=list)
    coverage_ratio: float = 0.0
    boolean_structure_supported: bool = True
    aggregation_complete: bool = True
    all_numeric_expressions_bound: bool = True
    all_requested_outputs_bound: bool = True
    complexity: int = 0
    operation: str | None = None
    group_fields: list[str] = Field(default_factory=list)
    aggregation_requests: list[AggregationRequest] = Field(default_factory=list)
    ratios: list[RatioRequest] = Field(default_factory=list)
    derived_metrics: list[DerivedMetricRequest] = Field(default_factory=list)
    percentile_requests: list[PercentileRequest] = Field(default_factory=list)
    output_fields: list[str] = Field(default_factory=list)
    order_requests: list[OrderRequest] = Field(default_factory=list)
    limit: int | None = None
    fixed_bins: bool = False
    wants_spatial: bool = False
    wants_basement: bool = False
    wants_count: bool = False
    wants_temporal: bool = False
    # 결과 형태. count는 단일 스칼라. 구간별·연도별은 group.
    query_kind: str = "count"
    datasets: list[str] = Field(default_factory=list)
    spatial_path: str | None = None

    def consumed(self) -> list[Span]:
        return (
            self.places
            + self.metrics
            + self.numbers
            + self.boolean_ops
            + self.aggregations
            + self.order
            + self.limits
            + self.outputs
            + self.comparisons
            + self.ranges
            + self.groups
        )

    def coverage(self) -> ContractCoverage:
        grouping_complete = (not self.groups) or bool(self.group_fields)
        ordering_complete = (not self.order) or bool(self.order_requests)
        return ContractCoverage(
            entity_complete=True,
            predicate_complete=self.boolean_structure_supported
            and self.all_numeric_expressions_bound,
            aggregation_complete=self.aggregation_complete,
            grouping_complete=grouping_complete,
            output_complete=self.all_requested_outputs_bound,
            ordering_complete=ordering_complete,
        )

    def is_sufficient(self) -> bool:
        """Router에 넘길 만큼 Contract가 닫혀 있는지."""
        return self.coverage().all_ok()


# BIN_HINTS(구간별)에 없는 그룹 표현. extract의 fixed_bins와 별도로 kind만 맞춘다.
_GROUP_KIND_RE = re.compile(r"(연도별|단위로\s*묶|준공연대별|년대별)")


_COUNTISH = (
    "몇 채",
    "몇채",
    "건수",
    "채수",
    "개수",
    "채야",
    "건물 수",
    "건물수",
    "몇 개",
    "몇개",
    "수는",
    "개가",
    "레코드 수",
    "개수만",
    "있는지",
    # 「찾아줘」는 목록 — count 단서로 쓰지 않음
)


def _explicit_count(question: str) -> bool:
    if any(k in question for k in ("중복", "차이", "평균", "비교")):
        return False
    # 목록·탐색 의도면 count 아님
    if any(
        k in question
        for k in ("찾아줘", "찾아라", "찾아주세요", "목록", "나열", "보여줘", "레코드를")
    ) and not any(
        k in question for k in ("몇 채", "몇채", "건수", "채수", "개수", "몇 개", "몇개")
    ):
        return False
    if "별" in question and any(
        k in question for k in ("보여", "집계", "나눠", "구분", "각각")
    ):
        return False
    if any(k in question for k in _COUNTISH):
        return True
    return bool(re.search(r"몇\s*(채|동|개)(?!%)", question))


def extract_contract(question: str, binding: Any | None = None) -> QueryContract:
    q = question
    if binding is None:
        from txt2sql.query_understanding.bind import bind_catalog

        binding = bind_catalog(q)
    places = _with_value(find_all(q, ops.PLACE_PATTERN, "place"), "place")
    places = _drop_false_places(q, places)
    metrics: list[Span] = []
    for text, field in ops.METRIC_MAP.items():
        for span in find_all(q, re.escape(text), "metric"):
            span.value = field
            metrics.append(span)
    metrics.extend(_categorical_metrics(q))
    metrics = dedupe_nested(metrics)
    # 「사용승인일이 기록된 … 평균 건축연령」→ 집계 메트릭은 연령, 승인일은 존재 필터
    if any(m.value == "building_age_years" for m in metrics) and any(
        k in q for k in ("기록된", "등록된", "있는")
    ):
        metrics = [m for m in metrics if m.value != "approval_date"]
    if "기초구역" in q or "산업단지" in q:
        for span in metrics:
            if span.value == "gross_floor_area_m2":
                span.value = "area_m2"

    aggregations: list[Span] = []
    for text, fn in ops.AGG_MAP.items():
        for span in find_all(q, re.escape(text), "aggregation"):
            span.value = fn
            aggregations.append(span)

    boolean_ops: list[Span] = []
    for pattern in ops.AND_PATTERNS:
        boolean_ops.extend(find_all(q, pattern, "and"))
    for pattern in ops.OR_PATTERNS:
        boolean_ops.extend(find_all(q, pattern, "or"))
    for pattern in ops.NOT_PATTERNS:
        boolean_ops.extend(find_all(q, pattern, "not"))
    boolean_ops = dedupe_nested(boolean_ops)

    ranges = _extract_ranges(q)
    order = _extract_order(q)
    outputs = _extract_outputs(q)
    comparisons = _extract_comparisons(q)
    groups: list[Span] = []
    for hint in ops.GROUP_HINTS:
        for span in find_all(q, re.escape(hint), "group"):
            span.value = ops.GROUP_FIELD_MAP.get(hint, hint)
            groups.append(span)
    groups = dedupe_nested(groups)
    limits = _extract_limits(q, places)
    numbers = _extract_numbers(q, ranges, places=places, limits=limits)
    if "기초구역" in q or "산업단지" in q:
        for span in numbers + ranges:
            if span.meta.get("field") == "gross_floor_area_m2":
                span.meta["field"] = "area_m2"

    percentile_requests = _extract_percentiles(q)
    ratios = _extract_ratios(q)
    derived_metrics = _extract_derived(q)
    fixed_bins = any(h in q for h in ops.BIN_HINTS)
    wants_spatial = any(
        h in q
        for h in (
            "안에",
            "내에",
            "내부",
            "안쪽",
            "주변",
            "이내",
            "반경",
            "버퍼",
            "교차",
            "겹치",
            "맞닿",
            "경계 안",
            "경계안",
        )
    ) or bool(getattr(binding, "spatial_path", None))
    wants_basement = bool(re.search(r"지하(?!철)", q))
    wants_count = _explicit_count(q)
    from txt2sql.query_understanding.temporal import parse_temporal_filters
    from txt2sql.domain import looks_like_age_question

    wants_temporal = bool(parse_temporal_filters(q)) or looks_like_age_question(q)

    contract = QueryContract(
        question=q,
        places=places,
        metrics=metrics,
        numbers=numbers,
        boolean_ops=boolean_ops,
        aggregations=aggregations,
        order=order,
        limits=limits,
        outputs=outputs,
        comparisons=comparisons,
        ranges=ranges,
        groups=groups,
        percentile_requests=percentile_requests,
        ratios=ratios,
        derived_metrics=derived_metrics,
        fixed_bins=fixed_bins,
        wants_spatial=wants_spatial,
        wants_basement=wants_basement,
        wants_count=wants_count,
        wants_temporal=wants_temporal,
        datasets=[item.id for item in getattr(binding, "datasets", [])],
        spatial_path=(
            f"{binding.spatial_path.left_entity}:{binding.spatial_path.op}:{binding.spatial_path.right_entity}"
            if getattr(binding, "spatial_path", None) is not None
            else None
        ),
    )
    consumed = contract.consumed()
    conflicts = conflicting_ranges(ranges)
    leftover = unresolved_content_spans(q, consumed)
    contract.unresolved_spans = leftover + conflicts
    contract.boolean_structure_supported = not any(
        item.kind == "or" and not _or_has_two_operands(q, item) for item in boolean_ops
    )
    contract.aggregation_complete = _aggregation_complete(contract)
    threshold_numbers = [
        item for item in numbers if item.meta.get("role", "threshold") == "threshold"
    ]
    contract.all_numeric_expressions_bound = all(
        item.meta.get("field") for item in threshold_numbers
    ) or not threshold_numbers
    if ranges:
        contract.all_numeric_expressions_bound = contract.all_numeric_expressions_bound and all(
            span.meta.get("low") is not None
            and span.meta.get("high") is not None
            and span.meta.get("field")
            for span in ranges
        )
    _bind_or_operands(q, boolean_ops)
    _bind_not_operands(q, boolean_ops, metrics)
    contract.all_requested_outputs_bound = _outputs_bound(outputs)
    contract.coverage_ratio = _slot_coverage(contract)
    contract.complexity = complexity_score(
        boolean_ops=boolean_ops,
        aggregations=aggregations,
        comparisons=comparisons,
        ranges=ranges,
        groups=groups,
    )
    if conflicts:
        contract.boolean_structure_supported = False
        contract.coverage_ratio = min(contract.coverage_ratio, 0.99)
    _finalize_requests(contract)
    return contract


_FALSE_PLACE_CONDITION_RE = re.compile(
    r"(?:이상|이하|초과|미만|넘는|작|크|높|낮).{0,2}이면?$|"
    r".*(?:구조|용도|시설|생활|업무|판매|숙박|공장|창고|주택)이면?$|"
    r".*(?:구조|용도)이$"
)


def _drop_false_places(question: str, spans: list[Span]) -> list[Span]:
    """공동주택·시설·조건어미·용도 접두를 장소로 오인하지 않는다."""
    from txt2sql.domain import (
        DETAIL_USAGE_ALIASES,
        STRUCTURE_ALIASES,
        USAGE_ALIASES,
    )

    kept: list[Span] = []
    semantic_phrases = tuple(ops.GROUP_HINTS) + tuple(ops.METRIC_MAP)
    domain_phrases = (
        tuple(USAGE_ALIASES)
        + tuple(DETAIL_USAGE_ALIASES)
        + tuple(STRUCTURE_ALIASES)
        + ("철근콘크리트구조", "철골철근콘크리트구조", "제1종근린생활시설", "제2종근린생활시설")
    )
    for span in spans:
        after = question[span.end : span.end + 3]
        if after.startswith(("주택", "시설", "차", "력", "원", "사", "설")):
            continue
        if span.text in {"공동", "동"}:
            continue
        if _FALSE_PLACE_CONDITION_RE.match(span.text):
            continue
        if any(
            span.text != phrase
            and span.text in phrase
            and phrase in question
            for phrase in semantic_phrases
        ):
            continue
        # 용도·구조 표현의 접두(업무시⊂업무시설, 제2종근린생활시⊂…시설)
        # 질문에 완전형이 없어도 도메인 사전 접두면 장소로 보지 않는다.
        if any(
            phrase.startswith(span.text) and phrase != span.text and len(span.text) >= 3
            for phrase in domain_phrases
        ):
            continue
        # 바로 뒤에 시설명 잔여 음절이 이어지면 장소가 아니다.
        if after.startswith(("설", "장", "택", "축")):
            continue
        if any(alias in span.text for alias in STRUCTURE_ALIASES):
            continue
        if any(
            alias in span.text and len(alias) >= 2
            for alias in ("이상", "이하", "초과", "미만", "구조")
        ):
            continue
        kept.append(span)
    return kept


def _with_value(spans: list[Span], key: str) -> list[Span]:
    for span in spans:
        span.value = span.text
        span.meta[key] = span.text
    return spans


def _extract_ranges(question: str) -> list[Span]:
    found: list[Span] = []
    for pattern in ops.RANGE_PATTERNS:
        for match in re.finditer(pattern, question):
            gd = match.groupdict()
            meta: dict[str, Any] = {
                "low": float(match.group("lo")),
                "high": float(match.group("hi")),
                "unit": gd.get("u2") or gd.get("u1"),
                "lo_rel": gd.get("lo_rel") or "이상",
                "hi_rel": gd.get("hi_rel")
                or ("사이" if "사이" in match.group(0) else "까지"),
            }
            field = _nearest_metric(question, match.start())
            if "층" in match.group(0) and "지하" not in match.group(0):
                field = "ground_floors"
            elif "층" in match.group(0) and "지하" in question[max(0, match.start() - 4) : match.start()]:
                field = "basement_floors"
            if field:
                meta["field"] = field
            found.append(
                Span(
                    kind="range",
                    text=match.group(0),
                    start=match.start(),
                    end=match.end(),
                    value=(meta["low"], meta["high"]),
                    meta=meta,
                )
            )
    return dedupe_nested(found)


_THRESHOLD_OP_RE = re.compile(r"^\s*(이상|이하|초과|미만|넘는|보다\s*큰|보다\s*작|보다\s*높|보다\s*낮)")
_THRESHOLD_OP_MAP = {
    "이상": "gte",
    "이하": "lte",
    "초과": "gt",
    "미만": "lt",
    "넘는": "gt",
    "보다 큰": "gt",
    "보다 작": "lt",
    "보다 높": "gt",
    "보다 낮": "lt",
}
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")


def _number_role(question: str, span: Span, *, places: list[Span], limits: list[Span]) -> str:
    """Classify numeric spans before metric binding."""
    if any(place.start <= span.start and span.end <= place.end for place in places):
        return "identifier"
    if any(lim.start <= span.start and span.end <= lim.end for lim in limits):
        return "limit"
    before = question[max(0, span.start - 4) : span.start]
    after = question[span.end : span.end + 8]
    unit = str(span.meta.get("unit") or "")
    raw = str(int(span.value)) if float(span.value).is_integer() else str(span.value)
    if before.endswith("제") and ("종" in after or after.startswith("종")):
        return "identifier"
    # 건축 경과년수(20년 넘고) — metric threshold가 아님
    if after.startswith("년") and not _YEAR_RE.match(raw):
        return "age"
    if _YEAR_RE.match(raw) and (
        after.startswith("년") or "사용승인" in question or "허가" in question
    ):
        return "year"
    if unit in {"m", "미터", "km"} and any(
        k in question for k in ("이내", "주변", "반경", "버퍼", "근처", "거리")
    ):
        return "distance"
    if after.startswith("%") or after.startswith("백분위") or after.startswith("분위"):
        return "percentile"
    if re.search(r"상위\s*$", question[: span.start]) and re.search(
        r"^\s*(개|곳|채)(?!%)", after
    ):
        return "limit"
    if re.search(r"^\s*(개|곳|채)(?!%)", after) and "상위" in question[: span.start + 1]:
        return "limit"
    return "threshold"


def _threshold_operator(question: str, span: Span) -> str | None:
    after = question[span.end : span.end + 12]
    match = _THRESHOLD_OP_RE.match(after)
    if not match:
        return None
    token = re.sub(r"\s+", " ", match.group(1).strip())
    for key, op in _THRESHOLD_OP_MAP.items():
        if token.startswith(key):
            return op
    return None


def _extract_numbers(
    question: str,
    ranges: list[Span],
    *,
    places: list[Span] | None = None,
    limits: list[Span] | None = None,
) -> list[Span]:
    places = places or []
    limits = limits or []
    found: list[Span] = []
    for match in re.finditer(ops.NUMBER_UNIT_PATTERN, question):
        span = Span(
            kind="number",
            text=match.group(0),
            start=match.start(),
            end=match.end(),
            value=float(match.group("num")),
            meta={"unit": match.group("unit"), "field": None},
        )
        if any(rng.contains(span) for rng in ranges):
            continue
        if any(lim.start <= span.start and span.end <= lim.end for lim in limits):
            continue
        role = _number_role(question, span, places=places, limits=limits)
        span.meta["role"] = role
        if role == "threshold":
            op = _threshold_operator(question, span)
            if op:
                span.meta["operator"] = op
        found.append(span)
    _bind_numbers_greedily(question, found)
    return found


def _extract_order(question: str) -> list[Span]:
    found: list[Span] = []
    for text in ops.SORT_ASC:
        for span in find_all(question, re.escape(text), "order"):
            if question[: span.start].rstrip().endswith("보다"):
                continue
            span.value = "asc"
            found.append(span)
    for text in ops.SORT_DESC:
        for span in find_all(question, re.escape(text), "order"):
            if question[: span.start].rstrip().endswith("보다"):
                continue
            span.value = "desc"
            found.append(span)
    return dedupe_nested(found)


def _extract_limits(question: str, places: list[Span] | None = None) -> list[Span]:
    found: list[Span] = []
    places = places or []
    for match in re.finditer(ops.LIMIT_PATTERN, question):
        if question[match.end() : match.end() + 1] == "%":
            continue
        start, end = match.start(), match.end()
        if any(item.start <= start and end <= item.end for item in places):
            continue
        found.append(
            Span(
                kind="limit",
                text=match.group(0),
                start=start,
                end=end,
                value=int(match.group("n")),
            )
        )
    return found


def _extract_outputs(question: str) -> list[Span]:
    found: list[Span] = []
    for hint in ops.OUTPUT_HINTS:
        for span in find_all(question, re.escape(hint), "output"):
            span.value = ops.OUTPUT_FIELD_MAP.get(hint, hint)
            span.meta["field"] = span.value
            found.append(span)
    return dedupe_nested(found)


def _extract_comparisons(question: str) -> list[Span]:
    found: list[Span] = []
    for pattern in ops.COMPARE_PATTERNS:
        for match in re.finditer(pattern, question):
            left = match.groupdict().get("left")
            right = match.groupdict().get("right")
            rel = "lt" if any(k in match.group(0) for k in ("작", "낮")) else "gt"
            scale_raw = match.groupdict().get("scale")
            value = {
                "left": ops.METRIC_MAP.get(left or "", left),
                "op": rel,
                "right": ops.METRIC_MAP.get(right or "", right),
            }
            if scale_raw:
                value["scale"] = float(scale_raw)
            found.append(
                Span(
                    kind="comparison",
                    text=match.group(0),
                    start=match.start(),
                    end=match.end(),
                    value=value,
                    meta={"left": left, "right": right, "op": rel, "scale": scale_raw},
                )
            )
    return dedupe_nested(found)


def _nearest_metric(question: str, index: int) -> str | None:
    candidates: list[tuple[int, int, int, str]] = []
    for text, field in ops.METRIC_MAP.items():
        pos = question.rfind(text, 0, index + 1)
        if pos < 0:
            continue
        dist = index - pos
        if dist > 16:
            continue
        candidates.append((pos, pos + len(text), len(text), field))
    if not candidates:
        return None
    kept = [
        item
        for item in candidates
        if not any(
            item[0] >= other[0] and item[1] <= other[1] and other[2] > item[2]
            for other in candidates
        )
    ]
    kept.sort(key=lambda item: (index - item[0], -item[2]))
    return kept[0][3]


def _or_has_two_operands(question: str, span: Span) -> bool:
    left_tok, right_tok = _or_operand_tokens(question, span)
    return bool(left_tok and right_tok)


def _or_operand_tokens(question: str, span: Span) -> tuple[str, str]:
    left = re.findall(r"[가-힣A-Za-z0-9]+", question[: span.start])
    right = re.findall(r"[가-힣A-Za-z0-9]+", question[span.end :])
    skip = {"그리고", "이면서", "중", "그", "이", "저", "및"}
    left_tok = next((t for t in reversed(left) if t not in skip), "")
    right_tok = next((t for t in right if t not in skip), "")
    return left_tok, right_tok


def _bind_or_operands(question: str, boolean_ops: list[Span]) -> None:
    for span in boolean_ops:
        if span.kind != "or":
            continue
        left_tok, right_tok = _or_operand_tokens(question, span)
        span.meta["left"] = left_tok
        span.meta["right"] = right_tok
        span.value = (left_tok, right_tok)


def _bind_not_operands(
    question: str,
    boolean_ops: list[Span],
    metrics: list[Span],
) -> None:
    """Attach NOT scope to preceding OR operands or nearest categorical metrics."""
    categorical = [
        m
        for m in metrics
        if m.value in {"usage", "detail_usage", "structure", "violation_status"}
        and str(m.text) not in {"용도", "구조", "세부용도"}
    ]
    for span in boolean_ops:
        if span.kind != "not":
            continue
        # Prefer OR immediately before 제외/빼고
        prior_or = None
        for other in boolean_ops:
            if other.kind == "or" and other.end <= span.start:
                if prior_or is None or other.end > prior_or.end:
                    prior_or = other
        operands: list[str] = []
        fields: list[str] = []
        if prior_or is not None and span.start - prior_or.end <= 12:
            left = str(prior_or.meta.get("left") or "")
            right = str(prior_or.meta.get("right") or "")
            operands = [t for t in (left, right) if t]
            nearby = [
                m
                for m in categorical
                if m.end <= span.start and m.start >= max(0, prior_or.start - 24)
            ]
            fields = [str(m.value) for m in nearby]
            span.meta["scopes_or"] = True
        else:
            nearby = [
                m
                for m in categorical
                if m.end <= span.start and span.start - m.end <= 24
            ]
            if nearby:
                nearest = max(nearby, key=lambda m: m.end)
                operands = [str(nearest.text)]
                fields = [str(nearest.value)]
        span.meta["operands"] = operands
        span.meta["negated_fields"] = list(dict.fromkeys(fields))
        if operands:
            span.value = tuple(operands)


def _outputs_bound(outputs: list[Span]) -> bool:
    if not outputs:
        return True
    return all(bool(item.value) for item in outputs)


def _bind_numbers_greedily(question: str, numbers: list[Span]) -> None:
    used_spans: set[tuple[int, int]] = set()
    unit_fields = {
        "층": "ground_floors",
        "m": "height_m",
        "미터": "height_m",
        "㎡": "gross_floor_area_m2",
        "m2": "gross_floor_area_m2",
        "평": "gross_floor_area_m2",
    }
    for span in numbers:
        role = span.meta.get("role", "threshold")
        if role != "threshold":
            span.meta["field"] = None
            continue
        field, metric_span = _nearest_unused_metric(question, span.start, used_spans)
        if not field:
            field = unit_fields.get(str(span.meta.get("unit") or ""))
        if str(span.meta.get("unit") or "") == "층":
            window = question[max(0, span.start - 6) : span.start]
            if re.search(r"지하(?!철)", window):
                field = "basement_floors"
            elif "지상" in window:
                field = "ground_floors"
        span.meta["field"] = field
        if not span.meta.get("operator"):
            op = _threshold_operator(question, span)
            if op:
                span.meta["operator"] = op
        if metric_span is not None:
            used_spans.add(metric_span)


def _nearest_unused_metric(
    question: str,
    index: int,
    used: set[tuple[int, int]],
) -> tuple[str | None, tuple[int, int] | None]:
    candidates: list[tuple[int, int, int, str]] = []
    for text, field in ops.METRIC_MAP.items():
        pos = question.rfind(text, 0, index + 1)
        if pos < 0:
            continue
        dist = index - pos
        if dist > 24:
            continue
        span = (pos, pos + len(text))
        if span in used:
            continue
        candidates.append((pos, pos + len(text), len(text), field))
    if not candidates:
        return None, None
    kept = [
        item
        for item in candidates
        if not any(
            item[0] >= other[0] and item[1] <= other[1] and other[2] > item[2]
            for other in candidates
        )
    ]
    kept.sort(key=lambda item: (index - item[0], -item[2]))
    best = kept[0]
    return best[3], (best[0], best[1])


def _aggregation_complete(contract: QueryContract) -> bool:
    if not contract.aggregations:
        return True
    fns = {item.value for item in contract.aggregations}
    if "avg" in fns or "sum" in fns or "min" in fns or "max" in fns or "median" in fns or "stddev" in fns or "variance" in fns:
        return bool(contract.metrics) or bool(contract.groups)
    return True


def _slot_coverage(contract: QueryContract) -> float:
    slots = (
        contract.places
        + contract.metrics
        + contract.aggregations
        + contract.boolean_ops
        + contract.ranges
        + contract.comparisons
        + contract.order
        + contract.limits
        + contract.groups
        + contract.outputs
    )
    if contract.unresolved_spans:
        return 0.0 if not slots else round(
            max(0.0, (len(slots) - len(contract.unresolved_spans)) / max(len(slots), 1)),
            4,
        )
    if not slots:
        return 1.0
    bound = len(slots)
    if not contract.all_numeric_expressions_bound:
        bound -= 1
    if not contract.aggregation_complete:
        bound -= 1
    if not contract.all_requested_outputs_bound:
        bound -= 1
    return round(max(0.0, bound / len(slots)), 4)


def _extract_percentiles(question: str) -> list[PercentileRequest]:
    found: list[PercentileRequest] = []
    field = _nearest_metric(question, len(question))
    for match in re.finditer(r"상위\s*(\d+(?:\.\d+)?)\s*%", question):
        pct = float(match.group(1))
        found.append(
            PercentileRequest(percentile=max(0.0, min(1.0, 1.0 - pct / 100.0)), field=field)
        )
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*백분위", question):
        pct = float(match.group(1))
        if pct > 1:
            pct = pct / 100.0
        found.append(PercentileRequest(percentile=max(0.0, min(1.0, pct)), field=field))
    # 「25%, 50%, 75% 분위수」·「25·50·75% 분위」 다중 분위수
    if any(k in question for k in ("분위수", "분위", "퍼센타일", "percentile")):
        seen: set[float] = {round(float(item.percentile), 6) for item in found}
        for match in re.finditer(r"(\d+(?:\.\d+)?)\s*%", question):
            pct = float(match.group(1))
            if pct > 100:
                continue
            if pct > 1:
                pct = pct / 100.0
            key = round(max(0.0, min(1.0, pct)), 6)
            if key in seen:
                continue
            seen.add(key)
            found.append(PercentileRequest(percentile=key, field=field))
    return found


def _extract_ratios(question: str) -> list[RatioRequest]:
    if not any(h in question for h in ops.RATIO_HINTS):
        return []
    hits = sum(question.count(h) for h in ("비율", "퍼센트", "몇%"))
    hits += len(re.findall(r"(?<!\d)%", question))
    n = 2 if hits >= 2 or question.count("비율") >= 2 else 1
    has_den = "중" in question or "/" in question or "대비" in question
    return [RatioRequest(has_denominator=has_den) for _ in range(n)]


def _extract_derived(question: str) -> list[DerivedMetricRequest]:
    if "대비" in question or "비(" in question or re.search(r"건축면적\s*/\s*연면적", question):
        if "건축면적" in question and "연면적" in question:
            return [
                DerivedMetricRequest(
                    kind="divide",
                    left="building_area_m2",
                    right="gross_floor_area_m2",
                )
            ]
    return []


def _is_grouped_count_question(question: str, group_fields: list[str]) -> bool:
    """GROUP BY + 건수 질의 — 스칼라 count가 아니다."""
    if not group_fields:
        return False
    if any(k in question for k in ("집계", "보여", "나눠", "구분", "각각", "분포")):
        return True
    if "별" in question and any(
        k in question for k in ("건물 수", "건수", "채수", "몇", "수를")
    ):
        return True
    return False


def _metric_field_near(question: str, index: int, metrics: list[Span]) -> str | None:
    """Pick numeric/metric field closest to an aggregation token (prefer after)."""
    numeric_fields = {
        "height_m",
        "gross_floor_area_m2",
        "building_area_m2",
        "site_area_m2",
        "ground_floors",
        "basement_floors",
        "building_coverage_ratio",
        "floor_area_ratio",
        "area_m2",
    }
    candidates = [
        m
        for m in metrics
        if m.value in numeric_fields
    ]
    if not candidates:
        return None
    # Prefer metric immediately after the aggregation word (평균 높이).
    after = [m for m in candidates if m.start >= index]
    if after:
        after.sort(key=lambda m: (m.start - index, -len(m.text)))
        return str(after[0].value)
    before = [m for m in candidates if m.end <= index]
    if before:
        before.sort(key=lambda m: (index - m.end, -len(m.text)))
        return str(before[0].value)
    return _nearest_metric(question, index)


def _finalize_requests(contract: QueryContract) -> None:
    q = contract.question
    contract.group_fields = [
        str(item.value) for item in contract.groups if item.value
    ]
    seen_fn: list[str] = []
    for item in contract.aggregations:
        fn = str(item.value or "")
        if fn and fn not in seen_fn:
            seen_fn.append(fn)
            field = _metric_field_near(q, item.end, contract.metrics)
            if not field and contract.metrics:
                # Fall back to first numeric metric, not categorical usage.
                field = next(
                    (
                        str(m.value)
                        for m in contract.metrics
                        if m.value
                        and m.value
                        not in {
                            "usage",
                            "detail_usage",
                            "structure",
                            "violation_status",
                            "special_land",
                        }
                    ),
                    None,
                )
            contract.aggregation_requests.append(
                AggregationRequest(function=fn, field=str(field) if field else None)
            )
    if contract.wants_count and "count" not in seen_fn:
        contract.aggregation_requests.append(AggregationRequest(function="count"))
    # 「평균 … 과 건수」처럼 평균이 있어도 명시 건수는 aggregation에 유지
    if (
        "count" not in seen_fn
        and any(k in q for k in ("건수", "채수", "개수", "건물 수", "건물수"))
        and not any(item.function == "count" for item in contract.aggregation_requests)
    ):
        contract.aggregation_requests.append(AggregationRequest(function="count"))
        seen_fn.append("count")
    if (
        _is_grouped_count_question(q, contract.group_fields)
        and "count" not in seen_fn
        and not seen_fn
    ):
        contract.aggregation_requests.append(AggregationRequest(function="count"))
    for item in contract.percentile_requests:
        contract.aggregation_requests.append(
            AggregationRequest(
                function="percentile",
                field=item.field,
                percentile=item.percentile,
            )
        )
    contract.output_fields = [
        str(item.value) for item in contract.outputs if item.value
    ]
    for item in contract.order:
        contract.order_requests.append(
            OrderRequest(direction=str(item.value or "desc"))
        )
    if (
        not contract.order_requests
        and not contract.percentile_requests
        and any(h in q for h in ops.RANK_HINTS)
        and "백분위" not in q
        and not re.search(r"상위\s*\d+\s*%", q)
    ):
        contract.order_requests.append(OrderRequest(direction="desc"))
    if contract.limits:
        contract.limit = int(contract.limits[-1].value)
    if contract.ratios:
        contract.operation = "ratio"
    elif contract.percentile_requests:
        contract.operation = "percentile"
    elif contract.group_fields and (contract.order_requests or contract.limit):
        contract.operation = "group_rank"
    elif contract.order_requests or (contract.limit and any(h in q for h in ops.RANK_HINTS)):
        contract.operation = "rank"
    elif contract.aggregation_requests:
        contract.operation = "aggregate"
    elif _is_grouped_count_question(q, contract.group_fields):
        contract.operation = "aggregate"
    elif contract.output_fields:
        contract.operation = "list"
    else:
        # 건수 단서가 없으면 기본 count. 목록은 output_fields가 있을 때만.
        contract.operation = "count"
    contract.query_kind = _infer_query_kind(contract)


def _looks_like_group(contract: QueryContract) -> bool:
    """구간·그룹 질의. '건수'가 있어도 스칼라 count가 아니다."""
    if contract.fixed_bins or contract.operation == "group_rank":
        return True
    if contract.group_fields and contract.aggregation_requests:
        return True
    if _is_grouped_count_question(
        contract.question or "", contract.group_fields
    ):
        return True
    return bool(_GROUP_KIND_RE.search(contract.question or ""))


def _infer_query_kind(contract: QueryContract) -> str:
    if contract.ratios:
        return "ratio"
    if contract.operation == "percentile":
        return "scalar"
    if _looks_like_group(contract):
        return "group"
    if contract.operation == "rank":
        return "rank"
    if contract.operation == "list":
        return "list"
    fns = {item.function for item in contract.aggregation_requests if item.function}
    # avg·percentile과 count가 같이 있으면 한 행 다중 지표(scalar)
    if fns - {"count"}:
        return "scalar"
    if contract.wants_count or (contract.operation == "aggregate" and fns <= {"count"}):
        return "count"
    return contract.operation or "count"


def merge_contract(
    prev: QueryContract,
    delta: QueryContract,
    binding: Any | None = None,
) -> QueryContract:
    """직전 Contract에 후속 Δ를 병합한다. 직전 SQL을 재사용하지 않는다.

    결합 문장에 앞 질문의 '몇 채'가 남아 extract가 count로 기울 수 있다.
    Δ가 구간·목록이면 그걸 이긴다.
    """
    prev_q = (prev.question or "").strip()
    delta_q = (delta.question or "").strip()
    combined = prev_q
    if delta_q and delta_q not in prev_q:
        combined = f"{prev_q} {delta_q}".strip()
    merged = extract_contract(combined, binding=binding)
    if not delta.places and prev.places and not merged.places:
        merged.places = list(prev.places)
    if delta.fixed_bins or delta.group_fields or _GROUP_KIND_RE.search(
        delta.question or ""
    ):
        merged.query_kind = "group"
        merged.wants_count = False
    elif delta.wants_count:
        merged.wants_count = True
        merged.query_kind = "count"
        merged.operation = "aggregate"
    elif delta.query_kind and delta.query_kind != "count":
        merged.query_kind = delta.query_kind
        merged.wants_count = False
        if delta.query_kind == "list":
            merged.operation = "list"
    elif delta.query_kind and delta.query_kind != prev.query_kind:
        merged.query_kind = delta.query_kind
    if prev.datasets and not merged.datasets:
        merged.datasets = list(prev.datasets)
    return merged


def _categorical_metrics(question: str) -> list[Span]:
    from txt2sql.domain import (
        USAGE_ALIASES,
        extract_special_land,
        extract_structures,
    )

    found: list[Span] = []
    occupied: list[tuple[int, int]] = []
    for alias in sorted(USAGE_ALIASES, key=len, reverse=True):
        start = 0
        while True:
            i = question.find(alias, start)
            if i < 0:
                break
            end = i + len(alias)
            if not any(not (end <= a or i >= b) for a, b in occupied):
                occupied.append((i, end))
                found.append(
                    Span(
                        kind="metric",
                        text=alias,
                        start=i,
                        end=end,
                        value="usage",
                        meta={"field": "usage", "canonical": USAGE_ALIASES[alias]},
                    )
                )
            start = end
    for alias, pattern in extract_structures(question):
        i = question.find(alias)
        if i < 0:
            continue
        found.append(
            Span(
                kind="metric",
                text=alias,
                start=i,
                end=i + len(alias),
                value="structure",
                meta={"field": "structure", "pattern": pattern},
            )
        )
    for hint in ("위반건축물", "위반건축", "위반 건축"):
        i = question.find(hint)
        if i < 0:
            continue
        suffix = question[i + len(hint) : i + len(hint) + 5].replace(" ", "")
        if suffix.startswith("여부별"):
            break
        found.append(
            Span(
                kind="metric",
                text=hint,
                start=i,
                end=i + len(hint),
                value="violation_status",
                meta={"field": "violation_status"},
            )
        )
        break
    special = extract_special_land(question)
    if special is not None:
        label, _sql = special
        i = question.find(label) if label in question else 0
        found.append(
            Span(
                kind="metric",
                text=label,
                start=max(0, i),
                end=max(0, i) + len(label),
                value="special_land",
                meta={"field": "special_land"},
            )
        )
    return found

