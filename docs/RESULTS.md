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

## 2.0.0

Four things were built for 2.0 and each was run before it was kept. One was measured and thrown out. One benchmark was started and is not finished. All of it is below, in that order.

### A note at SendMessage time: tried, measured, removed

Run 3 above had one Opus agent kept alive with SendMessage doing half the bill, including plain review fixes. So I added a note: when a SendMessage job reads as a contained edit and the agent it goes to runs on Opus, the hook tells Claude a fresh agent on Sonnet is usually cheaper. Then I ran the same build again (run 4: hooks only, brief off, same prompt, same machine).

| | Run 3 (no note) | Run 4 (with the note) |
|---|---|---|
| Wall time | 26 min | 22 min |
| Result | 5 commits, 140 tests pass | 5 commits, 150 tests pass |
| Agent calls | 9 | 10 |
| SendMessage jobs | 6 | 9 (6 of them test re-runs sent to one Haiku agent) |
| Note fired | n/a | 2 times: "Fix M1 review findings", "Fix M3 review findings" |
| Where those two jobs ran | Opus | Opus, both times |
| Total cost | $5.79 | $5.77 |
| Main session | $1.24 (21%) | $1.53 (26%) |
| Subagents | $4.55 | $4.24 |
| Opus subagents | $3.38 | $2.94 |
| Checks | 9 pass | 8 pass |

It did not help, so it is not in 2.0.

- **Two cents apart.** That is nothing, on one pair of runs.
- **Both noted jobs still ran on Opus.** A hook on SendMessage speaks after Claude has decided: the note arrives with the message already on its way. The first note did not change the second decision either. The M3 fixes went to the Opus agent that had built M3.
- Run 4 did use three Opus builders ($1.04, $0.96, $0.95) where run 3 kept one alive for $3.03. I cannot credit the note with that: Claude never sent a contained edit to a fresh Sonnet agent, which is the one thing the note asks for.
- A fresh agent has to read the files again. I did not get to measure that cost, because no job moved.

What would be a different experiment: refusing the SendMessage with a reason, so Claude has to decide again. That blocks a tool call on a guess about cost, and I am not doing that without evidence that the guess is right.

The `continue` line in the ledger now records the tier the running agent is on, so a report can at least show how much follow-up work rode on Opus.

### Probing the pinned reviewer: effort down one step, defects counted

Since 1.3 a share of safe calls can run one step cheaper on purpose. It never fired in real use, because it skipped pinned agents and Claude sends everything to pinned agents. 1.4.3 showed that an effort passed on the call wins over the agent's definition, so 2.0 lets the probe reach `gearshift:reviewer`: with `probe` on, that share of reviewer calls runs at sonnet@medium in place of sonnet@high. Never another model, and never when the brief mentions money, auth, production or a hard-to-reproduce bug.

To see what that costs in findings I planted defects. Three real milestone diffs from the run 3 repo (store and stats, CLI, HTTP server), left uncommitted, with 11 lines changed so the code is wrong: 9 against a spec line (an alias may be 64 characters where the spec says 32, an expired link answers 404 where it says 410, import never counts skipped codes) and 2 that the spec does not spell out (import accepts any `created_at`, no limit on request body size). The tests that would catch them were removed, so the suite was green. Then 18 headless sessions, 6 per diff: a Haiku session calls `gearshift:reviewer` once with the same brief, `probe` at 0.5, and the hook's own coin decides the effort.

| | sonnet@high (as pinned) | sonnet@medium (probed) |
|---|---|---|
| Reviews | 11 | 7 |
| Planted defects found | 37 of 39 (95%) | 25 of 27 (93%) |
| Store and stats diff, 4 defects | 12 of 12 in 3 reviews | 12 of 12 in 3 reviews |
| CLI diff, 3 defects | 15 of 15 in 5 reviews | 3 of 3 in 1 review |
| Server diff, 4 defects | 10 of 12 in 3 reviews | 10 of 12 in 3 reviews |
| Average cost of a review | $0.076 | $0.058 |
| Numbered findings per review | 6.1 | 5.7 |

Graded by matching each planted defect in the reviewer's reply and reading every match on the CLI and server diffs.

- **The mechanism works live.** The hook rewrote 7 of 18 calls to effort medium and logged them as probes. The reviewer ran in the background each time and the result was read at SubagentStop.
- **No difference in defects found, and that is weak evidence.** Ten of the eleven defects were found by every review at both efforts. They were too easy: a spec line next to a constant. The one that separated anything, the missing body size limit, was found once in three at each effort.
- Medium cost about a quarter less per review ($0.058 against $0.076) and wrote slightly shorter reports.
- The coin gave the CLI diff five reviews at high and one at medium. I did not top it up.

What this does not show: that medium is safe on a diff with subtle bugs, on a long diff, or on anything the probe refuses to touch. `tune` files these probes under the agent and will not propose lowering it: it says to compare reviews by hand, because the ledger cannot see a finding that was missed. The 18 sessions cost $1.39.

### The first five minutes

Three things I tripped over myself on this machine, now in `doctor`:

- The plugin was at 1.0.0 while the CLI said 1.4.1. `doctor` now reads Claude Code's record of installed plugins and warns when the versions differ, with the command that fixes it. Run on this machine it reported both problems I actually had (plugin 1.4.1 against 1.5.0, and a stale pip copy).
- A `verify` command I had never run by hand. `doctor` prints it back with the time limit and says to run it once first.
- `doctor --bundle` prints a block for an issue: versions, settings, a week of counts, the last dozen event statuses. No paths, prompts, descriptions, session ids or commands. Tested with a ledger full of marked strings; none come out.

These are tested, and run by me on Windows. Nobody else has pasted a bundle yet.

### The walletcore benchmark: started, not finished, and not a result yet

The plan: replay walletcore's five tagged tasks on each tier alone, with a fresh headless Claude Code session per task, and grade with the tests from the commit that finished the task in the real repo. I stopped it before the last run and before writing it up properly, so read this as a lab note. It is the next thing to do.

24 of 25 runs finished (Fable on task 5 did not). Each cell is the reference feature tests passed, and the cost of the run.

| Task | Haiku | Sonnet | Opus | Fable (high) |
|---|---|---|---|---|
| 1 idempotency keys | 12 of 12, $0.03 | 12 of 12, $0.33 | 12 of 12, $0.58 | 12 of 12, $2.01 |
| 2 daily limits | 18 of 18, $0.02 | 18 of 18, $0.36 | 18 of 18, $0.61 | 18 of 18, $2.00 |
| 3 transfer fees | 8 of 17, $0.03 | 8 of 17, $0.38 | 17 of 17, $0.76 | 17 of 17, $2.34 |
| 4 concurrent transfers | 13 of 13, $0.04 | 13 of 13, $0.49 | 13 of 13, $1.43 | 13 of 13, $2.87 |
| 5 reconciliation report | 17 of 29, $0.13 | 17 of 29, $0.46 | 17 of 29, $1.41 | not run |

Opus at high effort (where the router sends tasks 1, 3 and 4) was also run: 11 of 12 on task 1 for $0.76, 17 of 17 on task 3 for $0.76, 13 of 13 on task 4 for $1.81.

Why this is not a result:

- **The reference tests know things the task text does not say.** Every failure I read comes down to a detail of the original implementation: a `main(argv, out=, err=)` signature and dictionary keys like `held_in_accounts` (task 5, all tiers, the same 12 tests), a column named `idempotency_key` on one table (Opus at high on task 1, after Haiku had passed it), whether the fee account keeps a stored balance or lives in the ledger only (task 3, Haiku and Sonnet both chose ledger-only). After the first Haiku run I started giving every run the tests' import lines. That was not enough.
- So a failed cell says "guessed a different design", not "wrote wrong code". I have not checked the code in the passing cells either, beyond the tests.
- One run each. Tasks 3 and 5 also fail one reference test outside the feature file on every tier, for the same kind of reason.

What I would still take from it, carefully: on tasks 1, 2 and 4 the reference tests are strict (thread storms, overdraft races) and Haiku passed all of them for 2 to 4 cents, where Sonnet cost $0.33 to $0.49 and Opus $0.58 to $1.43. That is the bench pack result again on something closer to real work, on three tasks.

To finish it properly the sandbox needs tests that only use what the task text promises, then the router and cascade rows, then more than one run per cell. The 24 runs cost $19.61.

**One session where the check sent work back.** Using what the Haiku run on task 3 had shown, I ran one headless session: a Sonnet main session hands task 3 to a Haiku subagent, and `verify` is the reference fee tests, kept outside the repo. The subagent reported the task done with its own 100 tests passing. The check failed 9 of 17. The hook held the subagent and handed it the output, it changed the fee account to keep a stored balance, the check passed, and only then did Claude get the result. $0.18.

I do not count that as the check catching a real mistake on its own. I arranged it knowing where Haiku would differ from the reference. The "mistake" is the design choice above, which Sonnet made too. And once it was sent back, the subagent opened the reference test helper, because the hook's message carries the check command and the path was in it. It shows the path from a failing check to a fixed result works on real model output. That is all.

## A full day of real work (2.0.0), and what the ledger gave away

Not a test: a normal working day with the plugin on, a Fable 5.2 session, numbers only.

| | Runs | List price |
|---|---|---|
| Opus (six builders, five reviews, two forks, one Explore) | 14 | $178.03 |
| Fable (one general-purpose agent) | 1 | $21.40 |
| Haiku (scouts and Explore) | 4 | $0.57 |
| Total delegated work | 19 | $200.00 |
| Same tokens at the session's Fable rates | | $291.29 |

31% below the session model's rates on the delegated work. The usual caveat: the second figure reprices the same tokens, it is not a second run. The hook filled in a model on three calls and set effort on four, the first time it has routed real work outside a test.

Two things the ledger showed that had nothing to do with which model was picked:

- **Idle agents are expensive.** Several long-lived agents wrote about 400,000 tokens of cache between two usage reports while producing a few thousand tokens of output. One builder went from $12.60 to $19.87 that way. Across the day it looked like $45 to $50, about a quarter of the spend. My reading is that an agent left idle past the cache window re-saves its whole context when it is sent more work; I have not confirmed the cause. `report` now estimates this.
- **The report said 8 of 22 agents had finished.** None of the six builders had a result recorded. That was a bug, not slow agents: they were launched in the same message, the hooks ran at once, and they overwrote each other's entries in the session file. Fixed in 2.0.1, with a test that launches six at once.

## What it cost

Roughly $1.50 at list price for everything above the 1.4.1 section, by my own tally of the runs. The five headless sessions were $2.09 together by Claude Code's own count ($0.34 to $0.48 each). The two sessions for 1.4.3 were $0.38 together. The three builds for 1.5.0 were $17.05 and the short SendMessage session $0.45. Run 4 was $5.77. The 18 seeded-defect reviews were $1.39. The unfinished walletcore benchmark was $19.61 for the 24 runs that finished, plus under $3 for runs I broke or stopped, and the session with the check $0.18.
