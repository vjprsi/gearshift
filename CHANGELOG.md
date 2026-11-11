# Changelog

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
