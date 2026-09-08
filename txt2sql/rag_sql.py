"""규칙 라우터를 우회하는 RAG+LLM SQL 경로 (벤치마크/파이프라인 공용)."""

from __future__ import annotations

from typing import Any

import psycopg

from txt2sql.config import Settings
from txt2sql.db import execute_query
from txt2sql.intent_router import fix_common_sql_mistakes
from txt2sql.progress import ProgressTracker
from txt2sql.schema_retriever import retrieve_schema
from txt2sql.spatial_templates import (
    building_in_dong_count_sql,
    extract_place_token,
    spatial_fewshot,
)
from txt2sql.sql_fix import load_name_maps, rewrite_display_names
from txt2sql.sql_generator import build_few_shot_for_question, generate_sql
from txt2sql.sql_validator import diagnose_sql, validate_sql_preexec

_SPATIAL_INTENT = ("안에", "내부", "속하는", "교차", "버퍼", "거리", "근처", "이내", "주변", "반경")


def _emit(progress: ProgressTracker | None, stage: str, message: str, **extra: Any) -> None:
    if progress is not None:
        progress.emit(stage, message, **extra)


def _llm_host(settings: Settings, ollama_client: Any | None) -> str | None:
    return settings.ollama_host if ollama_client is None else None


def has_spatial_sql(sql: str) -> bool:
    upper = sql.upper()
    return any(
        fn in upper
        for fn in (
            "ST_INTERSECTS",
            "ST_WITHIN",
            "ST_DWITHIN",
            "ST_DISTANCE",
            "ST_CONTAINS",
        )
    )


def normalize_sql(
    sql: str,
    table_map: dict[str, str],
    column_map: dict[str, str],
    *,
    question: str | None = None,
) -> str:
    from txt2sql.sql_d010_guard import rewrite_d198_columns_on_d010

    return rewrite_d198_columns_on_d010(
        fix_common_sql_mistakes(
            rewrite_display_names(sql, table_map, column_map),
            question=question,
        ),
        question,
    )


def run_rag_sql(
    question: str,
    settings: Settings,
    *,
    conn: psycopg.Connection,
    ollama_client: Any | None = None,
    skip_answer: bool = True,
    progress: ProgressTracker | None = None,
    fallback_reason: str | None = None,
    query_ir_snapshot: dict[str, Any] | None = None,
    logical_plan_snapshot: dict[str, Any] | None = None,
    binding_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Schema RAG + 동적 few-shot + SQLGlot/EXPLAIN + 실행 재시도.

    Emergency SQL fallback should pass fallback_reason + planning snapshots so
    RAG hits do not silently mask semantic-layer failures.
    """
    if fallback_reason:
        _emit(
            progress,
            "fallback",
            f"RAG emergency fallback: {fallback_reason}",
            fallback_reason=fallback_reason,
            query_ir_snapshot=query_ir_snapshot,
            logical_plan_snapshot=logical_plan_snapshot,
            binding_snapshot=binding_snapshot,
        )
    from txt2sql.security.llm_policy import allow_sample_values_for_llm

    host = _llm_host(settings, ollama_client)
    _emit(progress, "schema", "스키마 검색(임베딩 RAG)")
    retrieved = retrieve_schema(
        conn,
        question,
        embed_model=settings.ollama_embed_model,
        host=host,
        client=ollama_client,
        top_k=settings.schema_top_k,
        include_sample_values=allow_sample_values_for_llm(settings),
    )
    tables = retrieved.get("tables") or []
    _emit(
        progress,
        "schema",
        f"스키마 확보 ({len(tables)}개 테이블)",
        tables=tables,
    )
    schema_text = retrieved["schema_text"]
    table_map, column_map = load_name_maps(conn)
    spatial_intent = any(k in question for k in _SPATIAL_INTENT)
    place = extract_place_token(question)
    few_shot = build_few_shot_for_question(
        question,
        top_k=settings.example_top_k,
        embed_model=settings.ollama_embed_model,
        host=host,
        client=ollama_client,
    )
    _emit(
        progress,
        "schema",
        f"few-shot 예제 검색 (top_k={settings.example_top_k})",
    )

    def _gen(error_feedback: str | None = None, schema_extra: str = "") -> str:
        return generate_sql(
            question,
            schema_text + schema_extra,
            model=settings.ollama_model,
            host=host,
            client=ollama_client,
            error_feedback=error_feedback,
            few_shot=few_shot,
        )

    def _regen(error_feedback: str | None = None, schema_extra: str = "") -> str:
        return normalize_sql(
            _gen(error_feedback=error_feedback, schema_extra=schema_extra),
            table_map,
            column_map,
            question=question,
        )

    sql: str | None = None
    retries = 0
    max_retries = max(1, settings.sql_max_retries)
    pre_diag: str | None = None
    post_diag: str | None = None
    rows: list[dict[str, Any]] = []

    try:
        _emit(progress, "llm", "SQL 생성 (Ollama)")
        sql = _regen()
        _emit(progress, "sql", "SQL 초안 정규화 완료", sql=sql)

        if spatial_intent and not has_spatial_sql(sql):
            _emit(progress, "llm", "공간 조건 누락 → 재생성")
            sql = _regen(
                error_feedback=(
                    "Previous SQL missed PostGIS predicates.\n"
                    + spatial_fewshot(place)
                ),
                schema_extra="\n\n" + spatial_fewshot(place),
            )
            _emit(progress, "sql", "공간 SQL 재생성 완료", sql=sql)

        if (
            spatial_intent
            and place
            and place.endswith("동")
            and "건물" in question
            and ("건수" in question or "개수" in question or "몇" in question)
            and not has_spatial_sql(sql)
        ):
            sql = building_in_dong_count_sql(place)
            _emit(progress, "sql", "동 경계 템플릿 SQL 적용", sql=sql)

        pre_diag = validate_sql_preexec(
            question,
            sql,
            conn=conn if settings.use_explain else None,
            default_limit=settings.default_limit,
            use_explain=settings.use_explain,
        )
        if pre_diag:
            _emit(progress, "validate", f"사전 진단: {pre_diag}")
            sql = _regen(error_feedback=pre_diag)
            _emit(progress, "sql", "진단 반영 재생성 완료", sql=sql)
        else:
            _emit(progress, "validate", "사전 진단 통과 (domain/sqlglot/explain)")

        while True:
            _emit(progress, "execute", "DB 조회 실행")
            try:
                rows = execute_query(
                    conn, sql, default_limit=settings.default_limit
                )
                break
            except Exception as exc:
                conn.rollback()
                retries += 1
                _emit(
                    progress,
                    "error",
                    f"실행 오류 (재시도 {retries}/{max_retries}): {type(exc).__name__}",
                )
                if retries >= max_retries:
                    raise
                _emit(progress, "llm", "오류 피드백으로 SQL 재생성")
                sql = _regen(
                    error_feedback=(
                        f"{type(exc).__name__}: {exc}\n"
                        "Use physical table/column names only "
                        '(e.g. table "AL_D010_26_20250704", column "geometry").\n'
                        'District name filters must use "A4" (법정동명), not "A3".\n'
                        + (spatial_fewshot(place) if spatial_intent else "")
                    )
                )
                if spatial_intent and place and not has_spatial_sql(sql):
                    sql = building_in_dong_count_sql(place)
                _emit(progress, "sql", "재생성 SQL 확정", sql=sql)

        _emit(progress, "result", f"조회 완료 ({len(rows)}행)", row_count=len(rows))
        post_diag = diagnose_sql(question, sql, row_count=len(rows))
        if post_diag and len(rows) == 0 and retries < max_retries:
            _emit(progress, "validate", f"빈 결과 진단: {post_diag}")
            _emit(progress, "llm", "빈 결과 보정 재생성")
            sql = _regen(
                error_feedback=(
                    f"Query returned 0 rows. Likely issues:\n{post_diag}\n"
                    "Rewrite using AL_D010 + A4 LIKE for gu filters when applicable."
                )
            )
            _emit(progress, "execute", "보정 SQL 재실행")
            rows = execute_query(conn, sql, default_limit=settings.default_limit)
            _emit(
                progress,
                "result",
                f"재조회 완료 ({len(rows)}행)",
                row_count=len(rows),
            )

        return {
            "ok": True,
            "answer": None if skip_answer else "",
            "sql": sql,
            "tables": tables,
            "rows": rows,
            "row_count": len(rows),
            "route": "rag_sql",
            "diagnostics": post_diag or pre_diag,
            "retries": retries,
            "error": None,
            "fallback_reason": fallback_reason,
            "query_ir_snapshot": query_ir_snapshot,
            "logical_plan_snapshot": logical_plan_snapshot,
            "binding_snapshot": binding_snapshot,
        }
    except Exception as exc:
        _emit(progress, "error", f"실패: {type(exc).__name__}: {exc}")
        return {
            "ok": False,
            "answer": None,
            "sql": sql,
            "tables": tables,
            "rows": [],
            "row_count": 0,
            "route": "rag_sql",
            "diagnostics": pre_diag,
            "retries": retries,
            "error": f"{type(exc).__name__}: {exc}",
            "fallback_reason": fallback_reason,
            "query_ir_snapshot": query_ir_snapshot,
            "logical_plan_snapshot": logical_plan_snapshot,
            "binding_snapshot": binding_snapshot,
        }
