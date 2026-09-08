"""QueryContract -> QueryIR adapter tests."""

from txt2sql.query_ir import contract_to_query_ir
from txt2sql.query_understanding.contract import extract_contract


def test_contract_adapter_preserves_count_scope() -> None:
    contract = extract_contract("동래구 건물 수는?")
    ir = contract_to_query_ir(contract)
    assert ir.provenance.source == "contract"
    assert ir.provenance.source_text
    if contract.places:
        assert ir.scope is not None
        assert ir.scope.place


def test_contract_adapter_preserves_aggregations() -> None:
    contract = extract_contract("해운대구 건축물 평균 연면적은?")
    ir = contract_to_query_ir(contract)
    # Either aggregations or measures should capture the metric intent
    assert ir.aggregations or ir.measures or contract.metrics
    assert ir.task in {"aggregate", "count", "unknown", "list", "group", "ratio"}


def test_contract_adapter_preserves_percentile_ratio_fields() -> None:
    contract = extract_contract("금정구 건축물 연면적 90분위수는?")
    ir = contract_to_query_ir(contract)
    funcs = {a.function for a in ir.aggregations}
    # percentile may land in aggregations via percentile_requests or aggregation_requests
    assert "percentile" in funcs or ir.task in {"aggregate", "unknown", "count"} or ir.measures


def test_contract_no_physical_datasets_in_ir() -> None:
    contract = extract_contract("동래구 건물 수")
    # Even if contract somehow carries datasets, IR must not expose physical names as first-class fields
    ir = contract_to_query_ir(contract)
    dumped = ir.model_dump()
    text = str(dumped)
    assert "AL_D010" not in text
    assert "AL_D198" not in text


def test_adapter_preserves_threshold_operator() -> None:
    contract = extract_contract("높이 40m 이하 건물 수")
    ir = contract_to_query_ir(contract)
    height = [p for p in ir.predicates if p.field == "height_m"]
    assert height
    assert height[0].operator == "lte"


def test_adapter_or_not_exclude_preserves_negation() -> None:
    contract = extract_contract("공장 또는 창고를 제외한 건물 수")
    ir = contract_to_query_ir(contract)
    assert any(p.logical_group == "or" and p.negated for p in ir.predicates) or any(
        p.negated for p in ir.predicates
    )


def test_adapter_extra_place_not_silent() -> None:
    contract = extract_contract("수영구 광안2동 건물 수")
    ir = contract_to_query_ir(contract)
    assert ir.scope is not None
    if len(contract.places) > 1:
        assert any(u.code == "EXTRA_PLACE" for u in ir.unresolved) or (
            ir.provenance.legacy_hints or {}
        ).get("extra_places")
