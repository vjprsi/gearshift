# Changelog

## 1.4.3 (2026-11-11)

From a review of 1.4.2. The brief said a task sent again after `ESCALATE:` starts one step up. For gearshift's own pinned agents it did not: the pin was returned before the retry was looked at, so the same agent ran on the same model again.

- Checked live first: a `model` and `effort` passed on an Agent call win over the agent's definition (scout ran on Sonnet at high when asked to).
- So a gearshift agent that fell short and gets the same task again now runs one rung up, same agent, same instructions. A model Claude passed is kept, shadow mode only logs, the ceiling holds (the agent's own tier is always allowed). Other plugins' and your own pinned agents are left alone.
- `report` and the status line count these as routed by gearshift (action `bumped`).
- Six more tests on recorded payloads, including `ESCALATE:` from a pinned agent followed by the same call again.

Not done: live it ran with a scripted `ESCALATE:`, one agent, foreground, headless. In shadow mode the session file records the rung a retry would have had, not the one it ran on; that was already so for unpinned agents and I have not changed it. Probing for pinned agents is now possible and not built.

## 1.4.2 (2026-11-10)

I ran 1.4.1 for real: five headless sessions on Windows with a recorder next to the hooks. The main idea held (the agent id at launch is the one at SubagentStop, a failing check does hold a background subagent). Five smaller things were wrong. Write-up in `docs/RESULTS.md`.

- The message that sends a subagent back quoted the check command cut at 120 characters, so it could not run it again. It gets the whole command now.
- The session brief says what a reply starting with `ESCALATE:` means and to send the task again. For a background agent nothing else can tell the main session.
- A retry with notes added ("a previous attempt may already have...") is recognised as the same task, so it starts one rung up. Only for tasks that fell short.
- A retry at the ceiling stays on the rung that failed. It used to drop back to the class's home rung and announce "one step up".
- A held subagent is asked to repeat its full report, because its first reply never reaches the main session.
- Tests: `tests/live_payloads.json` holds the recorded payloads (paths removed) and eight tests replay them.

Not done: the retry match and the full-report line were changed after the last live session and have only met tests. `ESCALATE:` from a foreground agent has still not happened live. All of this was headless, with a general-purpose subagent in a Sonnet session; no interactive session, no pinned agent. Probing and `tune` are where they were.

## 1.4.1 (2026-11-10)

A real bug, found by reading my own ledger from a live session.

- Background subagents: Claude Code fires PostToolUse when a background agent is launched (`status: async_launched`), not when it finishes. Everything that looked at the result there (escalation, errors, the `verify` check) was looking at the wrong moment, and `report` counted launches as finished runs. The outcome is now read at SubagentStop, from the subagent's own final message.
- `verify` for a background agent: if the check fails, the subagent is not allowed to stop yet. It gets the failing output once and is told to fix it or answer `ESCALATE:`. A second failure is recorded and the next attempt starts one rung up.
- SubagentStop is no longer an async hook, because an async hook cannot hold the subagent back.
- Old ledger lines that recorded a launch as "done" are ignored by `report` and `tune`.
- `report` shows check results and explains why token counts are larger than an agent's context.

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
