"""Streamlit UI: ask a question, see every query, read a cited answer, export the weekly PDF.

    streamlit run app.py              # live: uses the provider in .env
    streamlit run app.py -- --mock    # no API calls
"""
from __future__ import annotations

import re
import sys
from datetime import date, timedelta

import streamlit as st

from analytics_agent.agent import AgentResult, AnalyticsAgent, Step
from analytics_agent.charts import build_chart
from analytics_agent.config import AS_OF_DATE, DB_PATH, DEFAULT_REPORT_WEEK, MAX_LLM_CALLS_PER_QUESTION
from analytics_agent.db import ReadOnlyDB
from analytics_agent.llm import LLMClient, LLMError
from analytics_agent.report import build_report

EXAMPLES = [
    "Which region's revenue fell most last quarter and why?",
    "What was monthly revenue from January to August 2018?",
    "What are the top 5 product categories by revenue? Use English category names.",
    "Why did revenue jump in November 2017 compared with October 2017?",
    "What is our profit margin by product category?",
]
STATUS_LABEL = {"ok": "Ran", "error": "Failed", "blocked": "Blocked by guardrail", "timeout": "Timed out"}

st.set_page_config(page_title="Analytics Agent", page_icon=":bar_chart:", layout="wide")


@st.cache_resource
def get_db() -> ReadOnlyDB:
    return ReadOnlyDB()


def render_step(step: Step) -> None:
    label = f"{step.query_id} · {STATUS_LABEL[step.result.status]}"
    if step.result.ok:
        label += f" · {step.result.row_count:,} rows · {step.result.elapsed_ms} ms"
    with st.expander(label, expanded=False):
        if step.purpose:
            st.caption(step.purpose)
        st.code(step.result.sql.strip(), language="sql")
        if step.result.ok:
            st.dataframe(step.result.df, width="stretch", hide_index=True)
        else:
            st.error(step.result.error)
        for note in step.checks:
            st.warning(note)


def render_result(result: AgentResult, key: str) -> None:
    st.subheader(result.question)
    # Streamlit would read R$...R$ pairs as inline maths, so the dollar signs are escaped.
    st.markdown(result.answer.replace("$", r"\$"))

    grounding = result.grounding
    if grounding.unverified:
        st.warning(f"These figures were not found in any query result, so check them: "
                   f"{', '.join(grounding.unverified)}")
    elif grounding.total:
        calculated = f", {len(grounding.derived)} of them calculated" if grounding.derived else ""
        st.caption(f"Number check: all {grounding.total} figures trace to query results{calculated}.")
    if not result.answerable:
        st.info("The agent reported that the database does not hold this information.")
    if result.stop_reason == "call_limit":
        st.error(f"Stopped at the limit of {MAX_LLM_CALLS_PER_QUESTION} model calls.")

    step = result.chart_step()
    if step is not None:
        figure = build_chart(step.result.df, f"{step.query_id}: {step.purpose or 'Result'}")
        if figure is not None:
            st.plotly_chart(figure, width="stretch", theme=None, key=f"chart_{key}",
                            config={"displayModeBar": False})

    st.markdown(f"**Queries ({len(result.steps)})**")
    for item in result.steps:
        render_step(item)
    if not result.steps:
        st.caption("No queries were run.")

    pinned = st.session_state.pinned
    if result in pinned:
        st.caption("Added to the weekly report.")
    elif st.button("Add to weekly report", key=f"pin_{key}"):
        pinned.append(result)
        st.rerun()


def main() -> None:
    mock_flag = "--mock" in sys.argv
    st.session_state.setdefault("history", [])
    st.session_state.setdefault("pinned", [])

    if not DB_PATH.exists():
        st.error("Database not found. Run: python scripts/build_db.py")
        st.stop()

    with st.sidebar:
        st.header("Settings")
        mock = st.toggle("Mock mode (no API calls)", value=mock_flag)
        condition = st.radio("Prompt", ["enhanced", "plain"],
                             format_func={"enhanced": "Schema descriptions + examples", "plain": "Plain schema"}.get)
        llm = LLMClient(mock=mock)
        st.caption(f"Model: {llm.provider} / {llm.model}")
        st.caption(f"Treating today as {AS_OF_DATE}, where the data ends. Revenue is item price, "
                   "excluding freight and canceled or unavailable orders.")

        st.header("Weekly report")
        chosen = st.date_input("Week starting (Monday)", value=date.fromisoformat(DEFAULT_REPORT_WEEK),
                               min_value=date(2017, 1, 9), max_value=date(2018, 8, 20))
        monday = chosen - timedelta(days=chosen.weekday())
        st.caption(f"{len(st.session_state.pinned)} answer(s) added to this report.")
        if st.button("Build PDF", width="stretch"):
            with st.spinner("Building the report"):
                st.session_state.pdf = build_report(get_db(), monday.isoformat(), llm, st.session_state.pinned)
        if st.session_state.get("pdf"):
            pdf = st.session_state.pdf
            st.download_button("Download PDF", pdf.read_bytes(), file_name=pdf.name,
                               mime="application/pdf", width="stretch")

    st.title("Analytics Agent")
    st.caption("Ask a question about the Olist marketplace. Every query the agent runs is shown below its answer.")
    if mock:
        st.warning("Mock mode: answers are scripted from the evaluation set. No model is being called.")

    example = st.pills("Examples", EXAMPLES, label_visibility="collapsed")
    typed = st.chat_input("Ask about revenue, orders, delivery, reviews...")
    # A pill stays selected across reruns, so it only counts on the run where it changed.
    # Otherwise any later button click would ask the example again and spend an API call.
    new_example = example if example != st.session_state.get("last_example") else None
    st.session_state.last_example = example
    question = typed or new_example

    if question:
        with st.spinner("Working through the data"):
            try:
                agent = AnalyticsAgent(get_db(), llm, condition)
                st.session_state.history.append(agent.ask(question))
            except LLMError as exc:
                st.error(str(exc))

    for index, result in reversed(list(enumerate(st.session_state.history))):
        render_result(result, re.sub(r"\W", "", f"{index}{result.question}")[:40])
        st.divider()


main()
