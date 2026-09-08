"""의미가 불분명·모호한 질의어를 감지하고 확인을 요청한다."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row

from txt2sql.domain import (
    DETAIL_USAGE_ALIASES,
    USAGE_ALIASES,
    USAGE_CLASS_ALIASES,
    dong_requires_gu,
    extract_gu,
    extract_place,
    is_busan_wide,
)

_COL = re.compile(r"\b(A\d+)\b", re.I)

# 의도·문법 조사 등 (미지 단어 판별에서 제외)
_STOP = {
    "현재",
    "사용",
    "가능",
    "가능한",
    "사용가능",
    "사용가능한",
    "있는",
    "없는",
    "있는지",
    "있습니까",
    "해줘",
    "해주세요",
    "알려줘",
    "알려",
    "보여줘",
    "구해",
    "조회",
    "출력",
    "출력해",
    "출력해줘",
    "출력하라",
    "출력해라",
    "보여라",
    "보여줘라",
    "묶어",
    "묶어라",
    "묶어줘",
    "묶음",
    "단위",
    "단위로",
    "구하라",
    "구해라",
    "구해줘",
    "찾아",
    "찾아줘",
    "찾아라",
    "찾아주세요",
    "검색하라",
    "검색해라",
    "조회하라",
    "조회해라",
    "나열",
    "나열하라",
    "나열해라",
    "기간",
    "소요",
    "걸린",
    "허가",
    "준공",
    "사용승인",
    "긴",
    "짧은",
    "최장",
    "순으로",
    "양수",
    "정상값",
    "정상",
    "레코드",
    "최단",
    "뽑아",
    "뽑아라",
    "추출하라",
    "속성데이터",
    "속성",
    "표시하라",
    "표시해라",
    "표시해",
    "표시해줘",
    "차트로",
    "그래프로",
    "그려라",
    "그려줘",
    "최근",
    "최근에",
    "말해",
    "대한",
    "관련",
    "기준",
    "에서",
    "으로",
    "로서",
    "에는",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "의",
    "과",
    "와",
    "도",
    "만",
    "좀",
    "요",
    "까",
    "까요",
    "인가",
    "인가요",
    "입니까",
    "뭐야",
    "무엇",
    "어떤",
    "무슨",
    "몇",
    "개",
    "개수",
    "갯수",
    "몇개",
    "몇개야",
    "건수",
    "채",
    "채야",
    "채수",
    "수",
    "총",
    "및",
    "또는",
    "그리고",
    "또",
    "등",
    "약",
    "정도",
    "대략",
    "주요",
    "가장",
    "큰",
    "작은",
    "많은",
    "적은",
    "상위",
    "하위",
    "그중",
    "그중에서",
    "이면서",
    "이거나",
    "얼마나",
    "되나요",
    "돼요",
    "있어요",
    "있나요",
    "인가요",
    "이름과",
    "성격의",
    "구까지",
    "지하층",
    "지상층",
    "지상층수",
    "층수",
    "건축연령",
    "상관계수",
    "상관",
    "일수",
    "허가일",
    "사용승인일",
    "오래된",
    "건폐율",
    "용적율",
    "용적률",
    "그리고",
    "또는",
    "혹은",
    "제외",
    "이상",
    "이상인",
    "이상인것",
    "이상인것은",
    "이하",
    "이하인",
    "이하인것",
    "이하인것은",
    "초과",
    "초과인",
    "초과인것",
    "초과인것은",
    "미만",
    "미만인",
    "미만인것",
    "미만인것은",
    "넘는",
    "것은",
    "것들",
    "인것",
    "중에",
    "채야",
    "건이야",
    "인가요",
    "이내",
    "근처",
    "안에",
    "내부",
    "경계",
    "교차",
    "버퍼",
    "거리",
    "좌표",
    "점",
    "미터",
    "킬로미터",
    "제곱미터",
    "제곱킬로미터",
    "평방미터",
    "센티미터",
    "층",
    "높이",
    "면적",
    "연면적",
    "건물면적",
    "건축면적",
    "건축물면적",
    "대지면적",
    "특징",
    "특성",
    "요약",
    "설명",
    "의미",
    "뜻",
    "속성",
    "컬럼",
    "칼럼",
    "필드",
    "테이블",
    "데이터",
    "데이터셋",
    "자료",
    "스키마",
    "목록",
    "리스트",
    "소개",
    "개요",
    "구조",
    "구성",
    "포함",
    "담겨",
    "건물",
    "건축물",
    "아파트",
    "부산",
    "부산시",
    "부산광역시",
    "전체",
    "시내",
    "중에서",
    "높은",
    "낮은",
    "에서",
    "공동주택",
    "단독주택",
    "공장",
    "창고",
    "창고시설",
    "공공시설",
    "공공시설물",
    "공공용시설",
    "공공용",
    "산업단지",
    "기초구역",
    "행정동",
    "법정동",
    "겹치",
    "겹치는",
    "겹친",
    "교차",
    "교차하는",
    "걸친",
    "용도",
    "지어진",
    "지어진지",
    "건축년",
    "건축년수",
    "준공",
    "준공일",
    "시공년도",
    "시공연도",
    "시공년",
    "사용승인",
    "사용승인일",
    "허가일",
    "허가일자",
    "경과",
    "년수",
    "오래된",
    "숫자",
    "것의",
    "넘은",
    "질문",
    "결과",
    "답변",
    "부산",
    "부산시",
    "부산광역시",
    "모든",
    "전체",
    "전체에서",
    "시내",
    "시에서",
    "각각",
    "해당",
    "중",
    "후의",
    "후",
    "미만의",
    "이상의",
    "건물",
    "건축물",
    "숫자는",
    "수는",
    "그",
    "저",
    "그런",
    "저런",
    "이런",
    "궁금",
    "궁금해",
    "알고",
    "싶어",
    "부탁",
    "부탁해",
    "비율",
    "비율을",
    "비율이",
    "백분율",
    "백분율로",
    "퍼센트",
    "대비",
    "전체",
    "전체에서",
    "계산",
    "계산해",
    "계산해줘",
    "차이를",
    "차이가",
    "차이",
    "구조별",
    "법정동별",
    "용도별",
    "세부용도별",
    "층수별",
    "구별",
    "나눠",
    "나누어",
    "건수와",
    "건수를",
    "평균을",
    "보여",
}

# 측정 기준이 불명확한 형용사/감성어
_VAGUE = (
    "제일 좋은",
    "가장 좋은",
    "제일 괜찮은",
    "가장 괜찮은",
    "좋은",
    "괜찮은",
    "핫한",
    "핫한곳",
    "유명한",
    "예쁜",
    "멋진",
    "최고의",
    "최악",
    "최악의",
    "살기좋은",
    "살기 좋은",
    "추천",
    "괜찮은가",
    "좋은가",
    "어때보여",
    "베스트",
    "최고야",
    "최고인",
    "높은 건물",
    "높은건물",
    "큰 건물",
    "큰건물",
    "낮은 건물",
    "작은 건물",
)

_USAGE_WORDS = tuple(
    list(USAGE_ALIASES.keys())
    + list(DETAIL_USAGE_ALIASES.keys())
    + list(USAGE_CLASS_ALIASES.keys())
)


@dataclass(frozen=True)
class ClarifyAnswer:
    intent: str
    answer: str
    ambiguous_terms: list[str]
    options: list[dict[str, Any]]


_CHOICE_RE = re.compile(
    r"^\s*(?:번호\s*)?(?P<num>\d{1,2})\s*(?:번|번요|이요|입니다|요)?\s*[.。)]?\s*$"
)
_ORDINAL_MAP = {
    "첫번째": 1,
    "첫 번째": 1,
    "첫째": 1,
    "두번째": 2,
    "두 번째": 2,
    "둘째": 2,
    "세번째": 3,
    "세 번째": 3,
    "셋째": 3,
}


def parse_choice_index(question: str) -> int | None:
    """『1』『1번』『첫번째』 등 선택 번호를 파싱. 없으면 None."""
    q = question.strip()
    if not q:
        return None
    if q in _ORDINAL_MAP:
        return _ORDINAL_MAP[q]
    m = _CHOICE_RE.fullmatch(q)
    if m:
        return int(m.group("num"))
    return None


def _gu_dong_from_place(place: str) -> tuple[str | None, str | None]:
    parts = str(place).split()
    gu = next((p for p in parts if p.endswith("구") or p.endswith("군")), None)
    dong = next((p for p in reversed(parts) if p.endswith("동")), None)
    return gu, dong


def rewrite_question_with_place(base_question: str, place: str) -> str:
    """모호했던 동 질문을 구+동으로 구체화."""
    gu, dong = _gu_dong_from_place(place)
    if not dong:
        return base_question.strip()
    resolved = f"{gu} {dong}".strip() if gu else dong
    base = base_question.strip()
    # 이미 같은 구가 있으면 그대로 두고 동만 보장
    if gu and gu in base and dong in base:
        return base
    if dong in base:
        return base.replace(dong, resolved, 1)
    return f"{resolved} {base}"


def resolve_place_clarify_choice(
    question: str,
    *,
    last_route: str | None,
    last_question: str | None,
    options: list[dict[str, Any]] | None,
) -> tuple[str | None, str | None]:
    """직전 clarify_place에 대한 번호 선택 처리.

    Returns:
        (rewritten_question, error_message)
        - 선택 성공: (새 질문, None)
        - 번호 답변인데 범위 밖: (None, 안내문)
        - 해당 없음: (None, None)
    """
    if last_route != "clarify_place":
        return None, None
    opts = list(options or [])
    if not opts:
        return None, None
    idx = parse_choice_index(question)
    if idx is None:
        # 구 이름으로 직접 골라도 허용 (예: 강서구 / 해운대구)
        q = question.strip()
        for opt in opts:
            place = str(opt.get("place") or "")
            gu, _dong = _gu_dong_from_place(place)
            if gu and (q == gu or q == f"{gu}" or gu in q and len(q) <= len(gu) + 2):
                base = last_question or ""
                if not base:
                    return None, None
                return rewrite_question_with_place(base, place), None
        return None, None
    if idx < 1 or idx > len(opts):
        return None, (
            f"선택 번호는 1~{len(opts)} 사이여야 합니다. "
            f"예: 1 또는 1번"
        )
    place = str(opts[idx - 1].get("place") or "")
    base = last_question or ""
    if not place or not base:
        return None, "직전 확인 질문을 찾지 못했습니다. 구 이름을 넣어 다시 질문해 주세요."
    return rewrite_question_with_place(base, place), None


def check_ambiguity(
    conn: psycopg.Connection,
    question: str,
) -> ClarifyAnswer | None:
    """모호하면 확인 요청 답변을 반환. 문제 없으면 None."""
    q = question.strip()
    if not q:
        return None

    # 1) 감성·추천 등 측정 기준 불명
    vague_hits = [v for v in _VAGUE if v in q]
    # 긴 표현 우선 (제일 좋은 > 좋은)
    vague_hits = sorted(set(vague_hits), key=len, reverse=True)
    # 부분 중복 제거: '제일 좋은'이 있으면 '좋은' 제외
    filtered: list[str] = []
    for v in vague_hits:
        if any(v != keep and v in keep for keep in filtered):
            continue
        filtered.append(v)
    vague_hits = filtered
    # 임계·순위·필드 비교가 있으면 크기 형용사 soft vague 제외
    # (「높은 건물 수는?」처럼 기준 없는 건수는 계속 clarify)
    if vague_hits and any(
        k in q
        for k in (
            "이상",
            "이하",
            "초과",
            "미만",
            "상위",
            "가장",
            "제일",
            "㎡",
            "m2",
            "미터",
            "보다 큰",
            "보다 작",
            "보다 높",
            "보다 낮",
            "연면적",
            "대지면적",
            "건축면적",
            "건축물면적",
            "용적률",
            "용적율",
            "건폐율",
            "개",
        )
    ):
        vague_hits = [
            v
            for v in vague_hits
            if not any(x in v for x in ("높은", "큰", "낮은", "작은"))
        ]
    if vague_hits:
        return ClarifyAnswer(
            intent="clarify_vague",
            ambiguous_terms=vague_hits,
            options=[],
            answer=_vague_guidance(q, vague_hits),
        )

    # 지역 없는 단순 목록·개수
    if re.search(r"건물\s*\d+\s*개만", q) and not (extract_place(q) or extract_gu(q)):
        return ClarifyAnswer(
            intent="clarify_vague",
            ambiguous_terms=["지역"],
            options=[],
            answer="확인이 필요합니다. 지역(구·동)이 없습니다. 어느 구·동의 건물을 볼지 알려 주세요.",
        )

    # 「기초구역 안 건물 보여줘」처럼 대상 구역·장소·집계 단서가 모두 없을 때만 확인
    if (
        "기초구역" in q
        and "건물" in q
        and not re.search(r"BAS[_ ]?ID|기초구역번호|기초구역\s*번호", q, re.I)
        and not re.search(r"\d{5,}", q)
        and not (extract_place(q) or extract_gu(q))
        and not any(k in q for k in ("별", "겹치", "교차", "걸친", "분포", "건수", "채수", "몇"))
        and any(k in q for k in ("보여", "찾아", "목록", "리스트"))
    ):
        return ClarifyAnswer(
            intent="clarify_vague",
            ambiguous_terms=["기초구역"],
            options=[],
            answer=(
                "확인이 필요합니다. 기초구역번호(BAS_ID)가 없습니다. "
                "어느 기초구역인지 번호나 구·동을 알려 주세요."
            ),
        )

    from txt2sql.domain import is_vague_age_threshold

    if is_vague_age_threshold(q):
        return ClarifyAnswer(
            intent="clarify_vague",
            ambiguous_terms=["오래된"],
            options=[],
            answer=_age_threshold_guidance(q),
        )

    place = extract_place(q)
    gu_name = extract_gu(q)

    # 건축 경과년수인데 D198 미커버 구이면 데이터 한계 안내
    from txt2sql.domain import (
        d198_coverage_label,
        d198_table_for_gu,
        extract_age_years,
        looks_like_age_question,
    )

    if looks_like_age_question(q) and extract_age_years(q) is not None:
        d198_slot = any(k in q for k in ("허가일", "허가일자"))
        if d198_slot and gu_name and d198_table_for_gu(gu_name) is None:
            return ClarifyAnswer(
                intent="clarify_unsupported_age",
                ambiguous_terms=["허가일자"],
                options=[],
                answer=(
                    f"건물 ‘허가일’은 현재 {d198_coverage_label()} "
                    "용도별건물(AL_D198)로만 조회할 수 있습니다.\n"
                    "예: 금정구 구서동 단독주택 중 허가 후 30년 이상인 건수는?\n"
                    f"사용승인·경과년수는 전 구 GIS건물(AL_D010) 사용승인일자로 답합니다."
                ),
            )

    # 2) 지명 모호/미존재 (동이 여러 구에 있거나 데이터에 없음)
    if place and place.endswith("동"):
        if gu_name is None and dong_requires_gu(place):
            return ClarifyAnswer(
                intent="clarify_place",
                ambiguous_terms=[place],
                options=[],
                answer=(
                    f"확인 필요. {place}은 여러 구에 있어 구를 지정해야 한다.\n"
                    f"예: 중구 {place} 건물 몇 채야?"
                ),
            )
        places = _lookup_places(conn, place, gu=gu_name)
        if not places:
            admin = _lookup_admin_dong(conn, place)
            if not admin:
                from txt2sql.domain import legal_dong_guess

                guess = legal_dong_guess(place)
                hint = (
                    f"\n혹시 법정동 「{guess}」을(를) 말씀하신 건가요?"
                    if guess
                    else ""
                )
                return ClarifyAnswer(
                    intent="clarify_unknown_place",
                    ambiguous_terms=[place],
                    options=[],
                    answer=(
                        f"「{place}」에 해당하는 법정동명을 건물 데이터에서 찾지 못했습니다.\n"
                        "구 이름을 함께 적어 주시거나, 정확한 동명으로 다시 질문해 주세요."
                        f"{hint}\n"
                        "예: 금정구 구서동 아파트 특징, 해운대구 중동 건물 건수"
                    ),
                )
            # 행정동(구서1동 등)은 경계 교차 조회로 넘김
        # 구가 지정되지 않았고 후보가 2개 이상
        elif gu_name is None and len(places) > 1:
            lines = [
                f"「{place}」이(가) 여러 지역에 있어 의미가 불분명합니다.",
                "아래 중 어디를 말씀하신 건가요?",
            ]
            for i, p in enumerate(places, start=1):
                lines.append(
                    f"{i}) {p['place']} (건물 {int(p['n']):,}동)"
                )
            lines.append(
                "번호로 답해도 됩니다. 예: 1 또는 1번"
            )
            lines.append(
                f"또는 이렇게 다시 질문해 주세요: "
                f"{places[0]['place'].split()[-2]} {place} 건물 몇 채야?"
            )
            return ClarifyAnswer(
                intent="clarify_place",
                ambiguous_terms=[place],
                options=places,
                answer="\n".join(lines),
            )

    # 3) 물리 컬럼명만 있고 테이블/맥락이 불명확한 조회성 질문
    cols = [m.group(1).upper() for m in _COL.finditer(q)]
    if cols and _looks_like_data_query(q) and not _has_table_hint(q):
        # 메타 설명 질문이면 통과 (meta_qa가 테이블별 설명)
        if any(k in q for k in ("의미", "뜻", "뭐야", "무엇", "설명", "속성")):
            pass
        else:
            return ClarifyAnswer(
                intent="clarify_column",
                ambiguous_terms=cols,
                options=[],
                answer=(
                    f"컬럼 {', '.join(cols)}은(는) 테이블마다 의미가 다를 수 있습니다.\n"
                    "어느 데이터(예: 부산 건물 AL_D010, 산업단지, 용도별건물 AL_D198)를 "
                    "기준으로 할지 알려 주세요.\n"
                    f"또는 「{cols[0]} 컬럼 의미가 뭐야?」처럼 설명부터 요청할 수 있습니다."
                ),
            )

    # 4) 도메인에서 해석되지 않는 단어
    unknown = _unknown_terms(q, place=place, gu=gu_name)
    # 차트 종류 변경/안내·지표 필터 질문은 미지 용어 clarify 대상이 아님
    from txt2sql.chart_qa import (
        is_chart_accept_question,
        is_chart_capability_question,
        is_chart_decline_question,
        is_chart_metric_draw_question,
        is_chart_series_filter_question,
        is_chart_type_change_question,
    )
    from txt2sql.d198_attrs import (
        looks_like_value_bin_question,
        looks_like_year_stats_question,
    )
    from txt2sql.domain import (
        extract_building_name_candidate,
        has_anaphora,
        looks_like_building_name_lookup,
        looks_like_measure_threshold,
    )
    from txt2sql.guide_qa import _is_coverage_question
    from txt2sql.named_dataset_qa import (
        is_named_dataset_data_question,
        named_dataset_display_tokens,
        try_named_dataset_query,
    )
    from txt2sql.query_understanding.contract import extract_contract

    contract = extract_contract(q)
    # 업로드 표시명 데이터셋 조회는 미지용어 clarify 대상이 아님
    if is_named_dataset_data_question(q) and try_named_dataset_query(conn, q) is not None:
        unknown = []
    else:
        display_tokens = named_dataset_display_tokens(conn, q)
        if display_tokens:
            unknown = [u for u in unknown if u not in display_tokens]
    if (
        is_chart_type_change_question(q)
        or is_chart_capability_question(q)
        or is_chart_series_filter_question(q)
        or is_chart_metric_draw_question(q)
        or is_chart_accept_question(q)
        or is_chart_decline_question(q)
        or _is_coverage_question(q)
        or looks_like_building_name_lookup(q)
        or looks_like_measure_threshold(q)
        or looks_like_value_bin_question(q)
        or looks_like_year_stats_question(q)
        or has_anaphora(q)
        or bool(contract.ratios)
        or bool(contract.group_fields)
        or contract.fixed_bins
        or contract.operation in {"ratio", "aggregate", "group_rank"}
    ):
        unknown = []
    else:
        name = extract_building_name_candidate(q) or ""
        name_bits = set(name.split())
        compact_name = name.replace(" ", "")
        unknown = [
            u
            for u in unknown
            if u not in name_bits
            and u not in compact_name
            and compact_name not in u
        ]
    # 부산 전역·순위/집계처럼 의도가 분명하면 미지 단어 clarify를 생략
    if unknown and is_busan_wide(q):
        unknown = [u for u in unknown if not str(u).startswith("부산")]
    if unknown:
        return ClarifyAnswer(
            intent="clarify_unknown_term",
            ambiguous_terms=unknown,
            options=[],
            answer=unknown_term_guidance(unknown),
        )

    return None


def unknown_term_guidance(
    unknown: list[str],
    *,
    mapped: list[tuple[str, str]] | tuple[tuple[str, str], ...] | None = None,
) -> str:
    """라우터 어휘와 대응하지 못한 단어에 대한 보완 질문."""
    terms = ", ".join(unknown)
    lines: list[str] = []
    if mapped:
        mapped_txt = ", ".join(f"「{src}」→「{dst}」" for src, dst in mapped)
        lines.append(f"일부 표현은 라우터 용어로 바꿔 보았습니다 ({mapped_txt}).")
    lines.append(
        f"질문 속 「{terms}」을(를) 라우터에 있는 단어와 대응하지 못했습니다."
    )
    lines.append("다음 중 무엇에 가까운지 알려 주시면 이어서 조회할 수 있습니다.")
    lines.extend(
        [
            "- 장소(구/동)",
            "- 건물 용도(아파트·공동주택·단독주택 등)",
            "- 수치 조건(연면적·높이·층수, 예: 2000㎡ 단위로 묶기)",
            "- 시간(사용승인일·연도별·5년 단위)",
            "- 데이터셋/컬럼 설명",
        ]
    )
    return "\n".join(lines)


def _vague_guidance(q: str, vague_hits: list[str]) -> str:
    place = extract_place(q)
    usage_label = None
    if "아파트" in q or "공동주택" in q:
        usage_label = "아파트(공동주택)"
    elif "단독" in q:
        usage_label = "단독주택"
    elif "건물" in q:
        usage_label = "건물"

    terms = ", ".join(vague_hits)
    if any(k in terms for k in ("높은", "낮은", "큰", "작은")):
        lines = [
            f"확인이 필요합니다. 「{terms}」의 기준이 없습니다.",
            "높이·연면적·대지면적 중 어떤 지표와 임계값(예: 높이 50m 이상, 연면적 1만㎡ 이상)인지 알려 주세요.",
        ]
        if not (place or extract_gu(q)):
            lines.append("지역(구·동)도 함께 지정해 주시면 조회할 수 있습니다.")
        return "\n".join(lines)
    lines = [
        f"「{terms}」은(는) 주관적인 표현이라 데이터만으로 ‘최고’를 정할 수 없습니다.",
        "어떤 기준이 ‘좋음’인지 알려 주시면 그 속성으로 순위·대표 건물을 찾아 드립니다.",
    ]
    scope = ""
    if place and usage_label:
        scope = f"{place} {usage_label}"
    elif place:
        scope = place
    elif usage_label:
        scope = usage_label

    if scope:
        lines.append(f"예를 들어 {scope} 기준으로는 이렇게 물을 수 있습니다.")
        base = place or "해당 지역"
        target = "아파트" if usage_label and "아파트" in usage_label else (usage_label or "건물")
        lines.extend(
            [
                f"- {base}에서 연면적이 가장 큰 {target}는?",
                f"- {base}에서 건물면적이 가장 큰 {target}는?",
                f"- {base}에서 높이가 가장 높은 {target}는?",
                f"- {base}에서 지상층이 가장 많은 {target}는?",
                f"- {base} {target} 특징 요약해줘",
                "건물을 찾은 뒤에는 「그 아파트의 이름은?」「지번은?」처럼 이어서 물을 수 있습니다.",
            ]
        )
    else:
        lines.extend(
            [
                "예:",
                "- 구서동에서 연면적이 가장 큰 아파트는?",
                "- 구서동에서 건물면적이 가장 큰 아파트는?",
                "- 해운대구 높이 상위 5개 건물",
                "- 구서동 아파트 특징은?",
            ]
        )
    return "\n".join(lines)


def _age_threshold_guidance(q: str) -> str:
    from txt2sql.domain import d198_coverage_label, extract_gu, has_batchim

    place = extract_place(q) or extract_gu(q) or "해당 지역"
    if "단독" in q:
        usage = "단독주택"
    elif "아파트" in q or "공동주택" in q:
        usage = "아파트(공동주택)"
    else:
        usage = "건물"
    label = d198_coverage_label()
    eun = "은" if has_batchim(usage) else "는"
    return "\n".join(
        [
            "「오래된」은 주관적인 표현이라 몇 년이 지났는지를 숫자로 정해야 합니다.",
            f"건축 경과년수는 사용승인일 기준으로 조회하며, 현재 자료는 {label} "
            "용도별건물입니다.",
            "예:",
            f"- {place} {usage} 중 사용승인 후 30년 이상인 건수는?",
            f"- {place}에서 가장 오래된 {usage}{eun}?",
        ]
    )


def _lookup_places(
    conn: psycopg.Connection,
    dong: str,
    *,
    gu: str | None = None,
) -> list[dict[str, Any]]:
    """동명 정확 매칭(끝 토큰). 구가 있으면 해당 구만."""
    with conn.cursor(row_factory=dict_row) as cur:
        if gu:
            cur.execute(
                """
                SELECT "A4" AS place, COUNT(*) AS n
                FROM "AL_D010_26_20250704"
                WHERE ("A4" LIKE %s OR "A4" LIKE %s)
                  AND "A4" LIKE %s
                GROUP BY 1
                ORDER BY 2 DESC
                LIMIT 10
                """,
                (f"% {dong}", dong, f"%{gu}%"),
            )
        else:
            cur.execute(
                """
                SELECT "A4" AS place, COUNT(*) AS n
                FROM "AL_D010_26_20250704"
                WHERE ("A4" LIKE %s OR "A4" = %s OR "A4" LIKE %s)
                GROUP BY 1
                ORDER BY 2 DESC
                LIMIT 10
                """,
                (f"% {dong}", dong, f"% {dong}%"),
            )
        return list(cur.fetchall())


def _lookup_admin_dong(
    conn: psycopg.Connection,
    dong: str,
) -> str | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT "ADM_NM" AS name
            FROM "BND_ADM_DONG_PG"
            WHERE "ADM_NM" = %s OR "ADM_NM" LIKE %s
            ORDER BY CASE WHEN "ADM_NM" = %s THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (dong, f"% {dong}", dong),
        )
        row = cur.fetchone()
        return str(row["name"]) if row else None


def _looks_like_data_query(q: str) -> bool:
    return any(
        k in q
        for k in (
            "몇",
            "개수",
            "건수",
            "조회",
            "보여",
            "구해",
            "상위",
            "목록",
            "리스트",
            "세어",
        )
    )


def _has_table_hint(q: str) -> bool:
    keys = (
        "건물",
        "용도",
        "건축물",
        "공동주택",
        "위반건축",
        "채수",
        "AL_D010",
        "D010",
        "산업단지",
        "D060",
        "용도별건물",
        "D198",
        "기초구역",
        "행정동",
        "BND",
    )
    return any(k in q for k in keys)


def _unknown_terms(
    q: str,
    *,
    place: str | None,
    gu: str | None,
) -> list[str]:
    """조사 제거 후 남은 미등록 한글 토큰."""
    text = q
    # 부산 전역 장소 표현은 미지 단어가 아님
    for token in (
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
        "부산",
    ):
        text = text.replace(token, " ")
    for token in filter(None, [place, gu]):
        text = text.replace(token, " ")
    for w in _USAGE_WORDS:
        text = text.replace(w, " ")
    for col in _COL.findall(text):
        text = re.sub(re.escape(col), " ", text, flags=re.I)
    # 숫자·영문·기호 제거
    text = re.sub(r"[0-9a-zA-Z_\"'.,?？!！()[\]{}]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    raw = [t for t in re.findall(r"[가-힣]{2,}", text) if t not in _STOP]
    _particle = re.compile(
        r"(은|는|이|가|을|를|의|과|와|도|만|에서|에게|에|으로|로|까지|부터|보다|중)$"
    )
    cleaned: list[str] = []
    for t in raw:
        stripped = _particle.sub("", t)
        if stripped in _STOP:
            continue
        if len(stripped) >= 3:
            t2 = stripped
        elif t not in _STOP and len(t) >= 3:
            t2 = t
        else:
            continue
        if t2.endswith(("동", "구", "시", "군", "읍", "면", "리")):
            continue
        from txt2sql.gazetteer import is_known_place

        if is_known_place(t2) or is_known_place(t):
            continue
        if t2.startswith("부산"):
            continue
        if t2.startswith(("이상", "이하", "초과", "미만")):
            continue
        if t2 not in cleaned:
            cleaned.append(t2)
    if not cleaned:
        return []
    return [t for t in cleaned if len(t) >= 3][:3]
