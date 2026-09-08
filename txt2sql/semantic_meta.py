"""한국어 semantic metadata (동의어·핵심 컬럼 힌트).

DB column_metadata에 synonyms 컬럼이 없어도 M-Schema에 한국어 링킹 힌트를 붙인다.
신규 업로드 테이블은 table_metadata 표시명·설명에서 동의어를 자동 추출한다.
"""

from __future__ import annotations

import re

# table_name -> (동의어 목록)
TABLE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "AL_D010_26_20250704": (
        "건물",
        "건축물",
        "부산 건물",
        "GIS건물통합정보",
        "건물통합정보",
    ),
    "BND_ADM_DONG_PG": (
        "행정동",
        "행정구역",
        "동 경계",
        "구 경계",
        "센서스 기반 행정구역",
        "행정구역 동명",
    ),
    "TL_KODIS_BAS_26_202507": (
        "기초구역",
        "기초구역번호",
        "도로명주소 기초구역",
    ),
    "AL_D060_00_20250804": (
        "산업단지",
        "산단",
        "산업단지_전국",
    ),
    "pnu_def": (
        "지번",
        "필지",
        "PNU",
    ),
}

# (table_name or "*") -> column_name -> synonyms
COLUMN_SYNONYMS: dict[str, dict[str, tuple[str, ...]]] = {
    "*": {
        "geometry": ("공간", "도형", "경계", "위치"),
    },
    "AL_D010_26_20250704": {
        "A0": ("원천도형ID",),
        "A1": ("GIS건물통합식별번호", "건물통합식별번호"),
        "A2": ("고유번호", "PNU"),
        "A3": ("법정동코드", "법정동 코드"),
        "A4": ("법정동명", "법정동", "구", "동", "지역", "주소"),
        "A5": ("지번",),
        "A6": ("특수지코드",),
        "A7": ("특수지구분명", "산지", "특수지"),
        "A8": ("용도코드", "건축물용도코드"),
        "A9": ("용도", "용도명", "건축물용도명"),
        "A10": ("건축물구조코드", "구조코드"),
        "A11": ("건축물구조명", "구조"),
        "A12": ("건축물면적", "건물면적"),
        "A13": ("사용승인일자", "사용승인일"),
        "A14": ("연면적", "건물면적", "면적"),
        "A15": ("대지면적",),
        "A16": ("높이", "건물높이", "고도"),
        "A17": ("건폐율",),
        "A18": ("용적율", "용적률"),
        "A19": ("건축물ID", "건축물아이디"),
        "A20": ("위반건축물여부", "위반건축물"),
        "A21": ("참조체계연계키",),
        "A22": ("데이터기준일", "데이터기준일자", "기준일자"),
        "A23": ("원천시도시군구코드",),
        "A24": ("건물명",),
        "A25": ("건물동명",),
        "A26": ("지상층", "지상층수", "층수"),
        "A27": ("지하층", "지하층수"),
        "A28": ("데이터생성변경일자",),
    },
    "AL_D198_26260_20250115": {
        "A0": ("도형ID",),
        "A1": ("GIS건물통합식별번호",),
        "A2": ("고유번호", "PNU"),
        "A3": ("법정동코드",),
        "A4": ("법정동명", "법정동", "구", "동", "지역"),
        "A5": ("특수지구분코드",),
        "A6": ("특수지구분명", "산지", "특수지"),
        "A7": ("지번",),
        "A8": ("건물식별번호",),
        "A9": ("집합건물구분코드",),
        "A10": ("집합건물구분", "집합건축물", "일반건축물"),
        "A11": ("대장종류코드",),
        "A12": ("대장종류", "표제부", "일반건축물대장"),
        "A13": ("건물명",),
        "A14": ("건물동명",),
        "A15": ("건물주부구분코드",),
        "A16": ("건물주부구분명", "주건축물", "부속건축물"),
        "A17": ("건물대지면적", "대지면적"),
        "A18": ("건물건축면적", "건축면적"),
        "A19": ("건물연면적", "연면적", "면적"),
        "A20": ("용적율", "용적률"),
        "A21": ("건폐율",),
        "A22": ("건축물구조코드",),
        "A23": ("건축물구조명", "구조"),
        "A24": ("주요용도코드",),
        "A25": ("주요용도명", "용도", "용도명"),
        "A26": ("세부용도코드",),
        "A27": ("세부용도명", "세부용도"),
        "A28": ("건물용도분류코드",),
        "A29": ("건물용도분류명", "용도분류"),
        "A30": ("건물높이", "높이"),
        "A31": ("지상층", "지상층수", "층수"),
        "A32": ("지하층", "지하층수"),
        "A33": ("허가일", "허가일자"),
        "A34": ("사용승인일", "사용승인일자", "준공일"),
        "A35": ("데이터기준일", "기준일자"),
    },
    "AL_D198_26410_20250115": {
        "A0": ("도형ID",),
        "A1": ("GIS건물통합식별번호",),
        "A2": ("고유번호", "PNU"),
        "A3": ("법정동코드",),
        "A4": ("법정동명", "법정동", "구", "동", "지역"),
        "A5": ("특수지구분코드",),
        "A6": ("특수지구분명", "산지", "특수지"),
        "A7": ("지번",),
        "A8": ("건물식별번호",),
        "A9": ("집합건물구분코드",),
        "A10": ("집합건물구분", "집합건축물", "일반건축물"),
        "A11": ("대장종류코드",),
        "A12": ("대장종류", "표제부", "일반건축물대장"),
        "A13": ("건물명",),
        "A14": ("건물동명",),
        "A15": ("건물주부구분코드",),
        "A16": ("건물주부구분명", "주건축물", "부속건축물"),
        "A17": ("건물대지면적", "대지면적"),
        "A18": ("건물건축면적", "건축면적"),
        "A19": ("건물연면적", "연면적", "면적"),
        "A20": ("용적율", "용적률"),
        "A21": ("건폐율",),
        "A22": ("건축물구조코드",),
        "A23": ("건축물구조명", "구조"),
        "A24": ("주요용도코드",),
        "A25": ("주요용도명", "용도", "용도명"),
        "A26": ("세부용도코드",),
        "A27": ("세부용도명", "세부용도"),
        "A28": ("건물용도분류코드",),
        "A29": ("건물용도분류명", "용도분류"),
        "A30": ("건물높이", "높이"),
        "A31": ("지상층", "지상층수", "층수"),
        "A32": ("지하층", "지하층수"),
        "A33": ("허가일", "허가일자"),
        "A34": ("사용승인일", "사용승인일자", "준공일"),
        "A35": ("데이터기준일", "기준일자"),
    },
    "BND_ADM_DONG_PG": {
        "ADM_CD": ("행정구역코드", "행정동코드", "코드"),
        "ADM_NM": ("행정동명", "행정동", "동", "구"),
        "BASE_DATE": ("기준일", "정보갱신 기준일", "갱신기준일"),
    },
    "TL_KODIS_BAS_26_202507": {
        "BAS_ID": ("기초구역ID", "기초구역번호"),
        "BAS_AR": ("기초구역면적", "면적"),
        "BAS_MGT_SN": ("기초구역관리번호",),
        "CTP_KOR_NM": ("시도명",),
        "SIG_CD": ("시군구코드",),
        "SIG_KOR_NM": ("시군구명", "구", "구명"),
        "NTFC_DE": ("고시일자", "고시일"),
        "OPERT_DE": ("작업일자",),
        "MVMN_DE": ("이동일자",),
        "MVMN_RESN": ("이동사유",),
    },
    "AL_D060_00_20250804": {
        "A0": ("원천도형ID",),
        "A1": ("도면번호",),
        "A2": ("주제도명", "주제도"),
        "A3": ("데이터기준일자", "데이터기준일"),
        "A4": ("원천시도시군구코드", "시군구코드"),
        "A5": ("용도지역지구코드",),
        "A6": ("용도지역지구코드명", "용도지역지구", "용도지역"),
        "A7": ("고시일자", "고시일"),
        "A8": ("객체내용",),
        "A9": ("업무참조내용", "업무참조"),
    },
}

# 샘플 값 조회에 쓸 대표 텍스트 컬럼 (테이블별)
SAMPLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "AL_D010_26_20250704": ("A4", "A9"),
    "AL_D198_26260_20250115": ("A4", "A25"),
    "AL_D198_26410_20250115": ("A4", "A25"),
    "BND_ADM_DONG_PG": ("ADM_NM",),
    "TL_KODIS_BAS_26_202507": ("SIG_KOR_NM",),
    "AL_D060_00_20250804": ("A6",),
}


_LABEL_SPLIT = re.compile(r"[\s_/·,|（）()\[\]\-～~]+")
_LABEL_STOPWORDS = frozenset(
    {
        "면적",
        "행정동",
        "행정구역",
        "건물",
        "건축물",
        "용도",
        "코드",
        "기준일",
        "데이터",
        "활용",
        "미활용",
        "기반",
        "관련",
        "정보",
        "공간",
        "테이블",
        "레이어",
        "전국",
        "부산",
        "부산광역시",
        "광역시",
        "특별시",
        "자치시",
        "자치도",
        "시군구",
        "센서스",
        "표시",
        "구분",
        "여부",
        "일자",
        "날짜",
        "명칭",
        "이름",
        "번호",
        "식별",
        "컬럼",
        "필드",
    }
)


def synonyms_from_labels(*texts: str, limit: int = 16) -> tuple[str, ...]:
    """표시명·설명·분류에서 검색용 토큰을 뽑는다."""
    seen: list[str] = []
    for text in texts:
        raw = (text or "").strip()
        if not raw:
            continue
        for part in _LABEL_SPLIT.split(raw):
            token = part.strip().strip(".,;:…·")
            if len(token) < 2 or token.isdigit() or token in seen:
                continue
            seen.append(token)
            if len(seen) >= limit:
                return tuple(seen)
    return tuple(seen)


def distinctive_label_tokens(*texts: str, min_len: int = 4) -> tuple[str, ...]:
    """다른 레이어와 겹치기 쉬운 일반어를 뺀 검색 토큰."""
    out: list[str] = []
    for token in synonyms_from_labels(*texts):
        if len(token) < min_len or token in _LABEL_STOPWORDS:
            continue
        if token not in out:
            out.append(token)
    return tuple(out)


def _d198_synonyms(table_name: str) -> tuple[str, ...]:
    from txt2sql.domain import gu_from_d198_table

    gu = gu_from_d198_table(table_name)
    if not gu:
        return ()
    stem = gu.replace("구", "").replace("군", "")
    label = stem if len(stem) >= 2 else gu
    return (
        f"{gu} 건물",
        f"{label} 용도별건물",
        "사용승인",
        "건축년수",
        "용도별건물공간정보",
    )


def table_synonyms(
    table_name: str,
    *,
    display_name: str = "",
    description: str = "",
    category: str = "",
) -> tuple[str, ...]:
    seen: list[str] = []

    def add(items: tuple[str, ...] | list[str]) -> None:
        for item in items:
            token = (item or "").strip()
            if token and token not in seen:
                seen.append(token)

    add(distinctive_label_tokens(display_name, description, category))
    add((display_name.strip(),) if display_name.strip() else ())
    add(TABLE_SYNONYMS.get(table_name, ()))
    if table_name not in TABLE_SYNONYMS:
        add(_d198_synonyms(table_name))
    add(synonyms_from_labels(display_name, description, category))
    add((category.strip(),) if category.strip() else ())
    return tuple(seen)


def column_synonyms(
    table_name: str,
    column_name: str,
    *,
    display_name: str = "",
) -> tuple[str, ...]:
    specific = COLUMN_SYNONYMS.get(table_name, {}).get(column_name, ())
    if not specific and table_name.startswith("AL_D198_"):
        template = COLUMN_SYNONYMS.get("AL_D198_26260_20250115", {})
        specific = template.get(column_name, ())
    generic = COLUMN_SYNONYMS.get("*", {}).get(column_name, ())
    seen: list[str] = []
    for item in (*specific, *generic, *synonyms_from_labels(display_name)):
        token = (item or "").strip()
        if token and token not in seen:
            seen.append(token)
    return tuple(seen)


def tables_matching_labels(
    question: str,
    metadata_rows: list[dict[str, str]],
) -> list[str]:
    """질문에 표시명 고유 토큰이 있으면 해당 테이블을 앞에 둔다."""
    if not question:
        return []
    matched: list[str] = []
    for row in metadata_rows:
        name = (row.get("table_name") or "").strip()
        if not name or name in matched:
            continue
        tokens = distinctive_label_tokens(
            row.get("display_name") or "",
            row.get("description") or "",
            row.get("category") or "",
        )
        if any(token in question for token in tokens):
            matched.append(name)
    return matched


def tables_matching_exact_display_names(
    question: str,
    metadata_rows: list[dict[str, str]],
) -> list[str]:
    """질문 문자열에 표시명(또는 물리명)이 그대로 포함된 테이블을 최장 일치 순으로 반환."""
    if not question:
        return []
    q_lower = question.lower()
    hits: list[tuple[int, str]] = []
    for row in metadata_rows:
        name = (row.get("table_name") or "").strip()
        if not name:
            continue
        display = (row.get("display_name") or "").strip()
        score = 0
        if display and display in question:
            score = len(display)
        elif name in question or name.lower() in q_lower:
            score = len(name)
        if score:
            hits.append((score, name))
    hits.sort(key=lambda item: (-item[0], item[1]))
    out: list[str] = []
    for _, name in hits:
        if name not in out:
            out.append(name)
    return out


def format_synonyms(syns: tuple[str, ...], *, max_n: int = 6) -> str:
    if not syns:
        return ""
    return ", ".join(syns[:max_n])
