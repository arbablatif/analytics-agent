"""Command line entry point.

    python -m analytics_agent.cli ask "Which region's revenue fell most last quarter and why?"
    python -m analytics_agent.cli ask "..." --mock
    python -m analytics_agent.cli report --week 2018-08-13 --mock
"""
from __future__ import annotations

import argparse
import sys

from .agent import AgentResult, AnalyticsAgent
from .config import DEFAULT_REPORT_WEEK
from .db import ReadOnlyDB
from .llm import LLMClient
from .report import build_report


def print_result(result: AgentResult, llm: LLMClient) -> None:
    for step in result.steps:
        print(f"\n--- {step.query_id} [{step.result.status}] {step.purpose}")
        print(step.result.sql.strip())
        if step.result.ok:
            print(f"-> {step.result.row_count} rows in {step.result.elapsed_ms} ms")
            print(step.result.df.head(10).to_string(index=False))
        else:
            print(f"-> {step.result.error}")
        for note in step.checks:
            print(f"!! {note}")
    print(f"\n=== Answer ===\n{result.answer}\n")
    g = result.grounding
    print(f"Numbers: {len(g.grounded)} found in results, {len(g.derived)} derived, {len(g.unverified)} unverified"
          + (f" {g.unverified}" if g.unverified else ""))
    print(f"Model calls: {result.llm_calls} ({llm.api_calls} paid API calls this run)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Natural-language analyst over the Olist database.")
    commands = parser.add_subparsers(dest="command", required=True)
    ask = commands.add_parser("ask", help="Answer one question")
    ask.add_argument("question")
    ask.add_argument("--condition", choices=["plain", "enhanced"], default="enhanced")
    ask.add_argument("--mock", action="store_true", help="Run with no API calls")
    report = commands.add_parser("report", help="Write the weekly PDF")
    report.add_argument("--week", default=DEFAULT_REPORT_WEEK, help="Monday of the report week, YYYY-MM-DD")
    report.add_argument("--mock", action="store_true", help="Run with no API calls")
    args = parser.parse_args()

    llm = LLMClient(mock=args.mock)
    db = ReadOnlyDB()
    try:
        if args.command == "ask":
            print_result(AnalyticsAgent(db, llm, args.condition).ask(args.question), llm)
        else:
            print(f"Wrote {build_report(db, args.week, llm)}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
