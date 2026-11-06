# Evaluation results: mock pipeline test, NOT model accuracy

Paid API calls in this run: 0.

| Metric | plain | enhanced |
|---|---|---|
| Query ran (of answerable) | 27/27 (100%) | 27/27 (100%) |
| **Execution accuracy** (result matches gold) | 27/27 (100%) | 27/27 (100%) |
| 95% interval | 88-100% | 88-100% |
| **Answer accuracy** (all 30) | 29/30 (97%) | 29/30 (97%) |
| 95% interval | 83-99% | 83-99% |
| First query ran without error | 25/27 (93%) | 25/27 (93%) |
| Failed or blocked queries | 2 | 2 |
| Blocked by guardrail | 1 | 1 |
| Hit the call limit | 0 | 0 |
| Average model calls per question | 1.97 | 1.97 |
| Figures not traced to a result | 0 of 80 | 0 of 80 |

## Answer accuracy by difficulty

| Tier | plain | enhanced |
|---|---|---|
| easy | 10/10 (100%) | 10/10 (100%) |
| medium | 11/11 (100%) | 11/11 (100%) |
| hard | 5/6 (83%) | 5/6 (83%) |
| unanswerable | 3/3 (100%) | 3/3 (100%) |

## Paired comparison (enhanced vs plain)

Exact McNemar test. Only questions where the two prompts disagree carry information.

| Metric | Only plain correct | Only enhanced correct | p-value |
|---|---|---|---|
| Execution accuracy | 0 | 0 | 1.000 |
| Answer accuracy | 0 | 0 | 1.000 |

## Per question

| ID | Tier | plain SQL / answer | enhanced SQL / answer |
|---|---|---|---|
| E01 | easy | yes / yes | yes / yes |
| E02 | easy | yes / yes | yes / yes |
| E03 | easy | yes / yes | yes / yes |
| E04 | easy | yes / yes | yes / yes |
| E05 | easy | yes / yes | yes / yes |
| E06 | easy | yes / yes | yes / yes |
| E07 | easy | yes / yes | yes / yes |
| E08 | easy | yes / yes | yes / yes |
| E09 | easy | yes / yes | yes / yes |
| E10 | easy | yes / yes | yes / yes |
| M01 | medium | yes / yes | yes / yes |
| M02 | medium | yes / yes | yes / yes |
| M03 | medium | yes / yes | yes / yes |
| M04 | medium | yes / yes | yes / yes |
| M05 | medium | yes / yes | yes / yes |
| M06 | medium | yes / yes | yes / yes |
| M07 | medium | yes / yes | yes / yes |
| M08 | medium | yes / yes | yes / yes |
| M09 | medium | yes / yes | yes / yes |
| M10 | medium | yes / yes | yes / yes |
| M11 | medium | yes / yes | yes / yes |
| H01 | hard | yes / no | yes / no |
| H02 | hard | yes / yes | yes / yes |
| H03 | hard | yes / yes | yes / yes |
| H04 | hard | yes / yes | yes / yes |
| H05 | hard | yes / yes | yes / yes |
| H06 | hard | yes / yes | yes / yes |
| U01 | unanswerable | - / yes | - / yes |
| U02 | unanswerable | - / yes | - / yes |
| U03 | unanswerable | - / yes | - / yes |
