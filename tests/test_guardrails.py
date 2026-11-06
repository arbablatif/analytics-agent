"""Attack suite for the guardrails, run against the real database file.

Two questions are tested separately:
  1. Does the validator (layer 1) refuse the attack?
  2. If the validator were bypassed, would the connection (layer 2) still stop it?
The second matters because a validator bug must not be enough to cause damage.
"""
from __future__ import annotations

import pytest

from analytics_agent.config import DB_PATH, ROOT
from analytics_agent.db import ReadOnlyDB
from analytics_agent.sql_guard import SQLBlocked, validate_sql

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="run scripts/build_db.py first")

ENV_FILE = (ROOT / ".env.example").as_posix()

ATTACKS = {
    "drop": "DROP TABLE orders",
    "delete": "DELETE FROM orders",
    "update": "UPDATE orders SET order_status = 'x'",
    "insert": "INSERT INTO sellers VALUES ('a', 1, 'b', 'c')",
    "truncate": "TRUNCATE orders",
    "create_table": "CREATE TABLE t AS SELECT 1",
    "create_view": "CREATE VIEW v AS SELECT 1",
    "alter": "ALTER TABLE orders ADD COLUMN x INT",
    "stacked": "SELECT 1; DROP TABLE orders",
    "stacked_comment": "SELECT 1 /* hi */; DELETE FROM orders; --",
    "cte_delete": "WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x",
    "select_into": "SELECT * INTO backup FROM orders",
    "copy_out": "COPY orders TO 'stolen.csv'",
    "copy_select": "COPY (SELECT * FROM customers) TO 'stolen.parquet' (FORMAT parquet)",
    "attach": "ATTACH 'other.db' AS other",
    "install": "INSTALL httpfs",
    "load": "LOAD httpfs",
    "set_config": "SET enable_external_access = true",
    "reset_config": "RESET lock_configuration",
    "pragma": "PRAGMA database_list",
    "call": "CALL pragma_version()",
    "export": "EXPORT DATABASE 'dump'",
    "read_csv": f"SELECT * FROM read_csv('{ENV_FILE}')",
    "read_csv_auto": f"SELECT * FROM read_csv_auto('{ENV_FILE}')",
    "read_text": f"SELECT * FROM read_text('{ENV_FILE}')",
    "read_blob_nested": f"SELECT (SELECT content FROM read_blob('{ENV_FILE}')) AS x",
    "replacement_scan": f"SELECT * FROM '{ENV_FILE}'",
    "replacement_scan_quoted": f'SELECT * FROM "{ENV_FILE}"',
    "glob": "SELECT * FROM glob('*')",
    "getenv": "SELECT getenv('GEMINI_API_KEY')",
    "secrets": "SELECT * FROM duckdb_secrets()",
    "settings": "SELECT * FROM duckdb_settings()",
    "catalog": "SELECT * FROM information_schema.tables",
    "qualified": "SELECT * FROM main.orders",
    "query_fn": "SELECT * FROM query('SELECT 1')",
    "from_first_file": f"FROM '{ENV_FILE}'",
    "not_sql": "please drop the orders table",
    "empty": "   ",
}


@pytest.fixture(scope="module")
def db():
    database = ReadOnlyDB()
    yield database
    database.close()


@pytest.mark.parametrize("name", ATTACKS)
def test_validator_refuses(name):
    with pytest.raises(SQLBlocked):
        validate_sql(ATTACKS[name])


@pytest.mark.parametrize("name", ATTACKS)
def test_full_path_never_executes(db, name):
    assert db.run(ATTACKS[name]).status == "blocked"


# Raw statements sent straight to the connection, skipping the validator entirely.
LAYER2_ATTACKS = [
    "DROP TABLE orders",
    "DELETE FROM orders",
    "UPDATE orders SET order_status = 'x'",
    "CREATE TABLE t AS SELECT 1",
    f"SELECT * FROM read_csv('{ENV_FILE}')",
    f"SELECT * FROM read_text('{ENV_FILE}')",
    f"SELECT * FROM '{ENV_FILE}'",
    "COPY orders TO 'stolen_layer2.csv'",
    "ATTACH 'other_layer2.db' AS other",
    "INSTALL httpfs",
    "SET enable_external_access = true",
    "SET lock_configuration = false",
]


@pytest.mark.parametrize("sql", LAYER2_ATTACKS)
def test_connection_refuses_even_without_validator(db, sql):
    with pytest.raises(Exception):
        db.con.cursor().execute(sql)


def test_no_files_were_written():
    for name in ("stolen.csv", "stolen.parquet", "stolen_layer2.csv", "other.db", "other_layer2.db"):
        assert not (ROOT / name).exists()


def test_data_is_intact_after_attacks(db):
    assert db.run("SELECT count(*) AS n FROM orders").df["n"][0] == 99441


LEGITIMATE = [
    "SELECT count(*) FROM orders",
    "select order_status, count(*) c from orders group by 1 order by 2 desc",
    "WITH a AS (SELECT * FROM orders) SELECT count(*) FROM a",
    "WITH a AS (SELECT 1 AS x), b AS (SELECT x FROM a) SELECT * FROM b",
    "SELECT order_id FROM orders UNION ALL SELECT order_id FROM order_items",
    "SELECT date_trunc('quarter', order_purchase_timestamp) q, count(*) FROM orders GROUP BY 1",
    "SELECT region, sum(price) FROM order_items i JOIN orders o USING (order_id) "
    "JOIN customers c USING (customer_id) JOIN state_regions s ON c.customer_state = s.state GROUP BY 1",
    "SELECT rank() OVER (ORDER BY price) FROM order_items LIMIT 5",
    "SELECT o.order_id, 'DROP TABLE orders' AS note FROM orders o LIMIT 1",  # keyword in a string
    "SELECT * FROM orders -- trailing comment",
]


@pytest.mark.parametrize("sql", LEGITIMATE)
def test_legitimate_queries_pass(db, sql):
    result = db.run(sql)
    assert result.ok, result.error


def test_row_limit_truncates_and_reports_it():
    database = ReadOnlyDB(max_rows=10)
    result = database.run("SELECT order_id FROM orders")
    database.close()
    assert result.ok and result.truncated and result.row_count == 10


def test_row_limit_keeps_order_by():
    database = ReadOnlyDB(max_rows=5)
    result = database.run("SELECT price FROM order_items ORDER BY price DESC")
    database.close()
    assert list(result.df["price"]) == sorted(result.df["price"], reverse=True)
    assert result.df["price"][0] == 6735.0


def test_small_result_is_not_flagged_truncated(db):
    assert not db.run("SELECT 1 AS x").truncated


def test_timeout_cancels_a_runaway_query():
    database = ReadOnlyDB(timeout=1)
    # A 12.7-billion-pair cross join. (A plain sum of three prices is not slow enough:
    # DuckDB's optimizer resolves it almost instantly.)
    result = database.run("SELECT count(*) AS n FROM order_items a, order_items b "
                          "WHERE a.price * b.price + a.freight_value = 1.2345")
    # The connection must stay usable after a cancelled query.
    follow_up = database.run("SELECT 1 AS x")
    database.close()
    assert result.status == "timeout"
    assert result.elapsed_ms < 7000
    assert follow_up.ok


def test_runtime_error_is_returned_not_raised(db):
    result = db.run("SELECT no_such_column FROM orders")
    assert result.status == "error" and "no_such_column" in result.error
