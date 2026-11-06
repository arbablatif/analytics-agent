"""Mock LLM: runs the whole pipeline end to end with no API calls.

It replays gold SQL from eval/questions.yaml, then writes an answer from the rows that really
came back, so the database, guardrails, number check, charts and scoring all do real work.
Two scripted questions misbehave on purpose so that the retry and blocked paths also run.

Mock results test the plumbing. They say nothing about model accuracy.
"""
from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

import yaml

from .config import ROOT

if TYPE_CHECKING:
    from .llm import LLMReply

# Questions whose first attempt is deliberately wrong.
MISBEHAVE = {
    "E03": "SELECT sum(no_such_price_column) FROM order_items",          # database error, then retry
    "M02": "DROP TABLE order_items",                                     # guardrail block, then retry
}

FALLBACK_SQL = {
    "categor": """SELECT coalesce(t.product_category_name_english, p.product_category_name) AS category,
       round(sum(i.price), 2) AS revenue
FROM order_items i JOIN orders o USING (order_id) JOIN products p USING (product_id)
LEFT JOIN category_translation t ON t.product_category_name = p.product_category_name
WHERE o.order_status NOT IN ('canceled', 'unavailable')
GROUP BY 1 ORDER BY 2 DESC LIMIT 10""",
    "region": """SELECT r.region, round(sum(i.price), 2) AS revenue
FROM order_items i JOIN orders o USING (order_id) JOIN customers c USING (customer_id)
JOIN state_regions r ON r.state = c.customer_state
WHERE o.order_status NOT IN ('canceled', 'unavailable')
GROUP BY 1 ORDER BY 2 DESC""",
    "": """SELECT strftime(o.order_purchase_timestamp, '%Y-%m') AS month, round(sum(i.price), 2) AS revenue
FROM order_items i JOIN orders o USING (order_id)
WHERE o.order_status NOT IN ('canceled', 'unavailable')
  AND o.order_purchase_timestamp >= DATE '2017-01-01' AND o.order_purchase_timestamp < DATE '2018-09-01'
GROUP BY 1 ORDER BY 1""",
}


@lru_cache(maxsize=1)
def _scripts() -> dict[str, dict]:
    """Eval questions by exact text, so the mock can look up a scripted query."""
    data = yaml.safe_load((ROOT / "eval" / "questions.yaml").read_text(encoding="utf-8"))
    return {q["question"].strip().lower(): q for q in data["questions"]}


def _plan(question: str) -> tuple[list[str], bool, bool]:
    """Return (queries to run in order, is the question answerable, was it scripted)."""
    script = _scripts().get(question.strip().lower())
    if script is None:
        lowered = question.lower()
        key = next(k for k in FALLBACK_SQL if k in lowered)  # "" matches everything
        return [FALLBACK_SQL[key]], True, False
    if script.get("unanswerable"):
        return [], False, True
    queries = [script["gold_sql"]]
    if script["id"] in MISBEHAVE:
        queries.insert(0, MISBEHAVE[script["id"]])
    return queries, True, True


def _format(value) -> str:
    return f"{value:,.4f}".rstrip("0").rstrip(".") if isinstance(value, float) else str(value)


def _answer_from(result: dict, scripted: bool) -> str:
    """Build an answer from real rows, citing the query they came from."""
    rows = result.get("rows", [])[:15]
    lines = ["; ".join(f"{c} = {_format(v)}" for c, v in zip(result["columns"], row)) for row in rows]
    prefix = "" if scripted else "Mock mode only knows the evaluation questions, so this is a generic view. "
    return f"{prefix}[MOCK] From [{result['query_id']}]: " + " | ".join(lines)


def mock_reply(messages: list[dict]) -> LLMReply:
    from .llm import LLMReply  # imported here because llm.py imports this module

    queries, answerable, scripted = _plan(messages[0]["text"])
    tool_turns = [m for m in messages if m["role"] == "tool"]
    if not answerable:
        args = {"answer": "[MOCK] The database does not hold this information.", "answerable": False}
        return LLMReply("", [{"id": "mock_final", "name": "final_answer", "args": args}], cached=True)
    if len(tool_turns) < len(queries):
        args = {"sql": queries[len(tool_turns)], "purpose": "Scripted mock query"}
        return LLMReply("", [{"id": f"mock_{len(tool_turns)}", "name": "run_sql", "args": args}], cached=True)

    last = tool_turns[-1]["results"][-1]["result"]
    answer = _answer_from(last, scripted) if last.get("status") == "ok" else f"[MOCK] Failed: {last.get('error')}"
    args = {"answer": answer, "answerable": True, "chart_query": last.get("query_id", "")}
    return LLMReply("", [{"id": "mock_final", "name": "final_answer", "args": args}], cached=True)
