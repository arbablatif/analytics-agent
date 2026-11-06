"""Weekly PDF report: fixed KPI queries, optional pinned Q&As, and a full query appendix.

Every figure comes from a fixed SQL query that runs through the same guardrails as the agent.
The only LLM involvement is one optional narrative paragraph. If that paragraph states a number
the queries do not support, it is discarded and the template sentence is used instead.
"""
from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, Preformatted,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

from .agent import AgentResult
from .charts import line_chart
from .config import REPORTS_DIR
from .db import QueryResult, ReadOnlyDB
from .grounding import check_answer
from .llm import LLMClient, LLMError

VALID = "o.order_status NOT IN ('canceled', 'unavailable')"
INK = colors.HexColor("#0b0b0b")
MUTED = colors.HexColor("#52514e")
RULE = colors.HexColor("#e8e7e2")
TINT = colors.HexColor("#f4f3ef")


@dataclass
class WeeklyData:
    """The results of the fixed queries, plus the SQL for the appendix."""
    week_start: date
    kpis: dict
    regions: pd.DataFrame
    movers: pd.DataFrame
    trend: pd.DataFrame
    queries: list[tuple[str, str]] = field(default_factory=list)

    @property
    def week_end(self) -> date:
        return self.week_start + timedelta(days=6)


def _run(db: ReadOnlyDB, title: str, sql: str, queries: list[tuple[str, str]]) -> pd.DataFrame:
    result: QueryResult = db.run(sql)
    if not result.ok:
        raise RuntimeError(f"Report query '{title}' failed: {result.error}")
    queries.append((title, result.sql))
    return result.df


def collect_week(db: ReadOnlyDB, week_start: str) -> WeeklyData:
    start = date.fromisoformat(week_start)  # also rejects anything that is not a date
    if start.weekday() != 0:
        raise ValueError(f"{week_start} is not a Monday.")
    previous, end = start - timedelta(days=7), start + timedelta(days=7)
    trend_start = start - timedelta(days=7 * 11)
    in_period = f"o.order_purchase_timestamp >= DATE '{previous}' AND o.order_purchase_timestamp < DATE '{end}'"
    this_week = f"o.order_purchase_timestamp >= DATE '{start}'"
    last_week = f"o.order_purchase_timestamp < DATE '{start}'"
    queries: list[tuple[str, str]] = []

    sales = _run(db, "Revenue and orders, this week and last week", f"""
SELECT CASE WHEN {this_week} THEN 'this' ELSE 'last' END AS period,
       sum(i.price) AS revenue,
       count(DISTINCT i.order_id) AS orders
FROM order_items i JOIN orders o USING (order_id)
WHERE {VALID} AND {in_period}
GROUP BY 1""", queries).set_index("period")

    service = _run(db, "Late deliveries and review score for orders placed each week", f"""
WITH per_order AS (SELECT order_id, avg(review_score) AS score FROM order_reviews GROUP BY 1)
SELECT CASE WHEN {this_week} THEN 'this' ELSE 'last' END AS period,
       100.0 * count(*) FILTER (WHERE CAST(o.order_delivered_customer_date AS DATE)
                                    > CAST(o.order_estimated_delivery_date AS DATE))
             / count(*) AS late_pct,
       avg(p.score) AS review_score
FROM orders o LEFT JOIN per_order p USING (order_id)
WHERE o.order_status = 'delivered' AND o.order_delivered_customer_date IS NOT NULL AND {in_period}
GROUP BY 1""", queries).set_index("period")

    regions = _run(db, "Revenue by region", f"""
SELECT r.region,
       coalesce(sum(i.price) FILTER (WHERE {this_week}), 0) AS this_week,
       coalesce(sum(i.price) FILTER (WHERE {last_week}), 0) AS last_week
FROM order_items i JOIN orders o USING (order_id)
JOIN customers c USING (customer_id)
JOIN state_regions r ON r.state = c.customer_state
WHERE {VALID} AND {in_period}
GROUP BY 1 ORDER BY 2 DESC""", queries)

    movers = _run(db, "Largest category changes", f"""
WITH c AS (
  SELECT coalesce(t.product_category_name_english, p.product_category_name) AS category,
         coalesce(sum(i.price) FILTER (WHERE {this_week}), 0) AS this_week,
         coalesce(sum(i.price) FILTER (WHERE {last_week}), 0) AS last_week
  FROM order_items i JOIN orders o USING (order_id)
  JOIN products p USING (product_id)
  LEFT JOIN category_translation t ON t.product_category_name = p.product_category_name
  WHERE {VALID} AND {in_period} AND p.product_category_name IS NOT NULL
  GROUP BY 1
)
SELECT category, this_week, last_week, this_week - last_week AS change
FROM c ORDER BY abs(this_week - last_week) DESC LIMIT 6""", queries)

    trend = _run(db, "Weekly revenue, last 12 weeks", f"""
SELECT date_trunc('week', o.order_purchase_timestamp) AS week_start, sum(i.price) AS revenue
FROM order_items i JOIN orders o USING (order_id)
WHERE {VALID} AND o.order_purchase_timestamp >= DATE '{trend_start}'
  AND o.order_purchase_timestamp < DATE '{end}'
GROUP BY 1 ORDER BY 1""", queries)

    kpis = {}
    for period in ("this", "last"):
        revenue, orders = float(sales.loc[period, "revenue"]), int(sales.loc[period, "orders"])
        kpis[period] = {"revenue": revenue, "orders": orders, "aov": revenue / orders,
                        "late_pct": float(service.loc[period, "late_pct"]),
                        "review_score": float(service.loc[period, "review_score"])}
    return WeeklyData(start, kpis, regions, movers, trend, queries)


def _pct_change(now: float, before: float) -> float:
    return 100 * (now / before - 1) if before else 0.0


def template_narrative(week: WeeklyData) -> str:
    now, before = week.kpis["this"], week.kpis["last"]
    change = _pct_change(now["revenue"], before["revenue"])
    direction = "up" if change >= 0 else "down"
    region = week.regions.assign(delta=week.regions["this_week"] - week.regions["last_week"])
    largest = region.reindex(region["delta"].abs().sort_values(ascending=False).index).iloc[0]
    return (f"Revenue was R${now['revenue']:,.0f}, {direction} {abs(change):.1f}% on the previous week, "
            f"from {now['orders']:,} orders at an average of R${now['aov']:,.2f}. "
            f"{largest['region']} moved the most, {'gaining' if largest['delta'] >= 0 else 'losing'} "
            f"R${abs(largest['delta']):,.0f}. "
            f"{now['late_pct']:.1f}% of delivered orders arrived late, and the average review score was "
            f"{now['review_score']:.2f}.")


def llm_narrative(week: WeeklyData, llm: LLMClient) -> tuple[str, str]:
    """One cached LLM call. Returns (text, source), falling back to the template if unsupported."""
    fallback = template_narrative(week)
    if llm.mock:
        return fallback, "template (mock mode)"
    facts = {"kpis": week.kpis, "regions": week.regions.to_dict("records"), "movers": week.movers.to_dict("records")}
    system = ("You write the summary paragraph of a weekly e-commerce report. Use only the figures provided, "
              "in R$. Write 3 or 4 plain sentences. State what moved, not why, because the data does not "
              "show causes. Return the paragraph and nothing else.")
    try:
        reply = llm.chat(system, [{"role": "user", "text": json.dumps(facts, default=str)}], [])
    except LLMError as exc:
        return fallback, f"template (LLM unavailable: {str(exc)[:80]})"
    frames = [pd.DataFrame([week.kpis["this"], week.kpis["last"]]), week.regions, week.movers]
    if not reply.text or check_answer(reply.text, frames).unverified:
        return fallback, "template (LLM text failed the number check)"
    return reply.text, f"{llm.provider} / {llm.model}"


# ------------------------------------------------------------------ PDF layout
def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["Normal"]
    make = lambda name, **kw: ParagraphStyle(name, parent=base, fontName=kw.pop("font", "Helvetica"),  # noqa: E731
                                             alignment=TA_LEFT, **kw)
    return {
        "title": make("title", font="Helvetica-Bold", fontSize=20, leading=24, textColor=INK),
        "sub": make("sub", fontSize=10, leading=14, textColor=MUTED, spaceAfter=10),
        "h2": make("h2", font="Helvetica-Bold", fontSize=12, leading=16, textColor=INK, spaceBefore=12, spaceAfter=5),
        "body": make("body", fontSize=9.5, leading=14, textColor=INK, spaceAfter=5),
        "note": make("note", fontSize=8, leading=11, textColor=MUTED, spaceAfter=4),
        "tile_value": make("tile_value", font="Helvetica-Bold", fontSize=14, leading=18, textColor=INK),
        "tile_label": make("tile_label", fontSize=8, leading=10, textColor=MUTED),
        "code": make("code", font="Courier", fontSize=6.5, leading=8.5, textColor=INK),
    }


def _chart_image(fig, width_mm: float = 174, height_mm: float = 62) -> Image:
    width_px, height_px = int(width_mm * 5), int(height_mm * 5)
    png = fig.to_image(format="png", width=width_px, height=height_px, scale=2)
    return Image(io.BytesIO(png), width=width_mm * mm, height=height_mm * mm)


def _kpi_tiles(week: WeeklyData, s: dict) -> Table:
    now, before = week.kpis["this"], week.kpis["last"]
    relative = lambda key: f"{_pct_change(now[key], before[key]):+.1f}% vs last week"  # noqa: E731
    points = lambda key, digits, unit="": f"{now[key] - before[key]:+.{digits}f}{unit} vs last week"  # noqa: E731
    tiles = [("Revenue", f"R${now['revenue']:,.0f}", relative("revenue")),
             ("Orders", f"{now['orders']:,}", relative("orders")),
             ("Average order", f"R${now['aov']:,.2f}", relative("aov")),
             ("Late deliveries", f"{now['late_pct']:.1f}%", points("late_pct", 1, " pts")),
             ("Review score", f"{now['review_score']:.2f}", points("review_score", 2))]
    cells = [[Paragraph(label, s["tile_label"]), Paragraph(value, s["tile_value"]), Paragraph(delta, s["tile_label"])]
             for label, value, delta in tiles]
    table = Table([cells], colWidths=[34.8 * mm] * 5)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), TINT), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("LINEAFTER", (0, 0), (-2, -1), 3, colors.white),
                               ("LEFTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 6),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    return table


def _data_table(header: list[str], rows: list[list[str]], widths: list[float]) -> Table:
    table = Table([header] + rows, colWidths=[w * mm for w in widths], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5), ("FONT", (0, 1), (-1, -1), "Helvetica", 8.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK), ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.7, INK), ("LINEBELOW", (0, 1), (-1, -1), 0.3, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    return table


def _page_footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 10 * mm, "Revenue = item price, excluding freight and canceled or unavailable orders.")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _wrap_sql(sql: str, width: int = 118) -> str:
    lines = []
    for line in sql.strip().splitlines():
        while len(line) > width:
            cut = line.rfind(" ", 0, width)
            cut = cut if cut > 0 else width
            lines.append(line[:cut])
            line = "    " + line[cut:].lstrip()
        lines.append(line)
    return "\n".join(lines)


def build_report(db: ReadOnlyDB, week_start: str, llm: LLMClient, pinned: list[AgentResult] | None = None,
                 output: Path | None = None) -> Path:
    """Write the PDF for the week starting on `week_start` (a Monday) and return its path."""
    week = collect_week(db, week_start)
    narrative, source = llm_narrative(week, llm)
    s = _styles()
    pinned = pinned or []

    story: list = [
        Paragraph("Weekly sales report", s["title"]),
        Paragraph(f"{week.week_start:%d %b %Y} to {week.week_end:%d %b %Y}. Olist marketplace, all figures in R$.", s["sub"]),
        _kpi_tiles(week, s), Spacer(1, 8),
        Paragraph("Summary", s["h2"]), Paragraph(escape(narrative), s["body"]),
        Paragraph(f"Summary source: {escape(source)}.", s["note"]),
    ]
    trend = week.trend.assign(week_start=lambda d: pd.to_datetime(d["week_start"]).dt.strftime("%Y-%m-%d"))
    story.append(_chart_image(line_chart(trend, "week_start", "revenue", "Weekly revenue, last 12 weeks", height=310)))

    region_rows = [[r.region, f"{r.this_week:,.0f}", f"{r.last_week:,.0f}", f"{r.this_week - r.last_week:+,.0f}",
                    f"{_pct_change(r.this_week, r.last_week):+.1f}%"] for r in week.regions.itertuples()]
    story.append(KeepTogether([Paragraph("Revenue by region", s["h2"]),
                               _data_table(["Region", "This week", "Last week", "Change", "Change %"],
                                           region_rows, [50, 31, 31, 31, 31])]))
    mover_rows = [[m.category.replace("_", " "), f"{m.this_week:,.0f}", f"{m.last_week:,.0f}", f"{m.change:+,.0f}"]
                  for m in week.movers.itertuples()]
    story.append(KeepTogether([Paragraph("Largest category changes", s["h2"]),
                               _data_table(["Category", "This week", "Last week", "Change"], mover_rows, [81, 31, 31, 31])]))

    if pinned:
        story += [PageBreak(), Paragraph("Questions asked this week", s["title"]), Spacer(1, 6)]
        for item in pinned:
            cited = [f"{st.query_id}: {st.purpose}" for st in item.ok_steps]
            story.append(KeepTogether([Paragraph(escape(item.question), s["h2"]),
                                       Paragraph(escape(item.answer).replace("\n", "<br/>"), s["body"]),
                                       Paragraph(escape("; ".join(cited)), s["note"])]))

    story += [PageBreak(), Paragraph("Appendix: every query in this report", s["title"]), Spacer(1, 6)]
    for title, sql in week.queries:
        story.append(KeepTogether([Paragraph(escape(title), s["h2"]), Preformatted(_wrap_sql(sql), s["code"])]))
    for item in pinned:
        for step in item.ok_steps:
            story.append(KeepTogether([Paragraph(escape(f"{item.question} [{step.query_id}]"), s["h2"]),
                                       Preformatted(_wrap_sql(step.result.sql), s["code"])]))

    output = output or REPORTS_DIR / f"weekly_report_{week.week_start}.pdf"
    output.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(output), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=18 * mm,
                            title=f"Weekly sales report {week.week_start}", author="analytics-agent")
    doc.build(story, onFirstPage=_page_footer, onLaterPages=_page_footer)
    return output
