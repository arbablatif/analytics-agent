"""Agent loop, LLM cache, provider adapters, charts and report. No test calls a real API."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd
import pytest

from analytics_agent import llm as llm_module
from analytics_agent.agent import AnalyticsAgent, Step, result_checks, tool_payload
from analytics_agent.charts import SERIES, build_chart
from analytics_agent.config import DB_PATH, DEFAULT_REPORT_WEEK
from analytics_agent.db import QueryResult, ReadOnlyDB
from analytics_agent.llm import LLMClient, LLMError, LLMReply
from analytics_agent.report import build_report, collect_week, llm_narrative, template_narrative

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="run scripts/build_db.py first")


@pytest.fixture(scope="module")
def db():
    database = ReadOnlyDB()
    yield database
    database.close()


class ScriptedLLM:
    """Stands in for LLMClient and replays a fixed list of replies."""
    mock, provider, model, api_calls = True, "scripted", "scripted", 0

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []

    def chat(self, system, messages, tools):
        self.seen.append((json.loads(json.dumps(messages)), [t["name"] for t in tools]))
        return self.replies.pop(0)


def sql_call(sql, purpose="check"):
    return LLMReply("", [{"id": "c1", "name": "run_sql", "args": {"sql": sql, "purpose": purpose}}])


def final_call(answer, answerable=True, chart_query=""):
    return LLMReply("", [{"id": "c2", "name": "final_answer",
                          "args": {"answer": answer, "answerable": answerable, "chart_query": chart_query}}])


# ------------------------------------------------------------------ agent loop
def test_error_is_fed_back_and_the_agent_retries(db):
    fake = ScriptedLLM([sql_call("SELECT nope FROM orders"), sql_call("SELECT count(*) AS n FROM orders"),
                        final_call("There are 99,441 orders [Q2].")])
    result = AnalyticsAgent(db, fake).ask("How many orders?")
    assert [s.result.status for s in result.steps] == ["error", "ok"]
    feedback = fake.seen[1][0][-1]["results"][0]["result"]
    assert feedback["status"] == "error" and "nope" in feedback["error"]
    assert result.grounding.grounded == ["99,441"]


def test_blocked_query_never_reaches_the_database_and_stays_in_the_trace(db):
    fake = ScriptedLLM([sql_call("DROP TABLE orders"), final_call("I cannot do that.", False)])
    result = AnalyticsAgent(db, fake).ask("Delete the orders table")
    assert result.steps[0].result.status == "blocked"
    assert db.run("SELECT count(*) AS n FROM orders").df["n"][0] == 99441


def test_loop_stops_at_the_call_limit(db):
    fake = ScriptedLLM([sql_call("SELECT 1 AS x")] * 3 + [final_call("Done [Q1].")])
    result = AnalyticsAgent(db, fake, max_calls=4).ask("Loop forever")
    assert result.llm_calls == 4 and len(fake.seen) == 4
    assert fake.seen[-1][1] == ["final_answer"]  # the last call may only answer


def test_a_model_that_never_answers_still_terminates(db):
    fake = ScriptedLLM([sql_call("SELECT 1 AS x")] * 3)
    result = AnalyticsAgent(db, fake, max_calls=3).ask("Loop forever")
    assert result.stop_reason == "call_limit" and not fake.replies


def test_plain_text_reply_is_accepted(db):
    result = AnalyticsAgent(db, ScriptedLLM([LLMReply("Hello.")])).ask("Hi")
    assert result.answer == "Hello."


def test_invented_number_is_flagged(db):
    fake = ScriptedLLM([sql_call("SELECT count(*) AS n FROM orders"), final_call("There are 120,000 orders [Q1].")])
    assert AnalyticsAgent(db, fake).ask("How many?").grounding.unverified == ["120,000"]


def test_result_checks_warn_about_empty_and_null_results():
    assert "no rows" in result_checks(QueryResult("x", "ok", pd.DataFrame({"a": []})))[0]
    assert "NULL" in result_checks(QueryResult("x", "ok", pd.DataFrame({"a": [None, None]})))[0]


def test_model_only_sees_a_bounded_slice(db):
    step = Step("Q1", "", db.run("SELECT order_id FROM orders LIMIT 500"))
    payload = tool_payload(step)
    assert len(payload["rows"]) == 50 and payload["row_count"] == 500
    json.dumps(payload)


def test_payload_handles_dates_and_nulls(db):
    step = Step("Q1", "", db.run("SELECT DATE '2018-01-01' AS d, NULL AS n, 1.23456789 AS f"))
    payload = tool_payload(step)
    json.dumps(payload)
    assert payload["rows"][0][1] is None


def test_unordered_results_are_repeatable(db):
    """Rows go into the prompt, so a changing row order would break the LLM cache."""
    sql = ("SELECT c.customer_state, sum(i.price) AS s FROM order_items i JOIN orders o USING (order_id) "
           "JOIN customers c USING (customer_id) GROUP BY 1")
    runs = {tuple(db.run(sql).df["customer_state"]) for _ in range(8)}
    assert len(runs) == 1


def test_payload_keeps_enough_precision_to_round_correctly(db):
    step = Step("Q1", "", db.run("SELECT 128.114993::DOUBLE AS aov"))
    assert round(tool_payload(step)["rows"][0][0], 2) == 128.11


# ------------------------------------------------------------------- mock mode
def test_mock_exercises_retry_and_block_paths_with_no_paid_calls(db):
    client = LLMClient(mock=True)
    agent = AnalyticsAgent(db, client)
    retried = agent.ask("What is the total revenue across the whole dataset?")
    blocked = agent.ask("What are the top 5 product categories by revenue? Use English category names.")
    assert [s.result.status for s in retried.steps] == ["error", "ok"]
    assert [s.result.status for s in blocked.steps] == ["blocked", "ok"]
    assert client.api_calls == 0


def test_mock_handles_unknown_questions(db):
    result = AnalyticsAgent(db, LLMClient(mock=True)).ask("Tell me about regions")
    assert result.ok_steps and "generic" in result.answer


# ------------------------------------------------------------------- LLM cache
def test_second_identical_call_is_served_from_disk(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(LLMClient, "_call_with_retry", lambda self, *a: calls.append(1) or LLMReply("hi"))
    client = LLMClient(provider="gemini", cache_dir=tmp_path)
    first = client.chat("sys", [{"role": "user", "text": "q"}], [])
    second = client.chat("sys", [{"role": "user", "text": "q"}], [])
    assert len(calls) == 1 and client.api_calls == 1
    assert not first.cached and second.cached and second.text == "hi"


def test_different_prompt_is_not_served_from_cache(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(LLMClient, "_call_with_retry", lambda self, *a: calls.append(1) or LLMReply("hi"))
    client = LLMClient(provider="gemini", cache_dir=tmp_path)
    client.chat("plain", [{"role": "user", "text": "q"}], [])
    client.chat("enhanced", [{"role": "user", "text": "q"}], [])
    assert len(calls) == 2


def test_cache_only_mode_refuses_to_call_the_api(tmp_path, monkeypatch):
    monkeypatch.setattr(LLMClient, "_call_with_retry", lambda self, *a: pytest.fail("called the API"))
    client = LLMClient(provider="gemini", cache_dir=tmp_path, cache_only=True)
    with pytest.raises(LLMError, match="cache-only"):
        client.chat("s", [{"role": "user", "text": "q"}], [])


def test_missing_key_gives_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = LLMClient(provider="anthropic", cache_dir=tmp_path)
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        client.chat("s", [{"role": "user", "text": "q"}], [])


def test_unknown_provider_is_refused():
    with pytest.raises(ValueError):
        LLMClient(provider="nobody")


# ---------------------------------------------------- provider adapters (fakes)
TOOL = [{"name": "run_sql", "description": "d", "parameters": {"type": "object", "properties": {}}}]
CONVERSATION = [
    {"role": "user", "text": "q"},
    {"role": "assistant", "text": "", "tool_calls": [{"id": "a", "name": "run_sql", "args": {"sql": "SELECT 1"}}]},
    {"role": "tool", "results": [{"id": "a", "name": "run_sql", "result": {"status": "ok"}}]},
]


def test_anthropic_adapter_translates_the_conversation(monkeypatch):
    import anthropic
    sent = {}

    class Fake:
        def __init__(self, api_key):
            self.messages = self

        def create(self, **kwargs):
            sent.update(kwargs)
            return SimpleNamespace(content=[
                SimpleNamespace(type="text", text="thinking"),
                SimpleNamespace(type="tool_use", id="t1", name="run_sql", input={"sql": "SELECT 2"})])

    monkeypatch.setattr(anthropic, "Anthropic", Fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    reply = llm_module._call_anthropic("m", "sys", CONVERSATION, TOOL)
    assert reply.tool_calls[0]["args"] == {"sql": "SELECT 2"}
    assert sent["messages"][1]["content"][0]["type"] == "tool_use"
    assert sent["messages"][2]["content"][0]["type"] == "tool_result"
    assert sent["tools"][0]["input_schema"] == TOOL[0]["parameters"]


def test_openai_adapter_translates_the_conversation(monkeypatch):
    import openai
    sent = {}

    class Fake:
        def __init__(self, api_key):
            self.chat = SimpleNamespace(completions=self)

        def create(self, **kwargs):
            sent.update(kwargs)
            call = SimpleNamespace(id="t1", function=SimpleNamespace(name="run_sql", arguments='{"sql": "SELECT 2"}'))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[call]))])

    monkeypatch.setattr(openai, "OpenAI", Fake)
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    reply = llm_module._call_openai("m", "sys", CONVERSATION, TOOL)
    assert reply.tool_calls[0]["args"] == {"sql": "SELECT 2"}
    assert sent["messages"][0]["role"] == "system" and sent["messages"][-1]["role"] == "tool"


def test_tool_free_calls_send_no_tools_key(monkeypatch):
    import openai
    sent = {}

    class Fake:
        def __init__(self, api_key):
            self.chat = SimpleNamespace(completions=self)

        def create(self, **kwargs):
            sent.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))])

    monkeypatch.setattr(openai, "OpenAI", Fake)
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    llm_module._call_openai("m", "sys", [{"role": "user", "text": "q"}], [])
    assert "tools" not in sent


# ---------------------------------------------------------------------- charts
def test_single_value_gets_no_chart():
    assert build_chart(pd.DataFrame({"revenue": [5.0]}), "t") is None


def test_time_series_becomes_a_line():
    frame = pd.DataFrame({"month": ["2018-01", "2018-02"], "revenue": [1.0, 2.0]})
    assert build_chart(frame, "t").data[0].type == "scatter"


def test_categories_become_sorted_bars():
    frame = pd.DataFrame({"region": ["a", "b", "c"], "revenue": [1.0, 3.0, 2.0]})
    bars = build_chart(frame, "t").data[0]
    assert bars.type == "bar" and list(bars.x) == [1.0, 2.0, 3.0]  # bottom to top, so largest on top


def test_only_one_axis_even_with_two_measures():
    frame = pd.DataFrame({"region": ["a", "b"], "revenue": [1000.0, 2000.0], "orders": [1, 2]})
    figure = build_chart(frame, "t")
    assert len(figure.data) == 1 and "yaxis2" not in figure.to_plotly_json()["layout"]


def test_a_change_column_is_charted_in_preference_to_raw_values():
    frame = pd.DataFrame({"region": ["a", "b"], "rev_q1": [10.0, 90.0], "rev_q2": [8.0, 95.0], "pct_change": [-20.0, 5.6]})
    assert sorted(build_chart(frame, "t").data[0].x) == [-20.0, 5.6]


def test_series_past_the_palette_fold_into_other():
    months = ["2018-01", "2018-02"]
    frame = pd.DataFrame([{"month": m, "category": f"c{i}", "revenue": float(i + 1)} for m in months for i in range(12)])
    figure = build_chart(frame, "t")
    names = [t.name for t in figure.data]
    assert len(names) == len(SERIES) and names[-1] == "Other"


def test_ids_are_not_treated_as_measures():
    frame = pd.DataFrame({"seller_id": [1, 2], "name": ["a", "b"]})
    assert build_chart(frame, "t") is None


# ---------------------------------------------------------------------- report
def test_weekly_figures_add_up(db):
    week = collect_week(db, DEFAULT_REPORT_WEEK)
    assert week.regions["this_week"].sum() == pytest.approx(week.kpis["this"]["revenue"])
    assert week.regions["last_week"].sum() == pytest.approx(week.kpis["last"]["revenue"])
    assert week.trend["revenue"].iloc[-1] == pytest.approx(week.kpis["this"]["revenue"])


def test_report_week_must_be_a_monday(db):
    with pytest.raises(ValueError):
        collect_week(db, "2018-08-14")


def test_report_rejects_sql_in_the_date(db):
    with pytest.raises(ValueError):
        collect_week(db, "2018-08-13'; DROP TABLE orders; --")


def test_template_narrative_passes_the_number_check(db):
    from analytics_agent.grounding import check_answer
    week = collect_week(db, DEFAULT_REPORT_WEEK)
    frames = [pd.DataFrame([week.kpis["this"], week.kpis["last"]]), week.regions]
    assert not check_answer(template_narrative(week), frames).unverified


def test_unsupported_llm_narrative_is_discarded(db):
    week = collect_week(db, DEFAULT_REPORT_WEEK)
    fake = SimpleNamespace(mock=False, provider="p", model="m",
                           chat=lambda *a: LLMReply("Revenue hit R$999,999 this week."))
    text, source = llm_narrative(week, fake)
    assert text == template_narrative(week) and "failed the number check" in source


def test_pdf_is_written_with_pinned_answers(db, tmp_path):
    client = LLMClient(mock=True)
    pinned = AnalyticsAgent(db, client).ask("What is the total revenue across the whole dataset?")
    path = build_report(db, DEFAULT_REPORT_WEEK, client, [pinned], tmp_path / "r.pdf")
    assert path.read_bytes()[:4] == b"%PDF" and path.stat().st_size > 10_000
