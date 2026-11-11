---
name: routing
description: Choose the model tier and effort level for delegated work. Load before spawning any subagent or parallel worker, when a result falls short and you are deciding whether to raise effort or move up a model, and when the user asks which model or effort to use for a task.
---

# gearshift routing

Run each piece of work on the cheapest model and effort that will finish it properly. Judge by
cost per finished task, not price per token.

## The rule everything else follows from

**Route by delegation. Never switch the main session's model or effort mid-task.**

Each model and each effort level has its own prompt cache. Changing either mid-session makes
the next request re-read the whole conversation uncached. The session keeps its model; routing
happens when you hand work to a subagent, by choosing that subagent's model and effort.

## Routing table

Prefer the pinned agents: their model and effort are fixed by their definitions, so nothing
depends on you remembering. For any other agent type, pass `model` and `effort` on the Agent call.

| The subagent's job | Agent | Model | Effort |
|---|---|---|---|
| Find files, grep, read logs, run tests and report, summarize long output | `gearshift:scout` | haiku | medium |
| Classify, tag, extract fields, convert formats, at volume | `gearshift:sorter` | haiku | low |
| A contained change against a clear spec; a small bug fix | `gearshift:fixer` | sonnet | medium |
| Review, bug hunt, investigation, comparison (read-only, needs judgment) | `gearshift:reviewer` | sonnet | high |
| Everyday documents, emails, summaries for people | any | sonnet | medium |
| Multi-file feature, hard-to-reproduce bug, performance work, computer use | `gearshift:builder` | opus | medium |
| Architecture, major refactor or migration plan, deep research report | `gearshift:architect` | opus | high |
| Proofs, novel algorithms, quantitative work, what Opus already failed at | `gearshift:prover` | fable | high |
| Unsure | any | opus | medium |

Guards on the table:

- **Haiku retrieves; it does not judge.** "Find where X is defined" is a scout job. "Find the
  bug" is not: a wrong answer would pass unnoticed. Anything where a mistake is not cheap to
  spot goes to Sonnet or stays with you.
- **A clear spec does not guarantee Sonnet is enough.** Migrations, concurrency bugs and
  performance work can need Opus even when fully specified.
- **High stakes** (production data, auth, payments, anything irreversible): never Haiku for
  edits, one effort level up, and give the agent a way to verify.
- **Fable is a rung you climb to, not a default.** For research and for orchestration Opus
  measured the same or better at a fraction of the cost. Start on Fable only when the brief
  itself says the problem is that hard.
- **If a tier is not available** on this account, use the next tier down one effort level
  higher, and say so in one line.
- **Always set effort on a delegated call.** A subagent with no effort inherits the session's,
  and a scout at `xhigh` spends many times the tokens for the same answer.

If the gearshift hook is installed it fills in `model` and `effort` on Agent calls that leave
them unset, so an unrouted call is still routed. A model you pass explicitly is kept.

## When not to delegate

Delegation has a coordination cost. In Anthropic's measurements it paid off for work that fans
out across independent parts, work larger than one context window, and routine work with a long
cost tail. It lost to a single model at lower effort for:

- one dependent chain, where each step needs the previous step's result
- work that fits comfortably in the current context
- a handful of tool calls

Delegate bulk reading (logs, wide file sweeps) and independent parallel parts. Every turn
re-sends the whole context, so verbose reading belongs in a cheap subagent that returns a short
summary. Ask for the summary shape you want: file paths and line numbers, not file contents.

## Escalation ladder

When a result is not good enough, climb in this order and stop at the first rung that works:

1. **Let it verify.** Give the agent a test, build or script to check its own work.
2. **Raise effort one level, up to `high`.** Effort buys more thinking and checking. It does
   not add skill the model lacks.
3. **Move up one model, at a lower effort** than the smaller model was using.

The full ladder, cheapest first:

`haiku@low → haiku@medium → haiku@high → sonnet@medium → sonnet@high → opus@medium → opus@high → opus@xhigh → fable@high → fable@xhigh`

Never choose `max` on your own; it is for measuring a ceiling. When you re-dispatch, pass along
what the failed attempt learned so the next agent does not start from zero.

Pinned agents end their reply with a line starting `ESCALATE:` when a task is beyond their
tier. Treat that as a result, not a failure: re-dispatch one rung up with their findings. Sending it to the same gearshift agent again
without a model works too: the hook runs that agent one rung up.

**Cheap-first retry.** When the outcome can be checked automatically, run the batch at low
effort and re-run only the failures at high. On Anthropic's SWE-bench Pro numbers this solved
about as many tasks as running everything at high, for roughly 40% less per task. It is a cost
saving, not a quality gain, and it needs a failure signal you trust.

## Advising on the session's model

You cannot change the session's model; the user does. Speak up in one line only on a clear
mismatch, then carry on as asked:

- **Too small:** architecture, a major migration, or a long unattended run on Sonnet or Haiku.
- **Too big:** a long stretch of small, well-scoped edits on Opus or Fable.

Starting points when the user asks:

| Work | Model | Effort |
|---|---|---|
| Most coding and knowledge work | opus | medium, then high |
| Well-scoped everyday coding | sonnet | medium, then high |
| Chat a person is waiting on | sonnet | low or medium |
| Long unattended runs, leading many subagents | opus | high, then xhigh |
| The hardest reasoning, after Opus at xhigh fell short | fable | high |

If the user does switch, suggest they compact first or start a fresh session with a short
written plan, so the new model does not pay to re-read the whole history.

## Do not oversell it

Mixed-model setups do not always win. A pricier model that finishes in fewer tokens can be
cheaper per finished task, and a pairing is worth keeping only if it beats the same model at
lower effort and the next model down running alone. If the user wants proof, measure it on
their own tasks with automatic pass/fail checks: `gearshift bench` does this.
