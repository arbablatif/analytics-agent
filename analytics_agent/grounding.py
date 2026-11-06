"""Number checking: does every figure in an answer trace back to a query result?

This is deterministic and makes no API calls. Each number written in the answer is
classified as:
  grounded    it appears in a successful query result, allowing for display rounding
  derived     it equals the difference or percent change of two values from one query
  unverified  neither, so the UI flags it for a human to look at
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

SCALES = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6,
          "b": 1e9, "bn": 1e9, "billion": 1e9}

NUMBER = re.compile(
    r"(?<![\w.,-])(?P<cur>R\$\s*)?(?P<sign>[-−+])?(?P<cur2>R\$\s*)?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s?(?P<scale>thousand|million|billion|bn|mn|[kKmMbB])\b)?(?P<pct>\s?%|\s?percent\b|\s?pp\b)?",
    re.IGNORECASE)

# Text that holds digits but no measured figure: citations, dates, quarters, ids.
NOT_FIGURES = [
    re.compile(r"\[?Q\d+\]?(?:\s*,\s*\[?Q\d+\]?)*"),                 # [Q1] citations, Q2 quarters
    re.compile(r"\b(?:19|20)\d{2}-\d{2}(?:-\d{2})?(?:[ T]\d{2}:\d{2}(?::\d{2})?)?\b"),  # ISO dates
    re.compile(r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b", re.I),
    re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?\b", re.I),
    re.compile(r"\b[0-9a-f]{32}\b"),                                 # Olist hashed ids
]


@dataclass
class Figure:
    text: str
    value: float
    tolerance: float
    is_percent: bool


@dataclass
class GroundingReport:
    grounded: list[str] = field(default_factory=list)
    derived: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.grounded) + len(self.derived) + len(self.unverified)

    @property
    def rate(self) -> float:
        """Share of figures that trace back to data. 1.0 if there were none to check."""
        return 1.0 if self.total == 0 else (len(self.grounded) + len(self.derived)) / self.total


def extract_figures(text: str) -> list[Figure]:
    """Pull the checkable numbers out of an answer."""
    for pattern in NOT_FIGURES:
        text = pattern.sub(" ", text)
    figures = []
    for match in NUMBER.finditer(text):
        raw = match.group("num").replace(",", "")
        value = float(raw)
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        scale = SCALES.get((match.group("scale") or "").lower(), 1.0)
        is_percent = bool(match.group("pct"))
        has_currency = bool(match.group("cur") or match.group("cur2"))
        # Bare years and small counting words ("top 5") are not measured figures.
        if not (is_percent or has_currency or scale != 1.0):
            if decimals == 0 and "," not in match.group("num"):
                if value <= 10 or 2015 <= value <= 2019:
                    continue
        tolerance = 0.5 * 10 ** (-decimals) * scale * 1.0001
        signed = -value if match.group("sign") in ("-", "−") else value
        figures.append(Figure(match.group(0).strip(), signed * scale, tolerance, is_percent))
    return figures


def _numbers(df: pd.DataFrame) -> np.ndarray:
    """All numeric cells in a result, as one array."""
    numeric = df.select_dtypes(include="number")
    if numeric.empty:
        return np.array([])
    values = numeric.to_numpy(dtype=float, na_value=np.nan).ravel()
    return np.unique(values[np.isfinite(values)])


def _found(figure: Figure, values: np.ndarray) -> bool:
    if values.size == 0:
        return False
    targets = [figure.value, abs(figure.value)]
    if figure.is_percent:  # 14.2% may be stored as 14.2 or as 0.142
        targets += [figure.value / 100, abs(figure.value) / 100]
    for target in targets:
        tolerance = figure.tolerance / 100 if abs(target) < abs(figure.value) else figure.tolerance
        if np.any(np.abs(np.abs(values) - abs(target)) <= tolerance):
            return True
    return False


def _derivable(figure: Figure, values: np.ndarray) -> bool:
    """True if the figure is a difference or percent change between two values."""
    if values.size < 2 or values.size > 400:  # large grids match anything by chance
        return False
    a, b = values[:, None], values[None, :]
    target = abs(figure.value)
    if figure.is_percent:
        with np.errstate(divide="ignore", invalid="ignore"):
            candidates = [(a - b) / b * 100, a / b * 100]
    else:
        candidates = [a - b]
    return any(bool(np.any(np.abs(np.abs(c) - target) <= figure.tolerance)) for c in candidates)


def check_answer(answer: str, frames: list[pd.DataFrame], question: str = "") -> GroundingReport:
    """Classify every figure in the answer against the given query results.

    Figures the user wrote in the question ("at least 500 orders") are skipped: repeating
    them is not a claim about the data.
    """
    report = GroundingReport()
    asked = {abs(f.value) for f in extract_figures(question)}
    arrays = [_numbers(df) for df in frames]
    everything = np.unique(np.concatenate(arrays)) if arrays else np.array([])
    for figure in extract_figures(answer):
        if abs(figure.value) in asked:
            continue
        if _found(figure, everything):
            report.grounded.append(figure.text)
        elif any(_derivable(figure, values) for values in arrays):
            report.derived.append(figure.text)
        else:
            report.unverified.append(figure.text)
    return report
