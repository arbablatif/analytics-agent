"""Run the 30-question evaluation and compare the plain and enhanced prompts.

    python -m eval.run_eval --mock                 # no API calls; tests the pipeline only
    python -m eval.run_eval --live                 # prints the API budget and stops
    python -m eval.run_eval --live --confirm-api   # really calls the API
    python -m eval.run_eval --live --cache-only    # re-score saved replies; cannot call the API

A live run needs --confirm-api because it is an API loop. It stops at --max-api-calls, and
after three consecutive provider errors. Replies are cached, so a rerun or a resume is free.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time

import yaml
from scipy.stats import binomtest

from analytics_agent.agent import AgentResult, AnalyticsAgent
from analytics_agent.config import MAX_LLM_CALLS_PER_QUESTION, RESULTS_DIR, ROOT
from analytics_agent.db import ReadOnlyDB
from analytics_agent.llm import LLMClient, LLMError
from eval.scorer import answer_correct, execution_match

CONDITIONS = ["plain", "enhanced"]


def load_questions() -> list[dict]:
    return yaml.safe_load((ROOT / "eval" / "questions.yaml").read_text(encoding="utf-8"))["questions"]


def wilson_interval(successes: int, total: int) -> tuple[float, float]:
    """95% Wilson interval, which behaves sensibly at small n and near 0% or 100%."""
    if total == 0:
        return (0.0, 0.0)
    z, p = 1.96, successes / total
    centre = (p + z * z / (2 * total)) / (1 + z * z / total)
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return (max(0.0, centre - half), min(1.0, centre + half))


def mcnemar_exact(a: list[bool], b: list[bool]) -> tuple[int, int, float]:
    """Paired exact test. Only questions where the two conditions disagree carry information."""
    only_a = sum(x and not y for x, y in zip(a, b))
    only_b = sum(y and not x for x, y in zip(a, b))
    if only_a + only_b == 0:
        return only_a, only_b, 1.0
    return only_a, only_b, binomtest(only_a, only_a + only_b, 0.5).pvalue


def score_one(question: dict, result: AgentResult, gold) -> dict:
    """Turn one agent run into a scored record."""
    frames = [s.result.df for s in result.ok_steps]
    statuses = [s.result.status for s in result.steps]
    answerable_question = not question.get("unanswerable")
    return {
        "id": question["id"], "tier": question["tier"], "question": question["question"],
        "query_ran": bool(frames) if answerable_question else None,
        "execution_correct": execution_match(frames, gold, question["match"]) if answerable_question else None,
        "answer_correct": answer_correct(result.answer, result.answerable, question),
        "first_query_ok": bool(statuses) and statuses[0] == "ok",
        "queries": len(statuses), "failed_queries": sum(s != "ok" for s in statuses),
        "blocked_queries": statuses.count("blocked"), "llm_calls": result.llm_calls,
        "stop_reason": result.stop_reason, "answerable": result.answerable,
        "figures": result.grounding.total, "figures_unverified": result.grounding.unverified,
        "answer": result.answer,
        "sql": [{"id": s.query_id, "status": s.result.status, "sql": s.result.sql, "error": s.result.error}
                for s in result.steps],
    }


def summarise(records: list[dict]) -> dict:
    answerable = [r for r in records if r["query_ran"] is not None]
    n, m = len(answerable), len(records)
    executed = sum(r["query_ran"] for r in answerable)
    correct_sql = sum(r["execution_correct"] for r in answerable)
    correct_answers = sum(r["answer_correct"] for r in records)
    figures = sum(r["figures"] for r in records)
    unverified = sum(len(r["figures_unverified"]) for r in records)
    by_tier: dict[str, list[int]] = {}
    for r in records:
        tier = by_tier.setdefault(r["tier"], [0, 0])
        tier[0] += r["answer_correct"]
        tier[1] += 1
    return {
        "n_answerable": n, "n_total": m, "query_ran": executed, "execution_correct": correct_sql,
        "answer_correct": correct_answers,
        "execution_ci": wilson_interval(correct_sql, n), "answer_ci": wilson_interval(correct_answers, m),
        "first_query_ok": sum(r["first_query_ok"] for r in answerable),
        "failed_queries": sum(r["failed_queries"] for r in records),
        "blocked_queries": sum(r["blocked_queries"] for r in records),
        "call_limit_hits": sum(r["stop_reason"] == "call_limit" for r in records),
        "avg_llm_calls": sum(r["llm_calls"] for r in records) / max(m, 1),
        "figures": figures, "figures_unverified": unverified, "by_tier": by_tier,
    }


def render_markdown(results: dict, summaries: dict, label: str, api_calls: int) -> str:
    def pct(a: int, b: int) -> str:
        return f"{a}/{b} ({100 * a / b:.0f}%)" if b else "n/a"

    def interval(ci) -> str:
        return f"{100 * ci[0]:.0f}-{100 * ci[1]:.0f}%"

    names = list(summaries)
    lines = [f"# Evaluation results: {label}", "", f"Paid API calls in this run: {api_calls}.", "",
             "| Metric | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    rows = [
        ("Query ran (of answerable)", lambda s: pct(s["query_ran"], s["n_answerable"])),
        ("**Execution accuracy** (result matches gold)", lambda s: pct(s["execution_correct"], s["n_answerable"])),
        ("95% interval", lambda s: interval(s["execution_ci"])),
        ("**Answer accuracy** (all 30)", lambda s: pct(s["answer_correct"], s["n_total"])),
        ("95% interval", lambda s: interval(s["answer_ci"])),
        ("First query ran without error", lambda s: pct(s["first_query_ok"], s["n_answerable"])),
        ("Failed or blocked queries", lambda s: str(s["failed_queries"])),
        ("Blocked by guardrail", lambda s: str(s["blocked_queries"])),
        ("Hit the call limit", lambda s: str(s["call_limit_hits"])),
        ("Average model calls per question", lambda s: f"{s['avg_llm_calls']:.2f}"),
        ("Figures not traced to a result", lambda s: f"{s['figures_unverified']} of {s['figures']}"),
    ]
    for title, fn in rows:
        lines.append(f"| {title} | " + " | ".join(fn(summaries[n]) for n in names) + " |")

    tiers = ["easy", "medium", "hard", "unanswerable"]
    lines += ["", "## Answer accuracy by difficulty", "", "| Tier | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for tier in tiers:
        cells = [pct(*summaries[n]["by_tier"][tier]) if tier in summaries[n]["by_tier"] else "n/a" for n in names]
        lines.append(f"| {tier} | " + " | ".join(cells) + " |")

    if len(names) == 2:
        a, b = names
        lines += ["", f"## Paired comparison ({b} vs {a})", "",
                  "Exact McNemar test. Only questions where the two prompts disagree carry information.", "",
                  f"| Metric | Only {a} correct | Only {b} correct | p-value |", "|---|---|---|---|"]
        for title, key in (("Execution accuracy", "execution_correct"), ("Answer accuracy", "answer_correct")):
            left = [r for r in results[a] if r[key] is not None]
            right = [r for r in results[b] if r[key] is not None]
            x, y, p = mcnemar_exact([r[key] for r in left], [r[key] for r in right])
            lines.append(f"| {title} | {x} | {y} | {p:.3f} |")

    lines += ["", "## Per question", "", "| ID | Tier | " + " | ".join(f"{n} SQL / answer" for n in names) + " |",
              "|---|---|" + "---|" * len(names)]
    mark = {True: "yes", False: "no", None: "-"}
    for position, record in enumerate(results[names[0]]):
        cells = [f"{mark[results[n][position]['execution_correct']]} / {mark[results[n][position]['answer_correct']]}"
                 for n in names]
        lines.append(f"| {record['id']} | {record['tier']} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mock", action="store_true", help="No API calls; tests the pipeline, not a model")
    mode.add_argument("--live", action="store_true", help="Use the provider in .env")
    parser.add_argument("--confirm-api", action="store_true", help="Actually make the live API calls")
    parser.add_argument("--cache-only", action="store_true", help="Replay cached replies, never call the API")
    parser.add_argument("--conditions", nargs="+", choices=CONDITIONS, default=CONDITIONS)
    parser.add_argument("--ids", nargs="+", help="Only these question ids")
    parser.add_argument("--max-api-calls", type=int, default=360, help="Stop once this many paid calls are made")
    parser.add_argument("--sleep", type=float, default=0.0, help="Seconds to wait between questions")
    parser.add_argument("--tag", default="", help="Suffix for the output files")
    args = parser.parse_args()

    questions = load_questions()
    if args.ids:
        questions = [q for q in questions if q["id"] in args.ids]
    worst_case = len(questions) * len(args.conditions) * MAX_LLM_CALLS_PER_QUESTION
    if args.live and not (args.confirm_api or args.cache_only):
        print(f"Live run: {len(questions)} questions x {len(args.conditions)} conditions, at most "
              f"{MAX_LLM_CALLS_PER_QUESTION} calls each, so at most {worst_case} API calls "
              f"(stops at {args.max_api_calls}).\nNothing was called. Add --confirm-api to run it.")
        return 0

    llm = LLMClient(mock=args.mock, cache_only=args.cache_only)
    db = ReadOnlyDB()
    gold = {q["id"]: db.run(q["gold_sql"]).df for q in questions if not q.get("unanswerable")}
    print(f"Model: {llm.provider} / {llm.model}")

    results: dict[str, list[dict]] = {}
    consecutive_errors, stopped = 0, ""
    for condition in args.conditions:
        agent = AnalyticsAgent(db, llm, condition)
        results[condition] = []
        for q in questions:
            if llm.api_calls >= args.max_api_calls:
                stopped = f"reached --max-api-calls {args.max_api_calls}"
                break
            start = time.perf_counter()
            try:
                result = agent.ask(q["question"])
                consecutive_errors = 0
            except LLMError as exc:
                consecutive_errors += 1
                print(f"  {condition} {q['id']}: {str(exc)[:200]}")
                if consecutive_errors >= 3:
                    stopped = "three consecutive provider errors"
                    break
                continue
            record = score_one(q, result, gold.get(q["id"]))
            results[condition].append(record)
            print(f"  {condition:<9}{q['id']} sql={record['execution_correct']!s:<6}"
                  f"answer={record['answer_correct']!s:<6}calls={record['llm_calls']} "
                  f"api_total={llm.api_calls} {time.perf_counter() - start:.1f}s", flush=True)
            if args.sleep:
                time.sleep(args.sleep)
        if stopped:
            break
    db.close()

    # A partial run must never replace a complete one, so it gets its own file name.
    complete = (not stopped and set(results) == set(args.conditions)
                and all(len(records) == len(questions) for records in results.values()))
    stem = f"eval_{'mock' if args.mock else 'live'}{'_' + args.tag if args.tag else ''}"
    label = "mock pipeline test, NOT model accuracy" if args.mock else f"{llm.provider} / {llm.model}"
    if not complete:
        print(f"INCOMPLETE RUN{': ' + stopped if stopped else ''}. Rerun the same command to resume from cache.")
        label += " (INCOMPLETE)"
        stem += "_partial"
    summaries = {name: summarise(records) for name, records in results.items() if records}
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / f"{stem}.json").write_text(json.dumps(
        {"label": label, "api_calls": llm.api_calls, "summaries": summaries, "results": results},
        indent=2, default=str), encoding="utf-8")
    if complete:
        (RESULTS_DIR / f"{stem}.md").write_text(render_markdown(results, summaries, label, llm.api_calls),
                                                encoding="utf-8")
    for name, s in summaries.items():
        print(f"{name}: execution {s['execution_correct']}/{s['n_answerable']}, "
              f"answer {s['answer_correct']}/{s['n_total']}, avg calls {s['avg_llm_calls']:.2f}")
    print(f"Paid API calls: {llm.api_calls}")
    return 0 if complete else 1


if __name__ == "__main__":
    sys.exit(main())
