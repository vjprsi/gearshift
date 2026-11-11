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

## First full live session (macOS, Claude Code 2.1.322, Opus 5.5 session)

About 35 minutes building a small payments wallet from scratch (accounts, transfers, idempotency keys, daily limits, fees, safe concurrent transfers, a reconciliation report; 124 tests at the end). The only instruction about routing was "delegate searches, reviews and test runs to subagents".

| | Runs | Tokens | List price |
|---|---|---|---|
| `gearshift:scout` (Haiku), test runs | 15 | 226,824 | $0.0159 |
| `gearshift:reviewer` (Sonnet), code reviews | 3 | 1,918,650 | $0.9598 |
| Total | 18 | | $0.9757 |
| Same tokens at the session's Opus rates | | | $2.5245 |

61% lower on the delegated part. The second figure reprices the same tokens; Opus would not have used exactly these tokens, so it is a comparison aid.

What I take from it:

- **Claude did the routing itself.** All 18 calls went to a pinned gearshift agent that Claude chose after reading the skill. The hook changed nothing, which means the hook's own routing, the judge, the escalate hint, retries and probing got no exercise in this session. They are still only tested against payloads.
- **Almost all the subagent money was three reviews.** 98% of the spend was the Sonnet reviewer; fifteen Haiku test runs cost under two cents together. If there is more to save, it is in how reviews are done, not in test runs.
- **The Sonnet reviewer found a real bug** in code that was already committed: with a non-UTC clock the day boundary was computed wrongly, so a daily transfer limit could be bypassed.
- ~~18 finished, none asked to escalate, none errored.~~ **Correction:** that line was wrong. Claude Code ran these subagents in the background, and for a background agent the PostToolUse hook fires when it is launched, not when it ends. 1.4.0 and earlier logged the launch as the result, so "18 finished" meant "18 launched" and an `ESCALATE:` would never have been seen. Fixed in 1.4.1: the real ending is read at SubagentStop. The cost figures are unaffected; they come from the subagent transcripts.

### The whole session, from Claude Code's own `/cost`

| Model | Role | Cost | Share |
|---|---|---|---|
| Opus 5.5 | main session | $6.39 | 87% |
| Sonnet 5.5 | 3 reviews | $0.96 | 13% |
| Haiku 5.5 | 15 test runs | $0.02 | 0.3% |
| Total | | $7.37 | |

So the 61% applies to 13% of the bill. Repricing the subagent tokens at Opus rates would have made the session about $8.92, which puts the routing at roughly 17% off the whole session. Same caveat as above: a comparison, not a measurement.

The bigger lesson is in the Opus row. Of that $6.39, about $2.82 was cache reads (14.1 million tokens over 79 requests, so around 178,000 tokens of context re-read per request), $1.87 was output and $1.68 was cache writes. **Most of the main session's cost was carrying context, not thinking.** Claude Code's own usage screen said the same thing from another angle: 45,000 tokens per request were descriptions of installed agents (about 300 of them from other plugins, seven are gearshift's).

Model routing does not touch that. Keeping the main session's context small does: clearing between tasks, removing agents and plugins that are not used, and delegating so that file reading happens in a subagent's context and not the main one.

One session, one project.

## What it cost

Roughly $1.50 at list price for everything on this page, by my own tally of the runs.
