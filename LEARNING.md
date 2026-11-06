# Learning Notes

Plain-language notes on how this project works and why it was built this way, so you can explain it to a client without reading the code.

## The one-minute version

A person asks a business question in English. The system writes a database query, runs it safely, looks at the result, fixes it if needed, and answers with the numbers and the exact queries behind them.

The hard part is not getting an AI to write SQL. The hard part is that **SQL can run perfectly and still be wrong**. In this project's test, the AI's SQL ran on all 27 answerable questions with the plain prompt, but only 18 returned the right result. Telling it what the business terms mean raised that to 25.

**Line for clients:** "The model already knows SQL. What it doesn't know is your business, and that's where the accuracy comes from."

## Key concepts

### The agent loop

A chatbot answers once. An agent works in a loop: try something, look at the result, decide what to do next.

Here the model has two tools, `run_sql` and `final_answer`. It can run a query, see an error, correct itself, run a follow-up, then finish. Plain code runs the loop, and the model only chooses the next step.

The loop has a hard limit of 6 model calls per question. Every call costs money, and a confused model could otherwise go round forever. On the last call the model is told to answer now. If it still doesn't, the program stops anyway and says so.

### Why "read-only" is not enough

"Just give it a read-only connection" is the obvious answer, and it's incomplete. A read-only DuckDB connection protects the database, not the machine around it. I tested this: with read-only alone, a query read a local file with `read_text`, and `COPY ... TO` wrote a new file to disk. So a bad query could still leak your `.env` secrets file.

So there are three layers, and each one assumes the others might fail:

1. **Validator.** Checks the SQL before it runs. It uses an allow-list: only known-good things pass, and anything it can't understand is refused. Block-lists fail because you can't list every bad thing in advance.
2. **Connection.** Read-only, external file and network access switched off, and the settings locked so a query can't switch them back on.
3. **Limits.** A timeout and a row cap, so a bad query can't hang the system or flood it.

This is called **defence in depth**. The tests attack layer 2 directly, skipping layer 1, to prove a bug in the validator wouldn't be enough to cause harm.

One detail worth knowing: DuckDB has no built-in query timeout, so a watchdog thread cancels a query after 10 seconds.

### The semantic layer: teaching the model your business

The "enhanced" prompt adds three things to the bare table list:

- **Descriptions** of what each column means.
- **Metric definitions**: what "revenue" means here, exactly.
- **Worked examples** of good queries, known as few-shot examples.

This data shows why it matters. "Revenue" could reasonably be any of these:

| Definition | Total |
|---|---|
| Item prices, all orders | R$13.59M |
| Item prices, excluding canceled and unavailable orders (our definition) | R$13.49M |
| Payments received, which include freight | R$16.01M |

None of these is "the" answer until the business decides. Without being told, the model guesses, and it guesses differently from one question to the next. Every one of the 8 questions the enhanced prompt won came down to a definition like this.

### The join trap (fan-out)

An order can have several items and several payments. Join those two tables directly and each item row repeats for every payment row, so totals inflate. Here that turns R$13.59M into R$14.21M, a 4.5% overstatement. **No error appears**: the query runs and the number looks plausible. This is the most common way real analytics goes silently wrong, and the fix is to reduce one side to one row per order first.

### Pinning "today"

The data ends in 2018, so "last quarter" relative to the real date means nothing. The system fixes "today" at 2018-08-31.

That wasn't enough. In the first live test the model treated Q3 2018 as last quarter, even though Q3 was only two-thirds finished, then reported a huge fall for the Southeast. The answer was confident and wrong. The fix was to define "last quarter" as the last *completed* quarter. It's a good story for clients: partial periods are a classic reporting mistake, for people too.

### "Fell most" has two answers

North fell most in percentage terms (−14.2%). Northeast fell most in money (−R$10,673). Both are correct, so a good analyst reports both. The scoring accepts either reading, so that the test doesn't favour one prompt.

### Answering "why" honestly

Data shows what moved, not what caused it. If order volume fell and average order value rose, that's a real finding, but the reason is still unknown. The agent is told to name the drivers and not to claim causes. Promising causes from sales data alone would be overselling.

### The number check

LLMs can state a number that was never in the data. So after each answer, plain code extracts every figure and looks for it in the query results, allowing for rounding and simple calculations. Anything it can't find is flagged on screen.

It caught a real case: the model wrote R$128.12 when the true value was 128.11. The cause was my own rounding before passing the data to the model.

Know its limit: it proves a number exists in a result, not that the sentence around it is correct. It's a useful net, not a guarantee.

## How the evaluation works

### Two kinds of accuracy

- **Execution accuracy:** did a query return the correct data?
- **Answer accuracy:** did the final text state the correct facts?

They can disagree. In one test the correct data had been fetched, but the final answer quoted a different query. Users read the answer, so both are measured.

Scoring is plain code with no AI judge. That makes it free and repeatable, and anyone can audit it. The drawback is rigidity, which leads to the next point.

### Why the flagship question counts as a miss

The enhanced answer to the headline question is good: both regions, correct figures. But it explained the fall by product category, and the checklist fixed before the run also required order counts. So it scores as a miss.

I left that alone on purpose. Changing the marking after seeing the results would make the score meaningless. Being willing to report that is what makes the other numbers believable.

### Gold answers: "double-derived" is not "hand-checked"

Each correct answer was worked out twice with no shared code: once in SQL, and once in pandas from the original CSV files. If two separate routes agree, a coding mistake is unlikely.

It isn't proof. Both routes were written by the same author, so a shared misreading of a question would pass both. That's why the README says "independently double-derived" and doesn't claim a person checked them.

### Is the improvement real?

Going from 67% to 90% sounds big, but 30 questions is a small sample, so the honest ranges are wide (49-81% and 74-97%).

The right tool here is a **paired test**. Both prompts answered the same questions, so only the questions where they disagreed carry information. That was 8 wins against 1, with p = 0.039: unlikely to be luck, though not a proof. With 30 questions you need at least 6 wins with no losses to reach significance at all.

Be careful what you claim. It's one model, one dataset and one run.

## Design decisions

| Decision | Why | Trade-off |
|---|---|---|
| DuckDB over PostgreSQL | No server, one file, fast for analytics, easy for anyone to reproduce | A real client would likely use their own warehouse, and the guardrails would need rebuilding on that platform's permissions |
| Tool calling over parsing text | The provider returns structured arguments, so less can break | Each provider needs a small adapter |
| Deterministic scoring over an AI judge | Free, repeatable, auditable | Rigid: it can mark a good answer down, as with the flagship question |
| Fixed SQL for the weekly report | A recurring report must be exact and identical each week | Changing it means editing code |
| Cache every reply to disk | Reruns cost nothing, and results can be reproduced | The cache must be keyed on everything that affects the reply |
| `--mock` mode | The whole system runs with no key and no cost | It proves the plumbing, not the accuracy, and its scores must never be quoted as results |
| One DuckDB thread | Makes query output repeatable | Slower on big data; irrelevant at this size |

The last row came from a real bug. With four threads, an unordered query returned its rows in 13 different orders over 25 runs. The rows go into the prompt, so the cache missed and a replay would have made paid calls. A cache-only mode exposed this before it cost anything.

## What to tell a client

- **Expect the setup work to be definitions, not code.** Agreeing what "revenue" and "late" mean is where the accuracy comes from.
- **Show every query.** People trust an answer they can inspect, and analysts can check it in seconds.
- **Don't promise perfection.** About 90% on a test set means roughly 1 in 10 answers needs catching, so keep a person in the loop for decisions that matter.
- **Test on your own questions.** Build a small set with known answers before going live. Without one, you can't tell whether a change helped.
- **Use database permissions as the real safety boundary.** Give the agent a dedicated read-only account, and treat the SQL validator as a second line of defence.

## Likely questions

**Can it delete my data?** The connection is read-only, and the test suite tries this directly. On a client's system, also use a read-only database account.

**Does my data go to the AI provider?** Yes, up to 50 rows of each query result, plus the schema. For sensitive data, use a provider agreement that fits, or a self-hosted model, and restrict which tables it can see.

**What does it cost?** The full 60-run evaluation took 201 API calls, about 3 to 4 per question. Check current model pricing for a figure, and note that repeated questions are free from the cache.

**Why not just use a dashboard?** Dashboards answer known questions well. This handles the unplanned follow-up. They complement each other.

**Would a bigger model fix the errors?** It might help with mistakes like M06, where the model ignored a definition it had been given. It can't fix missing definitions, because that knowledge isn't in the model. I haven't tested larger models here.
