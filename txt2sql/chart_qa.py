"""설명·비교 등 집계 답변을 차트로 제안·렌더링하기 위한 스펙."""

from __future__ import annotations

import re
from typing import Any

_ACCEPT = (
    "차트로",
    "차트 로",
    "차트 보",
    "차트보",
    "차트 그려",
    "시각화",
    "그래프로",
    "그래프 보",
)
_ACCEPT_FULL = (
    "보여줘",
    "보여 줘",
    "그려줘",
    "그려 줘",
    "그려",
    "부탁해",
    "부탁해요",
    "그래요",
    "좋아요",
)
_ACCEPT_SHORT = ("응", "네", "예", "좋아", "그래", "ㅇㅇ", "ok", "OK", "yes", "Yes")
_DECLINE = (
    "아니",
    "괜찮",
    "필요 없",
    "필요없",
    "됐어",
    "됐 어",
    "그만",
    "텍스트만",
    "사양",
    "no",
    "No",
)

_OFFER_SUFFIX = "\n\n이 내용을 차트로도 정리할 수 있어요. 차트로 보시겠어요?"

_CHART_TYPE_LABELS = {
    "bar": "막대",
    "doughnut": "도넛",
    "pie": "파이",
    "line": "선",
}


def parse_chart_type_request(question: str) -> str | None:
    """차트 종류 변경 요청이면 Chart.js type 문자열을 반환."""
    q = question.strip().lower()
    if not q:
        return None
    compact = re.sub(r"\s+", "", q)
    # 차트/그래프/그려 맥락이 있거나, 종류 명칭이 분명할 때
    chartish = any(
        k in q
        for k in (
            "차트",
            "그래프",
            "그려",
            "시각화",
            "도넛",
            "도우넛",
            "막대",
            "파이",
            "바차트",
            "bar",
            "pie",
            "doughnut",
            "line",
            "라인",
        )
    ) or any(
        k in compact
        for k in (
            "선차트",
            "막대차트",
            "파이차트",
            "도넛차트",
            "라인차트",
        )
    )
    if not chartish:
        return None
    if any(
        k in compact or k in q
        for k in (
            "막대차트",
            "막대",
            "바차트",
            "바 차트",
            "barchart",
            "bar chart",
            "bar",
        )
    ):
        return "bar"
    if any(
        k in compact or k in q
        for k in (
            "도넛차트",
            "도넛",
            "도우넛",
            "doughnut",
            "링차트",
            "링 차트",
        )
    ):
        return "doughnut"
    if any(
        k in compact or k in q
        for k in (
            "파이차트",
            "파이",
            "원형",
            "원그래프",
            "원 그래프",
            "piechart",
            "pie",
        )
    ):
        return "pie"
    if any(
        k in compact or k in q
        for k in (
            "선차트",
            "선그래프",
            "선 차트",
            "선 그래프",
            "라인차트",
            "라인 차트",
            "라인그래프",
            "linechart",
            "line",
        )
    ):
        return "line"
    return None


def is_chart_type_change_question(question: str) -> bool:
    return parse_chart_type_request(question) is not None


def with_chart_type(spec: dict[str, Any], chart_type: str) -> dict[str, Any]:
    out = dict(spec)
    out["type"] = chart_type
    return out


def chart_type_label(chart_type: str) -> str:
    return _CHART_TYPE_LABELS.get(chart_type, chart_type)


# (질문 키워드, 데이터셋 label에 포함되면 매칭)
_SERIES_FILTERS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("연면적",), ("연면적",)),
    (("건물면적", "건축물면적", "건축면적"), ("건물면적", "건축물면적", "건축면적", "면적")),
    (("용적율", "용적률"), ("용적율", "용적률")),
    (("건폐율", "건폐률"), ("건폐율", "건폐률")),
    (("면적",), ("면적",)),
    (("높이", "고도"), ("높이",)),
    (("층수", "지상층", "층"), ("층",)),
    (
        ("건물 수", "건물수", "건수", "채수", "개수", "동수"),
        ("건물 수", "건수", "개수"),
    ),
)


def is_chart_series_filter_question(question: str) -> bool:
    """직전 차트에서 일부 지표만 남기라는 후속 질의."""
    q = question.strip()
    if not q:
        return False
    if re.search(r"(?:있|없)지만", q):
        return False
    chartish = any(k in q for k in ("차트", "그래프", "그려", "시각화", "만으로"))
    from txt2sql.query_understanding.contract import extract_contract

    contract = extract_contract(q)
    if contract.wants_count and not chartish:
        return False
    if contract.operation in {"list", "group", "group_rank", "rank"} and not chartish:
        return False
    has_metric = any(any(k in q for k in keys) for keys, _ in _SERIES_FILTERS)
    if not has_metric:
        return False
    q_man = q.replace("미만", "\u0000")
    # 「금정구의 것만」「데이터 중 ~만」은 행/지역 필터이지 차트 시리즈가 아님
    if not chartish and (
        re.search(r"의\s*것만", q)
        or re.search(r"것만(?:\s|$)", q)
        or ("데이터 중" in q and "만" in q_man)
    ):
        return False
    onlyish = any(
        k in q
        for k in (
            "만으로",
            "만으로만",
            "만 그려",
            "만그려",
            "만 보여",
            "만보여",
            "만 다시",
            "만다시",
            "만 차트",
            "만차트",
            "만 그래프",
            "만으로 차트",
            "만으로 그려",
        )
    ) or bool(re.search(r"(?<!것)[가-힣0-9]만(?:으로|으)?(?:\s|$)", q_man))
    if re.search(r"(이상|이하|초과|미만)만", q):
        return False
    if not onlyish and "만" not in q_man:
        return False
    # 「기록된 것만 보여줘」 같은 list 필터는 차트 후속이 아님
    if any(k in q for k in ("보여줘", "보여 줘", "목록", "찾아")) and not any(
        k in q for k in ("차트", "그래프", "그려", "시각화")
    ):
        return False
    if onlyish:
        return True
    # 「높이만 차트로」처럼 차트 맥락 + 만
    return "만" in q_man and any(
        k in q for k in ("차트", "그래프", "그려", "시각화", "다시")
    )


def is_chart_metric_draw_question(question: str) -> bool:
    """「평균용적율로 그려라」처럼 특정 지표로 차트를 그리라는 후속 질의.

    「만」이 없어도 지표+그리기 동사가 있으면 참. 첫 질의(맥락 없음)에는
    파이프라인이 이 함수만으로 SQL을 가로채지 않는다.
    """
    q = question.strip()
    if not q:
        return False
    has_metric = any(any(k in q for k in keys) for keys, _ in _SERIES_FILTERS)
    if not has_metric:
        return False
    return any(k in q for k in ("그려", "차트", "그래프", "시각화"))


def _source_datasets(spec: dict[str, Any]) -> list[dict[str, Any]]:
    raw = spec.get("all_datasets") or spec.get("datasets") or []
    return [dict(d) for d in raw if isinstance(d, dict)]


def _requested_series_keys(question: str) -> list[tuple[str, ...]]:
    """질문에 언급된 시리즈 매칭 키(label 부분문자열) 목록."""
    q = question.strip()
    found: list[tuple[str, ...]] = []
    # 연면적 → 면적 보다 먼저 소비되도록 순서 유지, 이미 잡힌 넓은 키 중복 방지
    used_spans: list[str] = []
    for keys, label_bits in _SERIES_FILTERS:
        hit = next((k for k in keys if k in q), None)
        if not hit:
            continue
        # '면적'이 '연면적' 일부로만 잡힌 경우 스킵
        if hit == "면적" and "연면적" in q:
            continue
        if hit == "면적" and any(k in q for k in ("용적율", "용적률", "건폐율", "건폐률")):
            continue
        if hit == "층" and any(k in q for k in ("층수", "지상층")):
            # 더 긴 키로 이미 처리됐을 수 있음
            pass
        if any(hit in u or u in hit for u in used_spans):
            continue
        used_spans.append(hit)
        found.append(label_bits)
    return found


def _dataset_matches(label: str, label_bits: tuple[str, ...]) -> bool:
    lab = str(label or "")
    return any(bit in lab for bit in label_bits)


def filter_chart_series(
    spec: dict[str, Any], question: str
) -> tuple[dict[str, Any] | None, str]:
    """지표 일부만 남긴 차트 스펙과 안내 문구를 반환."""
    source = _source_datasets(spec)
    if len(source) < 1:
        return None, "차트에 표시할 지표가 없습니다."

    wanted = _requested_series_keys(question)
    if not wanted:
        names = ", ".join(
            str(d.get("label") or "?") for d in source
        )
        return None, (
            f"어떤 지표만 남길지 알려 주세요. 현재 차트 지표: {names}."
        )

    kept: list[dict[str, Any]] = []
    for ds in source:
        label = str(ds.get("label") or "")
        if any(_dataset_matches(label, bits) for bits in wanted):
            kept.append(dict(ds))

    if not kept:
        names = ", ".join(str(d.get("label") or "?") for d in source)
        return None, (
            "요청하신 지표가 현재 차트에 없습니다. "
            f"선택 가능한 지표: {names}."
        )

    out = dict(spec)
    out["all_datasets"] = source
    out["datasets"] = kept
    # 단일 시리즈면 단위 추정
    if len(kept) == 1:
        lab = str(kept[0].get("label") or "")
        if "(m)" in lab or "높이" in lab:
            out["unit"] = "m"
        elif "%" in lab or "용적" in lab or "건폐" in lab:
            out["unit"] = "%"
        elif "㎡" in lab or "면적" in lab:
            out["unit"] = "㎡"
        elif "층" in lab:
            out["unit"] = "층"
        elif "동" in lab or "수" in lab:
            out["unit"] = "동"

    labels = ", ".join(str(d.get("label") or "?") for d in kept)
    answer = f"요청하신 대로 {labels} 지표만으로 차트를 다시 그렸습니다."
    return out, answer


def infer_chart_rebuild_route(
    session_route: str | None, rows: list[dict[str, Any]]
) -> str | None:
    """차트 후속에서 스펙을 다시 만들 때 쓸 원본 route."""
    route = str(session_route or "")
    if route and not route.startswith("chart_"):
        return route
    if not rows:
        return None
    first = rows[0]
    if (
        len(rows) >= 2
        and (first.get("label") is not None or first.get("place") is not None)
        and first.get("cnt") is not None
        and any(k in first for k in ("avg_far", "avg_height", "avg_area", "avg_floors"))
    ):
        return "building_profile_compare"
    if len(rows) >= 2 and _named_dataset_chartable(first):
        return "named_dataset_attr"
    return None


def _named_dataset_chartable(row: dict[str, Any]) -> bool:
    has_name = any(k in row for k in ("ADM_NM", "adm_nm", "label", "name"))
    if not has_name:
        return False
    skip = {
        "ADM_NM",
        "adm_nm",
        "label",
        "name",
        "BASE_DATE",
        "ADM_CD",
        "sigungu_cd",
        "sgis_cd",
        "admdong_cd",
        "geometry",
    }
    for key, raw in row.items():
        if key in skip:
            continue
        try:
            float(raw)
            return True
        except (TypeError, ValueError):
            continue
    return False


def _chart_from_named_dataset_rows(
    rows: list[dict[str, Any]],
    *,
    question: str = "",
) -> dict[str, Any] | None:
    """ADM_NM + 수치 컬럼(n10/urban_pc 등) 목록을 막대 차트로."""
    if len(rows) < 2:
        return None
    first = rows[0]
    name_key = next(
        (k for k in ("ADM_NM", "adm_nm", "label", "name") if k in first),
        None,
    )
    if not name_key:
        return None
    skip = {
        name_key,
        "BASE_DATE",
        "ADM_CD",
        "sigungu_cd",
        "sgis_cd",
        "admdong_cd",
        "geometry",
        "ha",
        "sqkm",
        "area_m2",
    }
    metric_keys: list[str] = []
    for key, raw in first.items():
        if key in skip:
            continue
        try:
            float(raw) if raw is not None else None
        except (TypeError, ValueError):
            continue
        else:
            if raw is not None:
                metric_keys.append(str(key))
    if not metric_keys:
        return None
    qn = question or ""
    preferred = None
    ratio_keys = ("_per_m2", "_per_ha", "_per_sqkm", "_per_area")
    wants_ratio = any(k in qn for k in ("면적대비", "면적 대비", "면적당", "밀도"))

    def _first_ratio(keys: list[str]) -> str | None:
        for key in keys:
            if key.endswith(ratio_keys):
                return key
        return None

    if wants_ratio:
        preferred = _first_ratio(metric_keys)
    if preferred is None:
        for key in metric_keys:
            if key.lower() in qn.lower() or key in qn:
                preferred = key
                break
    if preferred is None:
        preferred = _first_ratio(metric_keys)
    if preferred is None:
        # 연령대·인구 계열 우선
        for key in metric_keys:
            if re.fullmatch(r"n\d{2}(?:_day|_night|_late)?", key, flags=re.I):
                preferred = key
                break
    if preferred is None:
        for key in ("urban_pc", "urban_area", "tot", "den_tot"):
            if key in metric_keys:
                preferred = key
                break
    if preferred is None:
        preferred = metric_keys[0]

    labels: list[str] = []
    values: list[float] = []
    for row in rows:
        name = row.get(name_key)
        raw = row.get(preferred)
        if name is None or raw is None:
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        labels.append(str(name).strip())
        values.append(val)
    if len(labels) < 2:
        return None
    cap = 40
    label_name = preferred
    unit = ""
    m_age = re.fullmatch(r"n(\d{2})", preferred, flags=re.I)
    m_per = re.fullmatch(r"n(\d{2})_per_(m2|ha|sqkm|area)", preferred, flags=re.I)
    if m_per:
        age = m_per.group(1)
        denom = m_per.group(2)
        unit = {
            "m2": "명/㎡",
            "ha": "명/ha",
            "sqkm": "명/㎢",
            "area": "명/면적",
        }.get(denom, "")
        label_name = f"{age}대 면적당 활동인구"
    elif m_age:
        label_name = f"{m_age.group(1)}대 활동인구"
    elif preferred == "urban_pc":
        label_name = "1인당 시가화용지"
    elif preferred == "urban_area":
        label_name = "시가화용지 면적"
    return {
        "type": "bar",
        "title": _title_from_question(question, label_name),
        "labels": labels[:cap],
        "datasets": [{"label": label_name, "data": values[:cap]}],
        "all_datasets": [{"label": label_name, "data": values[:cap]}],
        "unit": unit,
    }


def is_chart_capability_question(question: str) -> bool:
    """가능한 차트/그래프 종류를 묻는 후속 질문."""
    q = question.strip()
    if not q:
        return False
    if not any(k in q for k in ("차트", "그래프", "시각화")):
        return False
    return any(
        k in q
        for k in (
            "가능",
            "종류",
            "어떤",
            "무슨",
            "뭐가",
            "무엇이",
            "할 수",
            "지원",
            "바꿔",
            "변경",
            "전환",
            "옵션",
            "형태",
        )
    )


def chart_capability_answer(spec: dict[str, Any] | None = None) -> str:
    current = ""
    if spec and spec.get("type"):
        current = (
            f"지금 보고 계신 것은 {chart_type_label(str(spec['type']))} 차트입니다. "
        )
    return (
        f"{current}"
        "같은 데이터로 바꿔 볼 수 있는 그래프는 다음입니다.\n"
        "- 막대 차트 (예: 「막대 차트로 그려라」)\n"
        "- 도넛 차트 (예: 「도넛 차트로」)\n"
        "- 파이 차트 (예: 「파이 차트로」)\n"
        "- 선 차트 (예: 「선 차트로」)\n"
        "원하시는 종류를 말씀해 주시면 바로 다시 그려 드릴게요."
    )


def is_chart_accept_question(question: str) -> bool:
    q = question.strip()
    if not q:
        return False
    if any(k in q for k in _DECLINE):
        return False
    if any(k in q for k in _ACCEPT):
        return True
    compact = re.sub(r"[\s!.?~ㅎㅋ]+", "", q)
    if compact in _ACCEPT_SHORT or q in _ACCEPT_SHORT:
        return True
    if q in _ACCEPT_FULL or compact in {re.sub(r"\s+", "", x) for x in _ACCEPT_FULL}:
        return True
    return False


def is_chart_decline_question(question: str) -> bool:
    q = question.strip()
    if not q:
        return False
    if any(k in q for k in _ACCEPT):
        return False
    compact = re.sub(r"[\s!.?~]+", "", q)
    if compact in ("아니", "아니요", "아니오", "ㄴㄴ", "괜찮아요", "괜찮아"):
        return True
    return any(k in q for k in _DECLINE)


def offer_suffix() -> str:
    return _OFFER_SUFFIX


def build_chart_spec(
    *,
    route: str | None,
    rows: list[dict[str, Any]] | None,
    question: str = "",
) -> dict[str, Any] | None:
    """집계/비교 결과에서 Chart.js용 스펙을 만든다. 불가하면 None."""
    route = str(route or "")
    rows = list(rows or [])
    if not rows:
        return None

    if route == "usage_overview" or route == "semantic_plan_distribution":
        return _chart_from_named_counts(
            rows,
            name_keys=("usage", "legal_dong", "structure", "ground_floors"),
            title=_title_from_question(question, "주요 용도 구성"),
            chart_type="doughnut",
            dataset_label="건물 수",
            unit="동",
        )

    if route == "semantic_plan_aggregate":
        # bins / group_by 구간·그룹 집계 → bar/doughnut (스칼라 1행은 차트 생략)
        if len(rows) >= 2:
            chart = _chart_from_named_counts(
                rows,
                name_keys=(
                    "usage",
                    "legal_dong",
                    "structure",
                    "ground_floors",
                    "sigungu_name",
                    "gross_floor_area_m2",
                    "height_m",
                    "range",
                ),
                title=_title_from_question(question, "구간·그룹 집계"),
                chart_type="bar",
                dataset_label="건물 수",
                unit="동",
            )
            if chart:
                return chart
        return None

    if route in {"building_profile", "building_profile_compare"}:
        usage_chart = _chart_from_named_counts(
            rows,
            name_keys=("usage",),
            title=_title_from_question(question, "용도 구성"),
            chart_type="doughnut",
            dataset_label="건물 수",
            unit="동",
        )
        if usage_chart:
            return usage_chart
        if route == "building_profile":
            return _chart_from_named_counts(
                rows,
                name_keys=("structure",),
                title=_title_from_question(question, "구조 구성"),
                chart_type="bar",
                dataset_label="건물 수",
                unit="동",
            )
        return _chart_profile_compare(rows, question)

    if route.startswith("building_rank_compare_"):
        metric = route.replace("building_rank_compare_", "", 1)
        return _chart_rank_compare(rows, metric=metric, question=question)

    if route == "building_profile_compare":
        return _chart_profile_compare(rows, question)

    if route in {"d198_year_stats", "d198_value_bins"}:
        from txt2sql.d198_attrs import (
            format_value_bin_label,
            format_year_stats_label,
            parse_value_bin,
        )

        labels: list[str] = []
        values: list[float] = []
        vspec = parse_value_bin(question) if route == "d198_value_bins" else None
        for row in rows:
            period = row.get("year", row.get("period", row.get("decade")))
            n = row.get("n", row.get("cnt"))
            if period is None or n is None:
                continue
            try:
                val = float(n)
            except (TypeError, ValueError):
                continue
            if vspec is not None:
                labels.append(format_value_bin_label(row, vspec))
            else:
                labels.append(format_year_stats_label(row, question=question))
            values.append(val)
        if len(labels) < 2:
            return None
        cap = 40
        fallback = "구간별 건수" if route == "d198_value_bins" else "연도별 건립 수"
        return {
            "type": "bar",
            "title": _title_from_question(question, fallback),
            "labels": labels[:cap],
            "datasets": [{"label": "건축물 수(동)", "data": values[:cap]}],
            "all_datasets": [{"label": "건축물 수(동)", "data": values[:cap]}],
            "unit": "동",
        }

    if route.startswith("named_dataset"):
        return _chart_from_named_dataset_rows(rows, question=question)

    return None


def attach_chart_offer(
    result: dict[str, Any],
    *,
    question: str = "",
) -> dict[str, Any]:
    """가능하면 chart_offer/chart_spec을 붙이고 안내 문구를 덧붙인다."""
    if not result.get("ok"):
        return result
    if result.get("chart") or result.get("route") in {"chart_render", "chart_decline"}:
        return result

    spec = build_chart_spec(
        route=result.get("route"),
        rows=result.get("rows"),
        question=question or "",
    )
    if not spec:
        return result

    out = dict(result)
    out["chart_offer"] = True
    out["chart_spec"] = spec
    answer = str(out.get("answer") or "").rstrip()
    if "차트로 보시겠어요" not in answer:
        out["answer"] = answer + _OFFER_SUFFIX
    return out


def _title_from_question(question: str, fallback: str) -> str:
    q = question.strip()
    if not q:
        return fallback
    title = re.sub(r"[?？!！.。]+$", "", q)
    # 서술형 어미는 제목에서 제거
    title = re.sub(
        r"(을|를)?\s*(설명하라|설명해줘|설명해|알려줘|알려 줘|비교해줘|비교해)$",
        "",
        title,
    ).strip()
    if len(title) > 36 or not title:
        return fallback
    return title


def _chart_from_named_counts(
    rows: list[dict[str, Any]],
    *,
    name_keys: tuple[str, ...],
    title: str,
    chart_type: str,
    dataset_label: str,
    unit: str,
) -> dict[str, Any] | None:
    labels: list[str] = []
    values: list[float] = []
    for row in rows:
        name = None
        for k in name_keys:
            if k in row and row.get(k) is not None:
                name = str(row.get(k)).strip()
                break
        if not name or name in {"(미상)", "미상"}:
            # 집계 헤더 행(cnt/kinds) 등은 건너뜀
            if "n" not in row and "cnt" in row:
                continue
            if not name:
                continue
        n = row.get("n")
        if n is None:
            n = row.get("count", row.get("cnt"))
        if n is None:
            continue
        try:
            val = float(n)
        except (TypeError, ValueError):
            continue
        if val < 0:
            continue
        labels.append(name)
        values.append(val)

    if len(labels) < 2:
        return None
    datasets = [
        {
            "label": dataset_label,
            "data": values[:12],
        }
    ]
    return {
        "type": chart_type,
        "title": title,
        "labels": labels[:12],
        "datasets": datasets,
        "all_datasets": [dict(d) for d in datasets],
        "unit": unit,
    }


def _metric_series(
    rows: list[dict[str, Any]], key: str, n: int
) -> tuple[list[float], bool]:
    values: list[float] = []
    present = False
    for row in rows:
        raw = row.get(key)
        if raw is None:
            values.append(0.0)
            continue
        try:
            values.append(float(raw))
            present = True
        except (TypeError, ValueError):
            values.append(0.0)
    if len(values) < n:
        values.extend([0.0] * (n - len(values)))
    return values[:n], present and any(v > 0 for v in values[:n])


def _chart_profile_compare(
    rows: list[dict[str, Any]], question: str
) -> dict[str, Any] | None:
    labels: list[str] = []
    counts: list[float] = []
    kept_rows: list[dict[str, Any]] = []
    for row in rows:
        label = row.get("label") or row.get("place")
        cnt = row.get("cnt")
        if label is None or cnt is None:
            continue
        try:
            c = float(cnt)
        except (TypeError, ValueError):
            continue
        labels.append(str(label))
        counts.append(c)
        kept_rows.append(row)

    if len(labels) < 2:
        return None

    n = len(labels)
    heights, has_height = _metric_series(kept_rows, "avg_height", n)
    fars, has_far = _metric_series(kept_rows, "avg_far", n)
    areas, has_area = _metric_series(kept_rows, "avg_area", n)
    floors, has_floors = _metric_series(kept_rows, "avg_floors", n)

    all_datasets: list[dict[str, Any]] = [{"label": "건물 수(동)", "data": counts}]
    if has_area:
        all_datasets.append({"label": "평균 연면적(㎡)", "data": areas})
    if has_height:
        all_datasets.append({"label": "평균 높이(m)", "data": heights})
    if has_far:
        all_datasets.append({"label": "평균 용적율(%)", "data": fars})
    if has_floors:
        all_datasets.append({"label": "평균 지상층", "data": floors})

    far_focus = any(k in question for k in ("용적율", "용적률", "건폐율", "건폐률"))
    datasets: list[dict[str, Any]] = [all_datasets[0]]
    unit = "동"
    if has_far and (far_focus or not has_height):
        datasets.append({"label": "평균 용적율(%)", "data": fars})
        unit = "동·%"
    elif has_height:
        datasets.append({"label": "평균 높이(m)", "data": heights})
        unit = "동·m"
    elif has_far:
        datasets.append({"label": "평균 용적율(%)", "data": fars})
        unit = "동·%"

    return {
        "type": "bar",
        "title": _title_from_question(question, "지역 비교"),
        "labels": labels,
        "datasets": datasets,
        "all_datasets": [dict(d) for d in all_datasets],
        "unit": unit,
    }


def _chart_rank_compare(
    rows: list[dict[str, Any]],
    *,
    metric: str,
    question: str,
) -> dict[str, Any] | None:
    col_map = {
        "높이": ("A16", "m"),
        "건물면적": ("A12", "㎡"),
        "연면적": ("A14", "㎡"),
        "지상층": ("A26", "층"),
    }
    col, unit = col_map.get(metric, ("A16", ""))
    labels: list[str] = []
    values: list[float] = []
    for row in rows:
        place = _short_place(str(row.get("A4") or row.get("place") or ""))
        raw = row.get(col) if col in row else row.get("metric_value")
        if not place or raw is None:
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        labels.append(place)
        values.append(val)
    if len(labels) < 2:
        return None
    datasets = [{"label": f"{metric}({unit})" if unit else metric, "data": values}]
    return {
        "type": "bar",
        "title": _title_from_question(question, f"지역별 최고 {metric}"),
        "labels": labels,
        "datasets": datasets,
        "all_datasets": [dict(d) for d in datasets],
        "unit": unit,
    }


def _short_place(address: str) -> str:
    if not address:
        return "—"
    # "부산광역시 동래구 사직동" → "사직동" 또는 "동래구"
    parts = address.replace("부산광역시", "").strip().split()
    if not parts:
        return address
    return parts[-1]
