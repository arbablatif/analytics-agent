"""Central settings: paths, guardrail limits and the fixed analysis date."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "olist.duckdb"
CACHE_DIR = ROOT / ".cache" / "llm"
SEMANTIC_DIR = ROOT / "semantic"
RESULTS_DIR = ROOT / "results"
REPORTS_DIR = ROOT / "reports"

# The dataset is historical, so "today" is pinned to where the data ends. Without
# this, "last quarter" would mean a quarter with no data at all.
AS_OF_DATE = "2018-08-31"
# Last week before order volume tails off (daily orders collapse from ~23 Aug 2018).
DEFAULT_REPORT_WEEK = "2018-08-13"

# ---- Guardrails -----------------------------------------------------------
QUERY_TIMEOUT_SECONDS = 10
MAX_ROWS_RETURNED = 1000   # rows the app keeps from any one query
MAX_ROWS_TO_LLM = 50       # rows the model sees (keeps prompts small and cheap)
DUCKDB_MEMORY_LIMIT = "1GB"
# One thread on purpose. With four, an unordered GROUP BY returned its rows in 13 different
# orders across 25 runs. The rows go into the prompt, so that changed the LLM cache key and
# turned cache hits into paid calls. The largest query here still takes about 135 ms.
DUCKDB_THREADS = 1

# ---- Agent ----------------------------------------------------------------
MAX_LLM_CALLS_PER_QUESTION = 6   # hard cap on the agent's API loop

# The only tables a query may touch. Anything else (files, catalogs) is rejected.
ALLOWED_TABLES = frozenset({
    "orders", "order_items", "order_payments", "order_reviews", "customers",
    "products", "sellers", "category_translation", "state_regions",
})
