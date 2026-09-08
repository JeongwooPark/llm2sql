"""Compiler facade + SQL AST allowlist tests."""

from __future__ import annotations

import pytest

from txt2sql.compiler.expressions import quote_ident, quote_literal
from txt2sql.compiler.postgis import postgis_fn
from txt2sql.compiler.safety import validate_compiled_sql
from txt2sql.db import assert_readonly_sql
from txt2sql.planner.executor_adapter import build_execution_plan
from txt2sql.compiler.sql import compile_plan_safe


def test_quote_helpers() -> None:
    assert quote_ident("A16") == '"A16"'
    assert quote_literal("동래구") == "'동래구'"
    assert quote_literal(None) == "NULL"


def test_postgis_mapping() -> None:
    assert postgis_fn("within") == "ST_Within"


def test_compile_from_physical_or_error() -> None:
    bundle = build_execution_plan("동래구 건물 평균 연면적")
    bundle.logical.status = "READY"
    bundle.logical.reason_codes = []
    sql, err = compile_plan_safe(bundle.physical)
    # May succeed or fail depending on SQP compiler support; must not crash
    assert sql is not None or err is not None


def test_validate_readonly() -> None:
    validate_compiled_sql('SELECT COUNT(*) FROM "AL_D010_26_20250704"')


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; DROP TABLE x",
        "WITH t AS (DELETE FROM x RETURNING *) SELECT * FROM t",
        "WITH t AS (UPDATE x SET a=1 RETURNING *) SELECT * FROM t",
        "WITH t AS (INSERT INTO x VALUES (1) RETURNING *) SELECT * FROM t",
        "SELECT * INTO tmp FROM x",
        "SELECT * FROM x FOR UPDATE",
        "SELECT * FROM x FOR SHARE",
        "SeLeCt pg_sleep(1)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT * FROM pg_catalog.pg_user",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM temp_deadbeef01",
        "SELECT 1; SELECT 2",
        "/* comment */ DELETE FROM x",
    ],
)
def test_malicious_sql_rejected(sql: str) -> None:
    with pytest.raises(ValueError):
        assert_readonly_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "WITH t AS (SELECT 1 AS n) SELECT n FROM t",
        "SELECT COUNT(*) FROM buildings UNION ALL SELECT 1",
        "SELECT ST_Area(geom) FROM parcels",
        'SELECT AVG("A16") FROM "AL_D010_26_20250704" WHERE "A2" = \'동래구\'',
    ],
)
def test_benign_sql_allowed(sql: str) -> None:
    assert_readonly_sql(sql)
