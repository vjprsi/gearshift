# Changelog

## 1.4.0 (2026-11-10)

Built from the first full live session and from a colleague's point that a cheap model being confidently wrong counted as a success.

- `verify`: a command the hook runs after a subagent that changed code returns. A failing check is reported to Claude with the output and the rung to redo the work on, and counts as falling short in `tune`.
- `doctor` counts installed agents and estimates the description tokens they add to every request.
- Status line shows the session cost and the main context size, with a nudge to `/clear` above 120k tokens. It now counts subagents by where they ran, including pinned agents Claude chose itself.
- Fixed: `tune` reported pinned agents as running on the session model. An escalation from a pinned agent now climbs from the agent's own model.
- PostToolUse hook timeout raised to 30s for the check.

Not done: `verify` has only run against test payloads. Probing still skips pinned agents, which is where all the traffic went in the live session.

## 1.3.0 (2026-11-10)

- Probing (`probe`, off by default): a share of safe, read-only calls goes one rung cheaper than the policy says, with the escalate hint, so the table can be tested on real work. Never code changes, never high-stakes work, never calls that named a model.
- `gearshift tune` now reads probes too and can propose moving a class down when its probes held up. It still applies nothing.
- `gearshift statusline`: one line for Claude Code's status bar with routed calls by tier, subagent spend and escalations. `--install` prints the settings snippet.
- CI: the Ubuntu image dropped Python 3.9, so that job tests 3.11; 3.9 stays covered on macOS and Windows.

Not done: probing and the status line have only run against test payloads.

## 1.2.1 (2026-11-10)

- First real runs, published in `docs/RESULTS.md`: the judge on four labelled sets, and the benchmark pack on Haiku, Sonnet, Opus, Fable, the router and the cascade.
- New `final` check type (the last line is the answer). The pack's eight reasoning tasks use it now: with exact match, Sonnet failed all eight on format while getting every answer right.
- What the runs showed: the judge took the multilingual set from 32% to 100%; the pack is too easy to separate tiers, and on it always-Haiku beat the router on cost by 13x.

## 1.2.0 (2026-11-10)

The router now looks at what happened after the route.

- Outcome tracking: the PostToolUse hook records when a subagent answers `ESCALATE:` or the call errors, and tells Claude which rung to re-run on.
- Retry-aware routing: the same task sent again after falling short starts one rung above where it failed. Tasks are matched by hashed words kept in the session state, not by storing prompts.
- Escalate hint: calls the hook routes to a cheaper model get one line telling the subagent how to say the task is beyond it. Setting `hint`, on by default.
- `gearshift tune`: per task class and tier, how many runs fell short, and a policy override when a class keeps falling short on its home tier. Proposes raises only. Applies nothing.
- `gearshift report` shows outcome counts.
- `gearshift bench pack`: 31 built-in tasks with automatic checks, a new `python` check type, and `--out` to write a results file.

Not done: `tune` has only seen test data.

## 1.1.0 (2026-11-10)

The hybrid router. Thanks to Orlando Murcia Perdomo, who told me keywords alone wouldn't survive a second language.

- Spanish and Portuguese in the keyword classifier: about 350 verb forms and the matching phrase patterns. On my 30 Spanish/Portuguese tasks it went from 30% to 100% right tier; on 20 more written afterwards and run once, 90% with nothing under-routed.
- Judge v2. Returns a class plus four facts (changes code, high stakes, hard bug, long run) instead of one word. Safety rules run on top: the model can't send a code change to Haiku.
- The judge now works inside the Claude Code hook. New `judge` setting: `auto` (default; on when `ANTHROPIC_API_KEY` is set), `off`, `api`, `claude`.
- Two transports that need nothing installed: direct HTTPS, and headless `claude -p` for people on a subscription.
- On-disk cache for judge answers. `judged` recorded in the ledger and shown in `gearshift route`.
- `gearshift route --judge` and `gearshift eval --judge` so you can measure the judge on your own tasks.
- `tests/cases_multi.jsonl`: 25 tasks in German, French, Italian, Hindi and Japanese.
- `judge_below` raised from 0.5 to 0.55 so it lines up with the confidence the hook acts on.
- PreToolUse hook timeout raised from 10s to 30s to leave room for the judge.

Not done: judge accuracy on real traffic is unmeasured, and the `claude` transport has only run against a fake binary.

## 1.0.0 (2026-11-10)

First release.

- Routing policy as data: 11 task classes, about 400 lead verbs, about 40 phrase signals, an escalation ladder.
- `route`, `Router`, `run`, `run_batch`, `cost_usd`, an Anthropic SDK adapter and an optional model judge.
- CLI: `route`, `session`, `ladder`, `cost`, `policy`, `eval`, `bench`, `report`, `doctor`.
- Claude Code plugin: the `routing` skill, seven pinned subagents, hooks for SessionStart, PreToolUse and PostToolUse on Agent, SubagentStop, UserPromptSubmit, PreModelSwitch and PostModelSwitch, and three commands.
- Prices and model table as of 2026-11-10: Fable 5.2, Opus 5.5, Sonnet 5.5, Haiku 5.5 and their predecessors.

Known gaps: the hooks are verified live on Claude Code 2.1.322 on Windows only, and the SDK adapter and the judge have only run against fakes so far. See Status in the README.

### After release

- `gearshift report` now names the agents that ran on each tier.
