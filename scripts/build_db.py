"""Download the public Olist dataset and build data/olist.duckdb.

The CSVs come from Olist's own GitHub repository (MIT licence), pinned to one commit
so that every rebuild produces the same database and the evaluation stays valid.

    python scripts/build_db.py
"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analytics_agent.config import DB_PATH, RAW_DIR  # noqa: E402

COMMIT = "d9e49802f3e92d09ee94ab9ccc5e457f207a8959"
BASE_URL = f"https://raw.githubusercontent.com/olist/work-at-olist-data/{COMMIT}/datasets"

# table name -> (source file, expected row count)
SOURCES = {
    "orders": ("olist_orders_dataset.csv", 99441),
    "order_items": ("olist_order_items_dataset.csv", 112650),
    "order_payments": ("olist_order_payments_dataset.csv", 103886),
    "order_reviews": ("olist_order_reviews_dataset.csv", 99224),
    "customers": ("olist_customers_dataset.csv", 99441),
    "products": ("olist_products_dataset.csv", 32951),
    "sellers": ("olist_sellers_dataset.csv", 3095),
    "category_translation": ("product_category_name_translation.csv", 71),
}

# Olist only records states. Brazil's five official (IBGE) macro-regions are public
# knowledge and give the agent a real "region" dimension.
REGIONS = {
    "North": "AC AP AM PA RO RR TO",
    "Northeast": "AL BA CE MA PB PE PI RN SE",
    "Central-West": "DF GO MT MS",
    "Southeast": "ES MG RJ SP",
    "South": "PR RS SC",
}


def download(filename: str) -> Path:
    """Fetch one CSV into data/raw unless it is already there."""
    target = RAW_DIR / filename
    if not target.exists():
        print(f"  downloading {filename}")
        urllib.request.urlretrieve(f"{BASE_URL}/{filename}", target)
    return target


def build() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.unlink(missing_ok=True)

    con = duckdb.connect(str(DB_PATH))  # the only writable connection in the project
    for table, (filename, expected_rows) in SOURCES.items():
        path = download(filename).as_posix()
        # sample_size=-1 scans the whole file, so types are inferred from every row.
        con.execute(f"CREATE TABLE {table} AS "
                    f"SELECT * FROM read_csv('{path}', header=true, sample_size=-1)")
        rows = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]
        print(f"  {table:<22}{rows:>8,} rows  sha256 {digest}")
        if rows != expected_rows:
            raise SystemExit(f"{table}: expected {expected_rows} rows, got {rows}")

    con.execute("CREATE TABLE state_regions (state VARCHAR PRIMARY KEY, region VARCHAR NOT NULL)")
    con.executemany("INSERT INTO state_regions VALUES (?, ?)",
                    [(s, r) for r, states in REGIONS.items() for s in states.split()])
    con.close()
    print(f"Built {DB_PATH}")


if __name__ == "__main__":
    build()
