"""Layer 1 of the guardrails: static SQL validation before anything reaches the database.

The rule is an allow-list, not a block-list: a query passes only if it parses as ONE
read-only SELECT over known tables. If it cannot be parsed, it is refused (fail closed).
The read-only connection in db.py is a second, independent layer behind this one.
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from .config import ALLOWED_TABLES

# Statement types that must never appear anywhere in the tree, even nested in a CTE.
FORBIDDEN_NODES = tuple(getattr(exp, name) for name in (
    "Insert", "Update", "Delete", "Drop", "Create", "Alter", "TruncateTable", "Merge",
    "Copy", "Command", "Set", "Pragma", "Attach", "Detach", "Use", "LoadData", "Into",
    "Describe", "Show", "Transaction", "Commit", "Rollback", "Export", "Install",
) if hasattr(exp, name))

# Functions that reach outside the database or expose its configuration.
FORBIDDEN_FUNCTION_PREFIXES = (
    "read_", "scan_", "parquet_", "sniff_", "glob", "getenv", "duckdb_", "pragma_",
    "query", "sql_auto_complete", "which_secret", "load_", "install_", "write_",
    "iceberg_", "delta_", "sqlite_", "postgres_", "mysql_", "getvariable", "current_setting",
)


class SQLBlocked(ValueError):
    """The query was refused before execution. The message is shown to the model."""


def _function_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    return (node.sql_name() or "").lower()


def validate_sql(sql: str) -> str:
    """Return the cleaned SQL if it is a safe single SELECT, otherwise raise SQLBlocked."""
    if not sql or not sql.strip():
        raise SQLBlocked("Empty query.")
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except SqlglotError as exc:
        raise SQLBlocked(f"Could not parse the query, so it was refused: {str(exc)[:200]}") from exc
    if len(statements) != 1:
        raise SQLBlocked(f"Exactly one statement is allowed; found {len(statements)}.")

    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.SetOperation, exp.Subquery)):
        raise SQLBlocked(f"Only SELECT queries are allowed; got {type(tree).__name__.upper()}.")

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise SQLBlocked(f"{type(node).__name__.upper()} is not allowed. Queries are read-only.")
        if isinstance(node, exp.Func):
            name = _function_name(node)
            if name.startswith(FORBIDDEN_FUNCTION_PREFIXES):
                raise SQLBlocked(f"Function {name}() is not allowed.")

    # Names defined by WITH clauses are legal table references inside this query.
    cte_names = {cte.alias.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        # FROM read_csv(...) arrives as a Table wrapping a function, so reject that
        # first. After that only bare, unqualified, known names may pass.
        if isinstance(table.this, exp.Func):
            raise SQLBlocked(f"Table function {_function_name(table.this)}() is not allowed.")
        name = table.name.lower()
        if table.db or table.catalog:
            raise SQLBlocked(f"Schema-qualified table {table.sql()} is not allowed.")
        if name not in ALLOWED_TABLES and name not in cte_names:
            raise SQLBlocked(f"Unknown table '{table.name}'. Allowed: {', '.join(sorted(ALLOWED_TABLES))}.")
    return sql.strip().rstrip(";").strip()
