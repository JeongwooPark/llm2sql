"""SQL 실행 결과를 한국어 자연어 답변으로 변환한다."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from txt2sql.building_row import (
    infer_row_dataset,
    row_approval_date as _br_approval_date,
    row_building_name,
    row_full_address,
    row_ground_floors,
    row_gross_floor_area,
    row_height as _br_height,
    row_lot_address,
    row_usage as _br_usage,
)
from txt2sql.d198_attrs import (
    COLUMN_LABELS as D198_COLUMN_LABELS,
    format_value_bin_label,
    format_year_stats_label,
    parse_value_bin,
    parse_year_stats,
)
from txt2sql.domain import (
    D198_BY_GU,
    d198_coverage_label,
    d198_table_for_gu,
    extract_calendar_year,
    extract_gu,
    extract_place,
    extract_special_land,
    extract_structure,
    extract_usage,
    is_busan_wide,
    wants_map_display,
)
from txt2sql.llm import chat, resolve_client
from txt2sql.progress import TokenCallback
from txt2sql.units import (
    UNIT_TOKEN,
    convert_for_schema,
    format_pyeong_from_m2,
    mentions_pyeong,
    pyeong_threshold,
    with_pyeong,
)

ANSWER_SYSTEM_PROMPT = """당신은 부산 GIS 데이터베이스 질의 결과를 사용자에게 설명하는 한국어 안내원입니다.
주어진 질문과 조회 결과만 근거로, 사람이 말하듯 자연스럽고 간결한 한국어로 답하세요.

규칙:
- 제공된 숫자·사실만 사용하세요. 추측·없는 건물명/수치는 만들지 마세요.
- 질문에 바로 답하는 문장으로 시작하세요.
- 여러 건을 나열할 때 각 건물명 옆에 사용승인일을 빠짐없이 붙이세요.
- 직전 목록에 날짜를 더해 달라는 후속이면 건수를 줄이지 마세요.
- '세부용도명', '특수지구분명', '사용승인일자 있음' 같은 스키마 용어를 나열하지 마세요.
- 조건을 제목처럼 이어 붙이지 마세요.
- '안내:' 머리말, '예:' 나열, 마크다운 불릿, SQL, 컬럼코드(A13 등)를 쓰지 마세요.
- coverage_note는 사용자가 이미 해당 구를 말한 경우에는 언급하지 마세요.
- 질문에 평이 있거나 결과에 평 환산이 있으면 ㎡와 평을 함께 쓰세요.
- 출력은 답변 본문만.

좋은 예:
금정구에서 사용승인일이 있는 아파트 중 가장 최근에 지어진 건물은 휴림 아르페입니다. 사용승인일은 2023년 3월 22일입니다.

나쁜 예:
안내: 용도별건물공간정보는 현재 일부 구·군 자료만 있습니다.
금정구 아파트(공동주택) 사용승인일자 있음 세부용도명 아파트 중에서 해당 조건의 건축물입니다. 예: 「휴림 아르페」 공동주택 일반.
"""

def _coverage_preface_map() -> dict[str, str]:
    label = d198_coverage_label()
    dataset = f"안내: 용도별건물공간정보는 현재 {label} 자료입니다."
    return {
        "building_age_count_d198_partial": (
            f"안내: 건축년수(사용승인·준공 경과)는 현재 {label} 용도별건물 "
            f"자료에만 있습니다. 부산시 전체가 아니라 {label} 합산으로 조회합니다."
        ),
        "d198_attr_count": dataset,
        "d198_attr_list": dataset,
        "d198_attr_rank": dataset,
        "d198_year_stats": (
            f"안내: 건립 연도(사용승인일) 통계는 현재 {label} 용도별건물 자료입니다."
        ),
        "d198_value_bins": (
            f"안내: 면적·높이 구간 통계는 현재 {label} 용도별건물 자료입니다."
        ),
    }


COVERAGE_PREFACE: dict[str, str] = _coverage_preface_map()

# 건물 테이블 주요 컬럼 → 한글 라벨 (LLM 혼동 방지)
_COLUMN_LABELS: dict[str, str] = {
    "A4": "법정동명",
    "A5": "지번",
    "A9": "용도명",
    "A12": "건물면적_㎡",
    "A14": "연면적_㎡",
    "A15": "대지면적_㎡",
    "A16": "높이_m",
    "A19": "연면적_㎡",
    "A24": "건물명",
    "A25": "주요용도명",
    "A26": "지상층수",
    "A27": "지하층수",
    "A30": "높이_m",
    "A31": "지상층수",
    "A33": "허가일자",
    "A34": "사용승인일자",
    "ADM_CD": "행정동코드",
    "ADM_NM": "행정동명",
    "BASE_DATE": "기준일",
    "BAS_ID": "기초구역번호",
    "BAS_AR": "기초구역면적_㎡",
    "BAS_MGT_SN": "기초구역관리번호",
    "CTP_KOR_NM": "시도명",
    "SIG_CD": "시군구코드",
    "SIG_KOR_NM": "시군구명",
    "NTFC_DE": "고시일자",
    "MVMN_DE": "이동일자",
    "MVMN_RESN": "이동사유",
    "cnt": "건수",
    "count": "건수",
    "CNT": "건수",
    "n": "건수",
    "pct": "비율_%",
    "admin_dong": "행정동명",
    "v": "값",
}

_SKIP_SUMMARY_COLS = frozenset({"A0", "A1", "A2", "A3", "geometry", "geom"})


def _fmt_date_ko(value: Any) -> str:
    """YYYY-MM-DD 등을 '2023년 3월 22일'로."""
    text = str(value or "").strip()
    m = re.match(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})", text)
    if not m:
        return text
    return f"{int(m.group(1))}년 {int(m.group(2))}월 {int(m.group(3))}일"


def fmt_value(value: Any) -> str:
    """숫자·스칼라를 한국어 답변용 문자열로 포맷."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if value.is_integer():
            return f"{int(value):,}"
        text = f"{value:,.6f}".rstrip("0").rstrip(".")
        return text
    if value is None:
        return "없음"
    return str(value)


# 하위 호환
_fmt_number = fmt_value


def _fmt_area(value: Any, question: str = "") -> str:
    """면적 표기. 질문에 평이 있으면 ㎡(평)로 같이 쓴다."""
    return with_pyeong(f"{_fmt_number(value)}㎡", value, question=question)


def _korean_ends_with_vowel(word: str) -> bool:
    if not word:
        return False
    last = word.strip()[-1]
    if last.isdigit() or last in "㎡m층채건":
        return False
    code = ord(last)
    if 0xAC00 <= code <= 0xD7A3:
        return (code - 0xAC00) % 28 == 0
    return False


def _eun_neun(word: str) -> str:
    return "는" if _korean_ends_with_vowel(word) else "은"


def _i_ga(word: str) -> str:
    return "가" if _korean_ends_with_vowel(word) else "이"


def _scalar_from_rows(rows: list[dict[str, Any]]) -> Any | None:
    if not rows:
        return None
    row = rows[0]
    for key in ("v", "cnt", "count", "CNT", "BAS_AR", "A14"):
        if key in row:
            return row[key]
    if len(row) == 1:
        return next(iter(row.values()))
    return None


def _looks_like_count_question(question: str) -> bool:
    return any(
        k in question
        for k in ("몇", "개수", "건수", "채수", "종류", "얼마", "세어", "총", "수는", "수가", "개야", "개?")
    ) or bool(re.search(r"\d*\s*개\b", question)) or (
        "수" in question and any(k in question for k in ("산업단지", "건물", "기초구역", "아파트", "주택"))
    )


def _summarize_rows(rows: list[dict[str, Any]], *, limit: int = 5) -> str:
    lines: list[str] = []
    for i, row in enumerate(rows[:limit], start=1):
        labeled = _label_row(row)
        parts = [f"{k}={_fmt_number(v)}" for k, v in labeled.items()]
        lines.append(f"{i}) " + ", ".join(parts))
    extra = len(rows) - limit
    if extra > 0:
        lines.append(f"… 외 {extra:,}건")
    return "\n".join(lines)


def _format_place_boundary_area(
    question: str,
    *,
    rows: list[dict[str, Any]],
    row_count: int,
) -> str:
    """행정·법정동·시·구 경계 면적 답변."""
    subject = _subject_phrase(question) or "해당 지역"
    if row_count == 1 and "adm_dong_n" in (rows[0] or {}):
        area = rows[0].get("area_m2")
        n = rows[0].get("adm_dong_n")
        try:
            area_f = float(area)
            n_i = int(n)
        except (TypeError, ValueError):
            return f"{subject}의 행정구역 면적을 계산하지 못했습니다."
        return (
            f"{subject}의 행정구역 면적은 약 {area_f:,.0f}㎡입니다. "
            f"(행정동 {n_i}개 합산, 행정동 경계 면적 기준)"
        )
    lines: list[str] = []
    total = 0.0
    for row in rows:
        name = row.get("ADM_NM") or row.get("adm_nm") or row.get("label")
        raw = row.get("area_m2")
        if name is None or raw is None:
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        total += val
        lines.append(f"{name} {val:,.0f}㎡")
    if not lines:
        return f"{subject}의 행정구역 면적 데이터를 찾지 못했습니다."
    if len(lines) == 1:
        return (
            f"{subject}의 행정구역 면적은 약 {lines[0].split(' ', 1)[1]}입니다. "
            "(행정동 경계 면적 기준)"
        )
    body = ", ".join(lines[:12])
    if len(lines) > 12:
        body += f" 외 {len(lines) - 12}개 동"
    return (
        f"{subject}의 행정구역 면적은 행정동별로 {body}이며, "
        f"합계 약 {total:,.0f}㎡입니다. (행정동 경계 면적 기준)"
    )


def _subject_phrase(question: str) -> str:
    """질문에서 장소·용도·조건을 모아 주어 구절을 만든다."""
    place = extract_place(question)
    usage = extract_usage(question)
    parts: list[str] = []
    if place:
        parts.append(place)
    elif is_busan_wide(question):
        parts.append("부산시")
    if usage:
        if "아파트" in question and usage == "공동주택":
            parts.append("아파트(공동주택)")
        else:
            parts.append(usage)
    elif "건물" in question or "건축물" in question:
        parts.append("건물")
    elif "기초구역" in question:
        parts.append("기초구역")
    elif "산업단지" in question:
        parts.append("산업단지")

    st = extract_structure(question)
    if st:
        parts.append(f"{st[0]} 구조")
    land = extract_special_land(question)
    if land:
        parts.append(land[0])

    m = re.search(
        r"(연면적|건물면적|건축면적|건축물면적|면적)\s*(?:이|가)?\s*"
        r"(\d+(?:\.\d+)?)\s*"
        rf"{UNIT_TOKEN}\s*(이상|초과|부터)\s*"
        r"(\d+(?:\.\d+)?)\s*"
        rf"{UNIT_TOKEN}\s*(이하|미만|까지|사이)",
        question,
    )
    if m:
        label = m.group(1)
        metric = "건물면적" if ("건물" in label or "건축" in label) else "연면적"
        lo = convert_for_schema(m.group(2), m.group(3), "㎡")
        hi = convert_for_schema(m.group(5), m.group(6), "㎡")
        lo_amt = lo.label if lo else f"{m.group(2)}㎡"
        hi_amt = hi.label if hi else f"{m.group(5)}㎡"
        parts.append(f"{metric} {lo_amt} {m.group(4)} {hi_amt} {m.group(7)}")
    else:
        m = re.search(
            r"(연면적|건물면적|건축면적|건축물면적|면적)\s*(?:이|가)?\s*(\d+(?:\.\d+)?)\s*"
            rf"{UNIT_TOKEN}\s*(이상|이하|초과|미만|넘는)",
            question,
        )
        if m:
            label, n, unit, rel = m.group(1), m.group(2), m.group(3), m.group(4)
            metric = "건물면적" if ("건물" in label or "건축" in label) else "연면적"
            shown = "초과" if rel == "넘는" else rel
            converted = convert_for_schema(n, unit, "㎡")
            amount = converted.label if converted else f"{n}㎡"
            parts.append(f"{metric} {amount} {shown}")
        else:
            m = re.search(
                rf"높이[가이]?\s*(\d+(?:\.\d+)?)\s*{UNIT_TOKEN}\s*"
                r"(이상|초과|부터)\s*"
                rf"(\d+(?:\.\d+)?)\s*{UNIT_TOKEN}\s*"
                r"(이하|미만|까지|사이)",
                question,
            )
            if m:
                lo = convert_for_schema(m.group(1), m.group(2), "m")
                hi = convert_for_schema(m.group(4), m.group(5), "m")
                lo_amt = lo.label if lo else f"{m.group(1)}m"
                hi_amt = hi.label if hi else f"{m.group(4)}m"
                parts.append(f"높이 {lo_amt} {m.group(3)} {hi_amt} {m.group(6)}")
            else:
                m = re.search(
                    rf"높이[가이]?\s*(\d+(?:\.\d+)?)\s*{UNIT_TOKEN}\s*"
                    r"(이상|이하|초과|미만|넘는)?",
                    question,
                )
                if m:
                    rel = m.group(3) or "이상"
                    shown = "초과" if rel == "넘는" else rel
                    converted = convert_for_schema(m.group(1), m.group(2), "m")
                    amount = converted.label if converted else f"{m.group(1)}m"
                    parts.append(f"높이 {amount} {shown}")
                else:
                    hit = pyeong_threshold(question)
                    if hit is not None:
                        converted, rel = hit
                        shown = "초과" if rel == "넘는" else rel
                        parts.append(f"연면적 {converted.label} {shown}")
                    else:
                        m = re.search(r"지상\s*층?[이]?\s*(\d+)\s*층", question)
                        if m:
                            rel = "이상"
                            if "미만" in question:
                                rel = "미만"
                            elif "이하" in question:
                                rel = "이하"
                            elif "초과" in question or "넘는" in question:
                                rel = "초과"
                            parts.append(f"지상 {m.group(1)}층 {rel}")

    year_hit = extract_calendar_year(question)
    if year_hit is not None:
        year, rel = year_hit
        parts.append(f"{year}년 {rel}".strip() if rel else f"{year}년")

    if not parts:
        return "해당 조건"
    return " ".join(parts)


def _place_label(question: str) -> str:
    place = extract_place(question)
    if place:
        return place
    if is_busan_wide(question):
        return "부산시"
    return ""


def _row_height(row: dict[str, Any], *, table: str | None = None, route: str | None = None) -> Any:
    return _br_height(row, table=table, route=route)


def _row_floors(row: dict[str, Any], *, table: str | None = None, route: str | None = None) -> Any:
    return row_ground_floors(row, table=table, route=route)


def _row_approval_date(
    row: dict[str, Any], *, table: str | None = None, route: str | None = None
) -> str:
    return _br_approval_date(row, table=table, route=route)


def _row_name(
    row: dict[str, Any],
    *,
    fallback: str | None = None,
    table: str | None = None,
    route: str | None = None,
) -> str | None:
    return row_building_name(row, table=table, route=route, fallback=fallback)


def _row_addr(
    row: dict[str, Any], *, table: str | None = None, route: str | None = None
) -> str:
    return row_full_address(row, table=table, route=route)


def _rank_target(question: str) -> str:
    usage = extract_usage(question)
    if "아파트" in question and usage == "공동주택":
        return "아파트"
    if usage:
        return usage
    return "건물"


def _row_usage(row: dict[str, Any], *, table: str | None = None, route: str | None = None) -> Any:
    return _br_usage(row, table=table, route=route)


def _looks_like_rank_question(question: str) -> bool:
    return any(
        k in question
        for k in (
            "가장 높",
            "제일 높",
            "가장 큰",
            "제일 큰",
            "가장 넓",
            "제일 넓",
            "가장 많",
            "제일 많",
            "최대",
            "1등",
        )
    )


def _infer_rank_route(question: str) -> str | None:
    superlative = any(
        k in question
        for k in (
            "가장 큰",
            "제일 큰",
            "가장 넓은",
            "제일 넓은",
            "가장넓은",
            "제일넓은",
            "최대",
            "1등",
            "최고",
        )
    )
    if any(k in question for k in ("가장 높", "제일 높")) or (
        "높이" in question and any(k in question for k in ("가장", "제일", "최대"))
    ):
        return "building_rank_높이"
    if any(k in question for k in ("건물면적", "건축면적", "건축물면적")) and superlative:
        return "building_rank_건물면적"
    if "연면적" in question and superlative:
        return "building_rank_연면적"
    if "대지면적" in question and superlative:
        return "building_rank_대지면적"
    if any(k in question for k in ("지상층", "층수")) and any(
        k in question for k in ("가장", "제일", "최대")
    ):
        return "building_rank_지상층"
    # 「가장 큰/넓은 건물」은 연면적(규모)으로 해석
    if any(
        k in question
        for k in (
            "가장 큰",
            "제일 큰",
            "가장큰",
            "제일큰",
            "가장 넓은",
            "제일 넓은",
            "가장넓은",
            "제일넓은",
        )
    ):
        return "building_rank_연면적"
    return None


def _natural_rank(question: str, route: str, row: dict[str, Any]) -> str:
    metric = str(route).replace("building_rank_", "")
    place = _place_label(question)
    target = _rank_target(question)
    where = f"{place} " if place else ""
    name_s = _row_name(row)
    jibeon = row.get("A5")

    if metric == "높이":
        lead = f"{where}{target} 중 높이가 가장 높은 건물은"
    elif metric == "지상층":
        lead = f"{where}{target} 중 지상층이 가장 많은 건물은"
    elif metric in {"건물면적", "연면적", "대지면적"} and any(
        k in question for k in ("넓", "넓은")
    ):
        lead = f"{where}{target} 중 {metric}이 가장 넓은 건물은"
    else:
        lead = f"{where}{target} 중 {metric}{_i_ga(metric)} 가장 큰 건물은"

    if name_s:
        lead += f" 「{name_s}」입니다."
    elif jibeon:
        lead += f" 지번 {jibeon} 건물입니다."
    else:
        lead += " 다음과 같습니다."

    detail = (
        f"위치는 {row.get('A4') or '—'}, 용도는 {_row_usage(row) or '—'}이며, "
        f"건물면적 {_fmt_area(row.get('A12'), question)}, "
        f"연면적 {_fmt_area(row.get('A14') if row.get('A14') is not None else row.get('A19'), question)}, "
        f"높이 {_fmt_number(_row_height(row))}m, "
        f"지상 {_fmt_number(_row_floors(row))}층입니다."
    )
    return f"{lead} {detail}"


def _natural_rank_list(
    question: str, route: str, rows: list[dict[str, Any]]
) -> str:
    metric = str(route).replace("building_rank_", "")
    place = _place_label(question)
    target = _rank_target(question)
    where = f"{place} " if place else ""

    if metric == "높이":
        head = f"{where}{target} 중 높이가 높은 상위 {len(rows)}곳은 다음과 같습니다."
    elif metric == "지상층":
        head = f"{where}{target} 중 지상층이 많은 상위 {len(rows)}곳은 다음과 같습니다."
    else:
        head = f"{where}{target} 중 {metric} 상위 {len(rows)}곳은 다음과 같습니다."

    lines = [head]
    for i, row in enumerate(rows, start=1):
        name_s = _row_name(row)
        who = (
            f"「{name_s}」"
            if name_s
            else f"지번 {row.get('A5') or '—'} 건물"
        )
        if metric == "높이":
            metric_txt = f"높이 {_fmt_number(_row_height(row))}m"
        elif metric == "지상층":
            metric_txt = f"지상 {_fmt_number(_row_floors(row))}층"
        elif metric == "건물면적":
            metric_txt = f"건물면적 {_fmt_area(row.get('A12'), question)}"
        elif metric == "대지면적":
            metric_txt = f"대지면적 {_fmt_area(row.get('A15'), question)}"
        else:
            metric_txt = f"연면적 {_fmt_area(row.get('A14'), question)}"
        lines.append(
            f"{i}) {who} — {row.get('A4') or '—'}, "
            f"{_row_usage(row) or '—'}, {metric_txt}, "
            f"지상 {_fmt_number(_row_floors(row))}층"
        )
    return "\n".join(lines)


def _natural_building_name_lookup(
    question: str, rows: list[dict[str, Any]]
) -> str:
    """특정 건물명 조회·설명 답변."""
    if not rows:
        return (
            f"{_subject_phrase(question)}에 해당하는 건물을 찾지 못했습니다. "
            "건물명·동명을 확인해 다시 질문해 주세요."
        )

    # 속성만 물은 경우 (주소/지번 등)
    attr_only = None
    if any(k in question for k in ("주소", "어디", "위치")):
        attr_only = "주소"
    elif "지번" in question:
        attr_only = "지번"
    elif any(k in question for k in ("높이",)):
        attr_only = "높이"
    elif "연면적" in question:
        attr_only = "연면적"
    elif any(k in question for k in ("건물면적", "건축면적")):
        attr_only = "건물면적"
    elif any(k in question for k in ("층수", "몇 층", "몇층", "지상층")):
        attr_only = "지상층"
    elif any(
        k in question
        for k in (
            "시공",
            "준공",
            "사용승인",
            "건축년",
            "건축연",
            "건설년",
            "건립년",
            "허가일",
            "건설일",
        )
    ):
        attr_only = "사용승인일"

    if len(rows) == 1 and attr_only:
        row = rows[0]
        name_s = _row_name(row, fallback="해당 건물")
        if attr_only == "주소":
            return f"「{name_s}」의 주소는 {_row_addr(row) or '—'}입니다."
        if attr_only == "지번":
            return f"「{name_s}」의 지번은 {row_lot_address(row) or '—'}입니다."
        if attr_only == "높이":
            return f"「{name_s}」의 높이는 {_fmt_number(_row_height(row))}m입니다."
        if attr_only == "연면적":
            return f"「{name_s}」의 연면적은 {_fmt_area(row.get('A14'), question)}입니다."
        if attr_only == "건물면적":
            return f"「{name_s}」의 건물면적은 {_fmt_area(row.get('A12'), question)}입니다."
        if attr_only == "지상층":
            return f"「{name_s}」의 지상층수는 {_fmt_number(_row_floors(row))}층입니다."
        if attr_only == "용도":
            return f"「{name_s}」의 용도는 {_row_usage(row) or '—'}입니다."
        if attr_only == "사용승인일":
            day = _fmt_date_ko(_row_approval_date(row)) if _row_approval_date(row) else "—"
            return f"「{name_s}」의 사용승인일은 {day}입니다."

    if len(rows) == 1:
        row = rows[0]
        name_s = _row_name(row)
        title = (
            f"「{name_s}」에 대한 정보입니다."
            if name_s
            else "요청하신 건물 정보입니다."
        )
        return (
            f"{title}\n"
            f"- 위치: {_row_addr(row) or '—'}\n"
            f"- 용도: {_row_usage(row) or '—'}\n"
            f"- 구조: {row.get('A11') or '—'}\n"
            f"- 건물면적: {_fmt_area(row.get('A12'), question)}\n"
            f"- 연면적: {_fmt_area(row.get('A14'), question)}\n"
            f"- 높이: {_fmt_number(_row_height(row))}m\n"
            f"- 지상층: {_fmt_number(_row_floors(row))}층\n"
            f"- 사용승인일: {_fmt_date_ko(_row_approval_date(row)) if _row_approval_date(row) else '—'}"
        )
    lines = [
        f"조건에 맞는 건물이 {len(rows)}건 있습니다. 주요 결과는 다음과 같습니다."
    ]
    for i, row in enumerate(rows[:10], start=1):
        name_s = _row_name(row, fallback=f"지번 {row_lot_address(row) or '—'}")
        if attr_only == "주소":
            lines.append(f"{i}) 「{name_s}」 — {_row_addr(row) or '—'}")
        elif attr_only == "사용승인일":
            day = (
                _fmt_date_ko(_row_approval_date(row))
                if _row_approval_date(row)
                else "—"
            )
            lines.append(f"{i}) 「{name_s}」 — {day}")
        else:
            lines.append(
                f"{i}) 「{name_s}」 — {row.get('A4') or '—'}, "
                f"{_row_usage(row) or '—'}, "
                f"높이 {_fmt_number(_row_height(row))}m, "
                f"지상 {_fmt_number(_row_floors(row))}층"
            )
    if len(rows) > 10:
        lines.append(f"… 외 {len(rows) - 10}건")
    return "\n".join(lines)


def _natural_industrial_names(
    question: str, rows: list[dict[str, Any]]
) -> str:
    subject = _subject_phrase(question)
    names: list[str] = []
    for row in rows:
        raw = row.get("name")
        if raw in (None, ""):
            continue
        s = str(raw).strip()
        if s and s not in names:
            names.append(s)
    if not names:
        return f"{subject}에 해당하는 산업단지 이름을 찾지 못했습니다."
    lines = [f"{subject} 이름은 다음과 같습니다."]
    for i, name in enumerate(names, start=1):
        lines.append(f"{i}) {name}")
    return "\n".join(lines)


def _natural_count(question: str, n: Any) -> str:
    subject = _subject_phrase(question)
    n_s = _fmt_number(n)
    if "종류" in question:
        return f"{subject}의 종류는 {n_s}가지입니다."
    if "건물" in question and "산업단지" in question:
        unit = "채"
        if "산업단지" not in subject:
            subject = f"{subject} 중 산업단지 내 건물".replace("  ", " ")
    elif "산업단지" in question or "기초구역" in question:
        unit = "개"
    elif any(k in question for k in ("채", "아파트", "주택", "건물")):
        unit = "채"
    else:
        unit = "건"
    return f"{subject}{_eun_neun(subject)} 모두 {n_s}{unit}입니다."


def format_map_display_answer(
    question: str,
    *,
    rows: list[dict[str, Any]],
    map_info: dict[str, Any] | None = None,
    include_map: bool = True,
) -> str:
    """「표시하라」질의: 주소 나열 대신 지도 표출 안내."""
    place = extract_place(question) or extract_gu(question) or ""
    usage = extract_usage(question)
    bits = [p for p in (place, usage) if p]
    if "건물" in question or "건축물" in question or not bits:
        if "건물" not in "".join(bits):
            bits.append("건물")
    year_hit = extract_calendar_year(question)
    if year_hit is not None:
        year, rel = year_hit
        bits.append(f"{year}년 {rel}".strip() if rel else f"{year}년")
    subject = " ".join(bits) if bits else "해당 건물"
    n: int | None = None
    scalar = _scalar_from_rows(rows)
    if scalar is not None:
        try:
            n = int(scalar)
        except (TypeError, ValueError):
            n = None
    if n is None and rows:
        n = _list_total(rows, len(rows))

    if map_info and map_info.get("available"):
        shown = int(map_info.get("feature_count") or 0)
        if n and shown and n > shown:
            return (
                f"{subject} 데이터를 지도에 표출했습니다. "
                f"전체 {n:,}동 가운데 {shown:,}동을 올렸습니다."
            )
        shown_n = shown or n
        if shown_n:
            return f"{subject} 데이터를 지도에 표출했습니다. ({shown_n:,}동)"
        return f"{subject} 데이터를 지도에 표출했습니다."
    if map_info and map_info.get("error"):
        if n:
            lead = f"{subject}{_eun_neun(subject)} {n:,}동입니다. "
        else:
            lead = f"{subject}을 조회했습니다. "
        return lead + f"지도에는 올리지 못했습니다. ({map_info.get('error')})"
    if not include_map:
        if n:
            return (
                f"{subject}{_eun_neun(subject)} {n:,}동입니다. "
                "지도 페이지에서 같은 질문을 하시면 위치가 표시됩니다."
            )
        return f"{subject}을 조회했습니다. 지도 페이지에서 위치를 확인할 수 있습니다."
    if n:
        return (
            f"{subject} 데이터를 지도에 표출했습니다. "
            f"조건에 해당하는 건물은 {n:,}동입니다."
        )
    return f"{subject} 데이터를 지도에 표출했습니다."


def _prose_without_markdown_table(text: str) -> str:
    """웹 스트리밍용 — 파이프 표는 HTML로 그리므로 본문에서 뺀다."""
    raw = (text or "").replace("\r\n", "\n")
    lines = raw.split("\n")
    kept: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if line.strip().startswith("|") and re.match(
            r"^\|[\s:|-]+\|$", nxt.strip()
        ):
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                i += 1
            continue
        kept.append(line)
        i += 1
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def build_share_distribution(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """법정동→행정동 비율 표를 result.table 형식으로."""
    if not rows:
        return None
    table_rows: list[dict[str, Any]] = []
    total = 0
    for row in rows:
        n = int(row.get("n") or 0)
        total += n
        pct = row.get("pct")
        try:
            pct_f = float(pct)
        except (TypeError, ValueError):
            pct_f = 0.0
        table_rows.append(
            {
                "range": str(row.get("admin_dong") or "미분류"),
                "n": n,
                "pct": pct_f,
            }
        )
    if not table_rows:
        return None
    peak = max(table_rows, key=lambda r: r["n"])
    return {
        "caption": "법정동 건물의 행정동 비율",
        "range_header": "행정동",
        "count_header": "건수",
        "share_header": "비율",
        "rows": table_rows,
        "total": total,
        "peak": peak,
    }


def _natural_admin_share(question: str, rows: list[dict[str, Any]]) -> str:
    """법정동 건물의 행정동 비율."""
    if not rows:
        return (
            f"{_subject_phrase(question)}를 행정동 경계로 나눠 볼 데이터가 없습니다."
        )
    if extract_usage(question) == "공동주택":
        head = "법정동 기준 아파트(공동주택)는"
    else:
        head = "법정동 기준 건물은"
    bits: list[str] = []
    lines = ["| 행정동 | 건수 | 비율 |", "|---|---:|---:|"]
    for row in rows:
        name = str(row.get("admin_dong") or "미분류")
        n = _fmt_number(row.get("n"))
        pct = row.get("pct")
        pct_s = _fmt_number(pct)
        bits.append(f"{name} {n}채({pct_s}%)")
        lines.append(f"| {name} | {n} | {pct_s}% |")
    lead = f"{head} {', '.join(bits)}입니다."
    return lead + "\n\n" + "\n".join(lines)


def _natural_admin_members(question: str, rows: list[dict[str, Any]]) -> str:
    """법정동에 대응하는 행정동 이름 목록."""
    names: list[str] = []
    for row in rows:
        name = str(row.get("admin_dong") or row.get("ADM_NM") or "").strip()
        if name and name not in names:
            names.append(name)
    place = extract_place(question) or extract_gu(question) or "해당 지역"
    if not names:
        return f"{place}에 대응하는 행정동을 찾지 못했습니다."
    listed = ", ".join(names)
    if len(names) == 1:
        return f"{place}에 해당하는 행정동은 {listed}입니다."
    return (
        f"{place}에 해당하는 행정동은 {len(names)}곳입니다. "
        f"{listed}입니다."
    )


def _threshold_metric(sql: str | None, route: str | None) -> tuple[str, str, str]:
    """(한글 지표명, 컬럼, 단위)."""
    r = str(route or "")
    if "height" in r:
        return "높이", "A16", "m"
    if "floor" in r:
        return "지상층수", "A26", "층"
    chunk = sql or ""
    m = re.search(r'"(A12|A14|A15)"\s*(?:>=|<=|>|<)', chunk)
    col = m.group(1) if m else "A14"
    if col == "A12":
        return "건물면적", "A12", "㎡"
    if col == "A15":
        return "대지면적", "A15", "㎡"
    return "연면적", "A14", "㎡"


def _list_total(rows: list[dict[str, Any]], row_count: int) -> int:
    """LIMIT 잘린 목록에서 COUNT(*) OVER() 전체 건수를 쓴다."""
    if rows:
        raw = rows[0].get("total_n")
        if raw not in (None, ""):
            try:
                return int(raw)
            except (TypeError, ValueError):
                pass
    return row_count


def _natural_threshold_list(
    question: str,
    *,
    sql: str | None,
    rows: list[dict[str, Any]],
    row_count: int,
    route: str | None,
) -> str:
    subject = _subject_phrase(question)
    metric, col, unit = _threshold_metric(sql, route)
    total = _list_total(rows, row_count)
    if total <= 0:
        return f"{subject}에 해당하는 건물은 없습니다."
    examples: list[str] = []
    for row in rows:
        name = str(row.get("A24") or "").strip()
        if not name:
            jibeon = str(row.get("A5") or "").strip()
            name = f"지번 {jibeon}" if jibeon else ""
        if not name:
            continue
        val = row.get(col)
        shown = _fmt_area(val, question) if unit == "㎡" else f"{_fmt_number(val)}{unit}"
        example = f"「{name}」 {metric} {shown}"
        if extract_calendar_year(question) is not None:
            day = _row_approval_date(row)
            if day:
                example = f"{example} (사용승인 {_fmt_date_ko(day)})"
        examples.append(example)
        if len(examples) >= 5:
            break
    lead = f"{subject}{_eun_neun(subject)} 모두 {total:,}동입니다."
    if not examples:
        return lead
    return (
        f"{lead} {metric}{_i_ga(metric)} 큰 대표 사례는 "
        f"{', '.join(examples)}입니다."
    )


def _natural_structure_list(
    question: str,
    *,
    rows: list[dict[str, Any]],
    row_count: int,
) -> str:
    subject = _subject_phrase(question)
    examples: list[str] = []
    for row in rows[:5]:
        name = str(row.get("A24") or "").strip() or "(이름 없음)"
        bits = [f"「{name}」"]
        land = str(row.get("A7") or "").strip()
        if land:
            bits.append(land)
        struct = str(row.get("A11") or "").strip()
        if struct:
            bits.append(struct)
        examples.append(" ".join(bits))
    total = _list_total(rows, row_count)
    if total <= 0:
        return f"{subject}에 해당하는 건축물은 없습니다."
    lead = f"{subject}{_eun_neun(subject)} 모두 {total:,}동입니다."
    if not examples:
        return lead
    return f"{lead} 대표 사례는 {', '.join(examples)}입니다."


def _natural_d198_list(
    question: str,
    *,
    rows: list[dict[str, Any]],
    row_count: int,
    route: str | None,
) -> str:
    place = extract_place(question) or extract_gu(question) or ""
    apt = "아파트" in question
    target = "아파트" if apt else "건물"

    def _name(row: dict[str, Any]) -> str:
        return (
            str(row.get("A13") or "").strip()
            or str(row.get("A14") or "").strip()
            or "(이름 없음)"
        )

    def _date(row: dict[str, Any]) -> str:
        return str(row.get("A34") or row.get("A33") or "").strip()

    if str(route) == "d198_attr_rank" and rows:
        row = rows[0]
        name = _name(row)
        day = _fmt_date_ko(_date(row)) if _date(row) else ""
        where = f"{place}에서 " if place else ""
        particle = _eun_neun(target)
        if any(k in question for k in ("최근", "지어", "건설일")):
            lead = f"{where}가장 최근에 지어진 {target}{particle} {name}입니다."
        elif any(k in question for k in ("오래", "먼저")):
            lead = f"{where}가장 오래전에 지어진 {target}{particle} {name}입니다."
        else:
            lead = f"{where}조건에 맞는 {target}{particle} {name}입니다."
        if day:
            lead += f" 사용승인일은 {day}입니다."
        return lead

    examples: list[str] = []
    show_n = min(len(rows), 10)
    for i, row in enumerate(rows[:show_n], start=1):
        name = _name(row)
        day = _fmt_date_ko(_date(row)) if _date(row) else ""
        bit = f"{i}) {name}"
        if day:
            bit += f" (사용승인일 {day})"
        examples.append(bit)
    where = f"{place}에서 " if place else ""
    if any(k in question for k in ("최근", "지어", "건설일")):
        lead = f"{where}최근에 지어진 {target}는 다음 {row_count}곳입니다."
    else:
        lead = f"{where}조건에 맞는 {target}는 다음 {row_count}곳입니다."
    if not examples:
        return lead
    return lead + "\n" + "\n".join(examples)


_THRESHOLD_LIST_ROUTES = frozenset(
    {
        "building_area_threshold_list",
        "building_height_threshold_list",
        "building_floor_threshold_list",
    }
)
_SIMPLE_COUNT_ROUTES = frozenset(
    {
        "building_place_count",
        "building_usage_count",
        "building_in_dong_spatial",
        "building_admin_dong_usage_count",
        "building_height_count",
        "building_floor_count",
        "building_area_threshold_count",
        "spatial_bas_dong_count",
        "spatial_bas_bnd_gu_count",
        "spatial_bldg_bas_count",
        "bas_count",
        "place_buffer_count",
        "place_buffer_outside_count",
        "buffer_count",
        "buildings_in_industrial",
        "industrial_count",
        "industrial_code_prefix",
        "building_structure_count",
        "building_special_land_count",
        "building_attr_count",
        "d198_attr_count",
        "building_age_count",
        "building_age_count_d198_partial",
    }
)
_THRESHOLD_COUNT_ROUTES = frozenset(
    {
        "building_area_threshold_count",
        "building_height_count",
        "building_floor_count",
        "building_structure_count",
        "building_special_land_count",
        "building_attr_count",
    }
)
_STRUCTURE_LIST_ROUTES = frozenset(
    {
        "building_structure_list",
        "building_special_land_list",
        "building_attr_list",
    }
)
_D198_LIST_ROUTES = frozenset(
    {"d198_attr_list", "d198_attr_lookup", "d198_attr_rank"}
)
_D198_COUNT_ROUTES = frozenset({"d198_attr_count"})


STATS_NARRATE_PROMPT = """당신은 부산 GIS 구간·연도 집계를 설명하는 한국어 안내원입니다.
질문과 집계 JSON만 근거로, 표 앞에 붙일 짧은 요약을 쓰세요.

규칙:
- 제공된 숫자만 사용하세요. 없는 구간·비율을 만들지 마세요.
- 2~4문장으로 총 동 수, 가장 많은 구간, 분포의 중심만 말하세요.
- 구간 이름은 집계 JSON의 range를 그대로 쓰세요. 평 환산이 있으면 빼지 마세요.
- 존댓말(~습니다)로 쓰고, 숫자는 천 단위 쉼표를 쓰세요.
- 모든 구간을 나열하지 마세요. 상세는 표로 보여 줍니다.
- 불릿·번호·마크다운 표·SQL·컬럼코드를 쓰지 마세요.
- 면적은 ㎡로 쓰되, 질문에 평이 있거나 구간 라벨에 평이 있으면 평도 함께 말하세요.
- 높이는 m, 건물은 동으로 쓰세요.
- 조사는 하나만 쓰세요. '은(는)'처럼 병기하지 마세요.
- 출력은 요약 본문만.
"""


def _period_count(row: dict[str, Any]) -> int:
    raw = row.get("n", row.get("cnt"))
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


def _stats_target(question: str) -> str:
    usage = extract_usage(question)
    if "아파트" in question:
        return "아파트"
    if usage:
        return usage
    return "건물"


def _stats_where(question: str) -> str:
    place = extract_place(question) or extract_gu(question) or ""
    if place:
        return f"{place} "
    return f"{d198_coverage_label()} "


def _pct(n: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(100.0 * n / total, 1)


def _object_particle(word: str) -> str:
    if not word:
        return "를"
    last = word[-1]
    code = ord(last)
    if 0xAC00 <= code <= 0xD7A3 and (code - 0xAC00) % 28:
        return "을"
    return "를"


def build_distribution(
    question: str,
    *,
    rows: list[dict[str, Any]],
    route: str | None,
    row_count: int | None = None,
) -> dict[str, Any] | None:
    """구간·연도 집계를 표/요약용 구조로 만든다."""
    n_rows = row_count if row_count is not None else len(rows)
    gu = extract_gu(question)
    if gu and gu not in D198_BY_GU and n_rows == 0:
        return None
    where = _stats_where(question)
    target = _stats_target(question)
    pairs: list[tuple[str, int]] = []
    range_header = "구간"
    caption = ""
    if route == "d198_value_bins":
        spec = parse_value_bin(question)
        if spec is None:
            return None
        width_txt = spec.width_label or f"{spec.bin_width:g}{spec.unit}"
        particle = _object_particle(spec.label)
        caption = (
            f"{where}{target} {spec.label}{particle} {width_txt} 단위로 묶은 "
            "건축물(동) 수입니다."
        )
        range_header = f"{spec.label} 구간"
        for row in rows:
            pairs.append((format_value_bin_label(row, spec), _period_count(row)))
    elif route == "d198_year_stats":
        spec = parse_year_stats(question)
        caption = f"{where}{target} 건립 수는 사용승인일 기준 건축물(동) 수입니다."
        range_header = "시기"
        filled = list(rows)
        if spec is not None and spec.mode == "decade" and spec.decades:
            by: dict[int, int] = {}
            for row in rows:
                key = row.get("decade", row.get("year"))
                try:
                    by[int(key)] = _period_count(row)
                except (TypeError, ValueError):
                    continue
            filled = [{"decade": d, "n": by.get(d, 0)} for d in spec.decades]
        for row in filled:
            if spec is not None and spec.mode == "decade":
                period = row.get("decade", row.get("year"))
                try:
                    label = f"{int(period)}년대"
                except (TypeError, ValueError):
                    label = str(period)
            else:
                label = format_year_stats_label(row, question=question, spec=spec)
            pairs.append((label, _period_count(row)))
    else:
        return None
    if not pairs:
        return None
    total = sum(n for _label, n in pairs)
    table_rows = [
        {"range": label, "n": n, "pct": _pct(n, total)} for label, n in pairs
    ]
    peak = max(table_rows, key=lambda r: r["n"]) if table_rows else None
    return {
        "caption": caption,
        "range_header": range_header,
        "count_header": "동 수",
        "share_header": "비율",
        "rows": table_rows,
        "total": total,
        "peak": peak,
    }


def format_stats_markdown(dist: dict[str, Any]) -> str:
    rh = dist.get("range_header") or "구간"
    ch = dist.get("count_header") or "동 수"
    sh = dist.get("share_header") or "비율"
    lines = [
        f"| {rh} | {ch} | {sh} |",
        "| --- | ---: | ---: |",
    ]
    for row in dist.get("rows") or []:
        lines.append(
            f"| {row['range']} | {int(row['n']):,} | {row['pct']:g}% |"
        )
    total = int(dist.get("total") or 0)
    lines.append(f"| 합계 | {total:,} | 100% |")
    return "\n".join(lines)


def format_distribution_answer(
    dist: dict[str, Any], *, prose: str | None = None
) -> str:
    lead = (prose or "").strip() or str(dist.get("caption") or "").strip()
    return f"{lead}\n\n{format_stats_markdown(dist)}"


def _looks_like_range_dump(text: str) -> bool:
    return len(re.findall(r"\d+\s*~\s*\d+", text)) >= 5


def narrate_distribution(
    question: str,
    dist: dict[str, Any],
    *,
    model: str,
    host: str | None = None,
    client: Any | None = None,
) -> str:
    payload = {
        "caption": dist.get("caption"),
        "total": dist.get("total"),
        "peak": dist.get("peak"),
        "bins": dist.get("rows"),
        "note": "구간 전체는 표로 보여 줍니다. 모든 칸을 나열하지 마세요.",
    }
    return _llm_narrate(
        system=STATS_NARRATE_PROMPT,
        user=(
            f"사용자 질문:\n{question.strip()}\n\n"
            f"집계(JSON):\n{json.dumps(payload, ensure_ascii=False)}\n\n"
            "위 숫자만으로 한국어 요약을 쓰세요."
        ),
        model=model,
        host=host,
        client=client,
        temperature=0.2,
        on_token=None,
        empty_error="빈 구간 요약",
    )


def _natural_year_stats(
    question: str,
    *,
    rows: list[dict[str, Any]],
    row_count: int,
) -> str:
    gu = extract_gu(question)
    if gu and d198_table_for_gu(gu) is None and row_count == 0:
        return (
            f"{gu}는 건립 연도(사용승인일) 통계 자료가 없습니다. "
            f"현재는 {d198_coverage_label()} 용도별건물만 연도별 집계가 됩니다."
        )
    dist = build_distribution(
        question, rows=rows, route="d198_year_stats", row_count=row_count
    )
    if dist is None:
        where = _stats_where(question)
        target = _stats_target(question)
        return (
            f"{where}{target} 건립 수는 사용승인일 기준 건축물(동) 수입니다. "
            "해당하는 자료를 찾지 못했습니다."
        )
    return format_distribution_answer(dist)


def _natural_value_bins(
    question: str,
    *,
    rows: list[dict[str, Any]],
    row_count: int,
) -> str:
    gu = extract_gu(question)
    if gu and d198_table_for_gu(gu) is None and row_count == 0:
        return (
            f"{gu}는 면적·높이 구간 통계 자료가 없습니다. "
            f"현재는 {d198_coverage_label()} 용도별건물만 구간별 집계가 됩니다."
        )
    dist = build_distribution(
        question, rows=rows, route="d198_value_bins", row_count=row_count
    )
    if dist is None:
        where = _stats_where(question)
        target = _stats_target(question)
        return f"{where}{target} 구간별 자료를 만들지 못했습니다."
    return format_distribution_answer(dist)


def _labels_for_route(route: str | None) -> dict[str, str]:
    if route and str(route).startswith("d198_"):
        labels = dict(_COLUMN_LABELS)
        labels.update(D198_COLUMN_LABELS)
        labels["A7"] = "지번"
        labels["A13"] = "건물명"
        labels["A14"] = "건물동명"
        labels["A16"] = "건물주부구분명"
        labels["A19"] = "건물연면적_㎡"
        labels["A25"] = "주요용도명"
        labels["A27"] = "세부용도명"
        return labels
    if route and str(route).startswith("followup_"):
        labels = dict(_COLUMN_LABELS)
        labels.update(D198_COLUMN_LABELS)
        return labels
    if route and str(route).startswith(
        ("d010_attr", "d060_attr", "bnd_attr", "bas_attr")
    ):
        from txt2sql.catalog_attrs import DATASETS

        labels = dict(_COLUMN_LABELS)
        for ds in DATASETS:
            if str(route).startswith(ds.intent_prefix):
                for attr in ds.attrs:
                    labels[attr.col] = attr.label
                break
        return labels
    return _COLUMN_LABELS


def _label_row(
    row: dict[str, Any],
    *,
    route: str | None = None,
    table: str | None = None,
) -> dict[str, Any]:
    effective_route = route
    if route and str(route).startswith("followup_"):
        if infer_row_dataset(row, table=table, route=route) == "d198":
            effective_route = "d198_attr_rank"
    labels = _labels_for_route(effective_route)
    labeled: dict[str, Any] = {}
    for k, v in row.items():
        if k in _SKIP_SUMMARY_COLS:
            continue
        if v is None or isinstance(v, (str, int, float, bool)):
            val: Any = v
        else:
            try:
                val = int(v) if int(v) == v else float(v)
            except Exception:
                val = str(v)
        key = labels.get(k, k)
        if re.fullmatch(r"A\d+", str(key)):
            continue
        labeled[key] = val
    return labeled


def _spoken_building(
    row: dict[str, Any],
    *,
    route: str | None = None,
    question: str | None = None,
    table: str | None = None,
) -> dict[str, Any]:
    """LLM에 넘길 핵심 사실만. 스키마 용어 나열을 막는다."""
    dataset = infer_row_dataset(row, table=table, route=route)
    is_d198 = dataset == "d198"
    name = row_building_name(row, table=table, route=route) or ""
    out: dict[str, Any] = {}
    if name:
        out["건물명"] = name
    addr = row_full_address(row, table=table, route=route)
    if addr:
        out["주소"] = addr
    lot = row_lot_address(row, table=table, route=route)
    if lot:
        out["지번"] = lot
    date = _row_approval_date(row, table=table, route=route)
    if date:
        out["사용승인일" if row.get("A34") else "허가일"] = _fmt_date_ko(date)
        if row.get("A34") and row.get("A33"):
            out["허가일"] = _fmt_date_ko(str(row.get("A33")).strip())
    usage = str(_row_usage(row, table=table, route=route) or "").strip()
    detail = str(row.get("A27") or "").strip()
    if detail == "아파트" or usage == "공동주택":
        out["용도"] = "아파트(공동주택)"
    elif usage:
        out["용도"] = usage
    height = _row_height(row, table=table, route=route)
    floors = _row_floors(row, table=table, route=route)
    area = row_gross_floor_area(row, table=table, route=route)
    if height not in (None, ""):
        out["높이_m"] = height
    if floors not in (None, ""):
        out["지상층"] = floors
    if area not in (None, ""):
        out["연면적_㎡"] = area
        if mentions_pyeong(question or ""):
            try:
                out["연면적_평"] = format_pyeong_from_m2(float(area))
            except (TypeError, ValueError):
                pass
    _ = is_d198
    return out


def _result_payload(
    *,
    rows: list[dict[str, Any]],
    row_count: int,
    route: str | None,
    question: str | None = None,
    preview_limit: int = 8,
) -> dict[str, Any]:
    if route in {"d198_year_stats", "d198_value_bins"}:
        preview_limit = max(preview_limit, 40)
    preview = rows[:preview_limit]
    q = (question or "").strip()
    gu = extract_gu(q)
    speak_compact = bool(
        route
        and str(route).startswith(("d198_", "building_rank_"))
        and route not in {"d198_year_stats", "d198_value_bins", "d198_attr_count"}
    )
    if (
        not speak_compact
        and preview
        and route
        and str(route).startswith("followup_")
        and ("A34" in preview[0] or "A13" in preview[0])
    ):
        speak_compact = True
    if speak_compact:
        clean_rows = [
            _spoken_building(row, route=route, question=q) for row in preview
        ]
    else:
        clean_rows = [_label_row(row, route=route) for row in preview]
    payload: dict[str, Any] = {
        "row_count": row_count,
        "preview_rows": clean_rows,
        "preview_truncated": max(0, row_count - len(clean_rows)),
    }
    scalar = _scalar_from_rows(rows) if rows else None
    if scalar is not None and row_count == 1 and len(rows[0]) <= 2:
        if hasattr(scalar, "__int__") and not isinstance(scalar, bool):
            try:
                scalar = int(scalar)
            except Exception:
                pass
        payload["scalar_value"] = scalar
    if route:
        if str(route).startswith("building_rank_"):
            payload["result_type"] = "건물_순위_1건"
            payload["rank_metric"] = str(route).replace("building_rank_", "")
        elif route == "building_age_count_d198_partial":
            payload["result_type"] = "건축경과년_건수"
            payload["scope"] = d198_coverage_label(joiner="+")
            if gu not in D198_BY_GU:
                payload["coverage_note"] = (
                    f"건축년수 자료는 {d198_coverage_label()} 용도별건물만 있습니다."
                )
        elif str(route).startswith("d198_"):
            if route == "d198_year_stats":
                payload["result_type"] = "연도별_건립_건수"
                payload["unit"] = "건축물(동) 수"
                payload["date_basis"] = "사용승인일"
            elif route == "d198_value_bins":
                payload["result_type"] = "수치_구간_건수"
                payload["unit"] = "건축물(동) 수"
            elif route == "d198_attr_rank":
                payload["result_type"] = "가장_최근_또는_조건_1건"
            elif route == "d198_attr_count":
                payload["result_type"] = "건수"
            else:
                payload["result_type"] = "목록"
            if gu not in D198_BY_GU:
                payload["coverage_note"] = (
                    f"용도별건물 사용승인·허가일은 {d198_coverage_label()} 자료입니다."
                )
        elif str(route).startswith(
            ("d010_attr", "d060_attr", "bnd_attr", "bas_attr")
        ):
            if str(route).endswith("_rank") or str(route).endswith("_lookup"):
                payload["result_type"] = (
                    "조건내_1건" if str(route).endswith("_rank") else "목록"
                )
            elif str(route).endswith("_count"):
                payload["result_type"] = "건수"
            else:
                payload["result_type"] = "목록"
        elif "count" in str(route).lower() or "cnt" in str(route).lower():
            payload["result_type"] = "건수"
        elif str(route).startswith("followup_"):
            payload["result_type"] = "직전_건물_속성"
        else:
            payload["result_type"] = "조회"
    return payload


def _strip_answer_text(text: str) -> str:
    text = text.strip()
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE).strip()
    fence = re.search(r"```(?:\w+)?\s*(.*?)```", text, re.IGNORECASE | re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    text = re.sub(r"^(답변|Answer)\s*[:：]\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"^안내:\s*[^\n]*(?:\n\n|\n)?",
        "",
        text,
        flags=re.MULTILINE,
    ).strip()
    return text.strip()


def _is_stiff_answer(text: str) -> bool:
    """스키마 라벨을 이어 붙인 템플릿형 답변인지."""
    if not text:
        return True
    bad = (
        "해당 조건의 건축물",
        "사용승인일자 있음",
        "세부용도명",
        "특수지구분명",
        "예: 「",
        "조회 결과는 다음과 같습니다",
    )
    return any(k in text for k in bad)


def _list_omits_dates(answer: str, rows: list[dict[str, Any]]) -> bool:
    """목록 행에 사용승인일이 있는데 답변에 날짜가 빠졌는지."""
    dated = [
        r
        for r in rows
        if str(r.get("A34") or r.get("A33") or "").strip()
    ]
    if len(dated) < 2:
        return False
    if re.search(r"\d{4}\s*년", answer) or re.search(r"\d{4}-\d{2}-\d{2}", answer):
        return False
    return True


def _resolve_client(*, host: str | None, client: Any | None) -> Any:
    return resolve_client(host=host, client=client)


def _chat(
    *,
    model: str,
    messages: list[dict[str, str]],
    host: str | None = None,
    client: Any | None = None,
    temperature: float = 0.2,
) -> str:
    return chat(
        model=model,
        messages=messages,
        host=host,
        client=client,
        temperature=temperature,
    )


def _chat_stream(
    *,
    model: str,
    messages: list[dict[str, str]],
    host: str | None = None,
    client: Any | None = None,
    temperature: float = 0.2,
    on_token: TokenCallback | None = None,
) -> str:
    """Ollama chat 스트림. 토큰마다 on_token을 호출하고 전체 문자열을 반환."""
    return chat(
        model=model,
        messages=messages,
        host=host,
        client=client,
        temperature=temperature,
        stream=True,
        on_token=on_token,
    )


def _llm_narrate(
    *,
    system: str,
    user: str,
    model: str,
    host: str | None,
    client: Any | None,
    temperature: float,
    on_token: TokenCallback | None,
    empty_error: str,
) -> str:
    raw = chat(
        model=model,
        host=host,
        client=client,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        stream=on_token is not None,
        on_token=on_token,
    )
    answer = _strip_answer_text(raw)
    if not answer:
        raise ValueError(empty_error)
    return answer


def emit_text_chunks(
    text: str,
    on_token: TokenCallback | None,
    *,
    chunk_size: int = 8,
    delay_s: float = 0.012,
) -> None:
    """비스트림 답변을 UI용으로 쪼개 전달."""
    if on_token is None or not text:
        return
    for i in range(0, len(text), chunk_size):
        on_token(text[i : i + chunk_size])
        if delay_s > 0:
            time.sleep(delay_s)


# 하위 호환
_emit_text_chunks = emit_text_chunks


PROFILE_SYSTEM_PROMPT = """당신은 부산 GIS 건물 집계 결과를 사용자에게 설명하는 한국어 안내원입니다.
주어진 질문과 집계 JSON만 근거로, 대화하듯 자연스러운 문단으로 요약하세요.

규칙:
- 제공된 숫자·사실만 사용하세요. 추측·없는 수치를 만들지 마세요.
- 불릿·번호 목록·마크다운을 쓰지 말고 2~6문장의 문단으로 답하세요.
- focus=usage_overview 이면 상위 용도 구성·비율을 중심으로 설명하고, 용도명의 일반적 의미는 짧게만 덧붙이세요.
- compare=true 이고 compare_kind=top_building 이면 각 지역의 ‘최고 건물’을 모두 소개하고, metric 기준으로 어느 지역 건물이 더 높은지/큰지 비교하세요.
- compare=true 이고 compare_kind=industrial 이면 지역 전체와 산업단지 내 건물을 대비해 설명하세요.
- far_focus=true 이거나 avg_far_pct가 있으면 용적율(필요 시 건폐율)을 중심으로 비교·설명하세요.
- compare=true 이고 일반 groups 집계면 평균·동 수 등을 비교하세요.
- groups에 place_basis가 있으면(행정동 경계 vs 법정동 주소) 집계 기준이 다르다는 점을 한 문장으로 밝히세요.
- groups에 '부산시 전역'이 있으면 특정 지역을 시 전체 평균·규모와 대비해 설명하세요.
- SQL, 컬럼코드(A12 등), 테이블명, 라우트명을 넣지 마세요.
- 면적은 ㎡로 쓰되, 질문에 평이 있거나 JSON에 평 환산이 있으면 평도 함께 쓰세요.
- 높이는 m, 층은 층, 용적율·건폐율은 %, 건물은 동/채로 쓰세요.
- apartment_note가 있으면 아파트=공동주택 집계라는 점을 자연스럽게 한 문장으로 언급하세요.
- far_note가 있으면 용적율 산출 방식을 한 문장으로 짧게 안내하세요.
- 조사는 자연스럽게 하나만 쓰세요. '은(는)', '이(가)', '을(를)'처럼 병기하지 마세요.
- 출력은 답변 본문만.
"""


def narrate_building_profile(
    question: str,
    *,
    payload: dict[str, Any],
    model: str,
    host: str | None = None,
    client: Any | None = None,
    on_token: TokenCallback | None = None,
) -> str:
    """특징 집계 JSON을 자연어 문단으로 변환."""
    user_content = (
        f"사용자 질문:\n{question.strip()}\n\n"
        f"집계 결과(JSON):\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        "위 집계만 근거로 한국어 특징 요약을 작성하세요."
    )
    return _llm_narrate(
        system=PROFILE_SYSTEM_PROMPT,
        user=user_content,
        model=model,
        host=host,
        client=client,
        temperature=0.3,
        on_token=on_token,
        empty_error="빈 프로필 LLM 답변",
    )


def generate_natural_answer(
    question: str,
    *,
    rows: list[dict[str, Any]],
    row_count: int,
    sql: str | None = None,
    route: str | None = None,
    model: str,
    host: str | None = None,
    client: Any | None = None,
    on_token: TokenCallback | None = None,
) -> str:
    """조회 결과를 LLM으로 자연스러운 한국어 답변으로 변환."""
    payload = _result_payload(
        rows=rows, row_count=row_count, route=route, question=question
    )
    user_content = (
        f"사용자 질문:\n{question.strip()}\n\n"
        f"조회 결과(JSON, 핵심 사실만):\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        "위 사실만으로 질문에 바로 답하세요. 스키마 용어를 나열하지 마세요."
    )
    _ = sql
    return _llm_narrate(
        system=ANSWER_SYSTEM_PROMPT,
        user=user_content,
        model=model,
        host=host,
        client=client,
        temperature=0.2,
        on_token=on_token,
        empty_error="빈 LLM 답변",
    )


def format_success_template(
    question: str,
    *,
    sql: str,
    rows: list[dict[str, Any]],
    row_count: int,
    route: str | None = None,
) -> str:
    """LLM 없이 규칙 기반으로 한국어 답변 (폴백)."""
    if row_count == 0:
        subject = _subject_phrase(question)
        return (
            f"{subject}에 해당하는 데이터를 찾지 못했습니다. "
            "조건을 바꿔 다시 질문해 주세요."
        )

    if route == "building_map_display" or wants_map_display(question):
        return format_map_display_answer(question, rows=rows, include_map=True)

    if route == "named_dataset_place_area":
        return _format_place_boundary_area(question, rows=rows, row_count=row_count)

    scalar = _scalar_from_rows(rows)
    single_metric = row_count == 1 and scalar is not None and len(rows[0]) <= 2

    if route in _THRESHOLD_LIST_ROUTES:
        return _natural_threshold_list(
            question,
            sql=sql,
            rows=rows,
            row_count=row_count,
            route=route,
        )

    if route in _STRUCTURE_LIST_ROUTES:
        return _natural_structure_list(
            question, rows=rows, row_count=row_count
        )

    rank_route = (
        str(route)
        if route and str(route).startswith("building_rank_")
        else _infer_rank_route(question)
    )
    if (
        rank_route
        and rows
        and "A4" in rows[0]
        and any(k in rows[0] for k in ("A14", "A16", "A12", "A19", "A30", "A24"))
    ):
        if row_count == 1:
            return _natural_rank(question, rank_route, rows[0])
        return _natural_rank_list(question, rank_route, rows)

    if (
        route == "building_name_lookup"
        and rows
        and any(k in rows[0] for k in ("A4", "A24", "A5"))
    ):
        return _natural_building_name_lookup(question, rows)

    if route == "industrial_names":
        return _natural_industrial_names(question, rows)

    if route == "legal_dong_admin_share":
        return _natural_admin_share(question, rows)

    if route == "legal_dong_admin_members":
        return _natural_admin_members(question, rows)

    if route == "d198_year_stats":
        return _natural_year_stats(question, rows=rows, row_count=row_count)

    if route == "d198_value_bins":
        return _natural_value_bins(question, rows=rows, row_count=row_count)

    if route in _D198_LIST_ROUTES:
        return _natural_d198_list(question, rows=rows, row_count=row_count, route=route)

    if route in _THRESHOLD_COUNT_ROUTES and single_metric:
        return _natural_count(question, scalar)

    if route in _SIMPLE_COUNT_ROUTES and single_metric:
        return _natural_count(question, scalar)

    if single_metric and _looks_like_count_question(question):
        if "종류" in question:
            return (
                f"{_subject_phrase(question)}의 종류는 "
                f"{_fmt_number(scalar)}가지입니다."
            )
        if (
            any(k in question for k in ("면적값", "연면적 값"))
            or ("얼마" in question and "얼마나" not in question)
        ) and not any(k in question for k in ("몇", "개수", "건수", "채")):
            return (
                f"{_subject_phrase(question)}의 값은 "
                f"{_fmt_number(scalar)}입니다."
            )
        return _natural_count(question, scalar)

    if single_metric:
        subject = _subject_phrase(question)
        return f"{subject}에 대한 조회 값은 {_fmt_number(scalar)}입니다."

    preview = _summarize_rows(rows)
    subject = _subject_phrase(question)
    if row_count == 1:
        return f"{subject}에 대한 조회 결과는 다음과 같습니다.\n{preview}"
    return (
        f"{subject} 기준으로 총 {row_count:,}건을 찾았습니다. "
        f"주요 결과는 아래와 같습니다.\n{preview}"
    )


def coverage_preface(route: str | None, question: str | None = None) -> str | None:
    """데이터 범위가 질문보다 좁을 때 앞에 붙일 안내문."""
    if not route:
        return None
    if question and extract_gu(question) in D198_BY_GU:
        return None
    return _coverage_preface_map().get(str(route))


def with_coverage_preface(
    answer: str, route: str | None, question: str | None = None
) -> str:
    preface = coverage_preface(route, question)
    if not preface:
        return answer
    body = answer.strip()
    # LLM이 비슷한 안내를 넣었으면 중복 제거에 가깝게 본문만 유지
    if body.startswith("안내:"):
        return body
    return f"{preface}\n\n{body}"


def format_success(
    question: str,
    *,
    sql: str,
    rows: list[dict[str, Any]],
    row_count: int,
    route: str | None = None,
    model: str | None = None,
    host: str | None = None,
    client: Any | None = None,
    on_token: TokenCallback | None = None,
) -> str:
    """성공 시 자연스러운 한국어 답변 (가능하면 LLM, 실패 시 템플릿)."""
    streamed = False
    used_llm = False

    if route in {"d198_year_stats", "d198_value_bins"}:
        dist = build_distribution(
            question, rows=rows, route=route, row_count=row_count
        )
        fallback = format_success_template(
            question,
            sql=sql,
            rows=rows,
            row_count=row_count,
            route=route,
        )
        answer = fallback
        if dist:
            answer = format_distribution_answer(
                dist, prose=str(dist.get("caption") or "")
            )
        final = with_coverage_preface(answer, route, question)
        if on_token is not None:
            emit_text_chunks(_prose_without_markdown_table(final), on_token)
        return final

    if route == "named_dataset_place_area":
        answer = format_success_template(
            question,
            sql=sql,
            rows=rows,
            row_count=row_count,
            route=route,
        )
        final = with_coverage_preface(answer, route, question)
        if on_token is not None:
            emit_text_chunks(final, on_token)
        return final

    if route == "legal_dong_admin_share":
        answer = format_success_template(
            question,
            sql=sql,
            rows=rows,
            row_count=row_count,
            route=route,
        )
        final = with_coverage_preface(answer, route, question)
        if on_token is not None:
            emit_text_chunks(_prose_without_markdown_table(final), on_token)
        return final

    if route == "legal_dong_admin_members":
        answer = format_success_template(
            question,
            sql=sql,
            rows=rows,
            row_count=row_count,
            route=route,
        )
        final = with_coverage_preface(answer, route, question)
        if on_token is not None:
            emit_text_chunks(final, on_token)
        return final

    if route in _THRESHOLD_LIST_ROUTES or route in _STRUCTURE_LIST_ROUTES:
        answer = format_success_template(
            question,
            sql=sql,
            rows=rows,
            row_count=row_count,
            route=route,
        )
        final = with_coverage_preface(answer, route, question)
        if on_token is not None:
            emit_text_chunks(final, on_token)
        return final

    if route == "building_map_display" or wants_map_display(question):
        answer = format_map_display_answer(
            question, rows=rows, include_map=True
        )
        final = with_coverage_preface(answer, route, question)
        if on_token is not None:
            emit_text_chunks(final, on_token)
        return final

    if route == "building_name_lookup":
        answer = format_success_template(
            question,
            sql=sql,
            rows=rows,
            row_count=row_count,
            route=route,
        )
        final = with_coverage_preface(answer, route, question)
        if on_token is not None:
            emit_text_chunks(final, on_token)
        return final

    if route in _SIMPLE_COUNT_ROUTES or (
        route and str(route).startswith("building_rank_")
    ):
        answer = format_success_template(
            question,
            sql=sql,
            rows=rows,
            row_count=row_count,
            route=route,
        )
        final = with_coverage_preface(answer, route, question)
        if on_token is not None:
            emit_text_chunks(final, on_token)
        return final

    if route == "semantic_v2":
        answer = format_success_template(
            question,
            sql=sql,
            rows=rows,
            row_count=row_count,
            route=route,
        )
        final = with_coverage_preface(answer, route, question)
        if on_token is not None:
            emit_text_chunks(_prose_without_markdown_table(final), on_token)
        return final

    answer: str
    if model and (client is not None or host):
        try:
            answer = generate_natural_answer(
                question,
                rows=rows,
                row_count=row_count,
                sql=sql,
                route=route,
                model=model,
                host=host,
                client=client,
                on_token=None,
            )
            answer = _strip_answer_text(answer)
            if _is_stiff_answer(answer) or _list_omits_dates(answer, rows):
                answer = format_success_template(
                    question,
                    sql=sql,
                    rows=rows,
                    row_count=row_count,
                    route=route,
                )
            else:
                used_llm = True
        except Exception:
            answer = format_success_template(
                question,
                sql=sql,
                rows=rows,
                row_count=row_count,
                route=route,
            )
    else:
        answer = format_success_template(
            question,
            sql=sql,
            rows=rows,
            row_count=row_count,
            route=route,
        )

    final = (
        answer.strip()
        if used_llm
        else with_coverage_preface(answer, route, question)
    )
    if used_llm:
        final = _strip_answer_text(final)
    if on_token is not None and not streamed:
        emit_text_chunks(final, on_token)
        streamed = True
    return final


def format_failure(
    question: str,
    *,
    error: str | Exception,
    sql: str | None = None,
) -> str:
    """실패 시 한국어 사유 설명."""
    q = question.strip().rstrip("?？")
    msg = str(error)
    reason = _humanize_error(msg)

    parts = [
        f"요청하신 「{q}」에는 지금은 답하지 못했습니다.",
        f"이유: {reason}",
    ]
    if sql:
        parts.append(f"시도한 SQL: {sql.strip()}")
    parts.append("질문을 조금 더 구체적으로 바꿔 다시 시도해 주세요.")
    return "\n".join(parts)


def _humanize_error(msg: str) -> str:
    lower = msg.lower()
    if "unsupported" in lower:
        return "현재 지원하지 않는 요청입니다(조회 외 작업 등)."
    if "undefinedcolumn" in lower or "칼럼은 없습니다" in msg:
        col = _extract_quoted(msg) or "알 수 없는 컬럼"
        return f"존재하지 않는 컬럼을 참조했습니다({col})."
    if "undefinedtable" in lower or "릴레이션" in msg:
        table = _extract_quoted(msg) or "알 수 없는 테이블"
        return f"존재하지 않는 테이블을 참조했습니다({table})."
    if "undefinedfunction" in lower or "연산자 없음" in msg:
        return "데이터 타입에 맞지 않는 연산/함수를 사용했습니다."
    if "syntaxerror" in lower or "구문 오류" in msg:
        return "생성된 SQL 구문이 올바르지 않습니다."
    if "readonly" in lower or "금지된 키워드" in msg or "select/with" in lower:
        return "데이터 변경/삭제 등 허용되지 않는 SQL입니다."
    if "timeout" in lower or "canceling statement" in lower:
        return "질의 실행 시간이 너무 길어 중단되었습니다."
    if "connection" in lower or "연결" in msg:
        return "데이터베이스 연결에 실패했습니다."
    compact = re.sub(r"\s+", " ", msg).strip()
    if len(compact) > 160:
        compact = compact[:157] + "..."
    return compact


def _extract_quoted(msg: str) -> str | None:
    m = re.search(r'"([^"]+)"', msg)
    if m:
        return m.group(1)
    m = re.search(r"'([^']+)'", msg)
    if m:
        return m.group(1)
    return None
