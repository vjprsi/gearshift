# Measuring on your own tasks

A mixed-model setup is only worth keeping if it beats every single-model run on your own workload, on cost per finished task. `gearshift bench` runs that comparison so you don't have to take my table on faith.

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
| `regex` | The pattern matches (multiline, dot matches newline). |
| `json_equals` | The first JSON object or array in the output equals the value. |
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
