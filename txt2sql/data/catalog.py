"""공간 테이블 목록·구조·메타데이터 (llm2_geodb database_manager 대응)."""

from __future__ import annotations

from typing import Any

from psycopg import sql

from txt2sql.config import Settings, database_url_for
from txt2sql.data.names import (
    create_column_description,
    extract_display_name_and_unit,
    is_geometry_column,
    is_protected_table,
    is_safe_ident,
    parse_al_table_name,
    split_schema_table,
)
from txt2sql.db import connect


def list_spatial_tables(settings: Settings) -> list[dict[str, str]]:
    schema = settings.map_schema or "public"
    if not is_safe_ident(schema):
        return []
    try:
        with connect(database_url_for(settings, "admin")) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        """
                        SELECT
                            c.table_schema,
                            c.table_name,
                            MAX(tm.display_name) AS display_name
                        FROM information_schema.columns c
                        LEFT JOIN table_metadata tm
                          ON tm.schema_name = c.table_schema
                         AND tm.table_name = c.table_name
                        WHERE c.table_schema = %s
                          AND c.udt_name IN ('geometry', 'geography', 'raster')
                          AND c.table_name NOT LIKE 'temp_%%'
                        GROUP BY c.table_schema, c.table_name
                        ORDER BY c.table_name
                        """,
                        (schema,),
                    )
                except Exception:
                    conn.rollback()
                    cur.execute(
                        """
                        SELECT c.table_schema, c.table_name, NULL AS display_name
                        FROM information_schema.columns c
                        WHERE c.table_schema = %s
                          AND c.udt_name IN ('geometry', 'geography', 'raster')
                          AND c.table_name NOT LIKE 'temp_%%'
                        GROUP BY c.table_schema, c.table_name
                        ORDER BY c.table_name
                        """,
                        (schema,),
                    )
                rows = cur.fetchall()
    except Exception:
        return []
    out: list[dict[str, str]] = []
    for row in rows:
        table = str(row["table_name"])
        sch = str(row["table_schema"])
        display = str(row.get("display_name") or "").strip() or table
        if is_protected_table(table):
            continue
        out.append(
            {
                "schema": sch,
                "table_name": table,
                "full_name": f"{sch}.{table}",
                "display_name": display,
            }
        )
    return out


def get_table_structure(settings: Settings, table_name: str) -> list[dict[str, Any]]:
    schema, table = split_schema_table(table_name, settings.map_schema or "public")
    with connect(database_url_for(settings, "admin")) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    column_name,
                    data_type,
                    udt_name,
                    is_nullable,
                    column_default,
                    character_maximum_length,
                    numeric_precision,
                    numeric_scale
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY ordinal_position
                """,
                (schema, table),
            )
            rows = cur.fetchall()
    return [
        {
            "column_name": row["column_name"],
            "data_type": row["udt_name"] or row["data_type"],
            "is_nullable": row["is_nullable"] == "YES",
            "column_default": row["column_default"],
            "character_maximum_length": row["character_maximum_length"],
            "numeric_precision": row["numeric_precision"],
            "numeric_scale": row["numeric_scale"],
        }
        for row in rows
    ]


def get_database_comments(settings: Settings, table_name: str) -> dict[str, Any]:
    schema, table = split_schema_table(table_name, settings.map_schema or "public")
    with connect(database_url_for(settings, "admin")) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT obj_description(c.oid) AS table_comment
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = %s AND c.relname = %s
                """,
                (schema, table),
            )
            table_row = cur.fetchone()
            cur.execute(
                """
                SELECT a.attname AS column_name,
                       col_description(a.attrelid, a.attnum) AS column_comment
                FROM pg_attribute a
                JOIN pg_class c ON c.oid = a.attrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = %s
                  AND c.relname = %s
                  AND a.attnum > 0
                  AND NOT a.attisdropped
                ORDER BY a.attnum
                """,
                (schema, table),
            )
            cols = cur.fetchall()
    comments: dict[str, str] = {}
    for row in cols:
        text = row.get("column_comment")
        if text:
            comments[str(row["column_name"])] = str(text)
    table_comment = None
    if table_row and table_row.get("table_comment"):
        table_comment = str(table_row["table_comment"])
    return {"table_comment": table_comment, "column_comments": comments}


def get_table_metadata(settings: Settings, table_name: str) -> dict[str, Any]:
    schema, table = split_schema_table(table_name, settings.map_schema or "public")
    result: dict[str, Any] = {"table_metadata": None, "column_metadata": {}}
    try:
        with connect(database_url_for(settings, "admin")) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT display_name, description, category, created_at, updated_at
                    FROM table_metadata
                    WHERE schema_name = %s AND table_name = %s
                    """,
                    (schema, table),
                )
                table_meta = cur.fetchone()
                cur.execute(
                    """
                    SELECT column_name, display_name, description, data_type, unit
                    FROM column_metadata
                    WHERE schema_name = %s AND table_name = %s
                    ORDER BY column_name
                    """,
                    (schema, table),
                )
                columns = cur.fetchall()
    except Exception:
        return result
    if table_meta:
        result["table_metadata"] = {
            "display_name": table_meta.get("display_name") or "",
            "description": table_meta.get("description") or "",
            "category": table_meta.get("category") or "",
            "created_at": _iso(table_meta.get("created_at")),
            "updated_at": _iso(table_meta.get("updated_at")),
        }
    for row in columns:
        result["column_metadata"][str(row["column_name"])] = {
            "display_name": row.get("display_name") or "",
            "description": row.get("description") or "",
            "data_type": row.get("data_type") or "",
            "unit": row.get("unit") or "",
        }
    return result


def get_table_display_name(settings: Settings, table_name: str) -> str:
    schema, table = split_schema_table(table_name, settings.map_schema or "public")
    try:
        with connect(database_url_for(settings, "admin")) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT display_name
                    FROM table_metadata
                    WHERE schema_name = %s AND table_name = %s
                    """,
                    (schema, table),
                )
                row = cur.fetchone()
    except Exception:
        return table
    if row and row.get("display_name"):
        return str(row["display_name"])
    return table


def update_table_metadata(
    settings: Settings,
    table_name: str,
    table_metadata: dict[str, Any],
    column_metadata: dict[str, Any],
) -> None:
    schema, table = split_schema_table(table_name, settings.map_schema or "public")
    with connect(database_url_for(settings, "admin")) as conn:
        with conn.cursor() as cur:
            _ensure_metadata_tables(cur)
            cur.execute(
                """
                SELECT 1 FROM table_metadata
                WHERE schema_name = %s AND table_name = %s
                """,
                (schema, table),
            )
            exists = cur.fetchone() is not None
            values = (
                table_metadata.get("display_name") or "",
                table_metadata.get("description") or "",
                table_metadata.get("category") or "",
            )
            if exists:
                cur.execute(
                    """
                    UPDATE table_metadata
                    SET display_name = %s,
                        description = %s,
                        category = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE schema_name = %s AND table_name = %s
                    """,
                    (*values, schema, table),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO table_metadata
                        (schema_name, table_name, display_name, description, category, updated_at)
                    VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    """,
                    (schema, table, *values),
                )
            for column_name, meta in (column_metadata or {}).items():
                if not is_safe_ident(str(column_name)):
                    raise ValueError(f"허용되지 않은 컬럼명입니다: {column_name}")
                payload = (
                    meta.get("display_name") or "",
                    meta.get("description") or "",
                    meta.get("data_type") or "",
                    meta.get("unit") or "",
                )
                cur.execute(
                    """
                    SELECT 1 FROM column_metadata
                    WHERE schema_name = %s AND table_name = %s AND column_name = %s
                    """,
                    (schema, table, column_name),
                )
                if cur.fetchone():
                    cur.execute(
                        """
                        UPDATE column_metadata
                        SET display_name = %s,
                            description = %s,
                            data_type = %s,
                            unit = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE schema_name = %s AND table_name = %s AND column_name = %s
                        """,
                        (*payload, schema, table, column_name),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO column_metadata
                            (schema_name, table_name, column_name, display_name,
                             description, data_type, unit, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                        """,
                        (schema, table, column_name, *payload),
                    )
            _update_comments(cur, schema, table, table_metadata, column_metadata)
        conn.commit()


def rename_table(settings: Settings, old_name: str, new_name: str) -> str:
    schema, old_table = split_schema_table(old_name, settings.map_schema or "public")
    _, new_table = split_schema_table(new_name, schema)
    if old_table == new_table:
        return f"{schema}.{new_table}"
    with connect(database_url_for(settings, "admin")) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = %s AND table_name = %s
                ) AS present
                """,
                (schema, old_table),
            )
            if not cur.fetchone()["present"]:
                raise ValueError("원본 테이블이 없습니다.")
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = %s AND table_name = %s
                ) AS present
                """,
                (schema, new_table),
            )
            if cur.fetchone()["present"]:
                raise ValueError("같은 이름의 테이블이 이미 있습니다.")
            cur.execute(
                sql.SQL("ALTER TABLE {}.{} RENAME TO {}").format(
                    sql.Identifier(schema),
                    sql.Identifier(old_table),
                    sql.Identifier(new_table),
                )
            )
            cur.execute(
                """
                UPDATE table_metadata
                SET table_name = %s, updated_at = CURRENT_TIMESTAMP
                WHERE schema_name = %s AND table_name = %s
                """,
                (new_table, schema, old_table),
            )
            cur.execute(
                """
                UPDATE column_metadata
                SET table_name = %s, updated_at = CURRENT_TIMESTAMP
                WHERE schema_name = %s AND table_name = %s
                """,
                (new_table, schema, old_table),
            )
        conn.commit()
    return f"{schema}.{new_table}"


def parse_table_code(settings: Settings, table_name: str) -> dict[str, Any] | None:
    schema, table = split_schema_table(table_name, settings.map_schema or "public")
    parsed = parse_al_table_name(table)
    if parsed is None:
        return None
    data_code = parsed["data_code"]
    pnu_code = parsed["pnu_code"]
    dataset_name = None
    pnu_name = None
    with connect(database_url_for(settings, "admin")) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "SELECT dataset_nm FROM col_def WHERE f_name = %s",
                    (data_code,),
                )
                row = cur.fetchone()
                if row:
                    dataset_name = row.get("dataset_nm") or list(row.values())[0]
            except Exception:
                conn.rollback()
            try:
                pnu_full = pnu_code + "0" * max(0, 10 - len(pnu_code))
                cur.execute(
                    'SELECT "PNU_NM" FROM pnu_def WHERE "PNU" = %s',
                    (pnu_full,),
                )
                row = cur.fetchone()
                if row:
                    pnu_name = row.get("PNU_NM") or list(row.values())[0]
            except Exception:
                conn.rollback()
            columns = _column_metadata_from_col_def(
                settings, cur, data_code, f"{schema}.{table}"
            )
    display = table
    if dataset_name and pnu_name:
        display = f"{dataset_name}_{pnu_name}"
    elif dataset_name:
        display = str(dataset_name)
    elif pnu_name:
        display = f"데이터_{pnu_name}"
    desc_parts: list[str] = []
    if dataset_name:
        desc_parts.append(str(dataset_name))
    if pnu_name:
        desc_parts.append(str(pnu_name))
    if parsed["formatted_date"]:
        desc_parts.append(f"{parsed['formatted_date']} 자료 갱신")
    elif parsed["update_date"]:
        desc_parts.append(f"{parsed['update_date']} 자료 갱신")
    return {
        "table_name": table,
        "data_code": data_code,
        "pnu_code": pnu_code,
        "update_date": parsed["update_date"],
        "dataset_name": dataset_name,
        "pnu_name": pnu_name,
        "formatted_date": parsed["formatted_date"],
        "display_name": display,
        "description": ", ".join(desc_parts) if desc_parts else f"{table} (코드 해석 불가)",
        "column_metadata": columns,
    }


def _column_metadata_from_col_def(
    settings: Settings,
    cur: Any,
    data_code: str,
    table_name: str,
) -> dict[str, Any]:
    try:
        structure = get_table_structure(settings, table_name)
    except Exception:
        return {}
    out: dict[str, Any] = {}
    for column in structure:
        name = str(column["column_name"])
        if is_geometry_column(name, str(column.get("data_type") or "")):
            continue
        try:
            cur.execute(
                """
                SELECT col_kor_nm, sample, etc
                FROM col_def
                WHERE col_eng_nm = %s AND f_name = %s
                """,
                (name, data_code),
            )
            row = cur.fetchone()
        except Exception:
            row = None
        if row:
            kor = row.get("col_kor_nm") or ""
            display, unit = extract_display_name_and_unit(str(kor))
            out[name] = {
                "display_name": display,
                "description": create_column_description(
                    row.get("sample"), row.get("etc")
                ),
                "unit": unit,
            }
        else:
            out[name] = {"display_name": "", "description": "", "unit": ""}
    return out


def _ensure_metadata_tables(cur: Any) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS table_metadata (
            id SERIAL PRIMARY KEY,
            schema_name VARCHAR(255) NOT NULL,
            table_name VARCHAR(255) NOT NULL,
            display_name VARCHAR(255),
            description TEXT,
            category VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(schema_name, table_name)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS column_metadata (
            id SERIAL PRIMARY KEY,
            schema_name VARCHAR(255) NOT NULL,
            table_name VARCHAR(255) NOT NULL,
            column_name VARCHAR(255) NOT NULL,
            display_name VARCHAR(255),
            description TEXT,
            data_type VARCHAR(255),
            unit VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(schema_name, table_name, column_name)
        )
        """
    )


def _update_comments(
    cur: Any,
    schema: str,
    table: str,
    table_metadata: dict[str, Any],
    column_metadata: dict[str, Any],
) -> None:
    desc = (table_metadata.get("description") or "").strip()
    if desc:
        cur.execute(
            sql.SQL("COMMENT ON TABLE {}.{} IS {}").format(
                sql.Identifier(schema),
                sql.Identifier(table),
                sql.Literal(desc),
            )
        )
    for column_name, meta in (column_metadata or {}).items():
        if not is_safe_ident(str(column_name)):
            continue
        comment = (meta.get("description") or "").strip()
        unit = (meta.get("unit") or "").strip()
        if unit:
            comment = f"{comment} (단위: {unit})" if comment else f"단위: {unit}"
        if not comment:
            continue
        cur.execute(
            sql.SQL("COMMENT ON COLUMN {}.{}.{} IS {}").format(
                sql.Identifier(schema),
                sql.Identifier(table),
                sql.Identifier(column_name),
                sql.Literal(comment),
            )
        )


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
