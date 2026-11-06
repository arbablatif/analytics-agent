"""Choose and build a Plotly chart from the shape of a query result. No LLM call involved.

Rules: a single value is not a chart; time goes on a line; categories go on sorted horizontal
bars; there is only ever one y-axis; series colours follow a fixed order, and anything past
eight series folds into "Other".
"""
from __future__ import annotations

import re

import pandas as pd
import plotly.graph_objects as go

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_MUTED = "#52514e"
GRID = "#e8e7e2"
OTHER = "#9a9992"
# Validated categorical palette, used in this order and never cycled.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]

MAX_SERIES = len(SERIES)
MAX_BARS = 15
LABEL_BARS_UP_TO = 8
DATE_TEXT = re.compile(r"^\d{4}-\d{2}(-\d{2})?")
ID_LIKE = re.compile(r"(^|_)(id|year|quarter|month|rank|row)($|_)|^q\d$", re.I)
CHANGE_LIKE = re.compile(r"chang|diff|delta|growth|drop|fall|decline", re.I)


def _is_time(series: pd.Series) -> bool:
    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    sample = series.dropna().astype(str).head(20)
    return not sample.empty and bool(sample.map(lambda v: bool(DATE_TEXT.match(v))).all())


def _pretty(name: str) -> str:
    return str(name).replace("_", " ").strip().capitalize()


def _layout(fig: go.Figure, title: str, y_title: str = "", x_title: str = "", height: int = 380) -> go.Figure:
    fig.update_layout(
        title={"text": title, "font": {"size": 15, "color": TEXT}, "x": 0, "xanchor": "left"},
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, height=height,
        font={"family": "Inter, Segoe UI, Helvetica, Arial, sans-serif", "size": 12, "color": TEXT_MUTED},
        margin={"l": 8, "r": 24, "t": 48, "b": 8},
        legend={"orientation": "h", "y": -0.16, "x": 0},
        hoverlabel={"bgcolor": "#ffffff", "font": {"color": TEXT}},
    )
    fig.update_xaxes(title=x_title, showgrid=False, linecolor=GRID, automargin=True)
    fig.update_yaxes(title=y_title, showgrid=True, gridcolor=GRID, zeroline=False, automargin=True)
    return fig


def _fold_series(df: pd.DataFrame, series_col: str, measure: str) -> pd.DataFrame:
    """Keep the largest series; merge the rest into 'Other' so no colour is invented."""
    totals = df.groupby(series_col)[measure].sum().abs().sort_values(ascending=False)
    if len(totals) <= MAX_SERIES:
        return df
    keep = set(totals.index[:MAX_SERIES - 1])
    folded = df.copy()
    folded[series_col] = folded[series_col].where(folded[series_col].isin(keep), "Other")
    return folded


def line_chart(df: pd.DataFrame, time_col: str, measure: str, title: str,
               series_col: str | None = None, height: int = 380) -> go.Figure:
    fig = go.Figure()
    frame = df.copy()
    if series_col:
        frame = _fold_series(frame, series_col, measure)
        frame = frame.groupby([time_col, series_col], as_index=False, sort=True)[measure].sum()
        order = frame.groupby(series_col)[measure].sum().sort_values(ascending=False).index
        names = [n for n in order if n != "Other"] + (["Other"] if "Other" in set(order) else [])
    else:
        frame = frame.sort_values(time_col)
        names = [None]
    for position, name in enumerate(names):
        part = frame if name is None else frame[frame[series_col] == name]
        color = OTHER if name == "Other" else SERIES[position]
        fig.add_trace(go.Scatter(
            x=part[time_col], y=part[measure], mode="lines+markers", name=str(name or _pretty(measure)),
            line={"color": color, "width": 2}, marker={"size": 8, "color": color, "line": {"color": SURFACE, "width": 2}},
            hovertemplate=f"%{{x}}<br>{_pretty(measure)}: %{{y:,.2f}}<extra>{name or ''}</extra>"))
    fig.update_layout(showlegend=series_col is not None, hovermode="closest")
    return _layout(fig, title, y_title=_pretty(measure), height=height)


def bar_chart(df: pd.DataFrame, label_col: str, measure: str, title: str, height: int | None = None) -> go.Figure:
    frame = df[[label_col, measure]].dropna().copy()
    frame[label_col] = frame[label_col].astype(str)
    frame = frame.reindex(frame[measure].abs().sort_values(ascending=False).index).head(MAX_BARS)
    has_negative = bool((frame[measure] < 0).any())
    # With losses in the mix, rank by signed value so that gains and falls separate cleanly.
    frame = frame.sort_values(measure) if has_negative else frame.iloc[::-1]  # Plotly draws bottom-up
    show_labels = len(frame) <= LABEL_BARS_UP_TO
    fig = go.Figure(go.Bar(
        x=frame[measure], y=frame[label_col], orientation="h", marker={"color": SERIES[0], "cornerradius": 4},
        text=frame[measure] if show_labels else None, texttemplate="%{x:,.0f}" if show_labels else None,
        textposition="outside", cliponaxis=False, textfont={"color": TEXT_MUTED},
        hovertemplate=f"%{{y}}<br>{_pretty(measure)}: %{{x:,.2f}}<extra></extra>"))
    fig.update_layout(bargap=0.3, showlegend=False)
    _layout(fig, title, x_title=_pretty(measure), height=height or max(240, 44 + 34 * len(frame)))
    fig.update_xaxes(showgrid=True, gridcolor=GRID)
    if has_negative:  # room for the value labels, which would otherwise hit the category names
        low, high = float(frame[measure].min()), float(frame[measure].max())
        padding = 0.16 * (max(high, 0) - low)
        fig.update_xaxes(range=[low - padding, max(high, 0) + padding])
    fig.update_yaxes(showgrid=False, title="", ticksuffix="  ")  # keeps labels off the bars
    return fig


def build_chart(df: pd.DataFrame, title: str) -> go.Figure | None:
    """Return a chart for this result, or None when a chart would add nothing."""
    if df is None or len(df) < 2 or df.shape[1] < 2:
        return None
    measures = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])
                and not pd.api.types.is_bool_dtype(df[c]) and not ID_LIKE.search(str(c))]
    if not measures:
        return None
    # One measure, one axis: a second scale would need a second chart. A column holding the
    # change is the point of a comparison, so it beats the raw before and after values.
    measure = next((c for c in measures if CHANGE_LIKE.search(str(c))), measures[0])
    others = [c for c in df.columns if c not in measures]
    time_cols = [c for c in others if _is_time(df[c])]
    labels = [c for c in others if c not in time_cols]

    if time_cols:
        return line_chart(df, time_cols[0], measure, title, series_col=labels[0] if labels else None)
    if len(labels) >= 2:  # e.g. region and category: combine into one label
        frame = df.copy()
        frame["group"] = frame[labels[0]].astype(str) + " · " + frame[labels[1]].astype(str)
        return bar_chart(frame, "group", measure, title)
    if labels:
        return bar_chart(df, labels[0], measure, title)
    return None
