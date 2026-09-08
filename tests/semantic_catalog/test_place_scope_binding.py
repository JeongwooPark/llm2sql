"""PlaceScopeBinding API tests (Phase 2)."""

from __future__ import annotations

from txt2sql.place_scope import building_place_predicate
from txt2sql.semantic_catalog.place_scope import (
    PlaceScopeContext,
    PlaceScopeBinding,
    resolve_place_scope,
)


def test_legal_dong_uses_a4() -> None:
    binding = resolve_place_scope("구서동")
    assert binding.semantic_type == "LEGAL_DONG"
    assert binding.physical_scope == "A4"
    sql = building_place_predicate("구서동")
    assert "A4" in sql


def test_admin_dong_uses_bnd() -> None:
    binding = resolve_place_scope("연산1동", context=PlaceScopeContext(prefer_admin=True))
    assert binding.semantic_type == "ADMIN_DONG"
    assert binding.physical_scope == "BND"


def test_sigungu_uses_a3() -> None:
    binding = resolve_place_scope("연제구")
    assert binding.semantic_type == "SIGUNGU"
    assert binding.physical_scope == "A3"
    assert binding.code == "26470"


def test_correction_prefers_admin_dong() -> None:
    ctx = PlaceScopeContext(
        question="연제구 건물? 아니, 연산1동",
        prefer_admin=True,
    )
    binding = resolve_place_scope("연산1동", context=ctx)
    assert binding.semantic_type == "ADMIN_DONG"
    assert binding.canonical_name == "연산1동"


def test_numbered_admin_dong_binding_uses_a4_without_spatial_cue() -> None:
    # P012 §8: 단순 동 이름 → A4; BND는 공간 cue/prefer_admin 있을 때만.
    binding = resolve_place_scope("대저1동")
    assert binding.semantic_type == "LEGAL_DONG"
    assert binding.physical_scope == "A4"
    with_cue = resolve_place_scope(
        "대저1동",
        context=PlaceScopeContext(question="대저1동 행정동 내부 건물"),
    )
    assert with_cue.semantic_type == "ADMIN_DONG"
    assert with_cue.physical_scope == "BND"


def test_place_scope_binding_model() -> None:
    b = PlaceScopeBinding(
        semantic_type="SIGUNGU",
        canonical_name="금정구",
        physical_scope="A3",
        code="26410",
        confidence=1.0,
    )
    assert b.semantic_type == "SIGUNGU"
