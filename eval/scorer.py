"""Deterministic scoring: no LLM judge, so scores are free, repeatable and auditable.

Execution accuracy compares what the agent's SQL returned with the gold result.
Answer accuracy compares what the final text says with the required facts.
"""
from __future__ import annotations

import itertools
import math
import re

import numpy as np
import pandas as pd

from analytics_agent.grounding import extract_figures

REL_TOL = 0.001    # 0.1%: tight enough to catch the 4.5% join inflation and missing filters
ABS_TOL = 0.0051   # allows a value rounded to two decimals


def _is_number(value) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return False
    return isinstance(value, (int, float, np.integer, np.floating)) and math.isfinite(float(value))


def numbers_close(predicted: float, gold: float) -> bool:
    """True if the predicted value equals gold, allowing for tolerance or display rounding."""
    predicted, gold = float(predicted), float(gold)
    if abs(predicted - gold) <= max(REL_TOL * abs(gold), ABS_TOL):
        return True
    # A query may round with ROUND(x, 1). Accept that if rounding gold gives the same value.
    return any(abs(predicted - round(predicted, d)) < 1e-9 and abs(round(gold, d) - predicted) < 1e-9
               for d in (1, 2, 3))


def _scaled_close(predicted, gold) -> bool:
    """Percentages may be stored as 14.2 or as 0.142."""
    return (numbers_close(predicted, gold) or numbers_close(predicted * 100, gold)
            or numbers_close(predicted / 100, gold))


def cells_equal(predicted, gold) -> bool:
    if _is_number(gold) and _is_number(predicted):
        return numbers_close(predicted, gold)
    if pd.isna(gold) or pd.isna(predicted):
        return bool(pd.isna(gold) and pd.isna(predicted))
    a, b = str(predicted).strip().lower(), str(gold).strip().lower()
    # 2018-01 (gold) should match a timestamp column holding 2018-01-01 00:00:00.
    return a == b or (re.fullmatch(r"\d{4}-\d{2}", b) is not None and a.startswith(b))


def _rows(df: pd.DataFrame) -> list[tuple]:
    return list(df.astype(object).itertuples(index=False, name=None))


def rows_match(predicted: pd.DataFrame, gold: pd.DataFrame) -> bool:
    """True if every gold row appears in `predicted` under some column mapping.

    Column names and order are ignored, and extra rows and columns are allowed: a full
    ranking contains the correct top 5, and whether the agent picked the right rows is
    what answer accuracy then checks.
    """
    if predicted.shape[1] < gold.shape[1] or len(predicted) < len(gold):
        return False
    gold_rows = _rows(gold)
    columns = range(predicted.shape[1])
    # Only try columns that could hold the first gold row's value in each position.
    candidates = [[c for c in columns if predicted.iloc[:, c].map(lambda v, g=gold_rows[0][j]: cells_equal(v, g)).any()]
                  for j in range(gold.shape[1])]
    for mapping in itertools.product(*candidates):
        if len(set(mapping)) != len(mapping):
            continue
        predicted_rows = _rows(predicted.iloc[:, list(mapping)])
        if all(any(all(cells_equal(p, g) for p, g in zip(row, gold_row)) for row in predicted_rows)
               for gold_row in gold_rows):
            return True
    return False


def values_present(frames: list[pd.DataFrame], gold: pd.DataFrame) -> bool:
    """True if every number in the gold result appears in some query result, in any shape."""
    available = []
    for df in frames:
        numeric = df.select_dtypes(include="number").to_numpy(dtype=float, na_value=np.nan).ravel()
        available.extend(v for v in numeric if math.isfinite(v))
    targets = gold.select_dtypes(include="number").to_numpy(dtype=float, na_value=np.nan).ravel()
    return all(any(_scaled_close(a, t) for a in available) for t in targets if math.isfinite(t))


def execution_match(frames: list[pd.DataFrame], gold: pd.DataFrame, mode: str) -> bool:
    """Did any successful query in the trace return the gold result?"""
    if mode == "values":
        return values_present(frames, gold)
    return any(rows_match(df, gold) for df in frames if not df.empty)


def number_stated(answer: str, expected: float) -> bool:
    """True if the answer states the figure, at whatever precision it chose to display it."""
    target = abs(float(expected))
    for figure in extract_figures(answer):
        value = abs(figure.value)
        if abs(value - target) <= max(figure.tolerance, 0.002 * target):
            return True
    # Small integers are skipped by extract_figures, so look for those as whole words.
    return target == int(target) and re.search(rf"(?<![\d.,Q]){int(target)}(?![\d.,]\d)", answer) is not None


def entity_stated(answer: str, entity: str) -> bool:
    """`entity` may list alternatives as "a|b". Matches whole words, ignoring case."""
    text = answer.lower().replace("_", " ")
    return any(re.search(rf"(?<!\w){re.escape(option.lower().replace('_', ' '))}(?!\w)", text)
               for option in entity.split("|"))


def answer_correct(answer: str, answerable: bool, question: dict) -> bool:
    """Does the final text state every required fact, or correctly decline?"""
    if question.get("unanswerable"):
        return not answerable
    if not answerable:
        return False
    return any(rubric_met(answer, rubric) for rubric in rubrics(question))


def rubrics(question: dict) -> list[dict]:
    """The acceptable rubrics. Most questions have one; any_of lists several valid readings."""
    expected = question["expected"]
    return expected.get("any_of", [expected])


def rubric_met(text: str, rubric: dict) -> bool:
    return (all(entity_stated(text, e) for e in rubric.get("entities", []))
            and all(number_stated(text, n) for n in rubric.get("numbers", [])))
