"""Independent second derivation of every gold answer.

Each function below recomputes one answer from the raw CSV files using pandas only. It shares
no code with the gold SQL and never touches DuckDB, so a mistake in one route is unlikely to be
repeated in the other. A question passes when both routes return the same result and its
`expected` facts appear in that result.

    python -m eval.verify_gold        # writes results/gold_verification.md
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd
import yaml

from analytics_agent.config import RAW_DIR, RESULTS_DIR, ROOT
from analytics_agent.db import ReadOnlyDB
from eval.scorer import numbers_close, rows_match, rubric_met, rubrics

DATE_COLUMNS = ["order_purchase_timestamp", "order_delivered_customer_date", "order_estimated_delivery_date"]
REGIONS = {"North": "AC AP AM PA RO RR TO", "Northeast": "AL BA CE MA PB PE PI RN SE",
           "Central-West": "DF GO MT MS", "Southeast": "ES MG RJ SP", "South": "PR RS SC"}


class Data:
    """The raw tables, plus a few joined frames that several questions share."""

    def __init__(self) -> None:
        read = lambda name: pd.read_csv(RAW_DIR / f"olist_{name}_dataset.csv")  # noqa: E731
        self.orders = read("orders")
        for column in DATE_COLUMNS:
            self.orders[column] = pd.to_datetime(self.orders[column])
        self.items, self.customers = read("order_items"), read("customers")
        self.payments, self.reviews = read("order_payments"), read("order_reviews")
        self.products, self.sellers = read("products"), read("sellers")
        translation = pd.read_csv(RAW_DIR / "product_category_name_translation.csv")
        names = dict(zip(translation.iloc[:, 0], translation.iloc[:, 1]))

        self.products["category"] = self.products["product_category_name"].map(
            lambda c: names.get(c, c) if isinstance(c, str) else None)
        state_to_region = {s: r for r, states in REGIONS.items() for s in states.split()}
        self.customers["region"] = self.customers["customer_state"].map(state_to_region)

        valid = self.orders[~self.orders["order_status"].isin(["canceled", "unavailable"])]
        # sales: one row per item of a revenue-counting order, with customer and category.
        self.sales = (self.items.merge(valid, on="order_id")
                      .merge(self.customers, on="customer_id")
                      .merge(self.products[["product_id", "category"]], on="product_id"))
        # delivered: orders with a real delivery date, flagged late by calendar date.
        delivered = self.orders[(self.orders["order_status"] == "delivered")
                                & self.orders["order_delivered_customer_date"].notna()].copy()
        delivered["late"] = (delivered["order_delivered_customer_date"].dt.normalize()
                             > delivered["order_estimated_delivery_date"].dt.normalize())
        self.delivered = delivered.merge(self.customers, on="customer_id")
        self.review_per_order = self.reviews.groupby("order_id")["review_score"].mean().rename("score")


def between(frame: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    stamp = frame["order_purchase_timestamp"]
    return frame[(stamp >= start) & (stamp < end)]


def one(**values) -> pd.DataFrame:
    return pd.DataFrame([values])


def e01(d): return one(orders=len(d.orders))
def e02(d): return one(people=d.customers["customer_unique_id"].nunique())
def e03(d): return one(revenue=d.sales["price"].sum())
def e04(d): return one(avg_score=d.reviews["review_score"].mean())
def e05(d):
    counts = d.sellers["seller_state"].value_counts()
    return one(seller_state=counts.index[0], sellers=int(counts.iloc[0]))
def e06(d): return one(pct=100 * (d.orders["order_status"] == "canceled").mean())
def e07(d):
    counts = d.payments["payment_type"].value_counts()
    return one(payment_type=counts.index[0], payments=int(counts.iloc[0]))
def e08(d): return one(avg_freight=d.items["freight_value"].mean())
def e09(d): return one(products=int(d.products["product_category_name"].isna().sum()))
def e10(d): return one(orders=int((d.orders["order_purchase_timestamp"].dt.year == 2017).sum()))


def m01(d): return d.sales.groupby("region")["price"].sum().reset_index()
def m02(d): return d.sales.groupby("category")["price"].sum().nlargest(5).reset_index()
def m03(d):
    frame = between(d.sales, "2018-01-01", "2018-09-01")
    month = frame["order_purchase_timestamp"].dt.strftime("%Y-%m")
    return frame.groupby(month)["price"].sum().reset_index()
def m04(d):
    days = (d.delivered["order_delivered_customer_date"]
            - d.delivered["order_purchase_timestamp"]).dt.total_seconds() / 86400
    return days.groupby(d.delivered["region"]).mean().reset_index()
def m05(d):
    frame = d.orders.merge(d.customers, on="customer_id").join(d.review_per_order, on="order_id", how="inner")
    stats = frame.groupby("customer_state")["score"].agg(["mean", "size"])
    stats = stats[stats["size"] >= 1000]["mean"]
    return one(state=stats.idxmax(), avg_score=stats.max())
def m06(d):
    frame = d.delivered[d.delivered["order_purchase_timestamp"].dt.year == 2018]
    return one(pct=100 * frame["late"].mean())
def m07(d):
    top = d.sales.groupby("seller_id")["price"].sum().nlargest(5).reset_index()
    return top.merge(d.sellers[["seller_id", "seller_state"]], on="seller_id")[["seller_id", "seller_state", "price"]]
def m08(d):
    frame = between(d.sales, "2018-04-01", "2018-07-01")
    return one(aov=frame["price"].sum() / frame["order_id"].nunique())
def m09(d):
    per_person = d.customers["customer_unique_id"].value_counts()
    return one(pct=100 * (per_person > 1).mean())
def m10(d):
    return one(q1=between(d.sales, "2018-01-01", "2018-04-01")["price"].sum(),
               q2=between(d.sales, "2018-04-01", "2018-07-01")["price"].sum())
def m11(d):
    pairs = (d.items.merge(d.products[["product_id", "category"]], on="product_id")
             .dropna(subset=["category"])[["order_id", "category"]].drop_duplicates())
    scored = pairs.join(d.review_per_order, on="order_id", how="inner")
    stats = scored.groupby("category")["score"].agg(["mean", "size"])
    stats = stats[stats["size"] >= 500]["mean"]
    return one(category=stats.idxmin(), avg_score=stats.min())


def h01(d):
    rows = []
    for region in ("North", "Northeast"):
        for start, end in (("2018-01-01", "2018-04-01"), ("2018-04-01", "2018-07-01")):
            frame = between(d.sales[d.sales["region"] == region], start, end)
            rows.append({"region": region, "revenue": frame["price"].sum(), "orders": frame["order_id"].nunique()})
    return pd.DataFrame(rows)
def h02(d):
    q1, q2 = (between(d.delivered, a, b) for a, b in (("2018-01-01", "2018-04-01"), ("2018-04-01", "2018-07-01")))
    table = pd.DataFrame({"n1": q1.groupby("customer_state").size(), "n2": q2.groupby("customer_state").size(),
                          "l1": 100 * q1.groupby("customer_state")["late"].mean(),
                          "l2": 100 * q2.groupby("customer_state")["late"].mean()}).dropna()
    table = table[(table["n1"] >= 300) & (table["n2"] >= 300)]
    best = (table["l2"] - table["l1"]).idxmin()
    return one(state=best, late_q1=table.loc[best, "l1"], late_q2=table.loc[best, "l2"])
def h03(d):
    frame = d.delivered.join(d.review_per_order, on="order_id", how="inner")
    return frame.groupby("late")["score"].mean().reset_index()[["score"]]
def h04(d):
    frame = d.sales.dropna(subset=["category"])
    first = between(frame, "2017-07-01", "2018-01-01").groupby("category")["price"].sum()
    second = between(frame, "2018-01-01", "2018-07-01").groupby("category")["price"].sum()
    table = pd.DataFrame({"a": first, "b": second}).dropna()
    best = (table["b"] - table["a"]).idxmax()
    return one(category=best, h2_2017=table.loc[best, "a"], h1_2018=table.loc[best, "b"])
def h05(d):
    totals = d.sales.dropna(subset=["category"]).groupby(["region", "category"])["price"].sum().reset_index()
    return totals.sort_values("price", ascending=False).groupby("region").head(3)
def h06(d):
    rows = []
    for start, end in (("2017-10-01", "2017-11-01"), ("2017-11-01", "2017-12-01"), ("2017-11-24", "2017-11-25")):
        frame = between(d.sales, start, end)
        rows.append({"period": start, "revenue": frame["price"].sum(), "orders": frame["order_id"].nunique()})
    return pd.DataFrame(rows)


DERIVATIONS = {name.upper(): fn for name, fn in list(globals().items())
               if len(name) == 3 and name[0] in "emh" and name[1:].isdigit()}


def same_result(sql_df: pd.DataFrame, pandas_df: pd.DataFrame, mode: str) -> bool:
    """Both routes must agree exactly in row count and content."""
    if len(sql_df) != len(pandas_df):
        return False
    if mode == "values":  # compare the sorted numbers, ignoring labels
        a = np.sort(sql_df.select_dtypes(include="number").to_numpy(dtype=float).ravel())
        b = np.sort(pandas_df.select_dtypes(include="number").to_numpy(dtype=float).ravel())
        return a.shape == b.shape and all(numbers_close(x, y) for x, y in zip(a, b))
    return rows_match(sql_df, pandas_df) and rows_match(pandas_df, sql_df)


def result_text(df: pd.DataFrame) -> str:
    # Fixed notation: the default would print large sums as 1.349440e+07.
    return df.to_string(index=False, float_format=lambda v: f"{v:.2f}")


def derived_facts(df: pd.DataFrame, question_id: str) -> str:
    """Figures a rubric needs that are computed from the result rather than stored in it."""
    if question_id != "H01":
        return ""
    north, northeast = df[df["region"] == "North"], df[df["region"] == "Northeast"]
    pct_fall = 100 * (1 - north["revenue"].iloc[1] / north["revenue"].iloc[0])
    amount_fall = northeast["revenue"].iloc[0] - northeast["revenue"].iloc[1]
    return f" North {pct_fall:.2f}% Northeast {amount_fall:.2f}"


def main() -> int:
    questions = yaml.safe_load((ROOT / "eval" / "questions.yaml").read_text(encoding="utf-8"))["questions"]
    data, db = Data(), ReadOnlyDB()
    lines = ["# Gold answer verification", "",
             "Each answer was derived twice: once in SQL (DuckDB) and once in pandas from the raw CSVs.",
             "The two routes share no code. Agreement is evidence, not proof: a shared misreading of a",
             "question would pass both. These answers have not been reviewed by a person.", "",
             "| ID | Question | SQL = pandas | Required facts in result | Result |", "|---|---|---|---|---|"]
    failures = 0
    for q in questions:
        if q.get("unanswerable"):
            lines.append(f"| {q['id']} | {q['question']} | n/a | n/a | Must decline: the data does not hold this |")
            continue
        result = db.run(q["gold_sql"])
        assert result.ok, f"{q['id']}: {result.error}"
        derived = DERIVATIONS[q["id"]](data)
        agree = same_result(result.df, derived, q["match"])
        blob = result_text(result.df)
        # Every alternative rubric must hold against the gold result, or it is a typo.
        # H01's percent and R$ figures are derived from the result, so they are checked
        # against the pandas derivation of those differences in `derived_facts`.
        facts = all(rubric_met(blob + derived_facts(result.df, q["id"]), r) for r in rubrics(q))
        failures += not (agree and facts)
        flag = "PASS" if agree and facts else "FAIL"
        print(f"{q['id']} {flag} agree={agree} facts={facts}\n{blob}\n")
        cell = "<br>".join(blob.splitlines()[:6]).replace("|", "/")
        lines.append(f"| {q['id']} | {q['question']} | {'yes' if agree else 'NO'} | "
                     f"{'yes' if facts else 'NO'} | <pre>{cell}</pre> |")
    db.close()
    lines += ["", f"**{len(DERIVATIONS) - failures} of {len(DERIVATIONS)} answerable questions agree "
                  "across both routes.** The 3 unanswerable questions have no result to compare."]
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "gold_verification.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(DERIVATIONS) - failures}/{len(DERIVATIONS)} agree")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
