"""GIS 질의용 공통 도메인 토큰 (장소·용도)."""

from __future__ import annotations

import re

GU_PATTERN = (
    r"(중구|서구|동구|영도구|부산진구|동래구|남구|북구|해운대구|사하구|"
    r"금정구|강서구|연제구|수영구|사상구|기장군|[가-힣]{1,6}구)"
)
DONG_PATTERN = r"([가-힣0-9]{1,12}동)"
# 거리(100m, 1km). 숫자+동(구서1동)과 구분하려고 단위를 요구한다.
LENGTH_DIST_PATTERN = (
    r"(\d+(?:\.\d+)?)\s*(킬로미터|㎞|km|미터|m)(?![a-zA-Z²2])"
)

GU_RE = re.compile(GU_PATTERN)
DONG_RE = re.compile(DONG_PATTERN)

# 부산 구·군 → 행정표준코드 (과도기 폴백·재export).
# 신규 SQL 생성은 gazetteer.sigungu_a3_prefix / place_scope 만 사용.
BUSAN_GU_CODES: dict[str, str] = {
    "중구": "26110",
    "서구": "26140",
    "동구": "26170",
    "영도구": "26200",
    "부산진구": "26230",
    "동래구": "26260",
    "남구": "26290",
    "북구": "26320",
    "해운대구": "26350",
    "사하구": "26380",
    "금정구": "26410",
    "강서구": "26440",
    "연제구": "26470",
    "수영구": "26500",
    "사상구": "26530",
    "기장군": "26710",
}

USAGE_ALIASES: dict[str, str] = {
    "아파트": "공동주택",
    "공동주택": "공동주택",
    "연립주택": "공동주택",
    "단독주택": "단독주택",
    "단독추택": "단독주택",
    "다가구": "단독주택",
    "공장": "공장",
    "창고": "창고시설",
    "창고시설": "창고시설",
    "학교": "교육연구시설",
    "교육연구시설": "교육연구시설",
    "제2종근린생활시설": "제2종근린생활시설",
    "제1종근린생활시설": "제1종근린생활시설",
    "근린생활": "제1종근린생활시설",
    "업무시설": "업무시설",
    "판매시설": "판매시설",
    "숙박": "숙박시설",
    "숙박시설": "숙박시설",
    "위락시설": "위락시설",
    "위락": "위락시설",
    "노유자시설": "노유자시설",
    "노유자": "노유자시설",
    "위험물저장및처리시설": "위험물저장및처리시설",
    "위험물저장": "위험물저장및처리시설",
    "위험물": "위험물저장및처리시설",
    "자동차관련시설": "자동차관련시설",
    "자동차관련": "자동차관련시설",
    "문화및집회시설": "문화및집회시설",
    "의료시설": "의료시설",
    "운동시설": "운동시설",
    "수련시설": "수련시설",
    "운수시설": "운수시설",
    "종교": "종교시설",
    "종교시설": "종교시설",
    "공공시설물": "공공용시설",
    "공공시설": "공공용시설",
    "공공용시설": "공공용시설",
    "분뇨쓰레기처리시설": "분뇨쓰레기처리시설",
    "분뇨.쓰레기처리시설": "분뇨쓰레기처리시설",
    "분뇨·쓰레기처리시설": "분뇨쓰레기처리시설",
    "분뇨 쓰레기처리시설": "분뇨쓰레기처리시설",
    "쓰레기처리시설": "분뇨쓰레기처리시설",
    "동식물관련시설": "동식물관련시설",
    "동.식물관련시설": "동식물관련시설",
    "교정및군사시설": "교정및군사시설",
    "방송통신시설": "방송통신시설",
    "발전시설": "발전시설",
    "묘지관련시설": "묘지관련시설",
    "관광휴게시설": "관광휴게시설",
    "가설건축물": "가설건축물",
    "장례식장": "장례식장",
}

# D198 세부용도(A27)·용도분류(A29). unknown-term 제외용. D010 A9에 강제 바인딩하지 않는다.
DETAIL_USAGE_ALIASES: dict[str, str] = {
    "아파트": "아파트",
    "오피스텔": "오피스텔",
    "사무소": "사무소",
    "다가구주택": "다가구주택",
    "다세대주택": "다세대주택",
    "일반음식점": "일반음식점",
    "소매점": "소매점",
    "학원": "학원",
}

USAGE_CLASS_ALIASES: dict[str, str] = {
    "문교사회용": "문교사회용",
    "공공용": "공공용",
    "주거용": "주거용",
    "상업용": "상업용",
    "공업용": "공업용",
    "농수산용": "농수산용",
}

# 구 없이 실행하면 동음이의가 섞이는 행정/법정 동명.
MULTI_GU_DONGS = frozenset({"중앙동"})

USAGE_PATTERN = (
    r"(아파트|공동주택|연립주택|단독주택|공장|창고시설|창고|"
    r"교육연구시설|제2종근린생활시설|제1종근린생활시설|"
    r"업무시설|판매시설|숙박시설|위락시설|노유자시설|"
    r"위험물저장및처리시설|자동차관련시설|문화및집회시설|"
    r"의료시설|운동시설|수련시설|운수시설|종교시설|"
    r"공공시설물|공공시설|공공용시설|"
    r"분뇨쓰레기처리시설|분뇨·쓰레기처리시설|분뇨\.쓰레기처리시설|"
    r"동식물관련시설|교정및군사시설|방송통신시설|발전시설|"
    r"묘지관련시설|관광휴게시설|가설건축물|장례식장)"
)
USAGE_RE = re.compile(USAGE_PATTERN)

# 건축물구조명(A11) — 긴 별칭 우선 매칭
STRUCTURE_ALIASES: dict[str, str] = {
    "철골철근콘크리트": "%철골철근콘크리트%",
    "철근콘크리트": "%철근콘크리트%",
    "철골콘크리트": "%철골콘크리트%",
    "프리케스트콘크리트": "%프리케스트콘크리트%",
    "프리캐스트콘크리트": "%프리케스트콘크리트%",
    "기타콘크리트": "%기타콘크리트%",
    "콘크리트": "%콘크리트%",
    "조적구조": "%조적%",
    "벽돌구조": "%벽돌%",
    "목구조": "%목%",
    "경량철골구조": "%경량철골%",
    "경량철골": "%경량철골%",
    "일반철골구조": "%일반철골%",
    "일반철골": "%일반철골%",
    "철골구조": "%철골%",
    "블록구조": "%블록%",
    "블럭구조": "%블럭%",
    "조적": "%조적%",
    "벽돌": "%벽돌%",
    "블록": "%블록%",
    "블럭": "%블럭%",
    "철골": "%철골%",
}

# 통칭 → 대장 표기. 문항 QID가 아니라 도메인 별칭이다.
BUILDING_NAME_ALIASES: dict[str, tuple[str, ...]] = {
    "엘시티": (
        "엘시티",
        "엘시티피이에프",
        "주식회사엘시티피이에프브이",
        "랜드마크타워",
    ),
    "엘크루": ("엘크루", "엘크루 블루오션", "엘크루블루오션"),
    "블루오션": ("엘크루 블루오션", "엘크루블루오션", "블루오션"),
}


def expand_building_name_aliases(name: str) -> list[str]:
    """조회 토큰과 동의어를 중복 없이 반환한다."""
    text = (name or "").strip()
    if not text:
        return []
    found: list[str] = [text]
    for alias, synonyms in BUILDING_NAME_ALIASES.items():
        if alias in text or text in alias:
            for item in synonyms:
                if item not in found:
                    found.append(item)
            continue
        for item in synonyms:
            if item in text or text in item:
                for extra in (alias, *synonyms):
                    if extra not in found:
                        found.append(extra)
                break
    return found


def extract_structure(question: str) -> tuple[str, str] | None:
    """질문의 구조 표현 → (표시명, A11 ILIKE 패턴)."""
    found = extract_structures(question)
    return found[0] if found else None


def exact_structure_label(question: str, alias: str | None = None) -> str | None:
    """질문이 공식 '…구조' 표기를 쓰면 A11 equality 값, 아니면 None(짧은 별칭→ILIKE).

    「일반목구조」처럼 복합 수식어+짧은 별칭은 equality로 오인하지 않는다.
    """
    compact = (question or "").replace(" ", "")
    label_alias = alias
    if label_alias is None:
        hit = extract_structure(question)
        if hit is None:
            return None
        label_alias = hit[0]
    candidates: list[str] = []
    if label_alias.endswith("구조"):
        candidates.append(label_alias)
    else:
        candidates.append(f"{label_alias}구조")
    compound_prefixes = ("일반", "특수", "기타", "간이", "전통")
    for label in candidates:
        idx = compact.find(label)
        if idx < 0:
            continue
        if any(
            idx >= len(prefix) and compact[idx - len(prefix) : idx] == prefix
            for prefix in compound_prefixes
        ):
            return None
        return label
    return None


def structure_a11_predicate(question: str, alias: str, pattern: str) -> str:
    """구조 별칭 → A11 SQL 술어 (공식명 equality, 그 외 ILIKE)."""
    exact = exact_structure_label(question, alias)
    if exact:
        safe = exact.replace("'", "''")
        return f"\"A11\" = '{safe}'"
    safe_pat = pattern.replace("'", "''")
    return f"\"A11\" ILIKE '{safe_pat}'"


def is_permit_lag_assumption(item: str) -> bool:
    """허가↔승인 시차 assumption 토큰 여부."""
    return (
        item.startswith("permit_day_gap_")
        or item.startswith("permit_year_gap:")
        or item.startswith("order_by_day_gap:")
        or item
        in {
            "permit_after_approval",
            "permit_approval_year_neq",
        }
    )


def assumptions_include_permit_lag(assumptions: list[str] | None) -> bool:
    return any(is_permit_lag_assumption(a) for a in (assumptions or []))


def extract_structures(question: str) -> list[tuple[str, str]]:
    """질문에 등장하는 구조 표현을 긴 별칭 우선·비중첩으로 모은다."""
    q = question or ""
    # 블럭지번(특수지)은 건축물구조 '블럭'과 구분
    skip_structure_aliases: set[str] = set()
    if any(k in q for k in ("블럭지번", "블록지번", "블럭 지번", "블록 지번")):
        skip_structure_aliases.update({"블럭", "블록", "블럭구조", "블록구조"})
    spans: list[tuple[int, int, str, str]] = []
    for alias in sorted(STRUCTURE_ALIASES, key=len, reverse=True):
        if alias in skip_structure_aliases:
            continue
        start = 0
        while True:
            i = q.find(alias, start)
            if i < 0:
                break
            spans.append((i, i + len(alias), alias, STRUCTURE_ALIASES[alias]))
            start = i + len(alias)
    spans.sort(key=lambda item: item[0])
    occupied: list[tuple[int, int]] = []
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for s, e, alias, pattern in spans:
        if any(not (e <= a or s >= b) for a, b in occupied):
            continue
        occupied.append((s, e))
        if alias in seen:
            continue
        seen.add(alias)
        found.append((alias, pattern))
    return found


def extract_special_land(question: str) -> tuple[str, str] | None:
    """질문의 특수지(A6/A7) 표현 → (표시명, SQL 조건)."""
    q = question.strip()
    if "산업단지" in q and "산지" not in q:
        return None
    mountain = (
        "산지",
        "산번지",
        "산 번지",
        "산 지번",
        "임야",
        "산에 있는",
        "산 위에",
        "산위에",
        "특수지가 산",
        "특수지구분 산",
        "특수지구분이 산",
    )
    if any(k in q for k in mountain):
        return (
            "산지",
            '("A6"::text = \'2\' OR TRIM(COALESCE("A7", \'\')) = \'산\')',
        )
    if any(k in q for k in ("일반지번", "일반 지번", "특수지 일반", "특수지구분 일반")):
        return (
            "일반지번",
            '("A6"::text = \'1\' OR TRIM(COALESCE("A7", \'\')) = \'일반\')',
        )
    if any(k in q for k in ("가지번", "가지 번지")):
        return (
            "가지번",
            '("A6"::text IN (\'3\', \'4\') OR COALESCE("A7", \'\') ILIKE \'%가지%\')',
        )
    if any(k in q for k in ("블럭지번", "블록지번", "블럭 지번", "블록 지번")):
        return (
            "블럭지번",
            (
                '("A6"::text IN (\'5\', \'6\', \'7\', \'8\') OR COALESCE("A7", \'\') ILIKE \'%블럭%\' '
                "OR COALESCE(\"A7\", '') ILIKE '%블록%')"
            ),
        )
    return None

# 구 단위 용도별건물(사용승인·허가일자 보유).
# 본선은 런타임 coverage (`set_d198_coverage` / data.coverage.refresh_dataset_coverage).
# discover_d198_coverage() 가 DB 업로드 테이블로 D198_BY_GU 를 채운다.
D198_BY_GU: dict[str, str] = {}

# 건축 경과년수 / 준공·사용승인 관련 표현
AGE_HINTS = (
    "지어진",
    "지어진지",
    "지어진 후",
    "건축년",
    "건축 년",
    "건축년수",
    "건축연령",
    "건축 연령",
    "준공",
    "준공일",
    "사용승인",
    "사용승인일",
    "허가일",
    "허가일자",
    "경과 년",
    "경과년",
    "년수",
    "년 넘",
    "년이 넘",
    "년된",
    "년 된",
    "년 이상",
    "년이상",
    "년 이하",
    "년 미만",
    "년미만",
    "오래된",
)

# 모듈이 `from txt2sql.domain import D198_TABLES` 해도 갱신이 보이게 리스트를 제자리 수정한다.
D198_TABLES: list[str] = []


# '공동주택'의 '공동' 등 지명이 아닌 ~동 오탐
_FALSE_DONG = frozenset(
    {
        "공동",
        "자동",
        "수동",
        "유동",
        "구동",
        "운동",
        "행동",
        "활동",
        "진동",
        "변동",
        "연동",
        "가동",
        "충동",
        "행정동",
        "법정동",
    }
)


def extract_place(question: str) -> str | None:
    """질문에서 동 또는 구 명칭을 추출 (동 우선)."""
    places = extract_places(question)
    return places[0] if places else None


def extract_places(question: str) -> list[str]:
    """질문에 등장하는 동·구를 순서대로 (중복 제거).

    전국 지명 사전이 있으면 등록된 법정동·행정동·구군만 최장일치한다.
    """
    from txt2sql.gazetteer import find_places, load_gazetteer

    gaz = load_gazetteer()
    if gaz.legal_dong or gaz.admin_dong:
        found: list[str] = []
        for hit in find_places(question):
            suffix = question[hit.end : hit.end + 3]
            if (
                (hit.name.endswith("면") and suffix.startswith("적"))
                or (hit.name.endswith("구") and suffix.startswith("분명"))
                or (hit.name.endswith("동") and suffix.startswith(("코드", "별")))
            ):
                continue
            # 「행정동」「법정동」안의 정동 부분일치 차단
            before = question[max(0, hit.start - 1) : hit.start]
            if hit.name == "정동" and before in {"행", "법"}:
                continue
            if hit.name not in found:
                found.append(hit.name)
        return found

    found = []
    for m in DONG_RE.finditer(question):
        dong = m.group(1)
        if dong in _FALSE_DONG:
            continue
        after = question[m.end() : m.end() + 2]
        if after.startswith(("주택", "시설", "차", "력", "원", "사")):
            continue
        if dong not in found:
            found.append(dong)
    if found:
        return found
    for m in GU_RE.finditer(question):
        gu = m.group(1)
        if gu not in BUSAN_GU_CODES:
            continue
        if gu not in found:
            found.append(gu)
    return found


def extract_gu(question: str) -> str | None:
    from txt2sql.gazetteer import find_places, load_gazetteer

    gaz = load_gazetteer()
    if gaz.sigungu:
        for hit in find_places(question):
            if hit.is_sigungu:
                suffix = question[hit.end : hit.end + 3]
                if suffix.startswith(("분명", "코드", "별")):
                    continue
                return hit.name
    for m in GU_RE.finditer(question):
        gu = m.group(1)
        if gu in BUSAN_GU_CODES:
            return gu
    return None


def extract_usage(question: str) -> str | None:
    """질문의 용도 별칭 → DB 건축물용도명(A9). 여러 개면 첫 번째."""
    usages = extract_usages(question)
    return usages[0] if usages else None


_GENERIC_INDUSTRIAL = frozenset(
    {
        "산업단지",
        "국가산업단지",
        "일반산업단지",
        "지방산업단지",
        "도시첨단산업단지",
        "농공단지",
        "자유무역지역",
    }
)


def extract_industrial_name(question: str) -> str | None:
    """고유 단지명. 유형명(국가산업단지 등)만 있으면 None."""
    names = extract_industrial_names(question)
    return names[0] if names else None


def extract_industrial_names(question: str) -> list[str]:
    """병렬 고유명(명지·녹산)과 단일 단지명을 모은다."""
    q = question or ""
    found: list[str] = []
    parallel = re.search(
        r"([가-힣0-9]{2,20})\s*[·･、,/]\s*([가-힣0-9]{2,20})\s*"
        r"((?:국가|일반|지방)?산업단지)",
        q,
    )
    if parallel:
        suffix = parallel.group(3)
        left, right = parallel.group(1), parallel.group(2)
        for stem in (left + suffix, right + suffix, f"{left},{right}{suffix}"):
            if stem not in found and stem not in _GENERIC_INDUSTRIAL:
                found.append(stem)
    for match in re.finditer(r"([가-힣0-9]{2,30}산업단지)", q):
        name = match.group(1)
        if name in _GENERIC_INDUSTRIAL:
            continue
        if name not in found:
            found.append(name)
    for match in re.finditer(
        r"([가-힣0-9]{2,20})\s+((?:국가|일반|지방|도시첨단)산업단지)",
        q,
    ):
        spaced = f"{match.group(1)} {match.group(2)}"
        combined = match.group(1) + match.group(2)
        for name in (spaced, combined):
            if name not in found and name not in _GENERIC_INDUSTRIAL:
                found.append(name)
    return found


def dong_requires_gu(name: str | None) -> bool:
    """구 없이 실행하면 동음이의가 섞이는 동명."""
    return bool(name) and name in MULTI_GU_DONGS


_BUILDING_KIND_HINTS = (
    "아파트",
    "건물",
    "빌라",
    "오피스텔",
    "단지",
    "주택",
    "건축물",
)
_BUILDING_NAME_EXPLICIT = (
    "건물명",
    "건물이름",
    "건물 이름",
    "아파트명",
    "단지명",
    "건물명칭",
)
_BUILDING_LOOKUP_HINTS = (
    "정보",
    "있",
    "알려",
    "찾아",
    "검색",
    "조회",
    "뭐야",
    "무엇",
    "대한",
    "관련",
    "설명",
    "대하여",
    "대해",
    "소개",
)
_BUILDING_ATTR_HINTS = (
    "주소",
    "지번",
    "위치",
    "어디",
    "높이",
    "연면적",
    "건물면적",
    "면적",
    "층수",
    "몇 층",
    "몇층",
    "용도",
    "구조",
    "이름",
    "건물명",
    "시공년도",
    "시공연도",
    "시공년",
    "시공일",
    "준공년도",
    "준공연도",
    "준공일",
    "건축년도",
    "건축연도",
    "건설년도",
    "건설연도",
    "건립년도",
    "건립연도",
    "사용승인일",
    "사용승인일자",
    "허가일",
    "허가일자",
    "건설일",
)
_NAME_STRIP_PHRASES = (
    "에 대한",
    "에대한",
    "에 대하여",
    "에대하여",
    "에 대해",
    "에대해",
    "에 관하여",
    "대하여",
    "대해",
    "설명하라",
    "설명해줘",
    "설명해 줘",
    "설명해주세요",
    "설명 해줘",
    "설명",
    "정보가 있는가",
    "정보가 있나요",
    "정보 있나",
    "정보있나",
    "정보 있어",
    "정보있어",
    "있나요",
    "있는가",
    "있을까",
    "알려줘",
    "알려 줘",
    "찾아줘",
    "찾아 줘",
    "찾아라",
    "찾아주세요",
    "찾아 주세요",
    "검색해",
    "검색하라",
    "검색해라",
    "조회해",
    "조회하라",
    "조회해라",
    "보여줘",
    "보여 줘",
    "보여주세요",
    "보여 주세요",
    "보여라",
    "표시해줘",
    "표시해 줘",
    "집계해줘",
    "집계해 줘",
    "정렬해줘",
    "정렬해 줘",
    "건물명",
    "건물이름",
    "건물 이름",
    "아파트명",
    "단지명",
    "주소는",
    "주소가",
    "주소",
    "지번은",
    "지번이",
    "지번",
    "위치는",
    "위치가",
    "위치",
    "높이는",
    "높이가",
    "높이",
    "연면적은",
    "연면적이",
    "연면적",
    "건물면적은",
    "건물면적이",
    "건물면적",
    "면적은",
    "면적이",
    "면적",
    "시공년도는",
    "시공년도",
    "시공연도는",
    "시공연도",
    "시공년",
    "시공일",
    "준공년도는",
    "준공년도",
    "준공연도",
    "준공일은",
    "준공일",
    "건축년도는",
    "건축년도",
    "건축연도",
    "건설년도는",
    "건설년도",
    "건립년도",
    "사용승인일자는",
    "사용승인일은",
    "사용승인일자",
    "사용승인일",
    "허가일은",
    "허가일자",
    "허가일",
    "건설일",
    "이상인것은",
    "이상인것",
    "이상은",
    "이상인 것",
    "이상",
    "이하인것은",
    "이하인것",
    "이하는",
    "이하인 것",
    "이하",
    "미만인것은",
    "미만인것",
    "미만은",
    "미만인 것",
    "미만",
    "초과인것은",
    "초과인것",
    "초과는",
    "초과인 것",
    "초과",
    "용도는",
    "용도가",
    "용도",
    "구조는",
    "구조가",
        "구조물",
        "구조",
        "콘크리트",
        "철근콘크리트",
        "산지",
        "산번지",
        "특수지",
    "층수는",
    "층수가",
    "층수",
    "몇층",
    "몇 층",
)
_NAME_STOP = frozenset(
    {
        "대한",
        "대하여",
        "대해",
        "관련",
        "정보",
        "설명",
        "설명하라",
        "주소",
        "지번",
        "위치",
        "높이",
        "연면적",
        "건물면적",
        "면적",
        "중에",
        "이중에",
        "이중",
        "이후",
        "이전",
        "이래",
        "지어진",
        "지은",
        "표시하라",
        "표시",
        "표출",
        "년도별",
        "각년도별",
        "이상",
        "이하",
        "초과",
        "미만",
        "넘는",
        "용도",
        "구조",
        "구조물",
        "콘크리트",
        "산지",
        "건폐율",
        "용적율",
        "용적률",
        "집합건축물",
        "표제부",
        "주건축물",
        "부속건축물",
        "세부용도",
        "용도분류",
        "주요용도명",
        "사용승인일자",
        "사용승인일",
        "시공년도",
        "시공연도",
        "시공년",
        "준공년도",
        "준공일",
        "건축년도",
        "건축연도",
        "건설년도",
        "건립년도",
        "허가일자",
        "허가일",
        "지하층",
        "종류",
        "층수",
        "있",
        "있는",
        "있는지",
        "있나",
        "있어",
        "주세요",
        "해줘",
        "하라",
        "찾아라",
        "찾아줘",
        "찾아",
        "검색하라",
        "조회하라",
        "보여줘",
        "보여",
        "보여주세요",
        "보여라",
        "표시해줘",
        "집계해줘",
        "정렬해줘",
        "알려줘",
        "좀",
        "그",
        "저",
        "이",
        "해당",
        "건물",
        "건축물",
        "아파트",
        "단지",
        "가장",
        "제일",
        "최대",
        "상위",
        "최고",
        "이름",
        "명칭",
        "뭐",
        "무엇",
        "어떤",
        "부산",
        "부산시",
        "부산광역시",
        "주변",
        "근처",
        "인근",
        "버퍼",
        "반경",
        "이내",
        "안에",
        "내부",
        "건수",
        "개수",
        "얼마",
        "얼마나",
        "예쁜",
        "멋진",
        "좋은",
        "괜찮은",
        "자동문",
        "오래된",
        "퍼센트",
        "퍼센트씩",
        "비율",
        "프로",
    }
)


def _replace_hangul_word(text: str, word: str) -> str:
    """한글 단어만 통째로 치환. '학교'가 '부산대학교' 안에서 잘리지 않게 한다."""
    if not word:
        return text
    return re.sub(rf"(?<![가-힣]){re.escape(word)}(?![가-힣])", " ", text)


def extract_building_name_candidate(question: str) -> str | None:
    """질문에서 동·구·용도·조사 문구를 제거한 건물명 후보."""
    text = question.strip()
    if not text:
        return None
    for place in extract_places(text):
        text = text.replace(place, " ")
    gu = extract_gu(question)
    if gu:
        text = text.replace(gu, " ")
    for alias in sorted(USAGE_ALIASES, key=len, reverse=True):
        text = _replace_hangul_word(text, alias)
    for alias in sorted(DETAIL_USAGE_ALIASES, key=len, reverse=True):
        text = _replace_hangul_word(text, alias)
    for alias in sorted(USAGE_CLASS_ALIASES, key=len, reverse=True):
        text = _replace_hangul_word(text, alias)
    for phrase in sorted(_NAME_STRIP_PHRASES, key=len, reverse=True):
        text = text.replace(phrase, " ")
    text = re.sub(r"[0-9a-zA-Z_\"'.,?？!！()[\]{}]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    tokens = re.findall(r"[가-힣]{2,}", text)
    kept: list[str] = []
    for t in tokens:
        t2 = re.sub(
            r"(은|는|이|가|을|를|의|과|와|도|만|에서|에게|에|으로|로|까지|부터|보다|중)$",
            "",
            t,
        )
        if len(t2) < 2 or t2 in _NAME_STOP:
            continue
        if t2.endswith(("동", "구", "시", "군")):
            continue
        if t2 not in kept:
            kept.append(t2)
    if not kept:
        return None
    # 「구서역포르투나」처럼 붙여 쓴 경우 역 기준으로 분리
    expanded: list[str] = []
    for t in kept:
        m = re.match(r"^(.+역)(.+)$", t)
        if m and len(m.group(2)) >= 2:
            for part in (m.group(1), m.group(2)):
                if part not in expanded:
                    expanded.append(part)
        else:
            if t not in expanded:
                expanded.append(t)
    return " ".join(expanded[:4])


def looks_like_measure_threshold(question: str) -> bool:
    """면적·높이·층수 등 수치 임계(이상/이하) 질의."""
    q = question.strip()
    if not q or not re.search(r"\d+", q):
        return False
    if not any(k in q for k in ("이상", "이하", "초과", "미만", "넘는")):
        return False
    return any(
        k in q
        for k in (
            "연면적",
            "건물면적",
            "건축면적",
            "대지면적",
            "면적",
            "높이",
            "층수",
            "지상층",
            "미터",
            "평",
            "킬로미터",
            "제곱미터",
        )
    )


def looks_like_building_name_lookup(question: str) -> bool:
    """특정 건물명(고유명사) 존재·정보 조회 질의인지."""
    q = question.strip()
    if not q:
        return False
    # 산업단지·기초구역·스키마 질의는 건물명 조회가 아님
    if any(
        k in q
        for k in (
            "산업단지",
            "기초구역",
            "GIS",
            "AL_D",
            "데이터셋",
            "어떤 데이터",
            "데이터가 있",
            "사용가능",
            "테이블",
            "컬럼",
            "칼럼",
            "스키마",
            "속성 설명",
            "속성데이터",
            "속성 데이터",
            "필드목록",
            "필드 목록",
        )
    ):
        return False
    # 「…속성데이터는?」처럼 스키마 나열은 건물명 조회가 아님
    if "속성" in q and any(k in q for k in ("데이터", "컬럼", "칼럼", "필드", "스키마")):
        return False
    # 업로드/표시명형 데이터셋 제목(밑줄·고유 토큰)은 건물명 조회가 아님
    if "_" in q and any(
        k in q
        for k in (
            "행정동",
            "용지",
            "면적",
            "정보",
            "구역",
            "단지",
            "시가화",
            "활동인구",
        )
    ):
        return False
    if looks_like_measure_threshold(q):
        return False
    # 세부용도·용도분류 목록은 건물명 조회가 아님
    if extract_detail_usages(q) or extract_usage_classes(q):
        if any(
            k in q
            for k in ("건물명", "지번", "보여", "찾아", "목록", "중 연면적", "중 높이")
        ):
            return False
    # 경과년수·용도분류는 건물명 조회가 아님
    if looks_like_age_question(q):
        return False
    if extract_usage_classes(q):
        return False
    if extract_calendar_year(q) is not None and any(
        k in q for k in ("지어", "준공", "사용승인", "이후", "이전", "이래", "까지")
    ):
        return False
    if any(k in q for k in ("평균", "합계", "용도별", "분포")):
        return False
    # 주관 형용사는 건물명이 아니라 clarify 경로로 보낸다
    if any(
        k in q
        for k in (
            "예쁜",
            "멋진",
            "좋은",
            "괜찮은",
            "핫한",
            "유명한",
            "추천",
            "오래된",
            "오래 된",
            "낡은",
        )
    ) and not any(k in q for k in ("가장 오래", "제일 오래")):
        return False
    # 장소+용도/건물 건수는 고유명사 조회가 아님
    place_hit = extract_place(q) or extract_gu(q)
    countish = any(
        k in q
        for k in (
            "몇",
            "건수",
            "개수",
            "채수",
            "채야",
            "수는",
            "수가",
            "얼마",
            "얼마나",
        )
    )
    if countish and extract_usage(q):
        return False
    if place_hit and countish and (
        extract_usage(q)
        or any(k in q for k in ("건물", "건축물"))
    ):
        return False
    if len(extract_places(q)) >= 2 and any(k in q for k in ("건물", "건축물", "채")):
        return False
    if any(k in q for k in ("주변", "근처", "인근", "버퍼", "반경")) and re.search(
        r"\d+(?:\.\d+)?\s*(?:킬로미터|㎞|km|미터|m)",
        q,
    ):
        return False
    if re.search(
        r"[가-힣0-9]{1,12}동\s*(?:안(?:에|쪽)?|내부|경계\s*안)",
        q,
    ) and any(k in q for k in ("건물", "건축물", "채")):
        return False
    if any(k in q for k in ("교차", "겹치", "인접", "맞닿", "버퍼", "반경")) and any(
        k in q for k in ("기초구역", "행정동", "행정구역", "건물", "건축물")
    ):
        return False
    if any(k in q for k in ("퍼센트", "비율", "몇%", "%씩", "몇 프로")):
        return False
    if ("종류" in q and "용도" in q) or "몇 가지" in q or "몇가지" in q:
        return False
    if extract_structure(q):
        return False
    if extract_special_land(q):
        return False
    # 순위·최댓값 질의는 건물명 조회가 아님
    if any(
        k in q
        for k in (
            "가장",
            "제일",
            "최대",
            "상위",
            "1등",
            "최고",
            "큰 순",
            "높은 순",
        )
    ):
        return False
    name = extract_building_name_candidate(q)
    if not name:
        return False
    name_tokens = [
        t for t in name.split() if t not in _NAME_STOP and len(t) >= 2
    ]
    # 「건물명과 지번」「건물명이 있는」만 있고 고유명사 후보가 없으면 목록 질의
    column_only = re.search(r"건물명\s*(과|와|,|/)", q) or any(
        k in q
        for k in (
            "건물명이 있는",
            "건물명 있는",
            "건물명이 없",
            "건물명 없",
            "건물명이 기록",
            "이름과 지번",
            "명칭과 지번",
        )
    )
    if column_only and not name_tokens:
        return False
    # 일반 명사만 남은 경우 제외
    if name.replace(" ", "") in {
        "산업단지",
        "기초구역",
        "산업",
        "단지",
        "건물",
        "아파트",
        "주택",
        "정보",
        "자료",
        "내용",
        "결과",
    }:
        return False
    if any(k in q for k in ("차이", "크기별", "용도별", "사유별", "이동사유", "몇 채", "채수", "세부용도", "주요용도", "용도분류")):
        return False
    compact = name.replace(" ", "")
    if len(compact) < 2:
        return False
    distinctive = len(compact) >= 3
    if not distinctive:
        from txt2sql.d198_attrs import is_d198_attr_question
        from txt2sql.catalog_attrs import is_catalog_attr_question

        if is_d198_attr_question(q) or is_catalog_attr_question(q):
            return False
    explicit = any(k in q for k in _BUILDING_NAME_EXPLICIT)
    if explicit:
        # 「건물명」만 출력 컬럼으로 언급되고 고유명사 후보가 없으면 목록 질의
        return bool(name_tokens) and distinctive
    has_place = bool(extract_place(q) or extract_gu(q))
    has_kind = any(k in q for k in _BUILDING_KIND_HINTS)
    has_lookup = any(k in q for k in _BUILDING_LOOKUP_HINTS)
    has_attr = any(k in q for k in _BUILDING_ATTR_HINTS)
    # 「구서역포르투나 아파트의 주소는?」— 단지명+용도+속성
    if has_kind and (has_lookup or has_attr) and distinctive:
        return True
    # 「구서역 포르투나의 시공년도는」— 고유명사 + 속성 (아파트/찾기 없이도)
    if has_attr and distinctive:
        return True
    # 「부산대학교를 찾아라」— 고유명사 + 찾기 (아파트/동 없이도)
    if has_lookup and distinctive:
        return True
    return has_place and has_kind and (has_lookup or has_attr)


def extract_usages(question: str) -> list[str]:
    """질문에 등장하는 용도들을 순서대로 (중복 제거)."""
    found: list[str] = []
    q = question or ""
    if re.search(
        r"제1\s*[·･、,/]\s*2종|제1종\s*[·･、,/]\s*제?2종|제1·2종",
        q,
    ):
        found.extend(["제1종근린생활시설", "제2종근린생활시설"])
    # 세부용도 전용 표현(다가구주택·오피스텔 등)은 USAGE 부분일치(다가구→단독)를 막는다.
    # 아파트처럼 USAGE에도 있는 별칭은 D010 경로를 위해 남겨 둔다.
    detail_occupied: list[tuple[int, int]] = []
    for alias in sorted(DETAIL_USAGE_ALIASES, key=len, reverse=True):
        if alias in USAGE_ALIASES:
            continue
        start = 0
        while True:
            i = q.find(alias, start)
            if i < 0:
                break
            detail_occupied.append((i, i + len(alias)))
            start = i + len(alias)
    # 긴 별칭 우선 매칭을 위해 위치 기반으로 스캔
    spans: list[tuple[int, int, str]] = []
    for alias in sorted(USAGE_ALIASES, key=len, reverse=True):
        start = 0
        while True:
            i = q.find(alias, start)
            if i < 0:
                break
            mapped = USAGE_ALIASES[alias]
            spans.append((i, i + len(alias), mapped))
            start = i + len(alias)
    spans.sort(key=lambda x: x[0])
    occupied: list[tuple[int, int]] = list(detail_occupied)
    for s, e, mapped in spans:
        if any(not (e <= a or s >= b) for a, b in occupied):
            continue
        occupied.append((s, e))
        if mapped not in found:
            found.append(mapped)
    return found


def d198_gu_for_dong(dong: str | None, *, question: str = "") -> str | None:
    """법정동명 → D198 커버리지에 등록된 시군구."""
    from txt2sql.gazetteer import sigungu_for_legal_dong

    name = (dong or "").strip()
    if not name:
        return None
    if name.endswith(("구", "군")):
        return name if name in D198_BY_GU else None
    if name in MULTI_GU_DONGS and not extract_gu(question):
        return None
    gu = sigungu_for_legal_dong(name, question=question)
    if gu and gu in D198_BY_GU:
        return gu
    return None


def extract_detail_usages(question: str) -> list[str]:
    """D198 세부용도명(A27) 별칭."""
    return _alias_hits(question, DETAIL_USAGE_ALIASES)


def extract_usage_classes(question: str) -> list[str]:
    """D198 용도분류명(A29) 별칭."""
    return _alias_hits(question, USAGE_CLASS_ALIASES)


def _alias_hits(question: str, aliases: dict[str, str]) -> list[str]:
    q = question or ""
    found: list[str] = []
    spans: list[tuple[int, int, str]] = []
    for alias in sorted(aliases, key=len, reverse=True):
        start = 0
        while True:
            i = q.find(alias, start)
            if i < 0:
                break
            spans.append((i, i + len(alias), aliases[alias]))
            start = i + len(alias)
    spans.sort(key=lambda item: item[0])
    occupied: list[tuple[int, int]] = []
    for s, e, mapped in spans:
        if any(not (e <= a or s >= b) for a, b in occupied):
            continue
        occupied.append((s, e))
        if mapped not in found:
            found.append(mapped)
    return found


def busan_gu_code(gu: str | None) -> str | None:
    """구·군 명칭 → 시군구 법정동코드(A3 접두). gazetteer 우선."""
    from txt2sql.gazetteer import sigungu_a3_prefix

    return sigungu_a3_prefix(gu)


def place_a4_predicate(place: str) -> str:
    """AL_D010 위치 필터. 구·군은 A3 접두, 법정동은 A4. (place_scope 위임)"""
    from txt2sql.place_scope import building_place_predicate

    return building_place_predicate(place)


def gu_from_pnu_code(pnu_code: str) -> str | None:
    """시군구 PNU(5자리) → 구·군 이름 (gazetteer 역조회, 부산 폴백)."""
    code = (pnu_code or "").strip()
    from txt2sql.gazetteer import load_gazetteer

    for name, value in load_gazetteer().sigungu_pnu_prefix.items():
        if value == code:
            return name
    for name, value in BUSAN_GU_CODES.items():
        if value == code:
            return name
    return None


def gu_from_d198_table(table: str) -> str | None:
    """AL_D198_{시군구코드}_{YYYYMMDD} → 구 이름. 시 단위(26) 등은 None."""
    from txt2sql.data.names import parse_al_table_name

    parsed = parse_al_table_name((table or "").split(".")[-1])
    if parsed is None or parsed.get("data_code") != "AL_D198":
        return None
    return gu_from_pnu_code(parsed["pnu_code"])


def set_d198_coverage(by_gu: dict[str, str]) -> None:
    """질의 엔진이 쓸 구→D198 테이블 맵을 갱신한다. 빈 dict 는 무시한다.

    정렬 키는 gazetteer PNU(없으면 99999). BUSAN_GU_CODES 는 폴백만.
    """
    if not by_gu:
        return
    from txt2sql.gazetteer import sigungu_a3_prefix

    def _sort_key(item: tuple[str, str]) -> str:
        gu_name = item[0]
        return sigungu_a3_prefix(gu_name) or BUSAN_GU_CODES.get(gu_name, "99999")

    ordered = dict(sorted(by_gu.items(), key=_sort_key))
    D198_BY_GU.clear()
    D198_BY_GU.update(ordered)
    D198_TABLES.clear()
    seen: list[str] = []
    for table in ordered.values():
        if table not in seen:
            seen.append(table)
    D198_TABLES.extend(seen)


def reset_d198_coverage() -> None:
    D198_BY_GU.clear()
    D198_TABLES.clear()


def d198_coverage_label(*, joiner: str = "·") -> str:
    names = list(D198_BY_GU.keys())
    if not names:
        return "등록된 구·군"
    return joiner.join(names)


def d198_unavailable_reason(feature: str = "해당 속성") -> str:
    """D198 미커버 시 사용자·플랜 안내 문구."""
    label = d198_coverage_label()
    if not D198_BY_GU:
        return f"{feature}은 용도별건물(D198) 데이터가 등록된 구·군에서만 조회할 수 있습니다"
    return (
        f"{feature}은 용도별건물(D198) 데이터가 등록된 구·군"
        f"({label})에서 조회할 수 있습니다"
    )


def d198_gu_matches(question: str, gu: str) -> bool:
    if gu in question:
        return True
    stem = gu.replace("구", "").replace("군", "")
    return len(stem) >= 2 and stem in question


def d198_gu_mentioned(question: str) -> str | None:
    for gu in D198_BY_GU:
        if d198_gu_matches(question, gu):
            return gu
    return None


def d198_gus_mentioned(question: str) -> list[str]:
    return [gu for gu in D198_BY_GU if d198_gu_matches(question, gu)]


def d198_table_for_gu(gu: str | None) -> str | None:
    if not gu:
        return None
    return D198_BY_GU.get(gu)


def is_permit_approval_lag_question(question: str) -> bool:
    """허가↔사용승인/준공 시차(걸린 기간·일수) 질의 — 건축 경과년수와 구분."""
    q = question or ""
    if "허가" not in q:
        return False
    if not any(k in q for k in ("사용승인", "준공")):
        return False
    return any(
        k in q
        for k in (
            "걸린",
            "허가 후",
            "허가일부터",
            "허가일에서",
            "허가일이",
            "허가연",
            "일수",
            "이내 준공",
            "기간",
        )
    )


def looks_like_age_question(question: str) -> bool:
    q = question or ""
    # 허가→승인 시차는 건축연령(경과년수) 라우트 대상이 아님
    if is_permit_approval_lag_question(q):
        return False
    if any(k in q for k in ("NULL", "null", "없는", "비어", "결측", "누락")):
        if any(k in q for k in ("사용승인일", "허가일", "준공일")) and not any(
            k in q
            for k in ("년", "오래", "최근", "이상", "이내", "미만", "넘", "경과", "된 지")
        ):
            return False
    if extract_age_years(q) is not None:
        return True
    return any(k in q for k in AGE_HINTS)


def is_vague_age_threshold(question: str) -> bool:
    """'오래된 단독주택은 몇 채'처럼 경과년수 숫자가 없는 주관 표현.

    '가장 오래된' 순위 질의는 제외한다.
    """
    q = question or ""
    if extract_age_years(q) is not None or extract_calendar_year(q) is not None:
        return False
    if any(
        k in q
        for k in ("가장 오래", "제일 오래", "가장 먼저 지어", "제일 먼저 지어")
    ):
        return False
    return any(k in q for k in ("오래된", "오래 된", "오래전", "낡은"))


def extract_calendar_year(question: str) -> tuple[int, str | None] | None:
    """'2020년 이후'처럼 달력 연도. 경과년수(30년)와 구분한다."""
    m = re.search(
        r"((?:19|20)\d{2})\s*년\s*(이후|이전|이래|까지)?",
        question,
    )
    if not m:
        return None
    return int(m.group(1)), m.group(2)


def calendar_year_predicate_sql(
    question: str,
    *,
    col: str = "A13",
    prefix: str = "",
) -> str | None:
    """사용승인·허가일 텍스트 컬럼에 대한 달력 연도 SQL."""
    hit = extract_calendar_year(question)
    if hit is None:
        return None
    year, rel = hit
    qcol = f'{prefix}"{col}"'
    yexpr = f"LEFT(regexp_replace({qcol}::text, '[^0-9]', '', 'g'), 4)"
    valid = f"({qcol}::text ~ '^[0-9]{{4}}')"
    if rel in {"이후", "이래"}:
        return f"{valid} AND {yexpr} >= '{year}'"
    if rel in {"이전", "까지"}:
        return f"{valid} AND {yexpr} < '{year}'"
    return f"{valid} AND {yexpr} = '{year}'"


def extract_age_years(question: str) -> int | None:
    """'30년 넘은', '10년 미만', '건축 20년' 등에서 연수 추출.

    1900~2100은 달력 연도('2020년 이후')로 보고 경과년수에서 제외한다.
    'N년 이상 걸린'(허가→승인 시차)도 경과년수에서 제외한다.
    """
    if extract_calendar_year(question) is not None:
        return None
    if is_permit_approval_lag_question(question):
        return None
    if re.search(r"\d+\s*년\s*(단위|간격|별|씩)", question):
        return None
    if re.search(r"[가-힣]+\s*년\s*(단위|간격|별|씩)", question):
        return None
    # 「N년 이상 걸린」은 시차 — 건축연령 연수로 쓰지 않음
    if re.search(r"\d+\s*년\s*(?:이\s*)?(?:이상|초과|넘)\s*걸린", question):
        return None
    m = re.search(
        r"(\d+)\s*년\s*(?:이\s*)?(?:넘|이상|이하|미만|초과|이내|된|지남|경과)?",
        question,
    )
    if m:
        return int(m.group(1))
    return None


def extract_age_compare(question: str) -> str:
    """경과년수 비교 방향.

    - lt: N년 미만 (더 최근) → 사용승인일 > today-N
    - lte: N년 이하 / 최근 N년(내) → 사용승인일 >= today-N
    - gt: N년 초과
    - gte: N년 이상/넘는 (기본)
    """
    if re.search(r"\d+\s*년\s*(?:이\s*)?미만", question) or "채 안" in question:
        return "lt"
    # 최근 N년 / N년 내 / N년 이내 → 최근 구간
    if re.search(r"최근\s*\d+\s*년", question):
        return "lte"
    if re.search(r"\d+\s*년\s*(?:이\s*)?(?:이내|내)", question):
        return "lte"
    if re.search(r"\d+\s*년\s*(?:이\s*)?이하", question):
        return "lte"
    if re.search(r"\d+\s*년\s*(?:이\s*)?(?:초과|넘는)", question) and "이상" not in question:
        # '넘는' alone often means >= in user speech; keep gte unless 초과
        if "초과" in question:
            return "gt"
    if re.search(r"\d+\s*년\s*(?:이\s*)?(?:이상|넘)", question):
        return "gte"
    return "gte"


def is_busan_wide(question: str) -> bool:
    return any(
        k in question
        for k in (
            "부산광역시",
            "부산시",
            "부산 전체",
            "부산내",
            "부산 내",
            "부산에서",
            "부산시에서",
            "부산광역시에서",
            "부산시에",
            "부산에",
            "부산만",
            "부산 ",
        )
    ) or question.strip().startswith("부산")


def has_batchim(text: str) -> bool:
    """마지막 글자에 받침이 있으면 True."""
    s = (text or "").strip()
    if not s:
        return False
    ch = s[-1]
    if "가" <= ch <= "힣":
        return (ord(ch) - 0xAC00) % 28 != 0
    if ch.isdigit():
        return ch in "013678"  # 대략적: 받침 느낌의 숫자 발음
    return False


def with_topic(text: str) -> str:
    """주제 조사 은/는."""
    s = (text or "").strip() or "해당 조건"
    return f"{s}{'은' if has_batchim(s) else '는'}"


def with_subject(text: str) -> str:
    """주격 조사 이/가."""
    s = (text or "").strip() or "해당 조건"
    return f"{s}{'이' if has_batchim(s) else '가'}"


def with_object(text: str) -> str:
    """목적격 조사 을/를."""
    s = (text or "").strip() or "해당 조건"
    return f"{s}{'을' if has_batchim(s) else '를'}"


def fix_dual_particles(text: str) -> str:
    """템플릿/모델이 남긴 '은(는)' 형태를 실제 조사로 교정."""
    if not text:
        return text

    def _repl_topic(m: re.Match[str]) -> str:
        return with_topic(m.group(1))

    def _repl_subj(m: re.Match[str]) -> str:
        return with_subject(m.group(1))

    def _repl_obj(m: re.Match[str]) -> str:
        return with_object(m.group(1))

    out = re.sub(r"([가-힣A-Za-z0-9]+)\s*은\(는\)", _repl_topic, text)
    out = re.sub(r"([가-힣A-Za-z0-9]+)\s*이\(가\)", _repl_subj, out)
    out = re.sub(r"([가-힣A-Za-z0-9]+)\s*을\(를\)", _repl_obj, out)
    return out


def sane_height_sql(
    height_col: str = "A16",
    floors_col: str = "A26",
    *,
    max_m: float = 600.0,
) -> str:
    """비정상 높이(날짜 오인입·단위 오류)를 제외하는 SQL 조건.

    층수와 함께 있을 때 층당 약 8m+여유를 넘는 값은 제외한다.
    """
    return (
        f'"{height_col}" > 0 AND "{height_col}" <= {max_m:g} AND '
        f'("{floors_col}" IS NULL OR "{height_col}" <= ("{floors_col}" * 8 + 30))'
    )


def sane_footprint_sql(
    area_col: str = "A12",
    floor_area_col: str = "A14",
    *,
    max_m2: float = 500_000.0,
) -> str:
    """비정상 건물면적(건축면적)을 제외.

    연면적보다 건물면적이 크게 큰 값은 오류로 본다(건축면적 ≤ 연면적).
    """
    return (
        f'"{area_col}" > 0 AND "{area_col}" <= {max_m2:g} AND '
        f'("{floor_area_col}" IS NULL OR "{floor_area_col}" <= 0 OR '
        f'"{area_col}" <= "{floor_area_col}" * 1.05 + 50)'
    )


def sane_floor_area_sql(
    floor_area_col: str = "A14",
    *,
    max_m2: float = 2_000_000.0,
) -> str:
    """비정상 연면적 제외."""
    return f'"{floor_area_col}" > 0 AND "{floor_area_col}" <= {max_m2:g}'

def age_date_predicate(date_col: str, years: int, compare: str) -> str:
    """사용승인/허가일자(text) 경과년수 조건 SQL 조각."""
    from txt2sql.query_understanding.temporal import reference_date_sql

    valid = f"\"{date_col}\" ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$'"
    casted = f"\"{date_col}\"::date"
    boundary = f"({reference_date_sql()} - INTERVAL '{years} years')"
    if compare == "lt":
        op = f"{casted} > {boundary}"
    elif compare == "lte":
        op = f"{casted} >= {boundary}"
    elif compare == "gt":
        op = f"{casted} < {boundary}"
    else:
        op = f"{casted} <= {boundary}"
    return f"{valid} AND {op}"


def legal_dong_guess(admin_dong: str) -> str | None:
    """구서1동 → 구서동 처럼 행정동에서 법정동 후보를 추정."""
    m = re.fullmatch(r"([가-힣]+)\d+동", admin_dong)
    if m:
        return m.group(1) + "동"
    return None


_ANAPHORA_HINTS = (
    "그 ",
    "그거",
    "그게",
    "그것",
    "해당 ",
    "이 건물",
    "그 건물",
    "그 아파트",
    "해당 아파트",
    "이중에",
    "이 중에",
    "이 중",
    "그중",
    "그중에",
    "그 집합",
    "그 공장",
    "남은 건물",
    "앞의",
    "방금",
    "아까",
    "위에서",
)


def has_anaphora(question: str) -> bool:
    q = question.strip()
    if q.startswith(("그", "해당", "앞", "방금", "아까", "이 ", "저 ")):
        return True
    return any(h in q for h in _ANAPHORA_HINTS)


_MAP_DISPLAY_HINTS = (
    "지도에 표시",
    "지도에 표출",
    "지도에 보여",
    "지도에 그려",
    "지도에 올려",
    "맵에 표시",
    "맵에 보여",
    "데이터를 표시",
    "데이터를 표출",
    "데이터 표시",
    "데이터 표출",
    "건물데이터를 표시",
    "건물 데이터를 표시",
    "건물을 표시",
    "건물을 표출",
    "표시하라",
    "표시해라",
    "표시해줘",
    "표시 해줘",
    "표출하라",
    "표출해줘",
    "표출해 줘",
)


def wants_map_display(question: str) -> bool:
    """건물·공간 데이터를 목록이 아니라 지도에 그리라는 요청."""
    q = (question or "").strip()
    if not q:
        return False
    if any(k in q for k in ("차트", "그래프", "표시명")):
        return False
    if any(k in q for k in _MAP_DISPLAY_HINTS):
        return True
    if re.search(r"(건물|건축물|아파트).{0,16}(표시|표출)", q):
        return True
    if re.search(r"(표시|표출).{0,12}(지도|맵)", q):
        return True
    return False


def looks_like_standalone_question(question: str) -> bool:
    """새 장소·새 주제로 보이는 독립 질의 (후속/기준병합 대상 아님)."""
    q = question.strip()
    if not q or has_anaphora(q):
        return False
    if is_busan_wide(q):
        return True
    if extract_gu(q):
        return True
    place = extract_place(q)
    if place:
        from txt2sql.gazetteer import is_locality

        if is_locality(place):
            return True
    # 짧은 기준 보정("건축년수")은 독립 질문이 아님
    if len(q) <= 12 and looks_like_age_question(q) and extract_age_years(q) is None:
        return False
    # 연수·집계가 한 문장에 갖춰진 신규 질의
    if extract_age_years(q) is not None and looks_like_age_question(q) and len(q) >= 16:
        return True
    if any(k in q for k in ("몇", "건수", "채수", "수는", "보여줘", "목록", "상위")) and len(q) >= 12:
        return True
    return False
