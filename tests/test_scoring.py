"""The scorers must fail wrong answers, not only pass right ones."""
from __future__ import annotations

import pandas as pd
import pytest

from analytics_agent.grounding import check_answer, extract_figures
from eval.run_eval import mcnemar_exact, wilson_interval
from eval.scorer import (answer_correct, entity_stated, execution_match, number_stated, numbers_close,
                         rows_match, values_present)

GOLD = pd.DataFrame({"region": ["North", "South"], "revenue": [100.0, 200.0]})


# ------------------------------------------------------------- execution match
def test_identical_result_matches():
    assert rows_match(GOLD.copy(), GOLD)


def test_column_names_and_order_do_not_matter():
    assert rows_match(pd.DataFrame({"r": [200.0, 100.0], "name": ["South", "North"]}), GOLD)


def test_extra_rows_and_columns_are_allowed():
    wider = pd.DataFrame({"region": ["North", "South", "East"], "revenue": [100.0, 200.0, 5.0], "n": [1, 2, 3]})
    assert rows_match(wider, GOLD)


def test_swapped_values_fail():
    assert not rows_match(pd.DataFrame({"region": ["North", "South"], "revenue": [200.0, 100.0]}), GOLD)


def test_missing_row_fails():
    assert not rows_match(GOLD.head(1), GOLD)


def test_fan_out_inflation_is_caught():
    """The items-to-payments join inflates revenue by 4.5%. That must not pass as correct."""
    gold = pd.DataFrame({"revenue": [13591644.0]})
    assert not rows_match(pd.DataFrame({"revenue": [14209115.0]}), gold)


def test_including_canceled_orders_is_caught():
    """Revenue with and without canceled orders differs by under 1%, and must still fail."""
    gold = pd.DataFrame({"revenue": [13494400.74]})
    assert not rows_match(pd.DataFrame({"revenue": [13591643.7]}), gold)


def test_rounded_value_matches():
    assert numbers_close(14.2, 14.2156)
    assert numbers_close(143.22, 143.2241)
    assert not numbers_close(14.3, 14.2156)


def test_month_text_matches_timestamp():
    gold = pd.DataFrame({"month": ["2018-01"], "revenue": [5.0]})
    assert rows_match(pd.DataFrame({"m": [pd.Timestamp("2018-01-01")], "revenue": [5.0]}), gold)


def test_values_mode_accepts_long_and_wide_layouts():
    gold = pd.DataFrame({"q1": [10.0], "q2": [20.0]})
    assert values_present([pd.DataFrame({"quarter": ["a", "b"], "revenue": [10.0, 20.0]})], gold)
    assert values_present([pd.DataFrame({"x": [10.0]}), pd.DataFrame({"y": [20.0]})], gold)
    assert not values_present([pd.DataFrame({"x": [10.0]})], gold)


def test_values_mode_accepts_fractions_for_percentages():
    assert values_present([pd.DataFrame({"share": [0.006285]})], pd.DataFrame({"pct": [0.6285]}))


def test_no_successful_query_fails():
    assert not execution_match([], GOLD, "rows")
    assert not execution_match([], GOLD, "values")


# --------------------------------------------------------------- answer scoring
@pytest.mark.parametrize("text", ["R$66,354.60", "R$ 66,355", "66354.6", "about R$66.35k", "66.4k"])
def test_number_is_found_at_any_display_precision(text):
    assert number_stated(f"Revenue was {text} [Q1].", 66354.6)


@pytest.mark.parametrize("text", ["R$66,954", "R$60,000", "R$6,635"])
def test_wrong_number_is_rejected(text):
    assert not number_stated(f"Revenue was {text} [Q1].", 66354.6)


def test_more_precise_answer_than_expected_still_counts():
    assert number_stated("0.6285% were canceled", 0.6285)
    assert number_stated("0.63% were canceled", 0.6285)


def test_citation_is_not_mistaken_for_a_figure():
    assert not number_stated("The answer is in [Q373].", 373)


def test_north_is_not_satisfied_by_northeast():
    assert not entity_stated("Northeast fell", "North")
    assert entity_stated("The North fell", "North")


def test_entity_alternatives_and_underscores():
    assert entity_stated("Rio de Janeiro improved", "RJ|Rio de Janeiro")
    assert entity_stated("health beauty leads", "health_beauty")


QUESTION = {"expected": {"entities": ["North"], "numbers": [14.2156]}}


def test_answer_needs_every_required_fact():
    assert answer_correct("North fell 14.2%", True, QUESTION)
    assert not answer_correct("North fell a lot", True, QUESTION)
    assert not answer_correct("South fell 14.2%", True, QUESTION)


def test_either_valid_reading_passes():
    question = {"expected": {"any_of": [{"entities": ["North"], "numbers": [14.2156]},
                                        {"entities": ["Northeast"], "numbers": [10673.16]}]}}
    assert answer_correct("North fell 14.2%", True, question)
    assert answer_correct("Northeast lost R$10,673", True, question)
    assert not answer_correct("Northeast fell 14.2%", True, question)


def test_refusing_an_answerable_question_fails():
    assert not answer_correct("North fell 14.2%", False, QUESTION)


def test_unanswerable_question_must_be_declined():
    question = {"unanswerable": True}
    assert answer_correct("No cost data exists.", False, question)
    assert not answer_correct("Margin is 30%.", True, question)


# -------------------------------------------------------------- number checking
RESULT = pd.DataFrame({"region": ["North", "North"], "revenue": [66354.6, 56921.9], "orders": [373, 309]})


def test_figures_in_results_are_grounded():
    report = check_answer("Revenue fell from R$66,355 to R$56,921.90 as orders went from 373 to 309 [Q1].", [RESULT])
    assert len(report.grounded) == 4 and not report.unverified


def test_invented_figure_is_flagged():
    report = check_answer("Revenue reached R$71,200 [Q1].", [RESULT])
    assert report.unverified == ["R$71,200"]


def test_calculated_figures_are_labelled_derived():
    report = check_answer("That is a fall of 14.2%, or R$9,432.70 [Q1].", [RESULT])
    assert len(report.derived) == 2 and not report.unverified


def test_scaled_figures_are_understood():
    frame = pd.DataFrame({"revenue": [2849730.26]})
    assert not check_answer("Revenue was R$2.85M.", [frame]).unverified
    assert not check_answer("Revenue was R$2.85 million.", [frame]).unverified


def test_dates_quarters_and_citations_are_not_figures():
    text = "In Q2 2018, from 2018-04-01 to 30 June, see [Q1] and [Q2], the top 5 regions"
    assert extract_figures(text) == []


def test_figures_repeated_from_the_question_are_not_flagged():
    report = check_answer("Among categories with at least 500 orders, X scored 3.61.",
                          [pd.DataFrame({"score": [3.61]})], "with at least 500 orders?")
    assert not report.unverified and report.total == 1


def test_no_figures_scores_full_rate():
    assert check_answer("No data exists for this.", []).rate == 1.0


# --------------------------------------------------------------------- statistics
def test_wilson_interval_is_sensible_at_the_extremes():
    low, high = wilson_interval(30, 30)
    assert 0.88 < low < 0.9 and high == pytest.approx(1.0)


def test_mcnemar_needs_six_clean_wins_for_significance():
    five = mcnemar_exact([False] * 5 + [True] * 25, [True] * 30)
    six = mcnemar_exact([False] * 6 + [True] * 24, [True] * 30)
    assert five[2] > 0.05 and six[2] < 0.05


def test_mcnemar_ignores_agreement():
    assert mcnemar_exact([True, False], [True, False]) == (0, 0, 1.0)
