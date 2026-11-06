"""The analyst loop: write SQL, run it behind the guardrails, check it, retry, answer.

The loop is bounded by MAX_LLM_CALLS_PER_QUESTION. Failed and blocked queries stay in the
trace, because the UI promises to show every query, not only the ones that worked.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

import pandas as pd

from .config import MAX_LLM_CALLS_PER_QUESTION, MAX_ROWS_TO_LLM
from .db import QueryResult, ReadOnlyDB
from .grounding import GroundingReport, check_answer
from .llm import LLMClient
from .prompts import TOOLS, build_system_prompt
from .schema import inspect_schema


@dataclass
class Step:
    """One query attempt in the trace."""
    query_id: str
    purpose: str
    result: QueryResult
    checks: list[str] = field(default_factory=list)


@dataclass
class AgentResult:
    question: str
    answer: str
    answerable: bool
    steps: list[Step]
    grounding: GroundingReport
    chart_query: str = ""
    llm_calls: int = 0
    stop_reason: str = "answered"  # answered | call_limit

    @property
    def ok_steps(self) -> list[Step]:
        return [s for s in self.steps if s.result.ok]

    def chart_step(self) -> Step | None:
        """The step to chart: the one the model named, otherwise the last that worked."""
        for step in self.ok_steps:
            if step.query_id == self.chart_query:
                return step
        return self.ok_steps[-1] if self.ok_steps else None


def result_checks(result: QueryResult) -> list[str]:
    """Automatic sanity checks on a result. Warnings go back to the model."""
    if not result.ok:
        return []
    notes = []
    if result.row_count == 0:
        notes.append("The query returned no rows. Check the filters and date range before concluding.")
    if result.truncated:
        notes.append("The result hit the row limit and was cut off. Aggregate it further.")
    for column in result.df.columns:
        if result.row_count and result.df[column].isna().all():
            notes.append(f"Column '{column}' is entirely NULL. A join or filter may be wrong.")
    return notes


def _json_safe(value):
    """Convert one result cell to something the LLM APIs can serialise."""
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, float):
        # Six places: at four, 128.114993 became 128.115, which the model then rounded to 128.12.
        return None if math.isnan(value) or math.isinf(value) else round(value, 6)
    if hasattr(value, "item"):  # numpy scalar
        return _json_safe(value.item())
    if isinstance(value, (bool, int, str)):
        return value
    return str(value)  # timestamps, decimals


def tool_payload(step: Step) -> dict:
    """What the model sees after a query: bounded, and tagged with the citation id."""
    result = step.result
    if not result.ok:
        return {"query_id": step.query_id, "status": result.status, "error": result.error}
    rows = result.df.head(MAX_ROWS_TO_LLM).astype(object).values.tolist()
    return {
        "query_id": step.query_id,
        "status": "ok",
        "columns": [str(c) for c in result.df.columns],
        "rows": [[_json_safe(v) for v in row] for row in rows],
        "row_count": result.row_count,
        "rows_shown": min(result.row_count, MAX_ROWS_TO_LLM),
        "warnings": step.checks,
    }


class AnalyticsAgent:
    def __init__(self, db: ReadOnlyDB, llm: LLMClient, condition: str = "enhanced",
                 max_calls: int = MAX_LLM_CALLS_PER_QUESTION):
        self.db = db
        self.llm = llm
        self.condition = condition
        self.max_calls = max_calls
        self.system = build_system_prompt(inspect_schema(db), condition)

    def _run_query(self, args: dict, steps: list[Step]) -> Step:
        result = self.db.run(str(args.get("sql", "")))
        step = Step(f"Q{len(steps) + 1}", str(args.get("purpose", "")), result)
        step.checks = result_checks(result)
        steps.append(step)
        return step

    def ask(self, question: str) -> AgentResult:
        messages: list[dict] = [{"role": "user", "text": question}]
        steps: list[Step] = []

        for call_number in range(1, self.max_calls + 1):
            if call_number == self.max_calls:
                # Last permitted call: only the answer tool remains, so the loop must end.
                messages.append({"role": "user", "text": "This is your last step. Call final_answer "
                                                         "now using the results you already have."})
            tools = [t for t in TOOLS if t["name"] == "final_answer"] if call_number == self.max_calls else TOOLS
            reply = self.llm.chat(self.system, messages, tools)

            finish = next((c for c in reply.tool_calls if c["name"] == "final_answer"), None)
            if finish:
                return self._finish(question, finish["args"], steps, call_number)
            if not reply.tool_calls:
                if reply.text:  # some models answer in plain text despite the instruction
                    return self._finish(question, {"answer": reply.text, "answerable": True}, steps, call_number)
                messages.append({"role": "user", "text": "Your reply was empty. Call run_sql or final_answer."})
                continue

            messages.append({"role": "assistant", "text": reply.text, "tool_calls": reply.tool_calls})
            results = []
            for call in reply.tool_calls:
                if call["name"] == "run_sql":
                    payload = tool_payload(self._run_query(call["args"], steps))
                else:
                    payload = {"status": "error", "error": f"Unknown tool {call['name']}."}
                results.append({"id": call["id"], "name": call["name"], "result": payload})
            messages.append({"role": "tool", "results": results})

        return self._give_up(question, steps)

    def _finish(self, question: str, args: dict, steps: list[Step], calls: int) -> AgentResult:
        answer = str(args.get("answer", "")).strip()
        grounding = check_answer(answer, [s.result.df for s in steps if s.result.ok], question)
        return AgentResult(question, answer, bool(args.get("answerable", True)), steps, grounding,
                           str(args.get("chart_query") or ""), calls)

    def _give_up(self, question: str, steps: list[Step]) -> AgentResult:
        answer = (f"I could not finish within the limit of {self.max_calls} model calls. "
                  "The queries attempted are listed below.")
        return AgentResult(question, answer, True, steps, GroundingReport(), "", self.max_calls, "call_limit")
