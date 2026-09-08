"""PlaceScopePolicy v1.0 — semantic place scope binding.

Policy:
  LEGAL_DONG → A4
  ADMIN_DONG → BND geometry
  SIGUNGU    → A3 prefix
  SIDO       → A2 / dataset scope
  PLACE      → gazetteer geometry
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from txt2sql.gazetteer import (
    KIND_ADMIN,
    KIND_LEGAL,
    KIND_SIDO,
    KIND_SIGUNGU,
    resolve_place_kind,
    sigungu_a3_prefix,
    uses_admin_boundary,
)

SemanticScopeType = Literal[
    "LEGAL_DONG",
    "ADMIN_DONG",
    "SIGUNGU",
    "SIDO",
    "PLACE",
    "UNKNOWN",
]

PhysicalScopeType = Literal["A4", "A3", "BND", "A2", "GAZETTEER", "NONE"]


class PlaceEntity(BaseModel):
    """Resolved place entity from query understanding."""

    model_config = ConfigDict(extra="forbid")

    name: str
    place_type: str | None = None
    sido: str | None = None
    sigungu: str | None = None
    legal_dong: str | None = None
    admin_dong: str | None = None
    aliases: list[str] = Field(default_factory=list)
    confidence: float = 1.0


class PlaceScopeContext(BaseModel):
    """Context for place scope resolution (correction, ambiguity)."""

    model_config = ConfigDict(extra="allow")

    question: str = ""
    default_sido: str = "부산광역시"
    corrected_from: str | None = None
    prefer_admin: bool = False
    prefer_legal: bool = False


class PlaceScopeBinding(BaseModel):
    """Canonical place scope binding result."""

    model_config = ConfigDict(extra="forbid")

    semantic_type: SemanticScopeType
    canonical_name: str
    physical_scope: PhysicalScopeType
    code: str | None = None
    geometry_id: str | None = None
    sigungu: str | None = None
    sido: str | None = None
    confidence: float = 1.0
    reason: str = ""


def _kind_to_semantic(kind: str | None, *, uses_bnd: bool) -> SemanticScopeType:
    if uses_bnd or kind == KIND_ADMIN:
        return "ADMIN_DONG"
    if kind == KIND_LEGAL:
        return "LEGAL_DONG"
    if kind == KIND_SIGUNGU:
        return "SIGUNGU"
    if kind == KIND_SIDO:
        return "SIDO"
    return "UNKNOWN"


def _semantic_to_physical(semantic: SemanticScopeType) -> PhysicalScopeType:
    mapping: dict[SemanticScopeType, PhysicalScopeType] = {
        "LEGAL_DONG": "A4",
        "ADMIN_DONG": "BND",
        "SIGUNGU": "A3",
        "SIDO": "A2",
        "PLACE": "GAZETTEER",
        "UNKNOWN": "NONE",
    }
    return mapping.get(semantic, "NONE")


def resolve_place_scope(
    place_entity: PlaceEntity | str,
    catalog: Any | None = None,
    context: PlaceScopeContext | None = None,
) -> PlaceScopeBinding:
    """Resolve place entity to semantic + physical scope binding.

  Router/Query Understanding decides place_name, place_type, context correction.
  A3/A4/BND selection is performed here per PlaceScopePolicy v1.0.
    """
    ctx = context or PlaceScopeContext()
    if isinstance(place_entity, str):
        entity = PlaceEntity(name=place_entity.strip())
    else:
        entity = place_entity

    name = entity.name.strip()
    if not name:
        return PlaceScopeBinding(
            semantic_type="UNKNOWN",
            canonical_name="",
            physical_scope="NONE",
            confidence=0.0,
            reason="empty_place",
        )

    kind = entity.place_type or resolve_place_kind(name, ctx.question)
    uses_bnd = uses_admin_boundary(
        name,
        prefer_admin=ctx.prefer_admin,
        question=ctx.question,
    )

    if ctx.prefer_admin or uses_bnd:
        semantic = "ADMIN_DONG"
        reason = "admin_dong_policy"
    elif ctx.prefer_legal or kind == KIND_LEGAL:
        semantic = "LEGAL_DONG"
        reason = "legal_dong_policy"
    elif kind == KIND_SIGUNGU or name.endswith(("구", "군")):
        semantic = "SIGUNGU"
        reason = "sigungu_policy"
    elif kind == KIND_SIDO:
        semantic = "SIDO"
        reason = "sido_policy"
    elif name.endswith(("동", "가", "리", "로")):
        semantic = "LEGAL_DONG"
        reason = "dong_suffix_legal_default"
    else:
        semantic = "PLACE"
        reason = "gazetteer_fallback"

    physical = _semantic_to_physical(semantic)
    code: str | None = None
    sido = entity.sido or ctx.default_sido
    sigungu = entity.sigungu

    if semantic == "SIGUNGU":
        code = sigungu_a3_prefix(name, sido=sido)
        if not sigungu and name.endswith(("구", "군")):
            sigungu = name
    elif semantic in {"ADMIN_DONG", "LEGAL_DONG"}:
        # Homonym dongs: prefer explicit parent sigungu/sido from the entity.
        if not sigungu and semantic == "LEGAL_DONG":
            from txt2sql.gazetteer import sigungu_for_legal_dong

            sigungu = sigungu_for_legal_dong(
                name, sido=sido, question=ctx.question
            )
        if sigungu:
            # Parent A3 prefix for compiler disambiguation (dong name alone is insufficient).
            code = sigungu_a3_prefix(sigungu, sido=sido)

    return PlaceScopeBinding(
        semantic_type=semantic,
        canonical_name=name,
        physical_scope=physical,
        code=code,
        geometry_id=f"bnd:{name}" if semantic == "ADMIN_DONG" else None,
        sigungu=sigungu,
        sido=sido,
        confidence=entity.confidence,
        reason=reason,
    )


def binding_to_sql_hint(binding: PlaceScopeBinding, *, alias: str = "") -> dict[str, Any]:
    """Return hints for SQL compiler from binding (no raw SQL here)."""
    return {
        "semantic_type": binding.semantic_type,
        "physical_scope": binding.physical_scope,
        "canonical_name": binding.canonical_name,
        "a3_prefix": binding.code if binding.physical_scope == "A3" else None,
        "use_bnd_join": binding.physical_scope == "BND",
        "alias": alias,
    }
