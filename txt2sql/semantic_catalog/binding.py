"""Semantic binding: concept requirements -> ranked dataset/field bindings."""

from __future__ import annotations

from dataclasses import dataclass, field

from txt2sql.semantic_catalog.concepts import FIELD_TO_CONCEPT, resolve_concept
from txt2sql.semantic_catalog.datasets import CONCEPT_PHYSICAL_FIELDS, DATASETS
from txt2sql.semantic_catalog.metrics import METRICS


@dataclass(frozen=True)
class SemanticBinding:
    concept: str
    dataset: str
    physical_field: str
    grain: str
    confidence: float
    reason: str
    alternatives: tuple["SemanticBinding", ...] = ()


@dataclass
class BindingResult:
    bindings: list[SemanticBinding] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    @property
    def primary_dataset(self) -> str | None:
        if not self.bindings:
            return None
        # lowest priority number wins among selected
        ranked = sorted(
            self.bindings,
            key=lambda b: (DATASETS[b.dataset].priority if b.dataset in DATASETS else 999, -b.confidence),
        )
        return ranked[0].dataset


def _candidates_for_concept(concept_key: str) -> list[SemanticBinding]:
    out: list[SemanticBinding] = []
    for ds in DATASETS.values():
        if concept_key not in ds.concepts:
            continue
        phys = CONCEPT_PHYSICAL_FIELDS.get(concept_key, {}).get(ds.dataset_id)
        if not phys:
            continue
        conf = 0.9 if ds.priority <= 10 else 0.7
        # Temporal concepts prefer D198 attribute ledger when available.
        if concept_key in {
            "building.approval_date",
            "building.permit_date",
            "building.age",
        } and ds.dataset_id == "building_attr_d198":
            conf = 0.97
        metric = METRICS.get(concept_key)
        reason = f"concept={concept_key}; dataset={ds.dataset_id}; priority={ds.priority}"
        if metric and metric.preferred_dataset == ds.dataset_id:
            conf = min(1.0, conf + 0.05)
            reason += "; preferred_metric_dataset"
        out.append(
            SemanticBinding(
                concept=concept_key,
                dataset=ds.dataset_id,
                physical_field=phys,
                grain=ds.grain,
                confidence=conf,
                reason=reason,
            )
        )
    out.sort(key=lambda b: (-b.confidence, DATASETS[b.dataset].priority))
    return out


def bind_concept(token: str, *, prefer_dataset: str | None = None) -> SemanticBinding | None:
    concept = resolve_concept(token)
    if concept is None:
        # try field alias
        key = FIELD_TO_CONCEPT.get(token)
        concept = resolve_concept(key) if key else None
    if concept is None:
        return None
    cands = _candidates_for_concept(concept.key)
    if not cands:
        return None
    if prefer_dataset:
        for c in cands:
            if c.dataset == prefer_dataset:
                alts = tuple(x for x in cands if x.dataset != prefer_dataset)
                return SemanticBinding(
                    concept=c.concept,
                    dataset=c.dataset,
                    physical_field=c.physical_field,
                    grain=c.grain,
                    confidence=c.confidence,
                    reason=c.reason + "; preferred",
                    alternatives=alts,
                )
    primary = cands[0]
    return SemanticBinding(
        concept=primary.concept,
        dataset=primary.dataset,
        physical_field=primary.physical_field,
        grain=primary.grain,
        confidence=primary.confidence,
        reason=primary.reason,
        alternatives=tuple(cands[1:]),
    )


def bind_concepts(
    tokens: list[str],
    *,
    require_same_grain: bool = True,
    question: str = "",
) -> BindingResult:
    result = BindingResult()
    chosen: list[SemanticBinding] = []
    for token in tokens:
        binding = bind_concept(token)
        if binding is None:
            result.unresolved.append(token)
            continue
        chosen.append(binding)
        result.bindings.append(binding)

    if require_same_grain and chosen:
        grains = {b.grain for b in chosen}
        datasets = {b.dataset for b in chosen}
        if len(datasets) > 1:
            # D010 vs D198 conflict on overlapping concepts
            if "building_gis_d010" in datasets and "building_attr_d198" in datasets:
                d198_tokens = {
                    "detail_usage",
                    "usage_class",
                    "permit_date",
                    "building_age_years",
                }
                # bare usage: same central grain policy as dataset_grain (MAIN485).
                if "usage" in tokens and "detail_usage" not in tokens:
                    from txt2sql.dataset_grain import needs_d198_building_grain

                    prefer_d198 = bool(question) and needs_d198_building_grain(
                        question
                    )
                else:
                    prefer_d198 = any(t in d198_tokens for t in tokens)
                if prefer_d198 or ("usage" in tokens and "height_m" in tokens):
                    result.bindings = [
                        b for b in result.bindings if b.dataset == "building_attr_d198"
                    ] or result.bindings
                else:
                    result.bindings = [
                        b for b in result.bindings if b.dataset == "building_gis_d010"
                    ] or result.bindings
                if len({b.dataset for b in result.bindings}) <= 1:
                    result.conflicts = [
                        c
                        for c in result.conflicts
                        if not c.startswith("SEMANTIC_DATASET_CONFLICT")
                    ]
                else:
                    result.conflicts.append("SEMANTIC_DATASET_CONFLICT:d010_vs_d198")
            elif len(grains) > 1:
                result.conflicts.append("SEMANTIC_GRAIN_MISMATCH")
    return result


def rank_datasets_for_concepts(concept_keys: list[str]) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for key in concept_keys:
        concept = resolve_concept(key)
        ck = concept.key if concept else FIELD_TO_CONCEPT.get(key, key)
        for ds in DATASETS.values():
            if ck in ds.concepts:
                scores[ds.dataset_id] = scores.get(ds.dataset_id, 0.0) + 1.0 / ds.priority
    return sorted(scores.items(), key=lambda x: -x[1])


def check_coverage(
    dataset_id: str,
    *,
    sido: str | None = None,
    sigungu: str | None = None,
) -> tuple[bool, str]:
    """Return (supported, reason) for geographic coverage."""
    ds = DATASETS.get(dataset_id)
    if ds is None:
        return False, "UNSUPPORTED_DATASET"
    if ds.coverage_sigungu and sigungu and sigungu not in ds.coverage_sigungu:
        return False, "UNSUPPORTED_DATASET_COVERAGE"
    if ds.coverage_sido and sido and sido not in ds.coverage_sido:
        return False, "UNSUPPORTED_SCOPE"
    return True, "ok"


def filter_by_coverage(
    bindings: list[SemanticBinding],
    *,
    sido: str | None = None,
    sigungu: str | None = None,
) -> tuple[list[SemanticBinding], list[str]]:
    """Filter bindings by dataset geographic coverage."""
    ok: list[SemanticBinding] = []
    unsupported: list[str] = []
    for b in bindings:
        supported, reason = check_coverage(b.dataset, sido=sido, sigungu=sigungu)
        if supported:
            ok.append(b)
        else:
            unsupported.append(f"{b.dataset}:{reason}")
    return ok, unsupported
