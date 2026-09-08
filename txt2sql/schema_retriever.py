from __future__ import annotations

import hashlib
import re
import time
from typing import Any

import psycopg

from txt2sql.llm import resolve_client
from txt2sql.semantic_meta import (
    SAMPLE_COLUMNS,
    column_synonyms,
    distinctive_label_tokens,
    format_synonyms,
    table_synonyms,
    tables_matching_exact_display_names,
    tables_matching_labels,
)


_TEXT_UDTS = frozenset({"text", "varchar", "bpchar", "name", "citext"})


def _sample_columns(table_name: str, cols: list[dict[str, Any]] | None = None) -> tuple[str, ...]:
    if table_name in SAMPLE_COLUMNS:
        return SAMPLE_COLUMNS[table_name]
    if table_name.startswith("AL_D198_"):
        return ("A4", "A25")
    if not cols:
        return ()
    picked: list[str] = []
    for col in cols:
        udt = str(col.get("udt_name") or "").lower()
        if udt not in _TEXT_UDTS and "char" not in udt:
            continue
        if not col.get("display_name"):
            continue
        picked.append(str(col["column_name"]))
        if len(picked) >= 2:
            break
    return tuple(picked)


# 행정구역/구·동 관련 질문에 강제 포함할 테이블
_ADMIN_HINT_TABLES = (
    "BND_ADM_DONG_PG",
    "TL_KODIS_BAS_26_202507",
    "pnu_def",
)

_ADMIN_KEYWORDS = (
    "구",
    "동",
    "행정",
    "법정동",
    "시군구",
    "기초구역",
    "부산",
    "해운대",
    "금정",
    "동래",
    "연제",
    "수영",
    "사하",
    "사상",
    "기장",
    "중구",
    "서구",
    "동구",
    "남구",
    "북구",
    "영도",
    "진구",
    "강서",
)


def _short_desc(text: str | None, max_len: int = 80) -> str:
    if not text:
        return ""
    # 코드 전체 사전(보조 설명 장문) 제거
    cut = re.split(r",\s*보조 설명:", text, maxsplit=1)[0]
    cut = re.sub(r"\s+", " ", cut).strip()
    if len(cut) > max_len:
        return cut[: max_len - 1] + "…"
    return cut


def embed_text(
    text: str,
    *,
    model: str,
    host: str | None = None,
    client: Any | None = None,
) -> list[float]:
    # mxbai-embed-large 등 임베딩 모델은 컨텍스트가 짧음(≈512 tokens)
    clipped = text.strip()
    if len(clipped) > 400:
        clipped = clipped[:400]
    client = resolve_client(host=host, client=client)
    response = client.embeddings(model=model, prompt=clipped)
    embedding = response["embedding"] if isinstance(response, dict) else response.embedding
    return list(embedding)


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in values) + "]"


_SKIP_SEARCH_TABLES = frozenset(
    {
        "geometry_columns",
        "geography_columns",
        "spatial_ref_sys",
        "raster_columns",
        "raster_overviews",
        "col_def",
        "column_metadata",
        "table_metadata",
        "llm_schema_catalog",
        "temp_data_sessions",
    }
)

# 연결 실패·카탈로그 비어 있을 때 쓰는 시드. 실제 검색 목록은 DB에서 발견한다.
_SEED_SEARCHABLE_FQNAMES = (
    "public.AL_D010_26_20250704",
    "public.AL_D060_00_20250804",
    "public.AL_D198_26260_20250115",
    "public.AL_D198_26410_20250115",
    "public.BND_ADM_DONG_PG",
    "public.TL_KODIS_BAS_26_202507",
    "public.pnu_def",
)


def is_searchable_table_name(name: str) -> bool:
    short = (name or "").split(".")[-1]
    if not short or short.lower() in {item.lower() for item in _SKIP_SEARCH_TABLES}:
        return False
    if short.startswith(("temp_", "v_", "v__", "pg_")):
        return False
    return True


def _fqname_to_table(fqname: str) -> str | None:
    parts = fqname.split(".")
    name = parts[-1] if parts else fqname
    if not is_searchable_table_name(name):
        return None
    return name


def _seed_searchable_fqnames() -> list[str]:
    from txt2sql.domain import D198_TABLES

    names = list(_SEED_SEARCHABLE_FQNAMES)
    for table in D198_TABLES:
        fq = f"public.{table}"
        if fq not in names:
            names.append(fq)
    return names


def discover_searchable_fqnames(conn: psycopg.Connection) -> list[str]:
    """공간 테이블 + pnu_def + 임베딩된 카탈로그 + 최신 D198 커버리지."""
    from txt2sql.domain import D198_TABLES

    names: list[str] = []
    seen: set[str] = set()
    latest_d198 = set(D198_TABLES)

    def add(fq: str) -> None:
        table = _fqname_to_table(fq)
        if not table:
            return
        if table.startswith("AL_D198_") and latest_d198 and table not in latest_d198:
            return
        if "." not in fq:
            fq = f"public.{table}"
        if fq not in seen:
            seen.add(fq)
            names.append(fq)

    spatial = conn.execute(
        """
        SELECT f_table_schema AS table_schema, f_table_name AS table_name
        FROM geometry_columns
        WHERE f_table_schema = 'public'
        UNION
        SELECT table_schema, table_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND udt_name IN ('geometry', 'geography', 'raster')
          AND table_name NOT LIKE 'temp_%'
        """
    ).fetchall()
    for row in spatial:
        add(f"{row['table_schema']}.{row['table_name']}")

    exists = conn.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'pnu_def'
        """
    ).fetchone()
    if exists:
        add("public.pnu_def")

    try:
        catalog_rows = conn.execute(
            """
            SELECT fqname
            FROM llm_schema_catalog
            WHERE kind = 'table' AND embedding IS NOT NULL
            """
        ).fetchall()
        for row in catalog_rows:
            add(str(row["fqname"]))
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass

    for table in D198_TABLES:
        add(f"public.{table}")

    return names or _seed_searchable_fqnames()


_SEARCHABLE_TTL_SEC = 20.0
_searchable_memo: dict[int, tuple[float, tuple[str, ...]]] = {}


def searchable_fqnames(conn: psycopg.Connection | None = None) -> list[str]:
    if conn is None:
        return _seed_searchable_fqnames()
    now = time.monotonic()
    key = id(conn)
    hit = _searchable_memo.get(key)
    if hit and now - hit[0] < _SEARCHABLE_TTL_SEC:
        return list(hit[1])
    try:
        names = discover_searchable_fqnames(conn)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return _seed_searchable_fqnames()
    _searchable_memo[key] = (now, tuple(names))
    if len(_searchable_memo) > 16:
        oldest = min(_searchable_memo, key=lambda k: _searchable_memo[k][0])
        _searchable_memo.pop(oldest, None)
    return names


def discover_searchable_tables(conn: psycopg.Connection) -> list[str]:
    return [fq.split(".")[-1] for fq in searchable_fqnames(conn)]


def _load_searchable_table_meta(
    conn: psycopg.Connection,
) -> list[dict[str, str]]:
    searchable = {fq.split(".")[-1] for fq in searchable_fqnames(conn)}
    try:
        rows = conn.execute(
            """
            SELECT table_name, display_name, description, category
            FROM table_metadata
            WHERE schema_name = 'public'
            """
        ).fetchall()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return []
    return [
        {
            "table_name": str(row["table_name"]),
            "display_name": str(row.get("display_name") or ""),
            "description": str(row.get("description") or ""),
            "category": str(row.get("category") or ""),
        }
        for row in rows
        if str(row["table_name"]) in searchable
    ]


def apply_label_boost(
    conn: psycopg.Connection,
    question: str,
    tables: list[str],
) -> list[str]:
    """메타데이터 고유 토큰이 질문에 있으면 해당 테이블을 RAG 후보 앞에 둔다."""
    meta = _load_searchable_table_meta(conn)
    if not meta:
        return tables
    matched = tables_matching_labels(question, meta)
    if not matched:
        return tables
    rest = [name for name in tables if name not in matched]
    return matched + rest


def apply_exact_display_pin(
    conn: psycopg.Connection,
    question: str,
    tables: list[str],
) -> tuple[list[str], list[str]]:
    """표시명/물리명이 질문에 그대로 있으면 해당 테이블을 주 후보로 고정한다.

    Returns:
        (재정렬된 tables, exact_matched table names)
    """
    meta = _load_searchable_table_meta(conn)
    if not meta:
        return tables, []
    exact = tables_matching_exact_display_names(question, meta)
    if not exact:
        return tables, []
    rest = [name for name in tables if name not in exact]
    return exact + rest, exact


def pin_named_dataset_schema(
    question: str,
    tables: list[str],
    exact: list[str],
) -> tuple[list[str], str]:
    """표시명 정확 매칭 시 스키마를 해당 테이블(+필요 시 행정경계)로 좁힌다."""
    if not exact:
        return tables, ""
    pinned = list(exact)
    placeish = any(
        k in question
        for k in (
            "구",
            "동",
            "군",
            "시",
            "행정",
            "법정",
            "부산",
            "금정",
            "해운대",
            "동래",
            "연제",
            "수영",
            "사하",
            "사상",
            "기장",
            "중구",
            "서구",
            "동구",
            "남구",
            "북구",
            "영도",
            "진구",
            "강서",
        )
    )
    if placeish and "BND_ADM_DONG_PG" not in pinned:
        pinned.append("BND_ADM_DONG_PG")
    names = ", ".join(f'"{t}"' for t in exact)
    tip = (
        f"\n- The question explicitly names dataset(s) {names}. "
        "MUST use that table as the main FROM. "
        "Do NOT query pnu_def or unrelated building tables "
        "(AL_D010/AL_D198) unless the named dataset itself is one of them.\n"
    )
    return pinned, tip


def search_catalog_tables(
    conn: psycopg.Connection,
    question: str,
    *,
    embed_model: str,
    host: str | None = None,
    client: Any | None = None,
    top_k: int = 5,
) -> list[str]:
    embedding = embed_text(
        question, model=embed_model, host=host, client=client
    )
    lit = vector_literal(embedding)
    rows = conn.execute(
        """
        SELECT fqname, kind
        FROM llm_schema_catalog
        WHERE embedding IS NOT NULL
          AND fqname = ANY(%s)
        ORDER BY embedding <=> %s::vector
        LIMIT %s
        """,
        (searchable_fqnames(conn), lit, top_k),
    ).fetchall()

    tables: list[str] = []
    for row in rows:
        name = _fqname_to_table(row["fqname"])
        if name and name not in tables:
            tables.append(name)
    return tables


def apply_admin_boost(
    question: str,
    tables: list[str],
    *,
    named_dataset_pin: bool = False,
) -> list[str]:
    if not any(kw in question for kw in _ADMIN_KEYWORDS):
        return tables
    out = list(tables)
    # 표시명으로 업로드 테이블이 고정되면 pnu_def/기초구역을 끼워 넣어
    # LLM이 잘못된 FROM을 고르지 않게 한다.
    hint_tables = (
        ("BND_ADM_DONG_PG",) if named_dataset_pin else _ADMIN_HINT_TABLES
    )
    for t in hint_tables:
        if t not in out:
            out.append(t)
    return out


def _fetch_pk_columns(conn: psycopg.Connection, table_name: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.table_schema = 'public'
          AND tc.table_name = %s
          AND tc.constraint_type = 'PRIMARY KEY'
        ORDER BY kcu.ordinal_position
        """,
        (table_name,),
    ).fetchall()
    return [r["column_name"] for r in rows]


def _fetch_fk_lines(conn: psycopg.Connection, table_name: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT
            kcu.column_name,
            ccu.table_name AS foreign_table,
            ccu.column_name AS foreign_column
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name
         AND ccu.table_schema = tc.table_schema
        WHERE tc.table_schema = 'public'
          AND tc.table_name = %s
          AND tc.constraint_type = 'FOREIGN KEY'
        ORDER BY kcu.ordinal_position
        """,
        (table_name,),
    ).fetchall()
    lines: list[str] = []
    for r in rows:
        lines.append(
            f'  FK "{r["column_name"]}" -> '
            f'"{r["foreign_table"]}"."{r["foreign_column"]}"'
        )
    return lines


def _fetch_sample_values(
    conn: psycopg.Connection,
    table_name: str,
    column_name: str,
    *,
    limit: int = 4,
) -> list[str]:
    """대표 텍스트 컬럼의 DISTINCT 샘플 (실패 시 빈 목록)."""
    try:
        rows = conn.execute(
            f"""
            SELECT DISTINCT "{column_name}" AS v
            FROM "{table_name}"
            WHERE "{column_name}" IS NOT NULL
              AND btrim("{column_name}"::text) <> ''
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
        out: list[str] = []
        for r in rows:
            val = r.get("v")
            if val is None:
                continue
            text = str(val).strip()
            if text and text not in out:
                out.append(text[:40])
        return out
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return []


def build_compact_schema(
    conn: psycopg.Connection,
    table_names: list[str],
    *,
    question: str = "",
    include_sample_values: bool = True,
) -> str:
    """M-Schema 스타일 compact schema (설명·동의어·PK/FK·샘플값)."""
    if not table_names:
        return "(관련 테이블을 찾지 못했습니다)"

    parts: list[str] = []
    for table_name in table_names:
        meta = conn.execute(
            """
            SELECT display_name, description, category
            FROM table_metadata
            WHERE schema_name = 'public' AND table_name = %s
            """,
            (table_name,),
        ).fetchone()

        geom = conn.execute(
            """
            SELECT type, srid, f_geometry_column
            FROM geometry_columns
            WHERE f_table_schema = 'public' AND f_table_name = %s
            """,
            (table_name,),
        ).fetchone()

        cols = conn.execute(
            """
            SELECT
                c.column_name,
                c.udt_name,
                cm.display_name,
                cm.description,
                cm.unit
            FROM information_schema.columns c
            LEFT JOIN column_metadata cm
              ON cm.schema_name = c.table_schema
             AND cm.table_name = c.table_name
             AND cm.column_name = c.column_name
            WHERE c.table_schema = 'public' AND c.table_name = %s
            ORDER BY c.ordinal_position
            """,
            (table_name,),
        ).fetchall()

        pk_cols = set(_fetch_pk_columns(conn, table_name))
        fk_lines = _fetch_fk_lines(conn, table_name)
        sample_cols = set(_sample_columns(table_name, cols))
        samples: dict[str, list[str]] = {}
        if include_sample_values:
            for col_name in _sample_columns(table_name, cols):
                samples[col_name] = _fetch_sample_values(conn, table_name, col_name)

        header = f'# Table: "{table_name}"'
        display_name = ""
        description = ""
        category = ""
        if meta:
            display_name = str(meta.get("display_name") or "")
            description = str(meta.get("description") or "")
            category = str(meta.get("category") or "")
            header += (
                f"\n  [display_name={display_name}; "
                "DO NOT use display_name as SQL table id]"
            )
            if category:
                header += f"\n  [category={category}]"
            if description:
                header += f"\n  [desc] {_short_desc(description, 100)}"
        syn_t = format_synonyms(
            table_synonyms(
                table_name,
                display_name=display_name,
                description=description,
                category=category,
            )
        )
        if syn_t:
            header += f"\n  [synonyms] {syn_t}"
        if pk_cols:
            header += "\n  [primary_key] " + ", ".join(f'"{c}"' for c in pk_cols)
        if geom:
            header += (
                f'\n  [geometry] "{geom["f_geometry_column"]}" '
                f"{geom['type']}, SRID={geom['srid']}"
            )

        col_lines: list[str] = []
        for col in cols:
            if col["udt_name"] in ("geometry", "geography"):
                continue
            cname = col["column_name"]
            flags: list[str] = []
            if cname in pk_cols:
                flags.append("PK")
            line = f'  - "{cname}" ({col["udt_name"]}'
            if flags:
                line += ", " + ",".join(flags)
            line += ")"
            if col.get("display_name"):
                line += f" | {col['display_name']}"
            extras: list[str] = []
            short = _short_desc(col.get("description"))
            if short:
                extras.append(short)
            if col.get("unit"):
                extras.append(f"unit={col['unit']}")
            syn_c = format_synonyms(
                column_synonyms(
                    table_name,
                    cname,
                    display_name=str(col.get("display_name") or ""),
                )
            )
            if syn_c:
                extras.append(f"synonyms={syn_c}")
            if cname in sample_cols and samples.get(cname):
                shown = ", ".join(samples[cname][:4])
                extras.append(f"samples=[{shown}]")
            if extras:
                line += " | " + "; ".join(extras)
            col_lines.append(line)

        block = header + "\n  [columns]\n" + "\n".join(col_lines)
        if fk_lines:
            block += "\n" + "\n".join(fk_lines)
        parts.append(block)

    tips = (
        "\n\nRules for columns:\n"
        "- Prefer Korean name columns for filters (e.g. \"A9\" 건축물용도명 over \"A8\" code).\n"
        "- Quote all identifiers. Do not SELECT geometry unless asked. Never SELECT *.\n"
        "- Always include LIMIT.\n"
        "- District/dong name filters: use LIKE '%이름%'.\n"
        "- Use synonyms above for schema linking (e.g. 인구/구/동 → matching columns).\n"
    )
    spatial_intent = any(
        k in question
        for k in ("안에", "내부", "속하는", "교차", "버퍼", "거리", "근처", "이내", "주변", "반경")
    )
    if spatial_intent:
        tips += (
            "- SPATIAL INTENT DETECTED: use ST_Intersects with "
            '"BND_ADM_DONG_PG" or "TL_KODIS_BAS_26_202507"; do not rely on attribute-only filters.\n'
            "- Dong buffer (주변/반경 N m) → ST_Union of matching BND_ADM_DONG_PG "
            "and ST_DWithin(...::geography, meters).\n"
        )
    return "\n\n".join(parts) + tips


def retrieve_schema(
    conn: psycopg.Connection,
    question: str,
    *,
    embed_model: str,
    host: str | None = None,
    client: Any | None = None,
    top_k: int = 5,
    include_sample_values: bool = True,
) -> dict[str, Any]:
    tables = search_catalog_tables(
        conn,
        question,
        embed_model=embed_model,
        host=host,
        client=client,
        top_k=top_k,
    )
    tables = apply_label_boost(conn, question, tables)
    tables, exact_named = apply_exact_display_pin(conn, question, tables)
    tables = apply_admin_boost(
        question, tables, named_dataset_pin=bool(exact_named)
    )
    extra_tips = ""
    if exact_named:
        tables, pin_tip = pin_named_dataset_schema(question, tables, exact_named)
        extra_tips += pin_tip

    # 건물 속성 질의: 기본은 AL_D010. D198은 등록된 구·건축년수·주요용도명.
    from txt2sql.domain import D198_BY_GU, D198_TABLES, d198_gu_mentioned, d198_gus_mentioned

    building_hints = ("건물", "건축", "연면적", "용도", "공동주택", "아파트", "주택")
    needs_d198 = (
        d198_gu_mentioned(question) is not None
        or "용도별건물" in question
        or "주요용도" in question
        or any(
            k in question
            for k in (
                "건축년",
                "준공",
                "사용승인",
                "허가일",
                "지어진",
                "경과년",
            )
        )
    )
    # 표시명으로 다른 데이터셋이 고정된 질문은 건물/D198 스키마를 끼워 넣지 않음
    allow_building_boost = not exact_named or any(
        t.startswith("AL_D010") or t.startswith("AL_D198") for t in exact_named
    )
    if allow_building_boost and any(h in question for h in building_hints):
        if "AL_D010_26_20250704" not in tables:
            tables.append("AL_D010_26_20250704")
        if needs_d198:
            for t in D198_TABLES:
                if t not in tables:
                    tables.append(t)
        else:
            # 미등록 구 질의에서 D198로 빠지지 않게 제거
            tables = [t for t in tables if not t.startswith("AL_D198_")]

    if "산업단지" in question:
        if "AL_D060_00_20250804" not in tables:
            tables.append("AL_D060_00_20250804")
        # 산업단지 전용 질의에서는 건물 테이블을 스키마에서 제외해 혼동 방지
        if "건물" not in question:
            tables = [
                t
                for t in tables
                if not t.startswith("AL_D010") and not t.startswith("AL_D198")
            ]
            if "AL_D060_00_20250804" not in tables:
                tables.insert(0, "AL_D060_00_20250804")

    # 구별 전용 테이블 우선 + 스키마 힌트
    mentioned = d198_gus_mentioned(question)
    if mentioned and allow_building_boost:
        keep = {D198_BY_GU[gu] for gu in mentioned if D198_BY_GU.get(gu)}
        for gu in reversed(mentioned):
            table = D198_BY_GU.get(gu)
            if not table:
                continue
            tables = [table] + [t for t in tables if t != table]
            extra_tips += (
                f'\n- For {gu} use table "{table}" '
                '(연면적 column "A19") or "AL_D010_26_20250704" ("A14").\n'
            )
            if "주요용도" in question:
                extra_tips += (
                    f'\n- "주요용도명" for {gu} MUST use '
                    f'"{table}"."A25" (not AL_D010 "A9").\n'
                )
        if "주요용도" in question:
            tables = [t for t in tables if not t.startswith("AL_D010")]
        if len(keep) == 1:
            only = next(iter(keep))
            tables = [t for t in tables if not t.startswith("AL_D198_") or t == only]

    schema_text = (
        build_compact_schema(
            conn,
            tables,
            question=question,
            include_sample_values=include_sample_values,
        )
        + extra_tips
    )
    return {"tables": tables, "schema_text": schema_text}


def build_catalog_summary(
    conn: psycopg.Connection,
    table_name: str,
) -> tuple[str, str]:
    """llm_schema_catalog용 summary / summary_kw 생성."""
    meta = conn.execute(
        """
        SELECT display_name, description, category
        FROM table_metadata
        WHERE table_name = %s
        """,
        (table_name,),
    ).fetchone()
    geom = conn.execute(
        """
        SELECT type, srid, f_geometry_column
        FROM geometry_columns
        WHERE f_table_schema = 'public' AND f_table_name = %s
        """,
        (table_name,),
    ).fetchone()
    cols = conn.execute(
        """
        SELECT c.column_name, c.udt_name, cm.display_name
        FROM information_schema.columns c
        LEFT JOIN column_metadata cm
          ON cm.table_name = c.table_name AND cm.column_name = c.column_name
        WHERE c.table_schema = 'public' AND c.table_name = %s
        ORDER BY c.ordinal_position
        """,
        (table_name,),
    ).fetchall()
    cnt = conn.execute(
        """
        SELECT reltuples::bigint AS n
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = %s
        """,
        (table_name,),
    ).fetchone()

    display = (meta or {}).get("display_name") or table_name
    category = (meta or {}).get("category") or ""
    description = _short_desc((meta or {}).get("description"), 120)
    search_tokens = distinctive_label_tokens(display, description, category)

    lines = [
        f"[테이블] public.{table_name}",
        f"- 표시명:{display} | 분류:{category}",
    ]
    if search_tokens:
        lines.append("- 검색어: " + ", ".join(search_tokens[:10]))
    if description:
        lines.append(f"- {description}")
    syns = table_synonyms(
        table_name,
        display_name=str(display),
        description=description,
        category=str(category),
    )
    if syns:
        lines.append("- 동의어: " + ", ".join(syns[:10]))
    if geom:
        lines.append(
            f"- geom={geom['type']}, SRID={geom['srid']}, col={geom['f_geometry_column']}"
        )
    if cnt:
        lines.append(f"- rows≈{cnt['n']}")

    # 핵심 한글 컬럼명만 (임베딩 컨텍스트 절약)
    labels = [
        col["display_name"]
        for col in cols
        if col.get("display_name") and col["udt_name"] not in ("geometry", "geography")
    ][:12]
    if labels:
        lines.append("- 주요컬럼: " + ", ".join(labels))

    summary = "\n".join(lines)
    kw_parts = [table_name, display, category, *search_tokens[:8], *labels[:8]]
    summary_kw = " ".join(p for p in kw_parts if p)[:200]
    return summary, summary_kw


def upsert_catalog_embedding(
    conn: psycopg.Connection,
    table_name: str,
    *,
    embed_model: str,
    host: str,
) -> None:
    summary, summary_kw = build_catalog_summary(conn, table_name)
    text = f"{summary}\n{summary_kw}"
    emb = embed_text(text, model=embed_model, host=host)
    lit = vector_literal(emb)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    fqname = f"public.{table_name}"
    conn.execute(
        """
        INSERT INTO llm_schema_catalog (fqname, kind, summary, summary_kw, embedding, summary_hash, updated_at)
        VALUES (%s, 'table', %s, %s, %s::vector, %s, NOW())
        ON CONFLICT (fqname) DO UPDATE SET
          kind = EXCLUDED.kind,
          summary = EXCLUDED.summary,
          summary_kw = EXCLUDED.summary_kw,
          embedding = EXCLUDED.embedding,
          summary_hash = EXCLUDED.summary_hash,
          updated_at = NOW()
        """,
        (fqname, summary, summary_kw, lit, digest),
    )
