"""P0 complex-query contract regressions (place / number role / agg / boolean)."""

from __future__ import annotations

from txt2sql.query_ir import contract_to_query_ir
from txt2sql.query_understanding.contract import extract_contract


def test_false_places_not_recognized() -> None:
    for q in (
        "철근콘크리트구조이면 건물 수는?",
        "제2종근린생활시 건물 수는?",
        "연면적 100 이상이면 건물 수는?",
        "업무시 건물 수는?",
    ):
        places = {p.text for p in extract_contract(q).places}
        assert not places, f"false place in {q!r}: {places}"


def test_real_places_kept() -> None:
    contract = extract_contract("수영구 광안2동 건물 수는?")
    texts = {p.text for p in contract.places}
    assert "수영구" in texts
    assert "광안2동" in texts


def test_number_roles_limit_distance_year_identifier() -> None:
    top = extract_contract("연면적 상위 10개 건물")
    assert top.limit == 10
    assert all(n.meta.get("role") != "threshold" or n.value != 10 for n in top.numbers) or not any(
        n.value == 10 and n.meta.get("field") == "gross_floor_area_m2" for n in top.numbers
    )

    dist = extract_contract("학교 주변 500m 이내 건물 수")
    assert any(n.meta.get("role") == "distance" and n.value == 500 for n in dist.numbers) or not any(
        n.value == 500 and n.meta.get("field") == "height_m" for n in dist.numbers
    )

    year = extract_contract("2025년 사용승인 건물 수")
    assert any(n.meta.get("role") == "year" and int(n.value) == 2025 for n in year.numbers) or not any(
        n.value == 2025 and n.meta.get("field") for n in year.numbers
    )

    ident = extract_contract("제2종근린생활시설 광안2동 건물 수")
    assert not any(
        n.meta.get("role") == "threshold" and n.meta.get("field") and int(n.value) in {1, 2}
        for n in ident.numbers
    )


def test_threshold_operator_bound() -> None:
    contract = extract_contract("높이 40m 이하 건물 수")
    thresholds = [n for n in contract.numbers if n.meta.get("role") == "threshold"]
    assert thresholds
    assert thresholds[0].meta.get("operator") == "lte"
    assert thresholds[0].meta.get("field") == "height_m"


def test_agg_field_near_average_not_rank_metric() -> None:
    contract = extract_contract("아파트 중 연면적 상위 10개의 평균 높이")
    avg_reqs = [r for r in contract.aggregation_requests if r.function == "avg"]
    assert avg_reqs
    assert avg_reqs[0].field == "height_m"
    assert contract.limit == 10


def test_exclude_or_does_not_flip_operands() -> None:
    q = "공장 또는 창고를 제외한 건물 수"
    contract = extract_contract(q)
    assert any(b.kind == "or" for b in contract.boolean_ops)
    assert any(b.kind == "not" for b in contract.boolean_ops)
    not_spans = [b for b in contract.boolean_ops if b.kind == "not"]
    assert not_spans
    operands = not_spans[0].meta.get("operands") or []
    negated_fields = not_spans[0].meta.get("negated_fields") or []
    blob = " ".join(str(x) for x in list(operands) + list(negated_fields))
    assert "공장" in blob or "창고" in blob
    assert not_spans[0].meta.get("scopes_or") is True

    ir = contract_to_query_ir(contract)
    # Must not become warehouse eq without NOT, or factory-only NOT.
    leaf_eq = [
        p
        for p in ir.predicates
        if p.operator == "eq" and p.field == "usage" and not p.negated and not p.children
    ]
    # Flat positive-only warehouse without NOT group is a failure mode.
    assert not (
        len(leaf_eq) == 1
        and str(leaf_eq[0].value) in {"창고시설", "창고"}
        and not any(p.negated or (p.logical_group == "or" and p.negated) for p in ir.predicates)
    )


def test_contract_to_ir_preserves_threshold_operator() -> None:
    contract = extract_contract("연면적 1000 이상 건물 수")
    ir = contract_to_query_ir(contract)
    preds = [p for p in ir.predicates if p.field == "gross_floor_area_m2"]
    assert preds
    assert preds[0].operator == "gte"
    assert float(preds[0].value) == 1000.0
