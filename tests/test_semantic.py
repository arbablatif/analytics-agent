"""The prompt knowledge must match the real schema and must not leak the evaluation."""
from __future__ import annotations

import re

import pytest
import yaml

from analytics_agent.config import ALLOWED_TABLES, DB_PATH, ROOT
from analytics_agent.db import ReadOnlyDB
from analytics_agent.prompts import BASE_INSTRUCTIONS, build_system_prompt, quarter_labels
from analytics_agent.schema import inspect_schema, load_few_shot, load_semantics

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="run scripts/build_db.py first")


def questions() -> list[dict]:
    return yaml.safe_load((ROOT / "eval" / "questions.yaml").read_text(encoding="utf-8"))["questions"]


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", text.lower()))


@pytest.fixture(scope="module")
def schema():
    db = ReadOnlyDB()
    yield inspect_schema(db)
    db.close()


def test_schema_inspection_finds_every_table(schema):
    assert set(schema) == ALLOWED_TABLES


def test_descriptions_match_the_real_schema(schema):
    described = load_semantics()["tables"]
    assert set(described) == set(schema)
    for table, info in described.items():
        assert set(info["columns"]) == {c.name for c in schema[table]}, table


def test_plain_prompt_holds_no_business_knowledge(schema):
    plain = build_system_prompt(schema, "plain")
    for phrase in ("METRIC DEFINITIONS", "PITFALLS", "WORKED EXAMPLES", "customer_unique_id: The real person"):
        assert phrase not in plain


def test_conditions_differ_only_in_the_knowledge_block(schema):
    plain, enhanced = (build_system_prompt(schema, c) for c in ("plain", "enhanced"))
    assert plain.startswith(BASE_INSTRUCTIONS) and enhanced.startswith(BASE_INSTRUCTIONS)
    assert len(enhanced) > len(plain)


def test_last_quarter_is_the_last_completed_one():
    assert quarter_labels("2018-08-31") == ("Q3 2018", "Q2 2018")
    assert quarter_labels("2018-02-10") == ("Q1 2018", "Q4 2017")
    assert "Q2 2018" in BASE_INSTRUCTIONS


def test_few_shot_examples_are_valid_sql():
    db = ReadOnlyDB()
    for example in load_few_shot():
        result = db.run(example["sql"])
        assert result.ok and result.row_count > 0, example["question"]
    db.close()


def test_few_shot_examples_do_not_overlap_the_evaluation():
    for example in load_few_shot():
        shot_q, shot_sql = words(example["question"]), words(example["sql"])
        for q in questions():
            overlap = len(shot_q & words(q["question"])) / len(shot_q | words(q["question"]))
            assert overlap < 0.5, (example["question"], q["id"])
            if "gold_sql" in q:
                assert shot_sql != words(q["gold_sql"]), q["id"]


def test_evaluation_set_has_the_promised_shape():
    items = questions()
    tiers = [q["tier"] for q in items]
    assert len(items) == 30 and len({q["id"] for q in items}) == 30
    assert (tiers.count("easy"), tiers.count("medium"), tiers.count("hard"), tiers.count("unanswerable")) == (10, 11, 6, 3)
    for q in items:
        if not q.get("unanswerable"):
            rubrics = q["expected"].get("any_of", [q["expected"]])
            assert any(r.get("numbers") for r in rubrics), f"{q['id']} has no required figure"
