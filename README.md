# gearshift

Model **and effort** routing for Claude. Every task in the right gear.

It picks the cheapest model tier and effort level that will actually finish a piece of work, tells you why, and gives you the next rung to try if the result isn't good enough.

Two ways to use it:

- **Claude Code plugin** – 7 subagents pinned to a model + effort, a hook that fills in model/effort on subagent calls that didn't set them, a ledger of what ran where and what it cost, and a heads-up before a `/model` switch throws away your prompt cache.
- **Python library + CLI** (zero dependencies) – `route()`, `run()` with escalation, `run_batch()` with cheap-first retry, and `bench` to check if mixing models really beats one model on *your* tasks.

## Why I built this

I'm CTO at [MOVii](https://www.movii.com.co), a payments company in Colombia. We've been moving our engineering team to an agent-first way of working with Claude Code, and once a whole team works like that, "which model is doing the grep" stops being a detail.

I came across [claude-model-router](https://github.com/Rajvardhman05/claude-model-router) by Raj Vardhman, a small skill that hands Anthropic's model-selection table to Claude. Great idea. But I kept wanting three more things:

1. **Enforcement.** A skill is advice. If it doesn't load, your scout runs on the big model anyway.
2. **Effort, not only model.** A subagent with no effort inherits the session's. A file search at `xhigh` is money on fire.
3. **Something I can call from code**, not just from Claude Code.

So this is that. The routing table lives in a JSON file, every row has a reason, and where Anthropic has published a number the reason is that number.

## Quick look

```text
$ gearshift route "Find every caller of parse_config() and list the file paths. Do not modify anything."
haiku @ medium   (claude-haiku-5-5)
  class       lookup   confidence 1.00
  evidence
    - verb "find" opens the task
    - explicit read-only instruction ("Do not modify anything")
    - search or read target ("every caller")
  if it falls short  haiku@high  ->  sonnet@medium  ->  sonnet@high  ->  opus@medium
  list price  $0.1 in / $0.5 out per MTok

$ gearshift route "Our websocket server leaks memory under load; find the leak and fix it."
opus @ medium   (claude-opus-5-5)
  class       build   confidence 0.53
  evidence
    - verb "find" opens the task
    - follow-on verb "fix"
    - bug hunt ("find the leak")
    - hard-to-reproduce or concurrency bug ("under load")
  if it falls short  opus@high  ->  opus@xhigh  ->  fable@high  ->  fable@xhigh
```

(output trimmed a little)

Both start with "find". One is a lookup, the other is a bug hunt that ends in a fix. Telling those two apart is most of the job.

## Install

Claude Code (2.1.251+; setting effort per call needs 2.1.292+):

```text
/plugin marketplace add vjprsi/gearshift
/plugin install gearshift@gearshift
```

The hook needs Python 3.9+ on your PATH. If it isn't there the hook just stays quiet, nothing breaks, and you still get the agents and the skill. On Windows it runs through Git Bash (Claude Code's default). `/gearshift:doctor` tells you if something is off.

Library / CLI:

```bash
pip install git+https://github.com/vjprsi/gearshift
gearshift route "Classify these 5,000 support tickets by product area, as JSON"
```

Or don't install anything: clone it and run `bin/gearshift route "..."`, or try the plugin for one session with `claude --plugin-dir /path/to/gearshift`.

## The table

| Class | What it is | Model | Effort |
|---|---|---|---|
| `lookup` | find files, grep, read logs, run tests and report | haiku | medium |
| `extract` | classify, tag, extract fields, convert formats, at volume | haiku | low |
| `edit` | contained change against a clear spec, small bug fix | sonnet | medium |
| `write` | everyday docs, emails, summaries for people | sonnet | medium |
| `review` | review, bug hunt, investigation, comparison | sonnet | high |
| `build` | multi-file feature, hard-to-reproduce bug, perf work, computer use | opus | medium |
| `design` | architecture, major refactor / migration | opus | high |
| `research` | deep research to a finished report, leading other agents | opus | high |
| `frontier` | proofs, novel algorithms, what smaller models already failed at | fable | high |
| `chat` | a question someone is waiting on | sonnet | low |
| `unsure` | not enough signal | opus | medium |

On top of that:

- high stakes (prod, auth, payments, anything irreversible) → never Haiku for an edit, one effort level up
- hard-to-reproduce bug → Opus, even if it's read-only investigation
- vague brief → Opus
- long unattended run → at least Opus and one effort level up (only way to reach `xhigh`)
- someone is waiting, or the brief says "quick" → one effort level down
- ceiling hit or tier not available → next tier down, one effort level up

It never picks `max`, and one stray word never buys you Fable.

The ladder when a result falls short, cheapest first:

```text
haiku@low → haiku@medium → haiku@high → sonnet@medium → sonnet@high → opus@medium → opus@high → opus@xhigh → fable@high → fable@xhigh
```

Effort up to `high` first, then the next model at a lower effort. Only above `opus@high` does more effort come before a bigger model.

## Why the table looks like this

Full reasoning is in [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md), numbers and links in [docs/EVIDENCE.md](docs/EVIDENCE.md). The parts I care most about:

**Route by delegating, never by switching.** In Claude Code every model and every effort level has its own prompt cache. Change either one mid-session and the next turn re-reads the whole conversation uncached. So the session keeps its model and the routing happens when work goes to a subagent.

**Effort is the second dial and often the better one.** On Anthropic's SWE-bench Pro numbers, Sonnet at high and Opus at low are within a point of each other at almost the same cost per solved task. That's why the ladder uses up effort before it changes model.

**Haiku retrieves, it doesn't judge.** In Anthropic's defect-search benchmark, Opus with Haiku subagents got 0.677 F1. With Sonnet subagents, 0.831. Reading code *looking for defects* is judgment. So "find where X is defined" goes to Haiku and "find the bug" doesn't.

**Fable is where you climb to, not where you start.** This is my biggest change from the original table, which sent refactors, architecture and deep research to Fable. Anthropic's own research benchmark has Fable 5.2 at 4.3x Sonnet's cost with no measurable gain, and as an orchestrator it cost about 19% more than Opus for the same score. So research and orchestration go to Opus here. Fable is the starting point only for the really hard reasoning, and the top of the ladder for everything else.

**Cheap-first retry.** If the work can be checked automatically: run at low, re-run only the failures at high. Same SWE-bench Pro numbers: ~97% solved at ~$0.17 per task, versus 95.3% at $0.29 with everything at high.

All of these are Anthropic's internal measurements and they say themselves they're directional. Don't take them, or my table, on faith. That's what `gearshift bench` is for.

## In Claude Code

### Pinned subagents

Model and effort are in the agent definition, so nothing depends on Claude remembering a table.

| Agent | Runs on | Use it for |
|---|---|---|
| `gearshift:scout` | haiku @ medium | find, grep, read logs, run tests and report (read-only) |
| `gearshift:sorter` | haiku @ low | classify / tag / extract / convert at volume |
| `gearshift:fixer` | sonnet @ medium | contained change against a clear spec |
| `gearshift:reviewer` | sonnet @ high | read-only review and investigation |
| `gearshift:builder` | opus @ medium | multi-file features, hard bugs |
| `gearshift:architect` | opus @ high | plans for migrations / refactors / architecture (doesn't implement) |
| `gearshift:prover` | fable @ high | the hardest reasoning, nothing else |

Each of them ends its answer with an `ESCALATE:` line when the task is above its pay grade, so the orchestrator re-dispatches one rung up with what was learned, instead of accepting a guess.

### The hook

When an Agent call has no model, the hook classifies the task and sets one. I made it conservative on purpose:

- if Claude passed a model, it's kept (`strict` mode replaces it)
- it never assigns a tier above your session model unless you raise the ceiling, so by default it can only route *down*
- if it isn't confident, it does nothing
- it doesn't touch Plan, forks, agents that pin their own model, or other plugins' agents
- it fails open: no Python, broken config, garbage input, read-only disk → exit 0, no output, your session goes on

Modes are `off`, `shadow` (log what it would do, change nothing), `fill` (default) and `strict`. If you don't trust it yet, run `shadow` for a day and look at the report. More in [docs/CLAUDE_CODE.md](docs/CLAUDE_CODE.md).

### The ledger

`/gearshift:report` shows how many subagent calls were routed, to which tiers, and the list-price cost, summed from each subagent's own transcript. It also reprices the same tokens at your session model's rates. I label that second number as a comparison and not as savings, because a bigger model wouldn't have used the same tokens.

### Before you drop your cache

`/model` mid-session re-sends the whole conversation uncached. The plugin adds one line with the token count and Claude Code's own cost estimate and suggests delegating that piece instead. It never blocks the switch.

## From code

```python
from gearshift import route

d = route("Classify these 5,000 support tickets by product area, as JSON")
d.tier, d.effort        # ('haiku', 'low')
d.api_params()          # {'model': 'claude-haiku-5-5', 'output_config': {'effort': 'low'}}
d.ladder                # [('haiku', 'medium'), ('haiku', 'high'), ('sonnet', 'medium'), ...]
print(d.explain())
```

Escalate only when a check fails:

```python
from gearshift import run
from gearshift.anthropic_adapter import make_call

result = run(task, make_call(), verify=my_check, max_attempts=3, budget_usd=0.25)
for a in result.attempts:
    print(a.tier, a.effort, a.ok, a.cost_usd)
```

Cheap-first over a batch:

```python
from gearshift import run_batch

batch = run_batch(items, make_call(), verify, first=("opus", "low"), retry=("opus", "high"))
batch.summary()         # pass rate, first-pass count, total cost, cost per finished task
```

You can also bound it: `route(task, ceiling="opus", floor="sonnet", available=[...], prefer="cost")`. Reference in [docs/API.md](docs/API.md), runnable scripts in [examples/](examples/).

### Measure it

```bash
gearshift bench my_tasks.jsonl --configs "sonnet@medium,opus@medium,router,cascade,cheap-first:opus"
```

Runs every config over your tasks (with automatic pass/fail checks) and prints pass rate and **cost per finished task**, failed attempts included. Keep the mixed setup only if it beats every single-model row. `--simulate` runs the harness on a fake model so you can see the output without a key; those numbers are made up and the output says so. See [docs/BENCH.md](docs/BENCH.md).

## How much should you trust the classifier?

There are two layers now. The first is keyword evidence (around 750 verbs in English, Spanish and Portuguese, around 40 groups of phrase patterns). It's instant, free and offline. It's good on phrasing it knows, and when it doesn't know it says `unsure` instead of guessing. The second is an optional judge: one Haiku call at low effort, asked only when the keywords aren't confident, with safety rules on top of its answer. More on that below.

The numbers in this section are for the keyword layer alone.

I scored it on five task sets. Each one was written before the rules that handle it, and run once blind:

| Blind run | Tasks | Right tier | Under-routed | Over-routed |
|---|---|---|---|---|
| Set 1 | 66 | 89.4% | 5 | 2 |
| Set 2 | 45 | 75.6% | 3 | 8 |
| Set 3 (written to avoid the vocabulary it knew) | 40 | 42.5% | 7 | 16 |
| Set 4 (after widening the vocabulary; never tuned against) | 40 | 90.0% | 1 | 3 |
| Spanish / Portuguese (after adding both languages; never tuned against) | 20 | 90.0% | 0 | 2 |

Set 3 is the one to remember. With unfamiliar wording most misses were `unsure`, which means Opus on the API (safe, not cheap) and "do nothing" in the hook. Across the English sets today, it hasn't sent a single task labelled above Haiku to Haiku. But the same person wrote the tasks and the rules, so treat this as a regression suite with a known blind history, not an independent benchmark. The whole story and the misses I left unfixed are in [docs/ACCURACY.md](docs/ACCURACY.md).

What I take from that:

- In Claude Code the classifier is the backstop. The real router is Claude reading the skill and picking a pinned agent. The hook catches the calls it forgot.
- Anything outside English, Spanish and Portuguese, and odd phrasing in any language, comes out `unsure`. That's what the judge is for.
- If you already know the kind of work, skip classification: `route(task, task_class="extract")`.

## The judge (new in 1.1)

My friend [Orlando Murcia Perdomo](https://www.linkedin.com/in/orlando-murcia-perdomo-458a291a2/) looked at 1.0 and said, more or less: keywords are a heuristic, use a small model with clear criteria and you get every language for free. He's right about the weakness. I didn't want to pay a model call on every decision though, so it's a hybrid:

1. Keywords first. If they're confident, done. No model call, no latency.
2. If they aren't, ask Haiku at low effort. It gets the class definitions from the policy file and answers with a class plus four facts: does it change code, is it high stakes, is it a hard bug, is it a long run.
3. Safety rules run on top of the answer. A task that changes code never goes to Haiku, whatever the model said. A hard bug with a fix goes up to Opus.

```bash
export GEARSHIFT_API_KEY=sk-ant-...   # that's it. judge=auto picks it up, in the hook too
gearshift route --judge api "Behebe den Fehler im Zahlungsmodul."
gearshift eval tests/cases_multi.jsonl --judge api     # score it yourself: 25 tasks in German, French, Italian, Hindi, Japanese
```

(`ANTHROPIC_API_KEY` works as well, but exporting that also moves Claude Code itself onto API billing. The separate name keeps your session on your subscription and only the judge on the key.)

No API key at all? `GEARSHIFT_JUDGE=claude` asks through headless Claude Code instead. It's slower (it starts a session), so it's opt-in. Answers are cached on disk, a judge error or timeout leaves the call alone, and a project file can't switch the judge on.

What I have NOT measured: how often the judge is right on real traffic. Without it, those 25 multilingual tasks score 32% (almost everything falls to `unsure`, so Opus). With a judge that answers correctly they score 100%, but that only proves the wiring. The real number is whatever `gearshift eval --judge api` prints on your machine, and I'd like to see it.

## It watches what happens next (new in 1.2)

A router that only decides before the work starts is guessing. 1.2 looks at what came back.

- **A way out for the cheap model.** When the hook moves a call down to a cheaper model, it adds one line to the prompt: if this is beyond you, answer `ESCALATE:` and say why. Without that, a small model has no way to say "too hard" and hands back something confident and wrong.
- **Escalations are acted on.** When a subagent answers `ESCALATE:`, the hook records it and tells Claude which rung to re-run on (`haiku@medium` asked for help, so `haiku@high`, then `sonnet@medium`, and so on up the ladder).
- **The same task twice starts higher.** If a task fell short and Claude sends it again in the same session, the hook doesn't classify it from scratch. It starts one rung above where it failed. The state file keeps hashes of the words, not your prompt.
- **`gearshift tune`.** Reads your own ledger and shows, per task class, how often runs fell short. If a class has 20+ runs on its home tier and 15% or more fell short, it prints the policy change to raise it. It never applies anything, and it never proposes going down.

```
$ gearshift tune
class     ran on   runs  fell short   verdict
edit      sonnet     30    7 ( 23%)   raise edit to sonnet@high
lookup    haiku      40    1 (  2%)   keep
review    sonnet      8    4 ( 50%)   not enough runs (8 of 20)
```

That's example output from the test data, not from real use. "Fell short" means the subagent asked to escalate, the call errored, or the task was re-sent on a bigger model. It does not mean finished work was correct. Nothing local can tell you that, which is why going down a tier is a `bench` question and not a `tune` one.

There's also a built-in benchmark now: `gearshift bench pack` runs 31 tasks with automatic checks (8 extraction, 8 small functions, 7 harder ones, 8 exact-answer reasoning) across the configs you name, and `--out results.md` writes a file you can commit. Every check in the pack is tested against a reference solution. I have not run it against the real models yet, so there are no results to show. That's the next thing.

## Status

This is 1.2, so here's exactly where it stands:

- 171 tests: routing, pricing, ladder, executor, ledger, every hook (run as a subprocess with the JSON payloads Claude Code documents), fail-open behaviour, plugin files. CI matrix is Linux / macOS / Windows.
- The plugin passes `claude plugin validate` and loads on Claude Code 2.1.322 (7 hook events, 7 agents, 4 skills registered).
- The hooks are verified end to end in a live session on Claude Code 2.1.322 on Windows: in `shadow` mode the call was logged and left alone, and in `fill` mode an unrouted Explore call was rewritten to Haiku and measured in the ledger. On my machine that same file-count task cost $0.31 on Opus (Claude Code's default for Explore) and $0.015 on Haiku, at list price. One run, not a benchmark. Other versions and platforms still need mileage, so start in `shadow` mode, check `/gearshift:report`, and open an issue with your `/gearshift:doctor` output if something is off.
- The Anthropic SDK adapter is tested with a fake client and checked against the real SDK signature (anthropic 2.8.0). The judge is tested with a fake model, a local fake of the Messages API and a fake `claude` binary. I haven't run it against the real API or measured its accuracy yet, and the headless `claude` transport is the least proven part of this repo.

## vs claude-model-router

Raj's skill is clear and well judged, go read it. What's different here:

| | claude-model-router | gearshift |
|---|---|---|
| Form | one skill | skill + 7 pinned agents + hooks + library + CLI |
| Enforcement | relies on the skill loading | pinned agents, plus a hook for unrouted calls |
| Effort | "leave at default" for one-offs | routed per task class, set on every delegated call |
| Fable | refactors, architecture, deep research | hardest reasoning only; the rest moved to Opus on the published numbers |
| Cost visibility | – | ledger from subagent transcripts |
| Outside Claude Code | – | `route`, `run`, `run_batch`, `bench` |
| Customizing | edit the skill | JSON policy with overrides |

## Config

| Setting | Values | Default |
|---|---|---|
| `mode` | `off`, `shadow`, `fill`, `strict` | `fill` |
| `ceiling` | `session`, `haiku`, `sonnet`, `opus`, `fable` | `session` |
| `set_effort` | `auto`, `always`, `never` | `auto` |
| `announce` | one line per routed subagent | `true` |
| `advise` | session-model and switch notes | `true` |
| `judge` | `auto`, `off`, `api`, `claude` (Haiku second opinion when the keywords are unsure) | `auto` |
| `hint` | add the `ESCALATE:` line to prompts the hook routes down | `true` |
| `brief` | routing brief in context at session start | `true` |
| `explore` | `route` or `leave` the built-in Explore agent | `route` |

Set them in the plugin options, with `GEARSHIFT_<NAME>` env vars, in `~/.config/gearshift/config.json`, or per project in `.gearshift.json`. A project file can only make routing *more* conservative. I don't want a repo I just cloned to be able to make my subagents more expensive, and neither do you.

Changing the table itself: [docs/POLICY.md](docs/POLICY.md).

## Limits

- The keyword layer knows English, Spanish and Portuguese. Everything else needs the judge, and without it comes out `unsure` (Opus on the API, untouched in the hook).
- The judge sends the first 1,500 characters of the subagent prompt to the Anthropic API. Same vendor your session already talks to, but it's one more call, so it's off unless a key is set or you opt in.
- The first route only sees the task text. From the second attempt on, the hook knows the task fell short and starts higher, but only within one session and only if the subagent said `ESCALATE:` or the call errored.
- `tune` counts what it can see. A subagent that finishes confidently and wrongly looks like a success in the ledger.
- Prices are list prices from Anthropic's pricing page as of 2026-11-10. No fast mode, no US-only multiplier, no negotiated rates. Update `src/gearshift/data/models.json` when a new generation ships.
- On a subscription the dollar figures are relative weight, not your bill.
- Tier aliases resolve differently per provider (on Bedrock / Vertex / Foundry `haiku` and `sonnet` can be older models). The ledger prices by the model ID that actually ran.
- A mixed setup doesn't always win. A pricier model that finishes in fewer tokens can be cheaper per finished task. Measure.

## Credits

Built by Vijay Pratap Singh ([@vjprsi](https://github.com/vjprsi)), CTO at MOVii.

The idea and the shape of the routing table come from [claude-model-router](https://github.com/Rajvardhman05/claude-model-router) (MIT, © 2026 Rajvardhman), see [NOTICE](NOTICE). The measurements are Anthropic's, from their public docs. This is an independent project, not affiliated with or endorsed by Anthropic.

MIT licensed. Issues and PRs welcome, especially tasks that route wrong.
