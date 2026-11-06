# Evaluation results: gemini / gemini-3.5-flash-lite

Paid API calls in this run: 201.

Run once at temperature 0. Scores are the original ones. The figure counts were refreshed from the
saved traces after a false alarm was fixed: figures repeated from the question are no longer checked.

| Metric | plain | enhanced |
|---|---|---|
| Query ran (of answerable) | 27/27 (100%) | 27/27 (100%) |
| **Execution accuracy** (result matches gold) | 18/27 (67%) | 25/27 (93%) |
| 95% interval | 48-81% | 77-98% |
| **Answer accuracy** (all 30) | 20/30 (67%) | 27/30 (90%) |
| 95% interval | 49-81% | 74-97% |
| First query ran without error | 27/27 (100%) | 27/27 (100%) |
| Failed or blocked queries | 7 | 9 |
| Blocked by guardrail | 6 | 9 |
| Hit the call limit | 1 | 0 |
| Average model calls per question | 3.70 | 3.00 |
| Figures not traced to a result | 3 of 150 | 0 of 168 |

## Answer accuracy by difficulty

| Tier | plain | enhanced |
|---|---|---|
| easy | 9/10 (90%) | 10/10 (100%) |
| medium | 6/11 (55%) | 10/11 (91%) |
| hard | 2/6 (33%) | 4/6 (67%) |
| unanswerable | 3/3 (100%) | 3/3 (100%) |

## Paired comparison (enhanced vs plain)

Exact McNemar test. Only questions where the two prompts disagree carry information.

| Metric | Only plain correct | Only enhanced correct | p-value |
|---|---|---|---|
| Execution accuracy | 0 | 7 | 0.016 |
| Answer accuracy | 1 | 8 | 0.039 |

## Per question

| ID | Tier | plain SQL / answer | enhanced SQL / answer |
|---|---|---|---|
| E01 | easy | yes / yes | yes / yes |
| E02 | easy | yes / yes | yes / yes |
| E03 | easy | no / no | yes / yes |
| E04 | easy | yes / yes | yes / yes |
| E05 | easy | yes / yes | yes / yes |
| E06 | easy | yes / yes | yes / yes |
| E07 | easy | yes / yes | yes / yes |
| E08 | easy | yes / yes | yes / yes |
| E09 | easy | yes / yes | yes / yes |
| E10 | easy | yes / yes | yes / yes |
| M01 | medium | yes / yes | yes / yes |
| M02 | medium | no / no | yes / yes |
| M03 | medium | yes / no | yes / yes |
| M04 | medium | yes / yes | yes / yes |
| M05 | medium | yes / yes | yes / yes |
| M06 | medium | no / no | no / no |
| M07 | medium | yes / yes | yes / yes |
| M08 | medium | no / no | yes / yes |
| M09 | medium | yes / yes | yes / yes |
| M10 | medium | yes / yes | yes / yes |
| M11 | medium | no / no | yes / yes |
| H01 | hard | no / no | yes / no |
| H02 | hard | no / no | yes / yes |
| H03 | hard | no / no | yes / yes |
| H04 | hard | yes / yes | yes / yes |
| H05 | hard | yes / no | yes / yes |
| H06 | hard | no / yes | no / no |
| U01 | unanswerable | - / yes | - / yes |
| U02 | unanswerable | - / yes | - / yes |
| U03 | unanswerable | - / yes | - / yes |
