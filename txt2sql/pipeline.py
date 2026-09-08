from __future__ import annotations

import re
from typing import Any

import psycopg

from txt2sql.answer import (
    emit_text_chunks,
    format_failure,
    format_success,
    format_success_template,
    build_distribution,
    build_share_distribution,
)
from txt2sql.chart_qa import (
    attach_chart_offer,
    build_chart_spec,
    chart_capability_answer,
    chart_type_label,
    filter_chart_series,
    infer_chart_rebuild_route,
    is_chart_accept_question,
    is_chart_capability_question,
    is_chart_decline_question,
    is_chart_metric_draw_question,
    is_chart_series_filter_question,
    parse_chart_type_request,
    with_chart_type,
)
from txt2sql.clarify_qa import (
    ClarifyAnswer,
    check_ambiguity,
    resolve_place_clarify_choice,
    unknown_term_guidance,
)
from txt2sql.config import Settings, database_url_for
from txt2sql.db import connect, execute_query
from txt2sql.domain import (
    extract_age_years,
    has_anaphora,
    looks_like_age_question,
    looks_like_building_name_lookup,
    looks_like_standalone_question,
)
from txt2sql.d198_attrs import (
    is_value_bin_followup,
    is_year_grain_followup,
    rows_as_bin_counts,
    session_has_year_stats,
    wrap_year_sql_as_bin,
    year_stats_grain,
)
from txt2sql.followup_qa import (
    answer_followup,
    is_followup_question,
    is_list_attr_followup,
    try_aggregate_top_value_followup,
    try_add_avg_to_group_followup,
    try_count_only_display_followup,
    try_group_prior_result_followup,
    try_subset_followup,
)
from txt2sql.guide_qa import _coverage_text, _is_coverage_question, try_guide
from txt2sql.intent_classifier import (
    IntentPrediction,
    classify_intent_hybrid,
    classify_intent_llm,
)
from txt2sql.intent_router import RoutedQuery, try_route
from txt2sql.meta_qa import answer_metadata_question, is_metadata_question
from txt2sql.named_dataset_qa import try_named_dataset_query
from txt2sql.place_area_qa import try_place_boundary_area_query
from txt2sql.profile_qa import (
    answer_profile_question,
    answer_usage_overview_question,
    is_profile_question,
    is_usage_overview_question,
)
from txt2sql.progress import ProgressCallback, ProgressTracker, TokenCallback
from txt2sql.query_contract import contract_is_executable_query
from txt2sql.query_understanding.bind import bind_catalog
from txt2sql.query_understanding.contract import extract_contract, merge_contract
from txt2sql.rag_sql import run_rag_sql
from txt2sql.rank_compare_qa import answer_rank_compare, is_rank_compare_question
from txt2sql.route_capability import (
    capability_for,
    detect_route_candidates,
    missing_requirements,
    pick_eligible_routed,
    route_allowed,
)
from txt2sql.route_dispatch import (
    DispatchMode,
    match_route,
    tables_for_intent,
    tables_from_sql,
)
from txt2sql.router_lexicon import map_unknown_to_router
from txt2sql.session import SessionContext
from txt2sql.semantic_plan import run_semantic_plan
from txt2sql.semantic_plan.followup import (
    apply_aggregate_followup,
    apply_count_display_followup,
    apply_followup_history,
    apply_result_anchor,
    is_semantic_plan_followup,
    merge_followup_plan,
)

_AGE_REPLY_HINTS = (
    "건축년수",
    "건축년",
    "준공일",
    "준공",
    "사용승인",
    "허가일",
    "경과 년",
    "경과년",
)
_UNKNOWN_TERM_NAME_HINTS = (
    "건물이름",
    "건물명",
    "건물 이름",
    "아파트명",
    "단지명",
    "이름",
    "명칭",
)
_META_ATTR_ONLY = re.compile(
    r"(지번|주소|이름|건물명|높이|용도|연면적|건물면적|층수|몇\s*층)"
    r"(은|는|이|가)?\s*\??"
)


def _ensure_result_table(
    result: dict[str, Any], question: str
) -> dict[str, Any]:
    """구간·비율 답변에 HTML 표용 table 페이로드를 붙인다."""
    if result.get("table"):
        return result
    route = str(result.get("route") or "")
    rows = list(result.get("rows") or [])
    table = None
    if route in {"d198_year_stats", "d198_value_bins"}:
        table = build_distribution(
            question, rows=rows, route=route, row_count=len(rows)
        )
    elif route == "legal_dong_admin_share":
        table = build_share_distribution(rows)
    elif route.startswith("semantic_plan_") and result.get("semantic_plan"):
        from txt2sql.semantic_plan.models import SemanticQueryPlan
        from txt2sql.semantic_plan.result_shape import build_semantic_result_table

        try:
            plan = SemanticQueryPlan.model_validate(result["semantic_plan"])
        except Exception:
            plan = None
        table = build_semantic_result_table(plan, rows, question=question)
    if table:
        result = dict(result)
        result["table"] = table
    return result


def _payload(
    *,
    ok: bool = True,
    answer: str | None = "",
    sql: str | None = None,
    tables: list[str] | None = None,
    rows: list | None = None,
    route: str | None = None,
    error: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    rows = list(rows or [])
    out: dict[str, Any] = {
        "ok": ok,
        "answer": answer,
        "sql": sql,
        "tables": list(tables or []),
        "rows": rows,
        "row_count": len(rows),
        "route": route,
        "error": error,
    }
    out.update(extra)
    return out


def _with_map(
    result: dict[str, Any],
    settings: Settings,
    question: str,
    session_id: str | None,
    progress: ProgressTracker | None = None,
    session: SessionContext | None = None,
) -> dict[str, Any]:
    """채팅 성공 후 지도 레이어를 붙인다. 실패해도 답변은 유지한다."""
    from txt2sql.map import attach_map

    return attach_map(
        result, settings, question, session_id, progress, session=session
    )


def _qa_ok(obj: Any, **extra: Any) -> dict[str, Any]:
    rows = getattr(obj, "rows", None)
    if rows is None:
        rows = getattr(obj, "options", [])
    return _payload(
        answer=obj.answer,
        sql=getattr(obj, "sql", None),
        tables=getattr(obj, "tables", None),
        rows=rows,
        route=obj.intent,
        **extra,
    )


def _llm_kw(settings: Settings, ollama_client: Any | None) -> dict[str, Any]:
    return {
        "model": settings.ollama_model,
        "host": settings.ollama_host if ollama_client is None else None,
        "client": ollama_client,
    }


def _resolve_unknown_terms(
    question: str,
    clarify: ClarifyAnswer,
    settings: Settings,
    progress: ProgressTracker,
    *,
    conn: psycopg.Connection,
    ollama_client: Any | None,
    deferred_route: Any | None,
) -> tuple[str, Any | None, ClarifyAnswer | None]:
    """미지 단어를 라우터 어휘에 맞추고, 못 맞추면 보완 질문을 남긴다."""
    progress.emit("route", "미지용어 → 라우터 유사어 탐색")
    syn = map_unknown_to_router(
        question,
        list(clarify.ambiguous_terms),
        **_llm_kw(settings, ollama_client),
    )
    if syn.mappings:
        mapped_txt = ", ".join(f"{src}→{dst}" for src, dst in syn.mappings)
        progress.emit("route", f"미지용어 대응 ({syn.source}): {mapped_txt}")
        question = syn.question
        routed_syn = try_route(question, conn=conn)
        if routed_syn is not None:
            deferred_route = routed_syn
    if syn.unmapped:
        remaining = ClarifyAnswer(
            intent="clarify_unknown_term",
            ambiguous_terms=list(syn.unmapped),
            options=[],
            answer=unknown_term_guidance(list(syn.unmapped), mapped=syn.mappings),
        )
        return question, deferred_route, remaining
    if syn.mappings:
        return question, deferred_route, None
    remaining = ClarifyAnswer(
        intent="clarify_unknown_term",
        ambiguous_terms=list(clarify.ambiguous_terms),
        options=[],
        answer=unknown_term_guidance(list(clarify.ambiguous_terms)),
    )
    return question, deferred_route, remaining


def _strip_count_tail(question: str) -> str:
    return re.sub(
        r"\s*(은|는)?\s*"
        r"(몇\s*채야|몇\s*개야|몇\s*채\??|몇\s*개\??|건수는\??|수는\??)\s*\??$",
        "",
        question.strip(),
    ).rstrip("?？")


def _expand_followup_question(
    question: str,
    session: SessionContext | None,
) -> str:
    """짧은 기준 보정·지시어 후속을 직전 질문과 합친다.

    새 장소·새 주제의 독립 질문은 그대로 둔다.
    """
    if session is None:
        return question
    q = question.strip()
    if is_semantic_plan_followup(q, session):
        return question
    if looks_like_standalone_question(q):
        return question
    if is_list_attr_followup(q, session):
        return question
    if year_stats_grain(q) is not None and session_has_year_stats(session):
        base = session.last_full_question or session.last_question
        if base and base.strip() != q:
            return f"{base} {q}"
    if is_value_bin_followup(q, session):
        base = session.last_full_question or session.last_question
        if base and base.strip() != q:
            return f"{base} {q}"
    base = session.last_full_question or session.last_question
    if not base:
        return question
    subsetish = has_anaphora(q) or any(
        h in q for h in ("그 중", "그중", "이 중", "이중에", "이 중에", "그중에")
    ) or (
        "제외" in q and any(k in q for k in ("건설일", "사용승인", "준공", "지어"))
    ) or (
        "최근" in q and re.search(r"\d+\s*개", q)
    ) or (
        re.search(r"\d+\s*개", q)
        and any(k in q for k in ("출력", "보여", "목록"))
    )
    if subsetish:
        follow = re.sub(
            r"^(그 중에|그 중|그중|이 중에|이중에|이 중|그중에)\s*",
            "",
            q,
        )
        return f"{_strip_count_tail(base)} 중에서 {follow}"
    if len(q) > 40:
        return question
    if not any(k in q for k in _AGE_REPLY_HINTS):
        return question
    last = base.strip()
    if looks_like_age_question(last) or (
        session.last_route
        and str(session.last_route).startswith("clarify_")
        and any(k in last for k in ("지어", "년", "단독", "주택", "건물"))
    ):
        if extract_age_years(last) is not None:
            return f"{last} (기준: {q})"
        if extract_age_years(q) is not None:
            return f"{last} {q}"
        return f"{last} (기준: {q})"
    return question


def ask(
    question: str,
    settings: Settings,
    *,
    on_progress: ProgressCallback | None = None,
    on_token: TokenCallback | None = None,
    session: SessionContext | None = None,
    session_id: str | None = None,
    include_map: bool = True,
) -> dict[str, Any]:
    """일회성 질의 (호환 API). 연결을 열고 닫는다.

    반복 호출 시에는 `Txt2SqlEngine` 사용을 권장한다.
    """
    from txt2sql.llm_usage import cleanup_llm_tracking

    try:
        return run_ask(
            question,
            settings,
            conn=None,
            ollama_client=None,
            on_progress=on_progress,
            on_token=on_token,
            session=session,
            session_id=session_id,
            include_map=include_map,
        )
    finally:
        cleanup_llm_tracking()


def _rewrite_session_question(
    question: str,
    session: SessionContext,
    progress: ProgressTracker,
    on_token: TokenCallback | None,
) -> tuple[str, dict[str, Any] | None]:
    rewritten, choice_err = resolve_place_clarify_choice(
        question,
        last_route=session.last_route,
        last_question=session.last_full_question or session.last_question,
        options=session.last_rows,
    )
    if choice_err:
        progress.emit("clarify", "선택 번호 범위 오류")
        emit_text_chunks(choice_err, on_token)
        return question, _payload(
            answer=choice_err,
            rows=session.last_rows,
            route="clarify_place",
            steps=progress.steps,
        )
    if rewritten:
        progress.emit("route", f"모호 지역 선택 → {rewritten}")
        question = rewritten

    if session.last_route == "clarify_unknown_term" and any(
        k in question.strip() for k in _UNKNOWN_TERM_NAME_HINTS
    ):
        base = session.last_full_question or session.last_question
        if base and not looks_like_standalone_question(question):
            rewritten = f"{base} 건물명"
            progress.emit("route", f"미지용어→건물명 조회: {rewritten}")
            question = rewritten

    if (
        session.last_route in {"industrial_count", "industrial_names"}
        and any(k in question for k in ("이름", "명칭", "목록", "리스트", "어떤"))
        and (
            has_anaphora(question)
            or "산업단지" in question
            or len(question.strip()) <= 18
        )
        and not looks_like_standalone_question(question)
    ):
        base = session.last_full_question or session.last_question
        if base and "산업단지" in base:
            rewritten = f"{base} 이름"
            progress.emit("route", f"산업단지 후속→이름: {rewritten}")
            question = rewritten

    return question, None


def _try_list_attr_followup(
    question: str,
    settings: Settings,
    progress: ProgressTracker,
    session: SessionContext | None,
    *,
    ollama_client: Any | None,
    on_token: TokenCallback | None,
) -> dict[str, Any] | None:
    """직전 목록을 유지한 채 사용승인일 등만 덧붙인다. 건수를 다시 자르지 않는다."""
    _ = ollama_client
    if session is not None and session.last_contract is not None:
        return None
    if session is None or not is_list_attr_followup(question, session):
        return None
    rows = list(session.last_rows)
    sql = session.last_sql or ""
    route = session.last_route or "d198_attr_list"
    fmt_q = session.last_full_question or session.last_question or question
    if not any(k in fmt_q for k in ("사용승인", "허가일", "건설일")):
        fmt_q = f"{fmt_q} 사용승인일"
    progress.emit("route", "직전 목록 유지·속성 추가")
    progress.emit("sql", "직전 SQL 재사용", sql=sql)
    answer = format_success_template(
        fmt_q,
        sql=sql,
        rows=rows,
        row_count=len(rows),
        route=route,
    )
    emit_text_chunks(answer, on_token)
    return _payload(
        answer=answer,
        sql=sql,
        tables=tables_from_sql(sql),
        rows=rows,
        route=route,
    )


def _try_year_grain_followup(
    question: str,
    settings: Settings,
    progress: ProgressTracker,
    session: SessionContext | None,
    *,
    ollama_client: Any | None,
    on_token: TokenCallback | None,
) -> dict[str, Any] | None:
    """직전 연도별 건립 건수를 N년 단위로 다시 묶는다."""
    _ = settings
    if session is not None and session.last_contract is not None:
        return None
    if session is None or not is_year_grain_followup(question, session):
        return None
    grain = year_stats_grain(question) or 10
    src_rows = list(session.last_rows or [])
    rows = rows_as_bin_counts(src_rows, grain)
    sql = wrap_year_sql_as_bin(session.last_sql or "", grain)
    if not rows or not sql:
        return None
    fmt_q = session.last_full_question or session.last_question or question
    if grain == 10:
        if "10년" not in fmt_q and "연대" not in fmt_q:
            fmt_q = f"{fmt_q} 10년 단위"
    elif f"{grain}년" not in fmt_q:
        fmt_q = f"{fmt_q} {grain}년 단위"
    progress.emit("route", f"직전 연도별 통계를 {grain}년 단위로 재집계")
    progress.emit("sql", f"연도 집계를 {grain}년 단위로 변환", sql=sql)
    answer = format_success(
        fmt_q,
        sql=sql,
        rows=rows,
        row_count=len(rows),
        route="d198_year_stats",
        on_token=on_token,
        **_llm_kw(settings, ollama_client),
    )
    extra: dict[str, Any] = {}
    table = build_distribution(
        fmt_q, rows=rows, route="d198_year_stats", row_count=len(rows)
    )
    if table:
        extra["table"] = table
    return _payload(
        answer=answer,
        sql=sql,
        tables=tables_from_sql(session.last_sql or sql),
        rows=rows,
        route="d198_year_stats",
        **extra,
    )


def _chart_reply(
    *,
    answer: str,
    route: str,
    progress: ProgressTracker,
    on_token: TokenCallback | None,
    route_msg: str,
    session: SessionContext | None = None,
    question: str | None = None,
    chart: dict[str, Any] | None = None,
) -> dict[str, Any]:
    progress.emit("route", route_msg)
    emit_text_chunks(answer, on_token)
    extra: dict[str, Any] = {"steps": progress.steps}
    if chart:
        extra["chart"] = chart
        extra["chart_spec"] = chart
    result = _payload(answer=answer, route=route, **extra)
    if session is not None and question is not None:
        session.update_from_result(question, result)
    return result


def _filter_chart_from_session(
    question: str,
    session: SessionContext,
    base_chart: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    chart, answer = filter_chart_series(base_chart, question)
    if chart is not None:
        return chart, answer
    rows = list(session.last_rows or [])
    if not rows:
        return None, answer
    rebuilt = build_chart_spec(
        route=infer_chart_rebuild_route(session.last_route, rows),
        rows=rows,
        question=question,
    )
    if not rebuilt:
        return None, answer
    return filter_chart_series(rebuilt, question)


def _try_chart_turn(
    question: str,
    session: SessionContext | None,
    progress: ProgressTracker,
    on_token: TokenCallback | None,
) -> dict[str, Any] | None:
    from txt2sql.query_understanding.contract import extract_contract
    from txt2sql.followup_qa import is_count_only_display_followup

    if is_count_only_display_followup(question):
        return None
    if extract_contract(question).wants_count:
        return None
    metric_draw = is_chart_metric_draw_question(question)
    has_spec = session is not None and (session.pending_chart or session.last_chart)
    can_rebuild = (
        session is not None
        and bool(session.last_rows)
        and infer_chart_rebuild_route(session.last_route, list(session.last_rows))
        is not None
    )
    if session is not None and (has_spec or (metric_draw and can_rebuild)):
        base_chart = session.pending_chart or session.last_chart
        if base_chart is None and can_rebuild:
            base_chart = build_chart_spec(
                route=infer_chart_rebuild_route(
                    session.last_route, list(session.last_rows)
                ),
                rows=list(session.last_rows),
                question=question,
            )
        if base_chart is None:
            return None
        if is_chart_capability_question(question):
            return _chart_reply(
                answer=chart_capability_answer(base_chart),
                route="chart_help",
                progress=progress,
                on_token=on_token,
                route_msg="차트 가능 종류 안내",
                session=session,
                question=question,
            )
        new_type = parse_chart_type_request(question)
        if new_type and not metric_draw and not is_chart_series_filter_question(question):
            chart = with_chart_type(base_chart, new_type)
            return _chart_reply(
                answer=f"같은 내용을 {chart_type_label(new_type)} 차트로 다시 정리했습니다.",
                route="chart_render",
                progress=progress,
                on_token=on_token,
                route_msg=f"차트 종류 변경 → {new_type}",
                session=session,
                question=question,
                chart=chart,
            )
        if is_chart_series_filter_question(question) or metric_draw:
            progress.emit("route", "차트 지표 필터")
            chart, answer = _filter_chart_from_session(question, session, base_chart)
            emit_text_chunks(answer, on_token)
            extra: dict[str, Any] = {"steps": progress.steps}
            if chart:
                extra["chart"] = chart
                extra["chart_spec"] = chart
            result = _payload(
                answer=answer,
                route="chart_render" if chart else "chart_help",
                **extra,
            )
            session.update_from_result(question, result)
            return result
        if is_chart_accept_question(question):
            chart = None
            if session.pending_chart:
                chart = dict(session.pending_chart)
            elif base_chart is not None:
                chart = dict(base_chart)
            if chart is not None:
                return _chart_reply(
                    answer="요청하신 내용을 차트로 정리했습니다.",
                    route="chart_render",
                    progress=progress,
                    on_token=on_token,
                    route_msg="차트 시각화 요청",
                    session=session,
                    question=question,
                    chart=chart,
                )
        if session.pending_chart and is_chart_decline_question(question):
            return _chart_reply(
                answer="알겠습니다. 텍스트 답변만 유지할게요. 다른 질문이 있으면 말씀해 주세요.",
                route="chart_decline",
                progress=progress,
                on_token=on_token,
                route_msg="차트 시각화 거절",
                session=session,
                question=question,
            )

    # pending 없이도 last_rows로 재구성 가능한 차트 수락
    if (
        session is not None
        and is_chart_accept_question(question)
        and bool(session.last_rows)
    ):
        rebuild_route = infer_chart_rebuild_route(
            session.last_route, list(session.last_rows)
        )
        if rebuild_route is not None:
            chart = build_chart_spec(
                route=rebuild_route,
                rows=list(session.last_rows),
                question=session.last_full_question or session.last_question or question,
            )
            if chart is not None:
                return _chart_reply(
                    answer="요청하신 내용을 차트로 정리했습니다.",
                    route="chart_render",
                    progress=progress,
                    on_token=on_token,
                    route_msg="차트 시각화 요청(재구성)",
                    session=session,
                    question=question,
                    chart=chart,
                )

    if is_chart_capability_question(question):
        return _chart_reply(
            answer=chart_capability_answer(None),
            route="chart_help",
            progress=progress,
            on_token=on_token,
            route_msg="차트 가능 종류 안내",
        )
    if is_chart_series_filter_question(question):
        return _chart_reply(
            answer=(
                "직전 차트가 없어 지표만 골라 다시 그릴 수 없습니다. "
                "먼저 비교·집계 답변에서 차트를 보신 뒤 "
                "「높이만으로 차트를 그려라」처럼 요청해 주세요."
            ),
            route="chart_help",
            progress=progress,
            on_token=on_token,
            route_msg="차트 지표 필터(맥락 없음)",
        )
    return None


def _guide_result(
    guide: Any,
    progress: ProgressTracker,
    on_token: TokenCallback | None,
) -> dict[str, Any]:
    progress.emit("route", f"안내 응답 ({guide.intent})")
    progress.emit("answer", "역할·제한·범위 안내 완료")
    emit_text_chunks(guide.answer, on_token)
    return _payload(answer=guide.answer, route=guide.intent, steps=progress.steps)


def _try_guide_turn(
    question: str,
    preferred: IntentPrediction | None,
    progress: ProgressTracker,
    on_token: TokenCallback | None,
) -> tuple[IntentPrediction | None, dict[str, Any] | None]:
    if preferred and preferred.intent in {"guide", "coverage", "out_of_scope"}:
        guide = try_guide(question)
        if (
            guide is None
            and preferred.intent == "coverage"
            and _is_coverage_question(question)
        ):
            progress.emit("route", "자료 범위 안내")
            answer = _coverage_text()
            emit_text_chunks(answer, on_token)
            return preferred, _payload(
                answer=answer, route="guide_coverage", steps=progress.steps
            )
        if guide is not None:
            return preferred, _guide_result(guide, progress, on_token)
        if preferred.intent == "coverage":
            preferred = IntentPrediction(
                "meta",
                preferred.confidence,
                "coverage 오분류→meta 재시도",
                "hybrid",
            )
            progress.emit("route", "coverage 오분류 → meta로 재해석")

    if preferred is None or preferred.intent not in {
        "guide",
        "coverage",
        "out_of_scope",
    }:
        guide = try_guide(question)
        if guide is not None:
            return preferred, _guide_result(guide, progress, on_token)
    return preferred, None


def _classify_intent(
    question: str,
    settings: Settings,
    progress: ProgressTracker,
    ollama_client: Any | None,
) -> IntentPrediction | None:
    if settings.intent_mode not in {"hybrid", "llm"}:
        return None
    progress.emit("route", f"의도 분류 ({settings.intent_mode})")
    llm = _llm_kw(settings, ollama_client)
    try:
        if settings.intent_mode == "hybrid":
            preferred = classify_intent_hybrid(
                question,
                threshold=settings.intent_confidence_threshold,
                **llm,
            )
        else:
            preferred = classify_intent_llm(question, **llm)
        progress.emit(
            "route",
            (
                f"의도={preferred.intent} "
                f"conf={preferred.confidence:.2f} ({preferred.source})"
            ),
        )
        return preferred
    except Exception as exc:
        progress.emit("route", f"의도 분류 실패 → 규칙: {type(exc).__name__}")
        return None


def run_ask(
    question: str,
    settings: Settings,
    *,
    conn: psycopg.Connection | None,
    ollama_client: Any | None,
    on_progress: ProgressCallback | None = None,
    on_token: TokenCallback | None = None,
    session: SessionContext | None = None,
    session_id: str | None = None,
    include_map: bool = True,
) -> dict[str, Any]:
    """핵심 파이프라인. conn/ollama_client가 있으면 재사용."""
    from txt2sql.llm_usage import activate_llm_tracking, deactivate_llm_tracking

    progress = ProgressTracker(on_step=on_progress)
    activate_llm_tracking(progress.record_llm)
    progress.emit("start", f"질문 수신: {question.strip()}")

    def finish(payload: dict[str, Any], q: str | None = None) -> dict[str, Any]:
        from txt2sql.evaluation.execution_trace import attach_execution_trace

        payload = dict(payload)
        payload = attach_execution_trace(
            payload,
            question=q or question,
            contract=contract,
            plan_bundle=plan_bundle,
        )
        payload["steps"] = progress.steps
        payload["stage_latency_ms"] = progress.stage_latency_ms()
        payload["llm_used"] = bool(progress.llm_calls)
        payload["llm_calls"] = list(progress.llm_calls)
        payload["selected_route"] = payload.get("route")
        if not include_map:
            return payload
        return _with_map(
            payload, settings, q or question, session_id, progress, session
        )

    if session is not None:
        question, early = _rewrite_session_question(
            question, session, progress, on_token
        )
        if early is not None:
            return finish(early)

    binding = bind_catalog(question)
    contract = extract_contract(question, binding=binding)
    if (
        session is not None
        and session.last_contract is not None
        and is_followup_question(question, session)
    ):
        from txt2sql.query_understanding.contract import QueryContract

        try:
            prev = QueryContract.model_validate(session.last_contract)
            contract = merge_contract(prev, contract, binding)
        except Exception:
            pass
    progress.emit(
        "understand",
        "Query Contract 생성",
        operation=contract.operation,
        complexity=contract.complexity,
    )

    # Build plan early so READY temporal/group/aggregate can run before meta/preferred steal.
    plan_bundle = None
    early_v2_result: dict[str, Any] | None = None
    try:
        from txt2sql.planner.executor_adapter import build_execution_plan

        plan_bundle = build_execution_plan(question, contract=contract)
        progress.emit(
            "plan",
            f"QueryIR/Plan: task={plan_bundle.query_ir.task} "
            f"logical={plan_bundle.logical.status} "
            f"physical={plan_bundle.physical.strategy}",
            query_ir_task=plan_bundle.query_ir.task,
            logical_status=plan_bundle.logical.status,
            physical_strategy=plan_bundle.physical.strategy,
            reason_codes=list(plan_bundle.logical.reason_codes),
        )
    except Exception as plan_exc:  # noqa: BLE001
        progress.emit("plan", f"plan build skipped: {type(plan_exc).__name__}")

    if plan_bundle is not None and conn is not None:
        finished = _try_priority_count_route(
            question,
            settings,
            progress,
            conn=conn,
            ollama_client=ollama_client,
            on_token=on_token,
            contract=contract,
        )
        if finished is not None:
            return finish(finished)

        try:
            from txt2sql.planner.semantic_executor import (
                should_try_semantic_v2,
                try_execute_semantic_v2,
            )

            if should_try_semantic_v2(plan_bundle):
                progress.emit(
                    "route",
                    "semantic-v2 executor 조기 시도",
                    physical_strategy=plan_bundle.physical.strategy,
                )
                early_v2_result = try_execute_semantic_v2(
                    question,
                    plan_bundle,
                    conn=conn,
                    default_limit=settings.default_limit,
                )
                if early_v2_result is not None and early_v2_result.get("ok"):
                    progress.emit(
                        "route",
                        "semantic-v2 적중",
                        execution_source="semantic_v2",
                        compiler_source="deterministic_compiler_v2",
                    )
                    llm = _llm_kw(settings, ollama_client)
                    rows = list(early_v2_result.get("rows") or [])
                    sql = early_v2_result.get("sql") or ""
                    answer = format_success(
                        question,
                        sql=sql,
                        rows=rows,
                        row_count=len(rows),
                        route="semantic_v2",
                        on_token=on_token,
                        **llm,
                    )
                    return finish(
                        _payload(
                            answer=answer,
                            sql=sql,
                            tables=early_v2_result.get("tables"),
                            rows=rows,
                            route="semantic_v2",
                            query_ir_task=early_v2_result.get("query_ir_task"),
                            logical_status=early_v2_result.get("logical_status"),
                            physical_strategy=early_v2_result.get("physical_strategy"),
                            execution_source="semantic_v2",
                            compiler_source="deterministic_compiler_v2",
                            fallback_source=None,
                            v2_failure_code=None,
                        )
                    )
                if early_v2_result is not None and not early_v2_result.get("ok"):
                    progress.emit(
                        "route",
                        "semantic-v2 typed failure → 기존 경로",
                        v2_failure_code=early_v2_result.get("v2_failure_code"),
                        fallback_source=early_v2_result.get("fallback_source"),
                    )
                else:
                    progress.emit("route", "semantic-v2 미지원/실패 → 기존 경로")
        except Exception as v2_exc:  # noqa: BLE001
            progress.emit("route", f"semantic-v2 early skip: {type(v2_exc).__name__}")

    # Interaction early: defer list_attr/chart steal when READY+v2 candidate
    # (non-followup). Real followups keep interaction handlers.
    from txt2sql.planner.semantic_executor import should_try_semantic_v2 as _should_v2

    defer_interaction = (
        plan_bundle is not None
        and _should_v2(plan_bundle)
        and not (session is not None and is_followup_question(question, session))
    )

    if not defer_interaction:
        listed = _try_list_attr_followup(
            question,
            settings,
            progress,
            session,
            ollama_client=ollama_client,
            on_token=on_token,
        )
        if listed is not None:
            listed["steps"] = progress.steps
            if session is not None and listed.get("ok"):
                keep_full = session.last_full_question
                session.update_from_result(question, listed)
                if keep_full:
                    session.last_full_question = keep_full
            return finish(listed)

        grained = _try_year_grain_followup(
            question,
            settings,
            progress,
            session,
            ollama_client=ollama_client,
            on_token=on_token,
        )
        if grained is not None:
            grained["steps"] = progress.steps
            if session is not None and grained.get("ok"):
                keep_full = session.last_full_question
                session.update_from_result(question, grained)
                if keep_full:
                    session.last_full_question = keep_full
            return finish(attach_chart_offer(grained, question=question))

        chart = _try_chart_turn(question, session, progress, on_token)
        if chart is not None:
            return finish(chart)
    else:
        progress.emit("route", "READY+v2 후보 → interaction early 보류")

    # 안내·범위 외는 의도분류 LLM보다 먼저 (지연·오분류 방지)
    guide_early = try_guide(question)
    if guide_early is not None:
        return finish(_guide_result(guide_early, progress, on_token))

    preferred = None
    followup_now = session is not None and is_followup_question(question, session)
    if not followup_now:
        preferred = _classify_intent(question, settings, progress, ollama_client)
    preferred, guide = _try_guide_turn(question, preferred, progress, on_token)
    if guide is not None:
        return finish(guide)

    effective = _expand_followup_question(question, session)
    if effective != question.strip():
        progress.emit("route", f"후속 기준 병합: {effective}")
        contract = extract_contract(effective, binding=bind_catalog(effective))
    try:
        if conn is None:
            with connect(database_url_for(settings, "query"), read_only=True) as owned:
                result = _ask_inner(
                    effective,
                    settings,
                    progress,
                    session,
                    conn=owned,
                    ollama_client=ollama_client,
                    on_token=on_token,
                    preferred_intent=preferred,
                    contract=contract,
                    plan_bundle=plan_bundle,
                    early_v2_result=early_v2_result,
                )
        else:
            result = _ask_inner(
                effective,
                settings,
                progress,
                session,
                conn=conn,
                ollama_client=ollama_client,
                on_token=on_token,
                preferred_intent=preferred,
                contract=contract,
                plan_bundle=plan_bundle,
                early_v2_result=early_v2_result,
            )
    except Exception as exc:
        progress.emit("error", f"처리 중 예외: {type(exc).__name__}")
        answer = format_failure(effective, error=exc, sql=None)
        emit_text_chunks(answer, on_token)
        result = _payload(
            ok=False,
            answer=answer,
            error=f"{type(exc).__name__}: {exc}",
        )
    result["steps"] = progress.steps
    result = _ensure_result_table(result, effective)
    before_offer = str(result.get("answer") or "")
    result = attach_chart_offer(result, question=effective)
    if (
        result.get("chart_offer")
        and on_token is not None
        and str(result.get("answer") or "").startswith(before_offer)
        and len(str(result.get("answer") or "")) > len(before_offer)
    ):
        emit_text_chunks(str(result["answer"])[len(before_offer) :], on_token)
    if session is not None and result.get("ok"):
        if looks_like_standalone_question(question) and not str(
            result.get("route") or ""
        ).startswith(("followup_", "d198_year_stats", "d198_value_bins")):
            session.clear_focus()
        session.update_from_result(effective, result)
        try:
            session.last_contract = contract.model_dump()
        except Exception:
            session.last_contract = None
    return finish(result, effective)


def _emit_contract_routing(
    progress: ProgressTracker,
    contract,
    candidates: list[str],
    *,
    missing: list[str] | None = None,
    decision: str | None = None,
) -> None:
    places = ",".join(str(item.value or item.text) for item in contract.places[:3]) or "-"
    groups = ",".join(contract.group_fields) or "-"
    aggs = ",".join(item.function for item in contract.aggregation_requests) or "-"
    progress.emit(
        "understand",
        f"[contract] entity={places}/group={groups}/aggs={aggs}/limit={contract.limit}",
    )
    progress.emit("route", f"[route] candidates={list(candidates)}")
    if missing is not None:
        miss_txt = ",".join(missing) if missing else "-"
        progress.emit("route", f"[capability] missing={miss_txt}")
    if decision is not None:
        progress.emit("route", f"[decision] {decision}")


def _capability_missing_for_candidates(candidates: list[str], contract) -> list[str]:
    if not contract.is_sufficient():
        return ["incomplete_contract"]
    last: list[str] = ["no_eligible_route"]
    for intent in candidates:
        miss = missing_requirements(capability_for(intent), contract)
        if miss:
            last = miss
        else:
            return []
    return last


def _maybe_finish_routed(
    question: str,
    settings: Settings,
    progress: ProgressTracker,
    *,
    conn: psycopg.Connection,
    ollama_client: Any | None,
    on_token: TokenCallback | None,
    routed: Any,
    route_label: str,
    contract,
) -> dict[str, Any] | None:
    result = _finish_routed_query(
        question,
        settings,
        progress,
        conn=conn,
        ollama_client=ollama_client,
        on_token=on_token,
        routed=routed,
        route_label=route_label,
        contract=contract,
    )
    if result.get("shape_failed"):
        # Router SQL이 계약 kind와 안 맞으면 SQP로 넘긴다.
        return None
    return result


_PREFERRED_ROUTE = {
    "rank_compare": "building_rank_compare",
    "usage_overview": "building_usage_count",
    "profile": "building_profile",
}


def _blocks_meta_profile_force(question: str) -> bool:
    """profile force가 Contract를 전부 지원하지 않으면 선점하지 않는다."""
    return not route_allowed("building_profile", extract_contract(question))


def _contract_looks_like_data_query(contract) -> bool:
    return bool(
        contract.places
        or contract.aggregation_requests
        or contract.wants_count
        or contract.group_fields
        or contract.output_fields
        or contract.order_requests
        or contract.ratios
        or contract.percentile_requests
        or contract.derived_metrics
        or contract.numbers
        or contract.ranges
    )


def _try_preferred_intent(
    question: str,
    settings: Settings,
    progress: ProgressTracker,
    *,
    conn: psycopg.Connection,
    ollama_client: Any | None,
    on_token: TokenCallback | None,
    preferred: IntentPrediction,
    contract,
) -> dict[str, Any] | None:
    """분류된 의도로 핸들러를 우선 시도. capability 부족이면 None → SQP."""
    intent = preferred.intent
    if intent in {"guide", "coverage", "out_of_scope", "sql"}:
        return None
    from txt2sql.domain import wants_map_display

    if wants_map_display(question) and intent == "profile":
        return None
    route_name = _PREFERRED_ROUTE.get(intent)
    if route_name is not None and not route_allowed(route_name, contract):
        progress.emit("route", f"선호 의도 capability 부족: {intent}")
        return None
    if intent == "meta" and _contract_looks_like_data_query(contract):
        return None
    llm = _llm_kw(settings, ollama_client)

    if intent == "rank_compare":
        progress.emit("route", "선호 의도: 최고 건물 비교")
        ranked = answer_rank_compare(conn, question, on_token=on_token, **llm)
        return None if ranked is None else _qa_ok(ranked)

    if intent == "usage_overview":
        from txt2sql.query_understanding.contract import extract_contract

        if extract_contract(question).wants_count:
            return None
        progress.emit("route", "선호 의도: 용도 구성 설명")
        usage_ov = answer_usage_overview_question(
            conn, question, on_token=on_token, force=True, **llm
        )
        return None if usage_ov is None else _qa_ok(usage_ov)

    if intent == "profile":
        progress.emit("route", "선호 의도: 건물 특징/비교")
        profile = answer_profile_question(
            conn, question, on_token=on_token, force=True, **llm
        )
        return None if profile is None else _qa_ok(profile)

    if intent == "meta":
        if looks_like_building_name_lookup(question):
            return None
        if _META_ATTR_ONLY.fullmatch(question.strip()):
            return None
        progress.emit("route", "선호 의도: 메타데이터")
        meta = answer_metadata_question(conn, question, force=True)
        if meta is None:
            return None
        emit_text_chunks(meta.answer, on_token)
        return _qa_ok(meta)

    if intent == "clarify":
        progress.emit("route", "선호 의도: 모호성 확인")
        clarify = check_ambiguity(conn, question)
        if clarify is None:
            return None
        if clarify.intent == "clarify_unknown_term":
            return None
        emit_text_chunks(clarify.answer, on_token)
        return _qa_ok(clarify, ambiguous_terms=clarify.ambiguous_terms)

    return None


def _finish_routed_query(
    question: str,
    settings: Settings,
    progress: ProgressTracker,
    *,
    conn: psycopg.Connection,
    ollama_client: Any | None,
    on_token: TokenCallback | None,
    routed: Any,
    route_label: str,
    contract=None,
) -> dict[str, Any]:
    """규칙 라우터 SQL을 실행하고 한국어 답변까지 만든다."""
    tables = tables_from_sql(routed.sql) or tables_for_intent(routed.intent)
    progress.emit("route", route_label)
    progress.emit("sql", "라우터 SQL 확정", sql=routed.sql)
    progress.emit("execute", "DB 조회 실행")
    try:
        rows = execute_query(
            conn,
            routed.sql,
            default_limit=settings.default_limit,
            statement_timeout_ms=settings.db_statement_timeout_ms,
        )
    except Exception as exc:
        progress.emit("error", f"실행 실패: {type(exc).__name__}")
        answer = format_failure(question, error=exc, sql=routed.sql)
        emit_text_chunks(answer, on_token)
        return _payload(
            ok=False,
            answer=answer,
            sql=routed.sql,
            tables=tables,
            route=routed.intent,
            error=f"{type(exc).__name__}: {exc}",
        )
    from txt2sql.semantic_plan.result_shape import verify_result

    # Router는 Plan이 없다. 건수 계약에 목록 행이 오면 SQP로 넘긴다.
    shape = verify_result(contract, list(rows or []))
    if not shape.ok:
        progress.emit(
            "route",
            f"결과 shape 불일치: {','.join(shape.reasons)} → Semantic Plan",
        )
        return _payload(
            ok=False,
            shape_failed=True,
            answer="",
            sql=routed.sql,
            tables=tables,
            rows=rows,
            route=routed.intent,
            error="shape_mismatch:" + ",".join(shape.reasons),
        )
    progress.emit("result", f"조회 완료 ({len(rows)}행)", row_count=len(rows))
    progress.emit("answer", "한국어 답변 생성")
    answer = format_success(
        question,
        sql=routed.sql,
        rows=rows,
        row_count=len(rows),
        route=routed.intent,
        on_token=on_token,
        **_llm_kw(settings, ollama_client),
    )
    extra: dict[str, Any] = {}
    if routed.intent in {"d198_year_stats", "d198_value_bins"}:
        table = build_distribution(
            question, rows=rows, route=routed.intent, row_count=len(rows)
        )
        if table:
            extra["table"] = table
    elif routed.intent == "legal_dong_admin_share":
        table = build_share_distribution(rows)
        if table:
            extra["table"] = table
    return _payload(
        answer=answer,
        sql=routed.sql,
        tables=tables,
        rows=rows,
        route=routed.intent,
        **extra,
    )


def _finish_semantic_query(
    question: str,
    settings: Settings,
    progress: ProgressTracker,
    *,
    semantic: dict[str, Any],
    on_token: TokenCallback | None,
) -> dict[str, Any]:
    """SQP SQL 실행 결과를 기존 payload 형태로 맞춘다."""
    from txt2sql.semantic_plan.models import SemanticQueryPlan
    from txt2sql.semantic_plan.result_shape import build_semantic_result_table

    rows = list(semantic.get("rows") or [])
    sql = semantic.get("sql")
    route = str(semantic.get("route") or "semantic_plan_list")
    answer = str(semantic.get("answer") or "")
    progress.emit("result", f"조회 완료 ({len(rows)}행)", row_count=len(rows))
    progress.emit("answer", "Semantic Plan 템플릿 답변")
    emit_text_chunks(answer, on_token)
    extra: dict[str, Any] = {
        "semantic_plan": semantic.get("semantic_plan"),
        "plan_quality": semantic.get("plan_quality"),
    }
    if not settings.semantic_plan_debug:
        extra.pop("plan_quality", None)
    plan_payload = semantic.get("semantic_plan")
    plan_obj = None
    if isinstance(plan_payload, dict):
        try:
            plan_obj = SemanticQueryPlan.model_validate(plan_payload)
        except Exception:
            plan_obj = None
    elif plan_payload is not None and hasattr(plan_payload, "query_kind"):
        plan_obj = plan_payload  # type: ignore[assignment]
    table = build_semantic_result_table(plan_obj, rows, question=question)
    if table:
        extra["table"] = table
    return _payload(
        answer=answer,
        sql=sql,
        tables=semantic.get("tables"),
        rows=rows,
        route=route,
        **extra,
    )


def _run_semantic(
    question: str,
    settings: Settings,
    *,
    conn: psycopg.Connection,
    ollama_client: Any | None,
    session: SessionContext | None,
    progress: ProgressTracker,
    contract,
    plan=None,
    force_llm: bool = False,
) -> dict[str, Any]:
    """SQP 한 번 실행. hybrid만 DB에 돌리고, force_llm은 shape 실패 재계획용."""
    return run_semantic_plan(
        question,
        settings,
        conn=conn,
        ollama_client=ollama_client,
        session=session,
        progress=progress,
        execute=settings.semantic_plan_mode == "hybrid",
        contract=contract,
        plan=plan,
        force_llm=force_llm,
    )


def _keep_executed_semantic(
    question: str,
    semantic: dict[str, Any],
    contract,
) -> dict[str, Any] | None:
    """shape 실패여도 실행된 list/group/scalar 행이 있으면 그 결과를 유지한다."""
    rows = list(semantic.get("rows") or [])
    if not rows:
        return None
    reasons = str(semantic.get("error") or "")
    if not (
        semantic.get("shape_failed")
        or any(
            token in reasons
            for token in (
                "count_row_count",
                "count_multi_numeric",
                "count_not_numeric",
                "shape_mismatch",
            )
        )
    ):
        return None
    from txt2sql.semantic_plan.models import SemanticQueryPlan
    from txt2sql.semantic_plan.result_shape import verify_result

    plan = None
    dumped = semantic.get("semantic_plan")
    if dumped:
        try:
            plan = SemanticQueryPlan.model_validate(dumped)
        except Exception:
            plan = None
    # 계약 kind가 틀려도 실행 Plan과 맞으면 통과
    checked = verify_result(contract, rows, plan=plan)
    if not checked.ok and plan is not None:
        checked = verify_result(None, rows, plan=plan)
    if not checked.ok:
        return None
    answer = str(semantic.get("answer") or "")
    if not answer and plan is not None:
        from txt2sql.semantic_plan.answer import format_semantic_answer

        answer = format_semantic_answer(
            question, plan=plan, rows=rows, row_count=len(rows)
        )
    if not answer:
        answer = format_success_template(
            question, sql=semantic.get("sql") or "", rows=rows, row_count=len(rows)
        )
    out = dict(semantic)
    out.update(
        {
            "ok": True,
            "fallback": False,
            "shape_failed": False,
            "answer": answer,
            "error": None,
            "fallback_reason": None,
            "rows": rows,
            "row_count": len(rows),
        }
    )
    return out


def _after_shape_fail(
    question: str,
    first: dict[str, Any],
    retried: dict[str, Any],
    contract,
) -> dict[str, Any]:
    """재계획 성공을 우선하고, 아니면 이미 나온 행을 유지한다."""
    if retried.get("ok") and not retried.get("fallback"):
        return retried
    return (
        _keep_executed_semantic(question, first, contract)
        or _keep_executed_semantic(question, retried, contract)
        or retried
    )


def _semantic_blocks_rag_fallback(semantic: dict[str, Any]) -> bool:
    """SQL 안전·명확화 실패는 RAG로 보내지 않는다. 계약 불완전은 RAG 허용."""
    if semantic.get("needs_clarification"):
        return True
    reason = str(semantic.get("fallback_reason") or "")
    if reason in {"readonly_failed", "sql_validation_failed"}:
        return True
    error = str(semantic.get("error") or "")
    if "readonly" in reason.lower() or "readonly" in error.lower():
        return True
    if "sql_validation" in reason.lower():
        return True
    return False


def _try_semantic_result(
    question: str,
    settings: Settings,
    progress: ProgressTracker,
    *,
    semantic: dict[str, Any],
    on_token: TokenCallback | None,
    shadow_message: str,
) -> dict[str, Any] | None:
    """hybrid면 SQP 결과를 반환하고, fallback/shadow면 None으로 다음 경로를 탄다."""
    if settings.semantic_plan_mode == "hybrid":
        if semantic.get("needs_clarification"):
            progress.emit("clarify", "Semantic Plan 확인 요청")
            emit_text_chunks(str(semantic.get("answer") or ""), on_token)
            return _payload(
                answer=semantic.get("answer"),
                route=semantic.get("route") or "semantic_plan_clarify",
                semantic_plan=semantic.get("semantic_plan"),
                ambiguous_terms=list(
                    (semantic.get("semantic_plan") or {}).get("ambiguities") or []
                ),
            )
        if semantic.get("ok") and not semantic.get("fallback"):
            return _finish_semantic_query(
                question,
                settings,
                progress,
                semantic=semantic,
                on_token=on_token,
            )
        progress.emit(
            "route",
            "Semantic Plan fallback → RAG+LLM: "
            f"{semantic.get('fallback_reason')}",
        )
        return None
    progress.emit(
        "plan",
        shadow_message,
        semantic_ok=semantic.get("ok"),
        fallback_reason=semantic.get("fallback_reason"),
        plan_version=(semantic.get("semantic_plan") or {}).get("version"),
        needs_clarification=semantic.get("needs_clarification"),
    )
    return None


def _try_priority_count_route(
    question: str,
    settings: Settings,
    progress: ProgressTracker,
    *,
    conn: psycopg.Connection,
    ollama_client: Any | None,
    on_token: TokenCallback | None,
    contract,
) -> dict[str, Any] | None:
    from txt2sql.count_routes import match_priority_count_route

    priority = match_priority_count_route(question)
    if priority is None:
        return None
    routed = RoutedQuery(priority.intent, priority.sql)
    progress.emit("route", f"우선 count 라우트: {priority.intent}")
    return _maybe_finish_routed(
        question,
        settings,
        progress,
        conn=conn,
        ollama_client=ollama_client,
        on_token=on_token,
        routed=routed,
        route_label=f"우선 count 라우트: {priority.intent}",
        contract=contract,
    )


def _ask_inner(
    question: str,
    settings: Settings,
    progress: ProgressTracker,
    session: SessionContext | None,
    *,
    conn: psycopg.Connection,
    ollama_client: Any | None,
    on_token: TokenCallback | None = None,
    preferred_intent: IntentPrediction | None = None,
    contract=None,
    plan_bundle=None,
    early_v2_result=None,
) -> dict[str, Any]:
    if contract is None:
        contract = extract_contract(question)
        progress.emit(
            "understand",
            "Query Contract 생성",
            operation=contract.operation,
            complexity=contract.complexity,
        )

    finished = _try_priority_count_route(
        question,
        settings,
        progress,
        conn=conn,
        ollama_client=ollama_client,
        on_token=on_token,
        contract=contract,
    )
    if finished is not None:
        return finished

    if session is not None:
        count_only = try_count_only_display_followup(question, session)
        if count_only is not None:
            finished = _maybe_finish_routed(
                question,
                settings,
                progress,
                conn=conn,
                ollama_client=ollama_client,
                on_token=on_token,
                routed=count_only,
                route_label="직전 조건 COUNT 전환",
                contract=contract,
            )
            if finished is not None:
                return finished

        _SESSION_SQL_FOLLOWUPS = frozenset(
            {"semantic_plan_aggregate", "followup_count_display"}
        )
        for followup_fn, label in (
            (try_group_prior_result_followup, "직전 결과 GROUP BY"),
            (try_add_avg_to_group_followup, "GROUP BY 평균 추가"),
            (try_aggregate_top_value_followup, "집계 최댓값 1건"),
        ):
            routed = followup_fn(question, session)
            if routed is None:
                continue
            if routed.intent not in _SESSION_SQL_FOLLOWUPS and not route_allowed(
                routed.intent, contract
            ):
                continue
            finished = _maybe_finish_routed(
                question,
                settings,
                progress,
                conn=conn,
                ollama_client=ollama_client,
                on_token=on_token,
                routed=routed,
                route_label=label,
                contract=contract,
            )
            if finished is not None:
                return finished

    if (
        session is not None
        and settings.semantic_plan_mode in {"shadow", "hybrid"}
        and is_semantic_plan_followup(question, session)
    ):
        base_plan = session.last_semantic_plan
        if base_plan is None:
            from txt2sql.semantic_plan.generator import try_heuristic_plan

            heur = try_heuristic_plan(
                session.last_full_question or session.last_question or "",
                contract=contract,
            )
            if heur is not None:
                usable = (not heur.requires_clarification) or bool(
                    heur.filters or heur.scope or heur.predicate
                )
                if usable:
                    dumped = heur.model_dump()
                    dumped["requires_clarification"] = False
                    dumped["ambiguities"] = []
                    base_plan = dumped
        if base_plan:
            history = apply_followup_history(
                question,
                base_plan,
                session.last_plan_events,
                base_override=(
                    session.last_plan_base if session.last_plan_events else None
                ),
            )
            merged = history[0] if history is not None else merge_followup_plan(
                question, base_plan
            )
            if merged is not None:
                from txt2sql.semantic_plan.plan_repair import (
                    apply_contract_operators,
                    inject_missing_predicates,
                )

                merged = apply_contract_operators(merged, contract)
                merged = inject_missing_predicates(merged, question, contract=contract)
                merged = apply_result_anchor(question, merged, session)
                merged = apply_count_display_followup(question, merged, session)
                merged = apply_aggregate_followup(question, merged, session)
                if history is not None:
                    session.last_plan_events = [item.model_dump() for item in history[1]]
                progress.emit("route", "Semantic Plan 후속 delta")
                semantic = _run_semantic(
                    question,
                    settings,
                    conn=conn,
                    ollama_client=ollama_client,
                    session=session,
                    progress=progress,
                    contract=contract,
                    plan=merged,
                )
                finished = _try_semantic_result(
                    question,
                    settings,
                    progress,
                    semantic=semantic,
                    on_token=on_token,
                    shadow_message="Semantic Plan follow-up shadow 완료",
                )
                if finished is not None:
                    return finished

    if session is not None:
        if session.last_contract is None:
            subset = try_subset_followup(question, session)
            if subset is not None and route_allowed(subset.intent, contract):
                finished = _maybe_finish_routed(
                    question,
                    settings,
                    progress,
                    conn=conn,
                    ollama_client=ollama_client,
                    on_token=on_token,
                    routed=subset,
                    route_label="직전 조건 유지 후속",
                    contract=contract,
                )
                if finished is not None:
                    return finished
        grain = year_stats_grain(question)
        if (
            grain is not None
            and grain >= 2
            and session_has_year_stats(session)
        ):
            base = session.last_full_question or session.last_question or ""
            merged = question
            if base and base.strip() not in question:
                merged = f"{base} {question}".strip()
            routed_year = try_route(merged, conn=conn)
            if routed_year is not None and routed_year.intent == "d198_year_stats" and route_allowed(
                routed_year.intent, contract
            ):
                finished = _maybe_finish_routed(
                    merged,
                    settings,
                    progress,
                    conn=conn,
                    ollama_client=ollama_client,
                    on_token=on_token,
                    routed=routed_year,
                    route_label=f"직전 연도 통계 {grain}년 단위",
                    contract=contract,
                )
                if finished is not None:
                    return finished
        if is_value_bin_followup(question, session):
            base = session.last_full_question or session.last_question or ""
            merged = question
            if base and base.strip() not in question:
                merged = f"{base} {question}".strip()
            routed_bin = try_route(merged, conn=conn)
            if routed_bin is not None and routed_bin.intent == "d198_value_bins" and route_allowed(
                routed_bin.intent, contract
            ):
                finished = _maybe_finish_routed(
                    merged,
                    settings,
                    progress,
                    conn=conn,
                    ollama_client=ollama_client,
                    on_token=on_token,
                    routed=routed_bin,
                    route_label="직전 조건 유지 수치 구간",
                    contract=contract,
                )
                if finished is not None:
                    return finished

    if is_followup_question(question, session) and session is not None:
        progress.emit("route", "후속 질문(직전 결과 참조)으로 판단")
        follow = answer_followup(conn, question, session)
        if follow.intent == "followup_no_context" or not follow.rows:
            progress.emit("answer", f"후속 답변 완료 ({follow.intent})")
            emit_text_chunks(follow.answer, on_token)
            return _qa_ok(follow)
        progress.emit("answer", "후속 답변 자연어 생성")
        answer = format_success(
            question,
            sql=follow.sql or "",
            rows=follow.rows,
            row_count=len(follow.rows),
            route=follow.intent,
            on_token=on_token,
            **_llm_kw(settings, ollama_client),
        )
        return _payload(
            answer=answer,
            sql=follow.sql,
            tables=follow.tables,
            rows=follow.rows,
            route=follow.intent,
        )

    mode: DispatchMode = (
        "baseline" if settings.route_dispatch_mode == "baseline" else "optimized"
    )
    route_match = match_route(question, mode=mode, conn=conn, contract=contract)
    deferred_route = route_match.deferred
    if deferred_route is None and route_match.early is None:
        deferred_route = try_route(question, conn=conn)
    candidates = detect_route_candidates(question, contract, conn=conn)
    _emit_contract_routing(progress, contract, candidates)
    picked = pick_eligible_routed([route_match.early, deferred_route], contract)
    if (
        picked is not None
        and route_match.early is not None
        and picked.intent == route_match.early.intent
    ):
        early = route_match.early
        if early.intent == "building_name_lookup":
            label = "건물명 조회 라우트"
        elif early.intent.startswith("building_rank_"):
            label = f"건물 순위 라우트 ({early.intent})"
        elif early.intent == "legal_dong_admin_members":
            label = "법정동 구성 행정동 목록"
        else:
            label = f"산업단지 라우트 ({early.intent})"
        _emit_contract_routing(
            progress, contract, candidates, missing=[], decision="ROUTER"
        )
        finished = _maybe_finish_routed(
            question,
            settings,
            progress,
            conn=conn,
            ollama_client=ollama_client,
            on_token=on_token,
            routed=early,
            route_label=label,
            contract=contract,
        )
        if finished is not None:
            return finished
        picked = None
    elif route_match.early is not None and (
        picked is None or picked.intent != route_match.early.intent
    ):
        miss = missing_requirements(capability_for(route_match.early.intent), contract)
        progress.emit(
            "route",
            f"early route capability 부족 또는 비용 순위 아님: {route_match.early.intent}",
            missing=miss,
            candidates=candidates,
        )

    if preferred_intent is not None and settings.intent_mode in {"hybrid", "llm"}:
        dispatched = _try_preferred_intent(
            question,
            settings,
            progress,
            conn=conn,
            ollama_client=ollama_client,
            on_token=on_token,
            preferred=preferred_intent,
            contract=contract,
        )
        if dispatched is not None:
            _emit_contract_routing(
                progress, contract, candidates, missing=[], decision="ROUTER"
            )
            return dispatched
        progress.emit("route", "선호 의도 처리 실패 → 규칙 체인")

    llm = _llm_kw(settings, ollama_client)
    if is_rank_compare_question(question) and route_allowed("building_rank_compare", contract):
        progress.emit("route", "복수 지역 최고 건물 비교로 판단")
        progress.emit("answer", "최고 건물 비교 답변 생성")
        ranked = answer_rank_compare(conn, question, on_token=on_token, **llm)
        if ranked is not None:
            progress.emit("answer", "최고 건물 비교 완료")
            _emit_contract_routing(
                progress, contract, candidates, missing=[], decision="ROUTER"
            )
            return _qa_ok(ranked)
        progress.emit("route", "최고 건물 비교 매칭 실패 → 계속 진행")

    if is_usage_overview_question(question) and route_allowed("building_usage_count", contract):
        from txt2sql.query_understanding.contract import extract_contract as _extract_contract

        if not _extract_contract(question).wants_count:
            progress.emit("route", "건물 용도 구성 설명 질의로 판단")
            progress.emit("answer", "용도 분포 자연어 생성")
            usage_ov = answer_usage_overview_question(
                conn, question, on_token=on_token, **llm
            )
            if usage_ov is not None:
                progress.emit(
                    "profile",
                    "용도 상위 집계 완료",
                    tables=usage_ov.tables,
                    sql=usage_ov.sql,
                )
                progress.emit("answer", "한국어 용도 설명 완료")
                _emit_contract_routing(
                    progress, contract, candidates, missing=[], decision="ROUTER"
                )
                return _qa_ok(usage_ov)
            progress.emit("route", "용도 구성 설명 매칭 실패 → 계속 진행")

    if is_profile_question(question) and route_allowed("building_profile", contract):
        progress.emit("route", "건물 특징 요약 질의로 판단")
        progress.emit("answer", "특징 요약 자연어 생성")
        profile = answer_profile_question(conn, question, on_token=on_token, **llm)
        if profile is not None:
            progress.emit(
                "profile",
                "속성 기반 집계 요약 완료",
                tables=profile.tables,
                sql=profile.sql,
            )
            progress.emit("answer", "한국어 특징 답변 완료")
            _emit_contract_routing(
                progress, contract, candidates, missing=[], decision="ROUTER"
            )
            return _qa_ok(profile)
        progress.emit("route", "특징 요약 매칭 실패 → 계속 진행")

    if is_metadata_question(question) and not contract_is_executable_query(contract):
        progress.emit("route", "메타데이터 설명 질의로 판단")
        meta = answer_metadata_question(conn, question)
        if meta is not None:
            progress.emit(
                "meta",
                f"속성/스키마 설명 생성 ({meta.intent})",
                tables=meta.tables,
            )
            progress.emit("answer", "한국어 설명 답변 완료")
            emit_text_chunks(meta.answer, on_token)
            return _qa_ok(meta)
        progress.emit("route", "메타 질의로 보였으나 매칭 실패 → SQL 경로")

    named = try_place_boundary_area_query(conn, question)
    if named is None:
        named = try_named_dataset_query(conn, question)
    if named is not None:
        from txt2sql.intent_router import RoutedQuery

        progress.emit(
            "route",
            f"업로드 표시명 데이터셋 조회 ({named.display_name})",
        )
        return _finish_routed_query(
            question,
            settings,
            progress,
            conn=conn,
            ollama_client=ollama_client,
            on_token=on_token,
            routed=RoutedQuery(intent=named.intent, sql=named.sql),
            route_label=f"named_dataset:{named.intent}",
            contract=None,
        )

    progress.emit("route", "모호성/미지 용어 점검")
    clarify = check_ambiguity(conn, question)
    followup_cue = has_anaphora(question) or any(
        k in question
        for k in ("그중", "그 중", "그 건물", "그 건물들", "첫번째", "첫 번째")
    )
    if followup_cue and clarify is not None and clarify.intent == "clarify_unknown_term":
        clarify = None
    deferred_unknown = None
    if clarify is not None and clarify.intent == "clarify_unknown_term":
        # 미지 용어는 라우트·Plan이 실패한 뒤에만 반환. 동음이의·컬럼 모호는 선제 유지.
        deferred_unknown = clarify
        clarify = None
    if clarify is not None:
        progress.emit(
            "clarify",
            f"확인 필요: {', '.join(clarify.ambiguous_terms) or clarify.intent}",
        )
        progress.emit("answer", "확인 요청 답변 완료")
        emit_text_chunks(clarify.answer, on_token)
        return _qa_ok(clarify, ambiguous_terms=clarify.ambiguous_terms)

    progress.emit("route", "규칙 라우터 매칭 시도")
    # plan_bundle already built early; rebuild only if missing.
    if plan_bundle is None:
        try:
            from txt2sql.planner.executor_adapter import build_execution_plan, route_allowed_by_plan

            plan_bundle = build_execution_plan(question, contract=contract)
            progress.emit(
                "plan",
                f"QueryIR/Plan: task={plan_bundle.query_ir.task} "
                f"logical={plan_bundle.logical.status} "
                f"physical={plan_bundle.physical.strategy}",
                query_ir_task=plan_bundle.query_ir.task,
                logical_status=plan_bundle.logical.status,
                physical_strategy=plan_bundle.physical.strategy,
                reason_codes=list(plan_bundle.logical.reason_codes),
            )
        except Exception as plan_exc:  # noqa: BLE001 — planning must not break legacy path
            progress.emit("plan", f"plan build skipped: {type(plan_exc).__name__}")

    from txt2sql.planner.executor_adapter import route_allowed_by_plan

    # Late semantic-v2 retry only if early path did not already attempt (success or typed fail).
    skip_late_v2 = early_v2_result is not None
    if plan_bundle is not None and not skip_late_v2:
        try:
            from txt2sql.planner.semantic_executor import (
                should_try_semantic_v2,
                try_execute_semantic_v2,
            )

            if should_try_semantic_v2(plan_bundle):
                progress.emit(
                    "route",
                    "semantic-v2 executor 재시도",
                    physical_strategy=plan_bundle.physical.strategy,
                )
                v2 = try_execute_semantic_v2(
                    question,
                    plan_bundle,
                    conn=conn,
                    default_limit=settings.default_limit,
                )
                if v2 is not None and v2.get("ok"):
                    progress.emit(
                        "route",
                        "semantic-v2 적중",
                        execution_source="semantic_v2",
                        compiler_source="deterministic_compiler_v2",
                    )
                    llm = _llm_kw(settings, ollama_client)
                    rows = list(v2.get("rows") or [])
                    sql = v2.get("sql") or ""
                    answer = format_success(
                        question,
                        sql=sql,
                        rows=rows,
                        row_count=len(rows),
                        route="semantic_v2",
                        on_token=on_token,
                        **llm,
                    )
                    return _payload(
                        answer=answer,
                        sql=sql,
                        tables=v2.get("tables"),
                        rows=rows,
                        route="semantic_v2",
                        query_ir_task=v2.get("query_ir_task"),
                        logical_status=v2.get("logical_status"),
                        physical_strategy=v2.get("physical_strategy"),
                        execution_source="semantic_v2",
                        compiler_source="deterministic_compiler_v2",
                        fallback_source=None,
                        v2_failure_code=None,
                    )
                if v2 is not None and not v2.get("ok"):
                    progress.emit(
                        "route",
                        "semantic-v2 typed failure → legacy 경로",
                        v2_failure_code=v2.get("v2_failure_code"),
                        fallback_source=v2.get("fallback_source"),
                    )
                    early_v2_result = v2
                else:
                    progress.emit("route", "semantic-v2 미지원/실패 → legacy 경로")
        except Exception as v2_exc:  # noqa: BLE001
            progress.emit("route", f"semantic-v2 skip: {type(v2_exc).__name__}")
    elif skip_late_v2 and early_v2_result is not None and not early_v2_result.get("ok"):
        progress.emit(
            "route",
            "semantic-v2 early typed failure 재시도 생략",
            v2_failure_code=early_v2_result.get("v2_failure_code"),
        )

    routed = picked
    if routed is None:
        routed = deferred_route if deferred_route is not None else try_route(question, conn=conn)
        routed = pick_eligible_routed([routed], contract)
    if (
        plan_bundle is not None
        and not route_allowed_by_plan(plan_bundle)
        and plan_bundle.logical.status in {"CLARIFY", "UNSUPPORTED"}
    ):
        progress.emit(
            "route",
            "LogicalPlan not READY → skip partial router execution",
            logical_status=plan_bundle.logical.status,
        )
        routed = None
    if routed is not None and not route_allowed(routed.intent, contract):
        miss = missing_requirements(capability_for(routed.intent), contract)
        progress.emit(
            "route",
            f"라우트 capability 부족: {routed.intent} → Semantic Plan",
            missing=miss,
            candidates=candidates,
        )
        routed = None
    if routed is not None:
        progress.emit(
            "route",
            f"라우트 적중: {routed.intent}",
            intent=routed.intent,
            execution_source="legacy_router",
            compiler_source="legacy_route_sql",
        )
        if routed.intent == "building_age_count_d198_partial":
            from txt2sql.domain import d198_coverage_label

            progress.emit(
                "clarify",
                f"건축년수는 용도별건물(D198) 등록 구·군({d198_coverage_label()}) 범위로 조회",
            )
        _emit_contract_routing(
            progress, contract, candidates, missing=[], decision="ROUTER"
        )
        finished = _maybe_finish_routed(
            question,
            settings,
            progress,
            conn=conn,
            ollama_client=ollama_client,
            on_token=on_token,
            routed=routed,
            route_label=f"라우트 적중: {routed.intent}",
            contract=contract,
        )
        if finished is not None:
            finished.setdefault("execution_source", "legacy_router")
            finished.setdefault("compiler_source", "legacy_route_sql")
            if plan_bundle is not None:
                finished.setdefault("query_ir_task", plan_bundle.query_ir.task)
                finished.setdefault("logical_status", plan_bundle.logical.status)
                finished.setdefault("physical_strategy", plan_bundle.physical.strategy)
            if (
                early_v2_result is not None
                and not early_v2_result.get("ok")
                and early_v2_result.get("v2_failure_code")
            ):
                finished.setdefault(
                    "v2_failure_code", early_v2_result.get("v2_failure_code")
                )
                finished.setdefault(
                    "fallback_source",
                    early_v2_result.get("fallback_source") or "legacy_after_semantic_v2",
                )
            return finished

    semantic: dict[str, Any] | None = None
    if settings.semantic_plan_mode in {"shadow", "hybrid"}:
        _emit_contract_routing(
            progress,
            contract,
            candidates,
            missing=_capability_missing_for_candidates(candidates, contract),
            decision="SEMANTIC_PLAN",
        )
        progress.emit("route", "라우트 미매칭 → Semantic Query Plan")
        semantic = _run_semantic(
            question,
            settings,
            conn=conn,
            ollama_client=ollama_client,
            session=session,
            progress=progress,
            contract=contract,
        )
        if semantic.get("shape_failed"):
            progress.emit("route", "SQP 결과 shape 불일치 → LLM Plan 1회")
            semantic = _after_shape_fail(
                question,
                semantic,
                _run_semantic(
                    question,
                    settings,
                    conn=conn,
                    ollama_client=ollama_client,
                    session=session,
                    progress=progress,
                    contract=contract,
                    force_llm=True,
                ),
                contract,
            )
        finished = _try_semantic_result(
            question,
            settings,
            progress,
            semantic=semantic,
            on_token=on_token,
            shadow_message="Semantic Plan shadow 완료",
        )
        if finished is not None:
            finished.setdefault("execution_source", "semantic_plan")
            finished.setdefault("compiler_source", "legacy_sqp_compiler")
            if plan_bundle is not None:
                finished.setdefault("query_ir_task", plan_bundle.query_ir.task)
                finished.setdefault("logical_status", plan_bundle.logical.status)
                finished.setdefault("physical_strategy", plan_bundle.physical.strategy)
            if (
                early_v2_result is not None
                and not early_v2_result.get("ok")
                and early_v2_result.get("v2_failure_code")
            ):
                finished.setdefault(
                    "v2_failure_code", early_v2_result.get("v2_failure_code")
                )
                finished.setdefault(
                    "fallback_source",
                    early_v2_result.get("fallback_source") or "legacy_after_semantic_v2",
                )
            return finished
        if settings.semantic_plan_mode == "hybrid" and semantic.get("fallback"):
            if _semantic_blocks_rag_fallback(semantic):
                error = (
                    semantic.get("error")
                    or semantic.get("fallback_reason")
                    or "plan_fallback"
                )
                answer = format_failure(question, error=error, sql=semantic.get("sql"))
                emit_text_chunks(answer, on_token)
                fb_src = "semantic_plan_fallback"
                if (
                    plan_bundle is not None
                    and getattr(plan_bundle.logical, "status", None) == "REPLAN"
                ):
                    fb_src = "completeness_replan"
                return _payload(
                    ok=False,
                    answer=answer,
                    sql=semantic.get("sql"),
                    tables=semantic.get("tables") or [],
                    error=error,
                    route=semantic.get("route"),
                    semantic_plan=semantic.get("semantic_plan"),
                    fallback_source=fb_src,
                )
            # Contract/plan incompleteness may fall through to RAG+LLM.
            progress.emit(
                "route",
                "Semantic Plan fallback → RAG+LLM 허용: "
                f"{semantic.get('fallback_reason')}",
            )

    if deferred_unknown is not None:
        progress.emit(
            "clarify",
            f"확인 필요: {', '.join(deferred_unknown.ambiguous_terms) or deferred_unknown.intent}",
        )
        progress.emit("answer", "확인 요청 답변 완료")
        emit_text_chunks(deferred_unknown.answer, on_token)
        return _qa_ok(
            deferred_unknown, ambiguous_terms=deferred_unknown.ambiguous_terms
        )

    progress.emit("route", "라우트 미매칭 → RAG+LLM 경로 (emergency fallback)")
    sqp_rag_reason: str | None = None
    if (
        settings.semantic_plan_mode == "hybrid"
        and isinstance(semantic, dict)
        and semantic.get("fallback")
        and not _semantic_blocks_rag_fallback(semantic)
    ):
        sqp_rag_reason = f"semantic_plan_fallback:{semantic.get('fallback_reason')}"
    rag_kwargs: dict[str, Any] = {
        "fallback_reason": sqp_rag_reason or "route_and_plan_miss",
    }
    if plan_bundle is not None:
        # Phase J: unrestricted RAG only after planner coverage miss; snapshot required.
        if sqp_rag_reason:
            fallback_reason = sqp_rag_reason
        elif plan_bundle.logical.status == "READY":
            fallback_reason = (
                f"emergency_after_ready_miss;physical={plan_bundle.physical.strategy}"
            )
        else:
            fallback_reason = (
                f"logical={plan_bundle.logical.status};physical={plan_bundle.physical.strategy}"
            )
        rag_kwargs.update(
            {
                "fallback_reason": fallback_reason,
                "query_ir_snapshot": plan_bundle.query_ir.model_dump(),
                "logical_plan_snapshot": {
                    "status": plan_bundle.logical.status,
                    "reason_codes": list(plan_bundle.logical.reason_codes),
                    "root_op": plan_bundle.logical.root.op,
                },
                "binding_snapshot": {
                    "datasets": [b.dataset for b in plan_bundle.logical.bindings],
                    "concepts": [b.concept for b in plan_bundle.logical.bindings],
                },
            }
        )
    rag = run_rag_sql(
        question,
        settings,
        conn=conn,
        ollama_client=ollama_client,
        skip_answer=True,
        progress=progress,
        **rag_kwargs,
    )
    rag_trace: dict[str, Any] = {
        "execution_source": "rag_sql",
        "compiler_source": "rag_llm",
        "fallback_source": rag_kwargs.get("fallback_reason"),
    }
    if plan_bundle is not None:
        rag_trace.update(
            {
                "query_ir_task": plan_bundle.query_ir.task,
                "logical_status": plan_bundle.logical.status,
                "physical_strategy": plan_bundle.physical.strategy,
            }
        )
    if not rag.get("ok"):
        answer = format_failure(
            question, error=rag.get("error") or "실패", sql=rag.get("sql")
        )
        emit_text_chunks(answer, on_token)
        return _payload(
            ok=False,
            answer=answer,
            sql=rag.get("sql"),
            tables=rag.get("tables"),
            error=rag.get("error"),
            **rag_trace,
        )

    progress.emit("answer", "LLM 한국어 답변 생성", execution_source="rag_sql")
    rows = list(rag.get("rows") or [])
    sql = rag.get("sql")
    answer = format_success(
        question,
        sql=sql or "",
        rows=rows,
        row_count=len(rows),
        route=None,
        on_token=on_token,
        **llm,
    )
    progress.emit("answer", "한국어 답변 생성 완료")
    return _payload(
        answer=answer,
        sql=sql,
        tables=rag.get("tables"),
        rows=rows,
        diagnostics=rag.get("diagnostics"),
        **rag_trace,
    )
