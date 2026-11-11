# Real results

First runs against the live API, 2026-11-10. One run each. I'm publishing them as they came out, including the parts that don't flatter the router.

## The judge: first run, nothing tuned afterwards

`gearshift eval <set> --judge api`. The judge is Haiku 5.5 at low effort, asked only when the keyword rules are not confident.

| Set | Tasks | Keywords only | Keywords + judge | Under-routed with judge |
|---|---|---|---|---|
| `cases_multi.jsonl` (German, French, Italian, Hindi, Japanese) | 25 | 32.0% | **100%** | 0 |
| `cases_es_blind.jsonl` (Spanish, Portuguese) | 20 | 90.0% | **100%** | 0 |
| `cases_holdout4.jsonl` (English, never tuned against) | 40 | 90.0% | **97.5%** | 1 |
| `cases_holdout3.jsonl` (English) | 40 | 100% | 100% | 0 |

The one miss left on set 4 is a task the keywords were confident and wrong about, so the judge was never asked. Calls took about a second each.

Read these with care. I wrote the tasks and the labels, the sets are small, and it is one run. What it does show is that the plumbing works against the real API and that Haiku reads task types in five languages it was given no vocabulary for.

One thing I noticed and left alone: for "Behebe den Fehler im Zahlungsmodul." (fix the bug in the payments module) the judge answered `unsure` with `changes_code` and `high_stakes` both true. That is a fair answer to a vague task, and it falls back to Opus.

## Benchmark pack: 31 tasks, every config

- Date: 2026-11-10
- Tasks: 31 (gearshift pack)
- Models: haiku = `claude-haiku-5-5`, sonnet = `claude-sonnet-5-5`, opus = `claude-opus-5-5`, fable = `claude-fable-5-2`
- Prices: list prices in `models.json` on that date

| Config | Passed | Cost per finished task | Total | p90 per task | Attempts |
|---|---|---|---|---|---|
| `haiku@medium` | 100% | $0.0002 | $0.0052 | $0.0005 | 1.00 |
| `sonnet@medium` | 100% | $0.0023 | $0.0709 | $0.0049 | 1.00 |
| `opus@medium` | 100% | $0.0065 | $0.2004 | $0.0166 | 1.00 |
| `fable@high` | 100% | $0.0193 | $0.5991 | $0.0411 | 1.00 |
| `router` | 100% | $0.0022 | $0.0684 | $0.0046 | 1.00 |
| `cascade` | 100% | $0.0023 | $0.0703 | $0.0047 | 1.00 |

Cost per finished task is total spend, failed attempts included, divided by the tasks that passed.
One run. Model output varies between runs, so small differences here mean nothing.

### What this says, honestly

- Everything passed everything. **The pack is too easy to separate the tiers**, so the only thing it measures is cost.
- On cost, the router came out 8.8x cheaper than always-Fable and 2.9x cheaper than always-Opus, about level with always-Sonnet.
- And **always-Haiku was 13x cheaper than the router and passed all 31**. On small, self-contained tasks like these, sending "edit" to Sonnet and "build" to Opus is paying for capability the task does not need. The routing table comes from Anthropic's numbers on agent work in real repositories. This pack is not that, and the table over-spends here.
- `cascade` cost the same as `router` because nothing failed, so nothing climbed.

### The check I changed after the first run

The first run used exact match on the eight reasoning tasks ("reply with the number only"). Haiku passed all eight. Sonnet 5.5 at medium effort failed all eight: it wrote out its working and then the right number, so the answer was correct and the format was not. I changed those eight checks to compare the last line (`final`) and re-ran everything; the table above is the re-run. If your pipeline parses the whole reply, the first result is the one that matters to you: Sonnet 23 of 31, Haiku 31 of 31.

### Harder questions did not separate them either

I tried eight harder exact-answer questions (counting permutations, integer partitions of 50, digit sum of 2^1000 and so on) on Haiku, Sonnet and Opus at medium effort. All three got the same seven right and missed the same one.

So single-call questions with a checkable answer do not show where this generation's cheaper models break. The difference Anthropic reports is in long agent runs: many steps, tools, a real codebase. Proving or disproving the table needs that kind of benchmark, and this repo does not have one yet.

## What it cost

Roughly $1.50 at list price for everything on this page, by my own tally of the runs.
