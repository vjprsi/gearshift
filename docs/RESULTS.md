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

## Does 1.4.1 do what it says? Five headless sessions (Windows, Claude Code 2.1.322, Sonnet 5.5 session)

1.4.1 was written from one ledger and tested on payloads I typed myself. So I ran it for real: a clean copy of walletcore, a subagent asked to add one small method and a test, and a recorder hook next to gearshift's that saved every payload Claude Code sent. The check (`verify`) was a script I controlled, so I could make it fail on purpose.

| Question | Answer | Evidence |
|---|---|---|
| Is the agent id at launch the same one at SubagentStop? | Yes | 6 background agents, 6 matches. The whole 1.4.1 fix stood on this guess. |
| Do `launched` and `done` pair up in the ledger? | Yes | every `launched` has one `done` with the same tool call id and agent id |
| Does a failing check hold a background subagent? | Yes | the reason arrives in the subagent as "Stop hook feedback", it works again, and SubagentStop fires a second time with `stop_hook_active: true` |
| Held, can it fix the problem and leave? | Yes | check asked for a missing file; subagent created it; second check passed; ledger says `verify: pass` |
| Is `ESCALATE:` seen for a background agent? | Yes | 5 of 5 when the check could not pass |
| Is `ESCALATE:` seen for a foreground agent? | Not in these five. Yes in the 1.4.3 run below | there a subagent was told to answer `ESCALATE:`, so it shows the reading works, not that a subagent escalates on its own |
| Does the same task sent again start one rung up? | Yes, if it is sent word for word | sonnet@medium then sonnet@high, foreground and background |

What was wrong, all fixed in 1.4.2:

- **The subagent could not run the check again.** The message that sends it back quoted the command cut at 120 characters. Mine was 130. It said so itself: "the command in the hook message was truncated".
- **Nobody told the main session what `ESCALATE:` means.** For a background agent the only hook that can talk to the main session fires at launch. The subagent answered `ESCALATE:` and the main session did nothing. The session brief now says to send the task again. With that one sentence, the main session retried on its own in the next run.
- **A natural retry was not recognised.** When the main session retried on its own it added "a previous attempt may already have added..." and changed the description. Word overlap fell to 0.53, under the 0.6 I required, so the retry started on the same rung. A task that fell short now matches when nearly all of its words are in the new one.
- **At the ceiling a retry went down.** Failed on sonnet@medium, failed on sonnet@high, and the third try would have gone back to sonnet@medium while saying "one step up". It stays on the rung that failed now and says there is nothing above it.
- **After being held, the subagent's first reply is lost.** The main session only got the second one: "unchanged from my earlier report", a report it never saw. The message now asks for the full report again.

Three things I did not expect:

- An Agent call that does not mention `run_in_background` ran in the background. Only an explicit `false` gave me a foreground agent. So the SubagentStop path is the normal one, not the special case.
- In the foreground SubagentStop fires before PostToolUse, and a foreground subagent is not held: the main session is told not to build on the result and to redo it one step up. That note arrived and was followed.
- walletcore's own suite has one test that cannot pass on Windows (it makes a folder with `?` in the name). A `verify` command that is already failing would blame every subagent for it. Run your check once by hand before you set it.

What this does not show. The last two fixes (the looser retry match and asking for the full report) were made after the fifth session and have only run in tests built from that session's payloads. Everything here is headless, one machine, one small task, a Sonnet session, a general-purpose subagent. No interactive session, no pinned agent (see the next section for that), no probing, no `tune`. The check failed because I made it fail, so this says the plumbing works, not that `verify` catches real mistakes.

The payloads are in `tests/live_payloads.json` with local paths removed, and `tests/test_live_payloads.py` replays them.

## A pinned agent that falls short (1.4.3)

A reviewer of 1.4.2 pointed at a hole. The new line in the brief says "send the same task again and the hook starts it one step up". For gearshift's own agents that was not true: a pinned agent was returned untouched before the retry was looked at, so `gearshift:scout` sent again ran on haiku@medium again.

Whether it could be fixed depended on one thing I had never checked: does a model passed on the call win over the model in the agent's definition? Two more headless sessions, same setup.

| Call | Resolved model | Effort the subagent ran at |
|---|---|---|
| `gearshift:scout`, nothing passed | Haiku 5.5 | medium (its definition) |
| `gearshift:scout`, `model: sonnet`, `effort: high` on the call | Sonnet 5.5 | high |

It does, for both. So in 1.4.3 a gearshift agent that fell short and gets the same task again keeps its instructions and runs one rung up. Then the same thing live, scout told to answer `ESCALATE:` three times, the main session passing no model:

| Attempt | What the hook did | Ran on |
|---|---|---|
| 1 | nothing, the pin stands | haiku@medium |
| 2 | set effort | haiku@high |
| 3 | set model and effort | sonnet@medium |

Limits. The `ESCALATE:` was scripted, so this is plumbing again. One agent (scout), foreground, headless. Other people's pinned agents are not touched: I can't see their definitions and the choice is theirs. This also answers the open question for probing pinned agents (a per-call effort does override the definition), but nothing is built on it yet.

## Three half-hour builds of the same project (1.5.0)

A different project from walletcore: `shorty`, a URL shortener with an SQLite store, a CLI, an HTTP server and five milestones in a spec. Headless, Windows, Claude Code 2.1.322, Opus 5.5 main session, told to delegate searches, test runs, contained edits and a review of each milestone. `probe` 0.2, `verify` set to the project's test command, no API key so no judge. The same prompt three times:

- **Run 1**: the plugin as shipped.
- **Run 2**: `GEARSHIFT_BRIEF=0`, to see whether the hook would have to route.
- **Run 3**: a copy of the plugin with the agents and skills removed, hooks only, brief off. Runs 1 and 2 were on 1.4.3; run 3 was on the 1.5.0 code.

| | Run 1 | Run 2 | Run 3 (hooks only) |
|---|---|---|---|
| Wall time | 30.5 min | 26 min | 26 min |
| Result | 5 commits, 142 tests pass | 5 commits, 143 tests pass | 5 commits, 140 tests pass |
| Agent calls | 19 | 20 | 9 |
| Who chose where it ran | Claude, 18 pinned agents; 1 bogus bump | Claude, 20 pinned agents | hook set the model on 2, effort on 5 more; Claude named a model on 5 |
| Classes | lookup 10, review 5, build 3, edit 1 | build 5, lookup 5, review 5, edit 5 | review 5, build 2, lookup 1, edit 1 |
| Unsure or low confidence | 0 | 0 | 0 |
| More work by SendMessage | 6 (not seen by 1.4.3) | 0 | 6 (seen) |
| Foreground / background | 9 / 10 | 0 / 20 | 4 / 5 |
| Checks | 8 pass | 15 pass | 9 pass (6 of them after SendMessage jobs) |
| Sent back, real escalations | 0, 0 | 0, 0 | 0, 0 |
| Probes | 0 | 0 | 1 |
| Total cost | $4.66 | $6.60 | $5.79 |
| Main session | $1.84 (39%) | $1.85 (28%) | $1.24 (21%) |
| Subagents | $2.82 | $4.75 | $4.55 |
| Main context, start to end | 37k to 111k | 37k to 114k | 36k to 83k |

No run compacted, none reached the 120k nudge. `debug.log` had route lines only in runs 1 and 2 and no errors in any run.

What the runs showed, each one fixed or noted in 1.5.0:

- **Turning the brief off did not make the hook route.** In run 2 Claude still picked a pinned agent for all 20 calls. The agent descriptions are enough. To exercise the hook I had to take the agents away.
- **`ESCALATE: none`.** The scout's instructions say to end with an `ESCALATE:` line when a task is beyond it. Twice it ended a clean test run with "ESCALATE: none". Both were logged as escalations, one sent the next test run to haiku@high, and `tune` showed lookups falling short 20% of the time. Fixed.
- **Most of the code in run 1 was written out of sight.** The main session started one fixer and then sent it six more jobs with SendMessage: review fixes and two whole milestones. Six of ten code-changing jobs had no log line, no check and nobody reading for `ESCALATE:`. Fixed: run 3 did the same thing and all six were logged and checked.
- **Reviews were being checked.** In runs 1 and 2 nine of ten reviews read as code changes, so the test suite ran after a read-only agent. Fixed: the agent's own tool list decides.
- **Different test runs were "retries".** "Run test suite M2" was a retry of "Run test suite M1", ten calls in all. Harmless until the false escalation above turned one into a bump. Fixed.
- **Run 2's suite took 18.7 seconds** against a 25 second limit that failed silently. Now a setting, and a timeout is logged.

What run 3 showed about the hook, which had never routed real work before:

- It classified all nine calls confidently and none looks wrong to me except one: the first review went to Opus (the diff was the whole store, and the rules read it as high stakes) and cost $0.35; the four on Sonnet cost $0.12 to $0.16 each.
- It moved two reviews from the session's Opus to Sonnet and set effort on five calls. Claude had already named a cheaper model on five of nine, and on two of those the table would have said Opus. So here the hook was a backstop more than a driver.
- **One Opus agent did milestones 1 to 4.** The first build call rightly stayed on Opus. Then the main session kept that agent and sent it five more jobs, two of them review fixes that the table would send to Sonnet. That one agent was $3.03, two thirds of the subagent spend and about half of the run. A job sent with SendMessage has no model to set: it runs on whatever the agent started on. Routing cannot reach it. I can see it and check it now, not move it.
- The one probe ran a review at sonnet@medium in place of sonnet@high and nothing went wrong that I can see. One probe says nothing.

An observation, not a finding: run 1 (brief on) had whole milestones done by the Sonnet fixer and cost $1.94 less than run 2 (brief off), where they went to the Opus builder, and both suites pass. That is one pair of runs. They also differ in foreground against background and in SendMessage against fresh agents, and each run wrote the tests it passes.

What three runs cannot show: whether `verify` catches a real mistake (no check failed in 32), whether a subagent escalates on its own (none did), whether probing is safe (one probe), whether the code from the cheaper runs is as good (I compared test counts, not the code), or anything about another project, an interactive session, or the judge.

## What it cost

Roughly $1.50 at list price for everything above the 1.4.1 section, by my own tally of the runs. The five headless sessions were $2.09 together by Claude Code's own count ($0.34 to $0.48 each). The two sessions for 1.4.3 were $0.38 together. The three builds for 1.5.0 were $17.05 and the short SendMessage session $0.45.
