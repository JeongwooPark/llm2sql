"""SQL 구문·도메인 진단 + PostgreSQL EXPLAIN 검증."""

from __future__ import annotations

import re
from typing import Any

import psycopg

from txt2sql.db import assert_readonly_sql, ensure_limit
from txt2sql.domain import (
    d198_coverage_label,
    d198_gu_mentioned,
    d198_table_for_gu,
    looks_like_age_question,
)


def diagnose_sql(question: str, sql: str, *, row_count: int | None = None) -> str | None:
    """문제가 있으면 재생성용 피드백 문자열, 없으면 None."""
    reasons: list[str] = []
    q = question
    s = sql
    upper = sql.upper()

    # 한글 구/동 필터에 A3 사용
    if re.search(r'"A3"\s+LIKE\s+\'%[가-힣]', s):
        reasons.append('Use "A4" (법정동명) for Hangul gu/dong filters, not "A3".')

    # 부산 전역 구 질의인데 D198만 사용 (등록된 D198 구·용도별건물·건축년수가 아닐 때)
    uses_d198 = "AL_D198_" in upper
    uses_d010 = "AL_D010_" in upper
    d198_ok = (
        d198_gu_mentioned(q) is not None
        or "용도별건물" in q
        or "주요용도" in q
        or looks_like_age_question(q)
    )
    if not d198_ok:
        from txt2sql.domain import (
            extract_detail_usages,
            extract_usage,
            extract_usage_classes,
        )
        from txt2sql.dataset_grain import place_has_d198_coverage

        d198_ok = bool(
            extract_usage(q)
            or extract_usage_classes(q)
            or extract_detail_usages(q)
            or place_has_d198_coverage(q)
        )
    gu_in_q = bool(re.search(r"[가-힣]{1,6}구", q))
    busan_wide = any(
        k in q for k in ("부산시", "부산광역시", "부산 전체", "부산내", "부산 내")
    ) or q.strip().startswith("부산")
    if (
        (gu_in_q or busan_wide)
        and "건물" in q
        and uses_d198
        and not uses_d010
        and not d198_ok
    ):
        reasons.append(
            'For Busan-wide / gu-level building queries prefer "AL_D010_26_20250704", '
            "not district-only AL_D198 tables."
        )

    # 높이 순위인데 D198 A30만 사용 (부산 전역·구 질의)
    if (
        any(k in q for k in ("가장 높", "제일 높", "높이"))
        and uses_d198
        and not uses_d010
        and not d198_ok
    ):
        reasons.append(
            'Building height ranking for Busan should use "AL_D010_26_20250704"."A16", '
            'not district-only AL_D198 "A30".'
        )

    # text 일자 컬럼을 date와 직접 비교 (PostgreSQL text < date 오류)
    if re.search(
        r'"(A13|A22|A33|A34)"\s*[<>]=?\s*CURRENT_DATE',
        s,
        flags=re.I,
    ) and not re.search(
        r'"(A13|A22|A33|A34)"::\s*date',
        s,
        flags=re.I,
    ):
        reasons.append(
            'Approval dates are stored as text; compare with "A34"::date on AL_D198 '
            "(or cast explicitly). Never compare text < date."
        )
    if (
        looks_like_age_question(q)
        and "AL_D010" in upper
        and re.search(r'"A13"\s*[<>=]', s)
    ):
        reasons.append(
            f'Age/오래된 queries must use {d198_coverage_label()} AL_D198 "A34"::date, '
            'not AL_D010 "A13".'
        )

    # 건축년수인데 데이터기준일(A35) 사용 / 달력연도를 INTERVAL 경과년수로 오인
    if any(k in q for k in ("지어진", "건축년", "준공", "사용승인", "년 미만", "년 이상")):
        if re.search(r'"A35"', s) or re.search(r"\bA35\b", s):
            reasons.append(
                'Building age must use AL_D198 "A34"(사용승인일자) or "A33"(허가일자), '
                'never "A35"(데이터기준일자).'
            )
        if "AL_D010" in upper and ("A34" in s or "INTERVAL" in upper):
            reasons.append(
                "AL_D010 has no approval date; use registered AL_D198 tables "
                f"({d198_coverage_label()}) with A34 text date cast."
            )
        if re.search(r"(?:19|20)\d{2}\s*년", q) and re.search(
            r"INTERVAL\s+'?(?:19|20)\d{2}\s*years'",
            s,
            flags=re.I,
        ):
            reasons.append(
                "Calendar years like 2020년 이후 must compare A34/A33 to '2020-01-01', "
                "not INTERVAL '2020 years'."
            )
        if (
            any(k in q for k in ("최근", "오래된"))
            and any(k in q for k in ("지어진", "준공"))
            and d198_gu_mentioned(q) is not None
            and "AL_D010" in upper
        ):
            reasons.append(
                f'최근/오래된 건축은 {d198_coverage_label()} AL_D198 "A34"(사용승인일자)를 쓰고 '
                "AL_D010 \"A13\" MAX 집계를 쓰지 마세요."
            )

    # 상위/순위인데 ORDER BY 없음
    if any(
        k in q
        for k in ("상위", "순위", "가장 큰", "가장 많은", "가장 높", "제일 높", "큰 순")
    ) and "ORDER BY" not in upper:
        reasons.append("Ranking questions require ORDER BY ... DESC NULLS LAST and LIMIT.")

    # 미터 거리/버퍼인데 geography 캐스트 없음 또는 D198 단독
    coord_buffer = any(k in q for k in ("미터", "킬로미터", "km", "근처", "버퍼", "이내")) and (
        "129." in q or "좌표" in q or "점(" in q
    )
    place_buffer = (
        bool(re.search(r"\d+(?:\.\d+)?\s*(?:킬로미터|㎞|km|미터|m)", q))
        and (
            any(k in q for k in ("주변", "근처", "인근", "버퍼", "반경"))
            or bool(
                re.search(r"\d+(?:\.\d+)?\s*(?:킬로미터|㎞|km|미터|m)\s*(?:안|이내)", q)
            )
        )
        and bool(re.search(r"[가-힣0-9]{1,12}동", q))
        and "건물" in q
        and not coord_buffer
    )
    if coord_buffer:
        if "AL_D198" in upper and "AL_D010" not in upper:
            reasons.append(
                'Coordinate buffer queries must use "AL_D010_26_20250704", not AL_D198.'
            )
        if "ST_DWITHIN" in upper and "GEOGRAPHY" not in upper:
            reasons.append(
                "For meter distances on SRID 4326, cast geometry to geography in ST_DWithin."
            )
    if place_buffer:
        if "AL_D198" in upper and "AL_D010" not in upper:
            reasons.append(
                'Place buffer queries must use "AL_D010_26_20250704", not AL_D198.'
            )
        if "ST_DWITHIN" not in upper:
            reasons.append(
                "Place buffer queries require ST_DWithin against the dong boundary."
            )
        elif "GEOGRAPHY" not in upper:
            reasons.append(
                "For meter distances on SRID 4326, cast geometry to geography in ST_DWithin."
            )
        if "BND_ADM_DONG" not in upper:
            reasons.append(
                'Place buffer queries must use "BND_ADM_DONG_PG" (dong polygon buffer).'
            )

    # 동 ∩ 기초구역인데 속성만
    if (
        "기초구역" in q
        and any(k in q for k in ("교차", "겹치", "안에", "인접"))
        and "산업단지" not in q
        and (
            "건물" in q
            or bool(re.search(r"[가-힣0-9]{1,12}동", q))
            or "행정" in q
            or "센서스" in q
        )
    ):
        if "ST_INTERSECTS" not in upper and "ST_WITHIN" not in upper and "ST_DWITHIN" not in upper:
            reasons.append(
                "기초구역 공간 질의는 ST_Intersects/ST_Within/ST_DWithin을 써야 합니다."
            )
    if (
        any(k in q for k in ("안에", "내부", "안쪽", "경계 안"))
        and "건물" in q
        and not place_buffer
    ):
        if "ST_INTERSECTS" not in upper:
            reasons.append(
                'Dong containment requires ST_Intersects with "BND_ADM_DONG_PG".'
            )

    # 산업단지 도형 자체 질의는 D060 필수. 건물·공장∩산단은 JOIN이면 통과.
    if "산업단지" in q and "AL_D060" not in upper:
        name_list = any(k in q for k in ("이름", "명칭", "목록", "리스트")) and not any(
            k in q for k in ("건물", "건축물", "공장", "창고")
        )
        buildingish = any(
            k in q for k in ("건물", "공장", "창고", "채", "용도")
        ) and not name_list
        if name_list or not buildingish:
            reasons.append('Industrial-park questions must use "AL_D060_00_20250804".')
        elif "ST_INTERSECTS" not in upper:
            reasons.append(
                'Industrial-park building questions must ST_Intersects "AL_D060_00_20250804".'
            )

    # 등록된 D198 구의 주요용도명 → D198 A25
    if "주요용도" in q or (("용도" in q) and ("종류" in q or "몇 가지" in q)):
        from txt2sql.domain import D198_BY_GU, d198_gus_mentioned

        for gu in d198_gus_mentioned(q):
            table = D198_BY_GU.get(gu)
            if not table:
                continue
            if "AL_D010" in upper or '"A9"' in s or re.search(r"\bA9\b", s):
                reasons.append(
                    f'{gu} "주요용도명" kinds/count must use '
                    f'"{table}"."A25" with A25 IS NOT NULL, not AL_D010 "A9".'
                )

    # 미등록 구 용도/건물 COUNT에 D198 사용 (행정구명만; 교육연구시설 등 용도어 오탐 방지)
    from txt2sql.domain import extract_gu

    gu_name = extract_gu(q) or d198_gu_mentioned(q)
    if gu_name and (
        d198_table_for_gu(gu_name) is None
        and uses_d198
        and not uses_d010
        and not any(k in q for k in ("건축년", "준공", "사용승인", "지어진"))
    ):
        reasons.append(
            f'For {gu_name} building/usage counts use "AL_D010_26_20250704" '
            'with "A9" for 용도 (not district-only AL_D198).'
        )

    # 실행은 됐지만 의심스러운 빈 결과 (구+건물 속성)
    if row_count == 0 and gu_in_q and "건물" in q and uses_d198 and not uses_d010:
        reasons.append(
            "Query returned 0 rows with AL_D198 only; retry with AL_D010 and A4 LIKE gu filter."
        )
    if (
        row_count == 0
        and gu_name
        and d198_table_for_gu(gu_name) is None
        and uses_d198
    ):
        reasons.append(
            "Query returned 0 rows on AL_D198 for a gu without D198 coverage; "
            'rewrite with "AL_D010" and "A9" for usage filters.'
        )

    if not reasons:
        return None
    return "\n".join(f"- {r}" for r in reasons)


def check_sql_syntax(sql: str) -> str | None:
    """SQLGlot로 PostgreSQL 구문 검사. 문제 있으면 피드백, 없으면 None."""
    try:
        import sqlglot
        from sqlglot.errors import ParseError
    except ImportError:
        return None

    try:
        sqlglot.parse_one(sql, read="postgres")
    except ParseError as exc:
        return f"SQL parse error (sqlglot/postgres): {exc}"
    except Exception as exc:
        return f"SQL parse error: {type(exc).__name__}: {exc}"
    return None


def explain_sql(
    conn: psycopg.Connection,
    sql: str,
    *,
    default_limit: int = 100,
) -> str | None:
    """EXPLAIN으로 실행 가능성 검사. 문제 있으면 피드백, 없으면 None."""
    try:
        assert_readonly_sql(sql)
        limited = ensure_limit(sql, default_limit=default_limit)
        body = limited.rstrip().rstrip(";")
        with conn.cursor() as cur:
            cur.execute(f"EXPLAIN {body}")
            _ = cur.fetchall()
        return None
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return f"EXPLAIN failed: {type(exc).__name__}: {exc}"


def validate_sql_preexec(
    question: str,
    sql: str,
    *,
    conn: psycopg.Connection | None = None,
    default_limit: int = 100,
    use_explain: bool = True,
) -> str | None:
    """사전 검증 통합: 도메인 진단 → SQLGlot → EXPLAIN."""
    parts: list[str] = []
    domain = diagnose_sql(question, sql)
    if domain:
        parts.append(domain)
    syntax = check_sql_syntax(sql)
    if syntax:
        parts.append(f"- {syntax}")
    if use_explain and conn is not None:
        expl = explain_sql(conn, sql, default_limit=default_limit)
        if expl:
            parts.append(f"- {expl}")
    if not parts:
        return None
    return "\n".join(parts)
