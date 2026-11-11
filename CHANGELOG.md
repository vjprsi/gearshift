# Changelog

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
