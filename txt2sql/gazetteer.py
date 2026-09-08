"""시도·구군·법정동·행정동 지명 사전 (전국).

정규식(~동) 대신 등록된 명칭만 최장일치로 찾아, 구서역·공동주택 같은 오탐을 줄인다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Iterable

_ADMIN_DONG_INSIDE = re.compile(r"\d동\s*안(?:의|에서|에|쪽|\s|$)")


class _TrieNode:
    """지명 최장일치용 트라이 노드."""

    __slots__ = ("children", "term")

    def __init__(self) -> None:
        self.children: dict[str, _TrieNode] = {}
        self.term: str | None = None


def _build_name_trie(names: tuple[str, ...]) -> _TrieNode:
    root = _TrieNode()
    for name in names:
        node = root
        for ch in name:
            nxt = node.children.get(ch)
            if nxt is None:
                nxt = _TrieNode()
                node.children[ch] = nxt
            node = nxt
        node.term = name
    return root


KIND_SIDO = "sido"
KIND_SIGUNGU = "sigungu"
KIND_LEGAL = "legal_dong"
KIND_ADMIN = "admin_dong"

_DATA_NAME = "gazetteer_data.json"

# BND_ADM_DONG_PG 센서스 ADM_CD 앞 2자리. 법정동 PNU(26=부산)와 다르다.
SIDO_CENSUS_PREFIX: dict[str, str] = {
    "서울특별시": "11",
    "부산광역시": "21",
    "대구광역시": "22",
    "인천광역시": "23",
    "광주광역시": "24",
    "대전광역시": "25",
    "울산광역시": "26",
    "세종특별자치시": "29",
    "경기도": "31",
    "강원도": "32",
    "강원특별자치도": "32",
    "충청북도": "33",
    "충청남도": "34",
    "전라북도": "35",
    "전북특별자치도": "35",
    "전라남도": "36",
    "경상북도": "37",
    "경상남도": "38",
    "제주도": "39",
    "제주특별자치도": "39",
}

# 법정동코드(PNU) 시·도 접두 2자리. AL_D010_26_… / A3 LIKE '26…%' 에 사용.
SIDO_PNU_PREFIX: dict[str, str] = {
    "서울특별시": "11",
    "부산광역시": "26",
    "대구광역시": "27",
    "인천광역시": "28",
    "광주광역시": "29",
    "대전광역시": "30",
    "울산광역시": "31",
    "세종특별자치시": "36",
    "경기도": "41",
    "강원도": "42",
    "강원특별자치도": "42",
    "충청북도": "43",
    "충청남도": "44",
    "전라북도": "45",
    "전북특별자치도": "45",
    "전라남도": "46",
    "경상북도": "47",
    "경상남도": "48",
    "제주도": "50",
    "제주특별자치도": "50",
}
SIDO_ALIAS_CANONICAL: dict[str, str] = {
    "서울": "서울특별시",
    "서울시": "서울특별시",
    "부산": "부산광역시",
    "부산시": "부산광역시",
    "대구": "대구광역시",
    "대구시": "대구광역시",
    "인천": "인천광역시",
    "인천시": "인천광역시",
    "광주": "광주광역시",
    "광주시": "광주광역시",
    "대전": "대전광역시",
    "대전시": "대전광역시",
    "울산": "울산광역시",
    "울산시": "울산광역시",
    "세종": "세종특별자치시",
    "세종시": "세종특별자치시",
    "경기": "경기도",
    "강원": "강원특별자치도",
    "충북": "충청북도",
    "충남": "충청남도",
    "전북": "전북특별자치도",
    "전남": "전라남도",
    "경북": "경상북도",
    "경남": "경상남도",
    "제주": "제주특별자치도",
}


@dataclass(frozen=True)
class PlaceHit:
    name: str
    kinds: frozenset[str]
    start: int
    end: int

    @property
    def is_admin_only(self) -> bool:
        """법정동 주소(A4)에 없고 행정동 경계로만 집계해야 하는 동."""
        return KIND_ADMIN in self.kinds and KIND_LEGAL not in self.kinds

    @property
    def is_dong(self) -> bool:
        return bool(self.kinds & {KIND_LEGAL, KIND_ADMIN})

    @property
    def is_sigungu(self) -> bool:
        return KIND_SIGUNGU in self.kinds


@dataclass(frozen=True)
class Gazetteer:
    sido: frozenset[str]
    sigungu: frozenset[str]
    legal_dong: frozenset[str]
    admin_dong: frozenset[str]
    names_by_len: tuple[str, ...]
    kinds_of: dict[str, frozenset[str]]
    name_trie: _TrieNode
    sigungu_sido: dict[str, tuple[str, ...]]
    admin_dong_prefixes: dict[str, tuple[str, ...]]
    # 시군구명 → 법정동코드(PNU) 5자리 (정책으로 고른 대표값).
    sigungu_pnu_prefix: dict[str, str]
    # 동명 구 등 후보 전부. 질의 시·도 컨텍스트로 재선택한다.
    sigungu_pnu_candidates: dict[str, tuple[str, ...]]
    # 법정동명 → 소속 시군구(전국, pnu_def 기반). 동명이의는 복수 후보.
    legal_dong_sigungu: dict[str, tuple[str, ...]]


def _kinds_map(
    sido: Iterable[str],
    sigungu: Iterable[str],
    legal: Iterable[str],
    admin: Iterable[str],
) -> dict[str, frozenset[str]]:
    acc: dict[str, set[str]] = {}
    for name, kind in (
        *((n, KIND_SIDO) for n in sido),
        *((n, KIND_SIGUNGU) for n in sigungu),
        *((n, KIND_LEGAL) for n in legal),
        *((n, KIND_ADMIN) for n in admin),
    ):
        if not name:
            continue
        acc.setdefault(name, set()).add(kind)
    return {k: frozenset(v) for k, v in acc.items()}


def invalidate_gazetteer() -> None:
    """파일 재생성 후 프로세스 캐시를 비운다."""
    load_gazetteer.cache_clear()


@lru_cache(maxsize=1)
def load_gazetteer() -> Gazetteer:
    raw = json.loads(
        files("txt2sql").joinpath(_DATA_NAME).read_text(encoding="utf-8")
    )
    sido = tuple(raw.get("sido") or ()) + tuple(raw.get("sido_aliases") or ())
    sigungu = tuple(raw.get("sigungu") or ())
    legal = tuple(raw.get("legal_dong") or ())
    admin = tuple(raw.get("admin_dong") or ())
    kinds = _kinds_map(sido, sigungu, legal, admin)
    # 시도 별칭(부산·서울)은 건물명(부산대학교) 오탐이 커서 지명 스캔에서 뺀다.
    scan = {
        n
        for n, ks in kinds.items()
        if KIND_SIDO not in ks or n in set(raw.get("sido") or ())
    }
    names = tuple(sorted(scan, key=len, reverse=True))
    sigungu_sido = {
        str(k): tuple(str(x) for x in (v or ()) if x)
        for k, v in (raw.get("sigungu_sido") or {}).items()
    }
    admin_dong_prefixes = {
        str(k): tuple(str(x) for x in (v or ()) if x)
        for k, v in (raw.get("admin_dong_prefixes") or {}).items()
    }
    sigungu_pnu_prefix = {
        str(k): str(v).strip()
        for k, v in (raw.get("sigungu_pnu_prefix") or {}).items()
        if str(k).strip() and str(v).strip().isdigit()
    }
    candidates_raw = raw.get("sigungu_pnu_candidates") or {}
    sigungu_pnu_candidates: dict[str, tuple[str, ...]] = {}
    for k, v in candidates_raw.items():
        name = str(k).strip()
        if not name:
            continue
        if isinstance(v, (list, tuple)):
            codes = tuple(
                str(c).strip()
                for c in v
                if str(c).strip().isdigit() and len(str(c).strip()) == 5
            )
        else:
            code = str(v).strip()
            codes = (code,) if code.isdigit() and len(code) == 5 else ()
        if codes:
            sigungu_pnu_candidates[name] = codes
    # 구 JSON에 candidates가 없으면 대표값만 후보로 둔다.
    for name, code in sigungu_pnu_prefix.items():
        sigungu_pnu_candidates.setdefault(name, (code,))
    legal_dong_sigungu: dict[str, tuple[str, ...]] = {}
    for dong, gus in (raw.get("legal_dong_sigungu") or {}).items():
        name = str(dong).strip()
        if not name:
            continue
        items = tuple(str(g).strip() for g in (gus or ()) if str(g).strip())
        if items:
            legal_dong_sigungu[name] = items
    return Gazetteer(
        sido=frozenset(sido),
        sigungu=frozenset(sigungu),
        legal_dong=frozenset(legal),
        admin_dong=frozenset(admin),
        names_by_len=names,
        kinds_of=kinds,
        name_trie=_build_name_trie(names),
        sigungu_sido=sigungu_sido,
        admin_dong_prefixes=admin_dong_prefixes,
        sigungu_pnu_prefix=sigungu_pnu_prefix,
        sigungu_pnu_candidates=sigungu_pnu_candidates,
        legal_dong_sigungu=legal_dong_sigungu,
    )


@lru_cache(maxsize=4096)
def _legal_dong_sigungu_from_db(dong: str) -> tuple[str, ...]:
    """pnu_def 에서 법정동 → 시군구 후보를 조회한다(DB 연결 시)."""
    name = (dong or "").strip()
    if not name:
        return ()
    try:
        from txt2sql.config import load_settings
        from txt2sql.db import connect

        settings = load_settings()
        with connect(settings.database_url) as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT split_part(trim("PNU_NM"), ' ', 2) AS gu
                FROM pnu_def
                WHERE trim("PNU_NM") LIKE %s
                  AND split_part(trim("PNU_NM"), ' ', 2) ~ '[구군]$'
                ORDER BY 1
                """,
                (f"% {name}",),
            ).fetchall()
        return tuple(str(row["gu"]).strip() for row in rows if row.get("gu"))
    except Exception:
        return ()


def sigungu_candidates_for_legal_dong(dong: str) -> tuple[str, ...]:
    """법정동명에 대응하는 시군구 후보(전국)."""
    name = (dong or "").strip()
    if not name:
        return ()
    cached = load_gazetteer().legal_dong_sigungu.get(name)
    if cached:
        return cached
    from_db = _legal_dong_sigungu_from_db(name)
    if from_db:
        return from_db
    return ()


def sigungu_for_legal_dong(
    dong: str,
    *,
    sido: str | None = None,
    question: str = "",
) -> str | None:
    """법정동명 → 대표 시군구(동명이의는 시·도·질문 맥락으로 축소)."""
    name = (dong or "").strip()
    if not name:
        return None
    candidates = list(sigungu_candidates_for_legal_dong(name))
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    preferred_sido = (sido or "").strip() or None
    if not preferred_sido and question:
        for token in load_gazetteer().sido:
            if token in question:
                preferred_sido = token
                break
        if not preferred_sido:
            for alias, canonical in SIDO_ALIAS_CANONICAL.items():
                if alias in question:
                    preferred_sido = canonical
                    break
    if not preferred_sido:
        try:
            from txt2sql.dataset_tables import primary_sido_pnu

            primary = primary_sido_pnu()
            for sido_name, prefix in SIDO_PNU_PREFIX.items():
                if prefix == primary:
                    preferred_sido = sido_name
                    break
        except Exception:
            preferred_sido = None

    g = load_gazetteer()
    if preferred_sido:
        narrowed = [
            gu
            for gu in candidates
            if preferred_sido in (g.sigungu_sido.get(gu) or ())
        ]
        if len(narrowed) == 1:
            return narrowed[0]
        if narrowed:
            candidates = narrowed

    from txt2sql.domain import D198_BY_GU, extract_gu

    explicit_gu = extract_gu(question)
    if explicit_gu and explicit_gu in candidates:
        return explicit_gu

    # 시·도 축소 후에도 동명이의면: D198 커버 구를 우선하고,
    # 여러 개면 gazetteer 후보 순서(대표 구)를 따른다. None으로 포기하면
    # usage+동 집계가 D010으로 퇴화한다.
    if D198_BY_GU:
        covered = [gu for gu in candidates if gu in D198_BY_GU]
        if len(covered) == 1:
            return covered[0]
        if covered:
            return covered[0]

    return candidates[0] if candidates else None


def classify_place(name: str) -> frozenset[str]:
    if not name:
        return frozenset()
    return load_gazetteer().kinds_of.get(name.strip(), frozenset())


def is_known_place(name: str) -> bool:
    return bool(classify_place(name))


def is_admin_dong(name: str | None) -> bool:
    return bool(name) and KIND_ADMIN in classify_place(str(name))


def is_legal_dong(name: str | None) -> bool:
    return bool(name) and KIND_LEGAL in classify_place(str(name))


def question_needs_admin_boundary(question: str | None) -> bool:
    """질문에 행정동 경계(BND) 집계가 필요한 표현이 있는지."""
    q = (question or "").strip()
    if not q:
        return False
    if "행정동" in q:
        return True
    spatial_cues = (
        "경계",
        "안에",
        "안에서",
        "안쪽",
        "내부",
        "이내",
        "반경",
        "밖에",
        "바깥",
        "외부",
        "겹치",
        "포함",
        "들어있",
        "들어 있는",
    )
    if any(cue in q for cue in spatial_cues):
        return True
    # 「연산1동 안」「괴정1동 안의」— 번호 행정동 + 공간 안 (Q366/Q367)
    return _ADMIN_DONG_INSIDE.search(q) is not None


def uses_admin_boundary(
    name: str | None,
    *,
    prefer_admin: bool = False,
    question: str = "",
) -> bool:
    """BND 행정동 경계 집계가 필요한지 (PlaceScopePolicy / P012 §8).

    - 명시적 공간 cue(행정동·경계·내부·이내·반경 등) 또는 prefer_admin → BND
    - 단순 동 이름만(번호 행정동 포함) → A4 속성 필터 (공간 조인 강제 금지)
    - 행정·법정 동시 등록이어도 cue 없으면 A4
    """
    if not name:
        return False
    text = str(name).strip()
    kinds = classify_place(text)
    needs_bnd = prefer_admin or question_needs_admin_boundary(question)
    if not needs_bnd:
        return False
    if KIND_ADMIN in kinds:
        return True
    return False


def sido_pnu_prefix(sido: str | None) -> str | None:
    """시도명 → 법정동(PNU) 시·도 접두 2자리."""
    canon = canonical_sido(sido) or (sido or "").strip()
    if not canon:
        return None
    return SIDO_PNU_PREFIX.get(canon)


def choose_sigungu_pnu_code(
    codes: list[str] | tuple[str, ...],
    *,
    question_sido: str | None = None,
    default_sido: str | None = None,
) -> str | None:
    """동명 구 후보 PNU 선택 정책.

    우선순위: 질문 시도 접두 > settings/default_sido 접두 > 가장 짧은 코드
    (동률이면 사전순). 부산(26) 하드 우선은 쓰지 않는다.
    """
    uniq = sorted({c.strip() for c in codes if str(c).strip().isdigit()})
    if not uniq:
        return None
    if len(uniq) == 1:
        return uniq[0]
    for sido in (question_sido, default_sido):
        prefix = sido_pnu_prefix(sido)
        if not prefix:
            continue
        matched = [c for c in uniq if c.startswith(prefix)]
        if len(matched) == 1:
            return matched[0]
        if matched:
            return min(matched, key=lambda c: (len(c), c))
    return min(uniq, key=lambda c: (len(c), c))


def sigungu_a3_prefix(
    gu: str | None,
    *,
    sido: str | None = None,
    default_sido: str | None = None,
) -> str | None:
    """시군구명 → 법정동코드(A3) 5자리.

    gazetteer 후보 + 시도 컨텍스트로 고른다.
    정책: 질문 sido > default_sido > 최단 코드.
    신규 SQL은 하드코드 26xxx에 의존하지 않고 이 함수(또는 결과)를 쓴다.
    """
    if not gu:
        return None
    name = gu.strip()
    gaz = load_gazetteer()
    candidates = list(gaz.sigungu_pnu_candidates.get(name) or ())
    if not candidates:
        code = gaz.sigungu_pnu_prefix.get(name)
        if code and code.isdigit():
            candidates = [code]
    if candidates:
        resolved_default = default_sido
        if resolved_default is None:
            try:
                from txt2sql.dataset_tables import primary_sido_pnu

                primary = primary_sido_pnu()
                for sido_name, prefix in SIDO_PNU_PREFIX.items():
                    if prefix == primary:
                        resolved_default = sido_name
                        break
            except Exception:
                resolved_default = "부산광역시"
        chosen = choose_sigungu_pnu_code(
            candidates,
            question_sido=sido,
            default_sido=resolved_default or "부산광역시",
        )
        if chosen:
            return chosen
    # 과도기 폴백: 부산 시군구 정적 맵 (gazetteer 재생성 전·누락 시)
    from txt2sql.domain import BUSAN_GU_CODES

    return BUSAN_GU_CODES.get(name)


def resolve_place_kind(name: str | None, question: str = "") -> str:
    """place 이름 → PlaceSpec.kind (gu|legal_dong|admin_dong|sido|unknown)."""
    if not name:
        return "unknown"
    text = name.strip()
    kinds = classify_place(text)
    prefer_admin = "행정동" in (question or "")
    if KIND_SIDO in kinds and text.endswith(("시", "도")):
        return "sido"
    if KIND_SIGUNGU in kinds and text.endswith(("구", "군")):
        return "gu"
    if uses_admin_boundary(text, prefer_admin=prefer_admin, question=question):
        return "admin_dong"
    if KIND_ADMIN in kinds and KIND_LEGAL not in kinds:
        return "admin_dong"
    if KIND_LEGAL in kinds:
        return "legal_dong"
    if text.endswith(("구", "군")):
        return "gu"
    if text.endswith("동"):
        return "legal_dong"
    return "unknown"


def is_locality(name: str | None) -> bool:
    """법정동·행정동·리·가·읍·면 (구·군·시도 제외)."""
    if not name:
        return False
    return bool(classify_place(str(name)) & {KIND_LEGAL, KIND_ADMIN})


_HANGUL_SYL = re.compile(r"[가-힣]")
_SHORT_RI_PARTICLES = (
    "으로",
    "에서",
    "까지",
    "부터",
    "이랑",
    "이나",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "의",
    "에",
    "와",
    "과",
    "도",
    "만",
    "로",
    "랑",
)


def _short_ri_ok(text: str, start: int, end: int, name: str) -> bool:
    """2글자 리(원리·고리)는 단어 경계일 때만. 원리원칙·고리원자력 오탐 방지."""
    if len(name) > 2 or not name.endswith("리"):
        return True
    if start > 0 and _HANGUL_SYL.match(text[start - 1]):
        return False
    rest = text[end:]
    if not rest:
        return True
    if not _HANGUL_SYL.match(rest[0]):
        return True
    for p in _SHORT_RI_PARTICLES:
        if rest.startswith(p):
            return True
    return False


def _scan_places(text: str) -> tuple[PlaceHit, ...]:
    """트라이 최장일치. 2글자 리(원리·고리)는 단어 경계일 때만 채택."""
    gaz = load_gazetteer()
    hits: list[PlaceHit] = []
    i = 0
    n = len(text)
    root = gaz.name_trie
    kinds_of = gaz.kinds_of
    while i < n:
        node: _TrieNode | None = root
        j = i
        best: str | None = None
        while node is not None and j < n:
            node = node.children.get(text[j])
            if node is None:
                break
            j += 1
            if node.term is not None and _short_ri_ok(text, i, j, node.term):
                best = node.term
        if best is None:
            i += 1
            continue
        hits.append(PlaceHit(best, kinds_of.get(best, frozenset()), i, i + len(best)))
        i += len(best)
    return tuple(hits)


@lru_cache(maxsize=1024)
def find_places(text: str) -> tuple[PlaceHit, ...]:
    """질문에 등장하는 등록 지명을 왼쪽부터 최장일치로 찾는다."""
    if not text:
        return ()
    return _scan_places(text)


def extract_gazetteer_places(text: str) -> list[str]:
    """동(법정·행정)을 먼저, 없으면 구·군. 등장 순서."""
    dongs: list[str] = []
    gus: list[str] = []
    seen: set[str] = set()
    for hit in find_places(text):
        if hit.name in seen:
            continue
        if hit.is_dong:
            seen.add(hit.name)
            dongs.append(hit.name)
        elif hit.is_sigungu:
            seen.add(hit.name)
            gus.append(hit.name)
    return dongs or gus


def canonical_sido(name: str | None) -> str | None:
    raw = (name or "").strip()
    if not raw:
        return None
    mapped = SIDO_ALIAS_CANONICAL.get(raw, raw)
    gaz = load_gazetteer()
    if mapped in gaz.sido or mapped in SIDO_CENSUS_PREFIX:
        return mapped
    return None


def census_adm_prefix(sido: str | None) -> str | None:
    canon = canonical_sido(sido) or (sido or "").strip()
    return SIDO_CENSUS_PREFIX.get(canon)


def unique_adm_cd_prefix(name: str | None) -> str | None:
    """행정동명이 전국에서 한 시도에만 있으면 그 센서스 접두어."""
    if not name:
        return None
    prefixes = load_gazetteer().admin_dong_prefixes.get(name.strip()) or ()
    if len(prefixes) == 1 and len(prefixes[0]) == 2 and prefixes[0].isdigit():
        return prefixes[0]
    return None


def unique_sigungu_adm_prefix(gu: str | None) -> str | None:
    if not gu:
        return None
    sidos = load_gazetteer().sigungu_sido.get(gu.strip()) or ()
    prefixes = []
    for sido in sidos:
        prefix = census_adm_prefix(sido)
        if prefix and prefix not in prefixes:
            prefixes.append(prefix)
    if len(prefixes) == 1:
        return prefixes[0]
    return None


def adm_cd_prefix_for_place(
    name: str | None,
    *,
    sido: str | None = None,
    gu: str | None = None,
) -> str | None:
    """질문의 시도·구 또는 (동명이 유일하면) 행정동으로 센서스 ADM_CD 접두어를 고른다."""
    prefix = census_adm_prefix(sido)
    if prefix:
        return prefix
    prefix = unique_sigungu_adm_prefix(gu)
    if prefix:
        return prefix
    return unique_adm_cd_prefix(name)
