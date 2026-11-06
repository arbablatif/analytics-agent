"""Layers 2 and 3 of the guardrails: a locked-down connection and a bounded executor.

Layer 2: the database is opened read-only, external access (files, network, extensions)
is switched off, and the configuration is locked so a query cannot switch it back on.
Layer 3: every query runs with a timeout and a row limit.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pandas as pd

from .config import (DB_PATH, DUCKDB_MEMORY_LIMIT, DUCKDB_THREADS, MAX_ROWS_RETURNED,
                     QUERY_TIMEOUT_SECONDS)
from .sql_guard import SQLBlocked, validate_sql


@dataclass
class QueryResult:
    """What happened to one query. `status` is ok, blocked, error or timeout."""
    sql: str
    status: str
    df: pd.DataFrame = field(default_factory=pd.DataFrame)
    error: str = ""
    truncated: bool = False
    elapsed_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def row_count(self) -> int:
        return len(self.df)


class ReadOnlyDB:
    def __init__(self, path: Path = DB_PATH, timeout: float = QUERY_TIMEOUT_SECONDS,
                 max_rows: int = MAX_ROWS_RETURNED):
        if not Path(path).exists():
            raise FileNotFoundError(f"{path} not found. Run: python scripts/build_db.py")
        self.timeout = timeout
        self.max_rows = max_rows
        self.con = duckdb.connect(str(path), read_only=True, config={
            "enable_external_access": "false",
            "autoinstall_known_extensions": "false",
            "autoload_known_extensions": "false",
            "memory_limit": DUCKDB_MEMORY_LIMIT,
            "threads": DUCKDB_THREADS,
        })
        # DuckDB shares one instance per file within a process, so a second connection
        # (a Streamlit rerun, for example) finds the lock already on and may not set it.
        locked = self.con.execute("SELECT current_setting('lock_configuration')").fetchone()[0]
        if not locked:
            self.con.execute("SET lock_configuration = true")

    def run(self, sql: str) -> QueryResult:
        """Validate, then execute with the timeout and row limit. Never raises for bad SQL."""
        try:
            clean = validate_sql(sql)
        except SQLBlocked as exc:
            return QueryResult(sql=sql, status="blocked", error=str(exc))

        # Ask for one row beyond the limit so that truncation can be detected. The
        # newline stops a trailing line comment in the model's SQL from eating the ")".
        bounded = f"SELECT * FROM (\n{clean}\n) AS bounded_result LIMIT {self.max_rows + 1}"
        cursor = self.con.cursor()  # own cursor: interrupt() then affects only this query
        outcome: dict = {}

        def work() -> None:
            try:
                outcome["df"] = cursor.execute(bounded).df()
            except Exception as exc:  # any database error goes back to the agent as text
                outcome["error"] = str(exc)

        start = time.perf_counter()
        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        worker.join(self.timeout)
        timed_out = worker.is_alive()
        if timed_out:
            cursor.interrupt()  # DuckDB has no statement timeout, so a watchdog enforces it
            worker.join(5)
        elapsed = int((time.perf_counter() - start) * 1000)
        cursor.close()

        if timed_out:
            return QueryResult(sql=clean, status="timeout", elapsed_ms=elapsed,
                               error=f"Query exceeded the {self.timeout:g}s limit and was cancelled.")
        if "error" in outcome:
            return QueryResult(sql=clean, status="error", error=outcome["error"][:600], elapsed_ms=elapsed)
        df = outcome["df"]
        truncated = len(df) > self.max_rows
        return QueryResult(sql=clean, status="ok", df=df.head(self.max_rows), truncated=truncated,
                           elapsed_ms=elapsed)

    def close(self) -> None:
        self.con.close()
