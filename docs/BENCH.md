# Measuring on your own tasks

A mixed-model setup is only worth keeping if it beats every single-model run on your own workload, on cost per finished task. `gearshift bench` runs that comparison so you don't have to take my table on faith.

## The built-in pack

```bash
gearshift bench pack --simulate                      # see the output without spending anything
gearshift bench pack --configs "haiku@medium,sonnet@medium,opus@medium,fable@high,router,cascade" --out results.md
```

31 tasks with automatic checks: 8 extraction and formatting tasks, 8 small Python functions, 7 harder ones (an LRU cache, an expression parser, a token bucket, splitting money without losing a cent), and 8 reasoning questions with one exact answer. Every code task has a reference solution in `tests/test_bench_pack.py` that must pass its own checks, and every numeric answer is recomputed there.

I ran it once on the real models and every config passed every task; see [RESULTS.md](RESULTS.md). So treat it as a cost check and a smoke test, not as a way to rank models.

What it is good for: a first look at cost per finished task across the tiers, and checking that a new model generation hasn't moved the table. What it is not: your workload. These are single calls with checkable answers, not agent runs in a real repository. If every config passes everything, the pack is telling you about cost only, and on tasks this size that is still the routing question.

The code tasks run the model's answer on your machine (in a temp folder, `python -I`, with a timeout). It is model-written code, so don't run the pack somewhere you wouldn't run a stranger's script.

`--out results.md` writes the table with the date, the task count and the exact model IDs, so the result can be committed and compared after the next model release.

## Task file

JSONL, one task per line:

```json
{"id": "inv-1", "prompt": "Extract the invoice number from: 'FACTURA No. FV-20391'. Reply with the number only.", "check": {"type": "equals", "value": "FV-20391"}}
{"id": "sum-1", "prompt": "...", "check": {"type": "regex", "value": "^\\d+ items?$"}}
{"id": "fn-1", "prompt": "Write a Python function slugify(s) ...", "check": {"type": "command", "value": "python checks/test_slugify.py", "timeout": 60}}
```

| Check | Passes when |
|---|---|
| `contains` | The value appears in the output (case-insensitive). |
| `equals` | The trimmed output equals the value. |
| `final` | The last non-empty line equals the value (markdown bold and a trailing period are ignored). Working shown above it is allowed. |
| `regex` | The pattern matches (multiline, dot matches newline). |
| `json_equals` | The first JSON object or array in the output equals the value. |
| `python` | The code block in the answer, with the asserts from `"tests"` appended, exits 0. |
| `command` | The shell command, given the output on stdin, exits 0. Its stderr becomes the feedback for the next attempt. |

`command` runs on your machine with your permissions. Only use task files you wrote or have read.

Use real tasks from your workload, and enough of them: Anthropic's own comparisons use hundreds, and on a set of 20 two expensive failures can be half the bill.

## Configurations

```bash
gearshift bench tasks.jsonl --configs "haiku@medium,sonnet@medium,sonnet@high,opus@medium,router,cascade,cheap-first:opus"
```

| Config | Meaning |
|---|---|
| `tier@effort` | One model, one effort, one attempt per task. |
| `router` | gearshift picks per task; one attempt. |
| `cascade` | gearshift picks, then climbs the ladder on failure (up to 3 attempts). |
| `cheap-first:tier` | Everything at low on that tier; failures re-run at high. |

Needs `pip install anthropic` and `ANTHROPIC_API_KEY`. It spends real money: every configuration runs every task.

## Reading the result

```text
config                 pass   cost/finished        total     p90/task  attempts
```

- **cost/finished**: total spend, failed attempts included, divided by tasks that passed. This is the number to compare.
- **p90/task**: the tail. A configuration with a good average and a bad tail will surprise you in production.
- **attempts**: above 1.0 means latency you are paying for the saving.

Keep `router` or `cascade` only if its cost per finished task beats every single-model row **at a pass rate you accept**. If a single model at a lower effort wins, use that and skip the complexity.

## `--simulate`

```bash
gearshift bench examples/bench_tasks.jsonl --simulate
```

replaces the API with a toy model so you can see the harness run. Its pass rates and token counts are invented constants in `bench.py`. The output says so on the first line. Do not quote it as a result.

## Caveats

- One run per task is noisy. Repeat, or use more tasks, before believing a difference of a few points.
- Sweep effort on your current model first. Anthropic's guidance is that this is often a better lever than a second model.
- A check that passes wrong answers makes cheap models look good. Read a sample of passes.
- Re-run after every model release.
