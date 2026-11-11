# gearshift in Claude Code

## Install

```text
/plugin marketplace add vjprsi/gearshift
/plugin install gearshift@gearshift
```

Requirements:

- Claude Code 2.1.251 or newer (the model-switch hooks need it). Setting effort per Agent call needs 2.1.292; on older versions the hook sets the model only.
- Python 3.9+ on PATH for the hook and the commands. The launcher tries `python3`, `python`, then `py`; set `GEARSHIFT_PYTHON` to choose one.
- On Windows, Git Bash (Claude Code's default shell for hooks).

Check with `/gearshift:doctor`. To try the plugin for one session without installing: `claude --plugin-dir /path/to/gearshift`.

## What gets installed

| Piece | What it does |
|---|---|
| Skill `gearshift:routing` | The routing table, guards, when not to delegate, the ladder. Loaded by the model before it delegates. |
| Agents `gearshift:scout`, `sorter`, `fixer`, `reviewer`, `builder`, `architect`, `prover` | Subagents with model and effort fixed in their definitions. |
| Hook: SessionStart | Remembers the session model; adds a short routing brief (about 300 tokens) to context. |
| Hook: PreToolUse on Agent | Fills in model and effort on calls that left them unset. |
| Hook: PostToolUse on Agent | Records which model actually ran. |
| Hook: SubagentStop | Totals the subagent's tokens and cost from its transcript. Runs in the background. |
| Hook: UserPromptSubmit | One line of advice on a clear session-model mismatch, once per session. |
| Hook: PreModelSwitch / PostModelSwitch | Shows what a mid-session switch re-sends; remembers the new model. |
| Commands `/gearshift:route`, `/gearshift:report`, `/gearshift:doctor` | Explain a route, show the ledger, check the install. |

## The PreToolUse hook, step by step

For each Agent call:

1. **Skip** if mode is `off`, or the agent type is `Plan`, `fork`, `statusline-setup` or `claude-code-guide`.
2. **Leave alone** if the agent's definition sets a model (anything other than `inherit`), if it belongs to another plugin, or if its definition cannot be found. `gearshift:*` agents are pinned by definition.
3. **Classify** the task from `description` and `prompt`.
4. **Leave alone** if the class is `unsure` or confidence is under 0.55.
5. **Apply the ceiling.** With the default `ceiling=session`, a route above the session's tier stays on the session model and gets one more effort level instead. If the session model is unknown, only Haiku and Sonnet routes are applied.
6. **Model:** in `fill` mode, set it only if the call named none. In `strict` mode, also replace one that differs. If the route equals the session tier, leave `model` unset so the subagent inherits the exact session model.
7. **Effort:** set it if the call named none, the agent's definition has none, the Claude Code version accepts the parameter, and `CLAUDE_CODE_EFFORT_LEVEL` is not set.
8. **Rewrite** the call if anything changed, record the decision in the ledger, and (if `announce` is on) show one line such as `gearshift: Explore -> haiku@medium (lookup, confidence 1.00)`.

The rewrite returns the documented PreToolUse output: `updatedInput` with the complete input (it replaces the whole object, so every original field is carried over) and `permissionDecision: "allow"`. Your deny and ask rules are still evaluated by Claude Code regardless. Set `GEARSHIFT_PERMISSION=ask` to be shown each rewritten call, or `none` to omit the decision.

## The judge

Keyword rules only know English, Spanish and Portuguese. For anything else, and for wording they don't recognise, the hook used to do nothing. Now it can ask Haiku:

| `judge` | What happens on a call the keywords can't classify |
|---|---|
| `off` | Nothing. The call is left alone, as in 1.0. |
| `api` | One Messages API call (Haiku, low effort, 6 second timeout) using `GEARSHIFT_API_KEY`, or `ANTHROPIC_API_KEY` if that is what you have. Use the first if you are on a subscription: exporting `ANTHROPIC_API_KEY` switches Claude Code itself to API billing. |
| `claude` | Headless `claude -p --model haiku` on your Claude Code login. No key needed. Slower, 20 second timeout. Experimental. |
| `auto` | `api` if the key is set, otherwise `off`. |

Confident calls never reach the judge. Answers are cached in `judge_cache.json` next to the ledger, so a repeated prompt costs nothing. The ledger marks judged decisions (`"judged": true`). If the judge fails, the hook does what it did before: nothing.

The headless child session runs with `GEARSHIFT_INSIDE_JUDGE=1`, and every gearshift hook exits immediately when it sees that, so the judge can't trigger itself.

Start it in shadow mode like everything else: `GEARSHIFT_MODE=shadow GEARSHIFT_JUDGE=claude`, then look at `/gearshift:report`.

## Outcomes: what happens after the route

Three hooks work together here.

**PreToolUse.** When the hook fills in a cheaper model, it appends this to the subagent prompt (setting `hint`):

> If this task turns out to need a stronger model than you (you are guessing, or you tried and could not get it right), stop and reply with a first line that starts with ESCALATE: followed by one sentence on why. Do not hand back work you are not confident in.

Calls the hook does not change are not touched. The pinned gearshift agents already carry the same rule in their definitions.

**PostToolUse.** If the answer has a line starting with `ESCALATE:`, the ledger records it with the reason, and Claude gets one line of context: which model the subagent ran on, why it stopped, and the next rung to re-run on. If the next rung is above your ceiling it says to take the task in the main session instead. An Agent call that comes back with an error status is recorded as failed. In shadow mode both are logged and nothing is said.

**PreToolUse again.** Each Agent call is fingerprinted (hashes of its distinct words, kept in the session state file, never the text). If a new call matches an earlier one in the same session that escalated or failed, the hook starts it one rung above where it fell short, even if the keyword rules would have been unsure. A repeat of a task that went fine is routed as usual. An explicit `model` on the call still wins.

Then, after a week or two of real use:

```bash
gearshift tune            # last 30 days
gearshift tune --all --min-runs 10 --raise-at 0.2
```

It groups finished runs by task class and the tier they ran on, and proposes a raise only when a class has enough runs on its home tier and too many fell short. The output ends with the JSON to merge into `~/.config/gearshift/policy.json`. Nothing is applied for you.

## Verify

`GEARSHIFT_VERIFY="python3 -m unittest discover -s tests"` (or `"verify": "..."` in `~/.config/gearshift/config.json`).

After an Agent call returns, if the task was a code change (class `edit` or `build`, or the `mutating` flag), the mode is `fill` or `strict`, and the subagent neither escalated nor errored, the hook runs the command in the project folder with a 25 second limit.

- Exit 0: recorded as `"verify": "pass"`, nothing is said.
- Any other exit: recorded as `"verify": "fail"`. Claude gets the model the subagent ran on, the last 600 characters of the output, an instruction not to build on the result, and the next rung to redo it on. The task is marked as fallen short, so sending it again starts one rung up.
- Timeout or the command could not start: recorded as nothing, treated as unknown.

**Background agents.** Claude Code often runs subagents in the background. For those, PostToolUse fires at launch, so the check runs at SubagentStop instead, when the agent really finishes. A failing check there sends the output back to the subagent once (it is not allowed to stop) with the instruction to fix it or answer `ESCALATE:`. If it fails again the result is recorded as a failed check and the next attempt at the task starts one rung up.

It applies whoever chose the model, the hook or Claude. If your suite takes longer than 25 seconds, point it at a fast subset. The command comes from your own settings only; a `.gearshift.json` in a repo is ignored for this key.

## Probing

Off by default. `GEARSHIFT_PROBE=0.1` (or `"probe": 0.1` in `~/.config/gearshift/config.json`) sends about one in ten eligible calls one rung down the ladder: `sonnet@high` becomes `sonnet@medium`, `sonnet@medium` becomes `haiku@high`.

A call is eligible only if all of this holds: the keyword rules were confident; the class is not `edit`, `build`, `frontier` or `unsure`; none of the flags `mutating`, `stakes`, `tricky`, `long_horizon`, `computer_use`, `ambiguous`, `major`, `multi_file` is set; the call named no model and no effort; it is not a retry; the mode is not `strict`. The choice is a hash of the call id, so it is repeatable.

A probed call always gets the escalate hint, is announced as a probe, and is logged with `probe_from`. `gearshift tune` judges probes separately from normal runs: with 15 or more probes of a class at a rung and at most 5% falling short, it proposes moving the class down; at 15% or more falling short it says to leave it.

## Status line

`gearshift statusline --install` prints the `statusLine` entry for `~/.claude/settings.json` with this machine's paths filled in. Claude Code passes session JSON on stdin; the command uses `session_id` (and the project directory if present) and reads the last 512 KB of the ledger. I built it from the documented input and tested it with sample payloads; it has not been checked in a live status bar yet.

## Modes

| Mode | Behaviour |
|---|---|
| `off` | Hooks do nothing. Agents and skill still available. |
| `shadow` | Classify and log, change nothing. `/gearshift:report` shows what would have happened. |
| `fill` | Default. Fill in what the orchestrator left unset. |
| `strict` | Also replace a model the orchestrator chose when it disagrees with the task class. |

If you want to see its judgment before trusting it, run `shadow` for a day. That's what I'd do.

## Settings

| Setting | Values | Default | Meaning |
|---|---|---|---|
| `mode` | `off` `shadow` `fill` `strict` | `fill` | Above. |
| `ceiling` | `session` `haiku` `sonnet` `opus` `fable` | `session` | Highest tier the hook may assign. It limits the hook's own choices; a model the orchestrator passes explicitly is not capped in `fill` mode. |
| `set_effort` | `auto` `always` `never` | `auto` | `auto` sets effort only on Claude Code 2.1.292+. |
| `announce` | bool | `true` | One line per routed subagent. |
| `advise` | bool | `true` | Mismatch and model-switch notes. |
| `brief` | bool | `true` | Routing brief at session start. |
| `explore` | `route` `leave` | `route` | Whether to route the built-in Explore agent. |
| `permission` | `allow` `ask` `none` | `allow` | Decision sent with a rewritten call. |
| `judge` | `auto` `off` `api` `claude` | `auto` | Second opinion from Haiku when the keyword rules aren't confident. `auto` is `api` when `GEARSHIFT_API_KEY` or `ANTHROPIC_API_KEY` is set, otherwise off. Cannot be set from a project file. |
| `hint` | `true` `false` | `true` | When the hook routes a call to a cheaper model, append one line telling the subagent to answer `ESCALATE:` if the task is beyond it. A project file can switch it off. |
| `probe` | `0` to `0.5` | `0` | Share of safe, read-only calls sent one rung cheaper than the policy says. See Probing. Cannot be set from a project file. |
| `verify` | a shell command | empty | Run after a subagent that changed code returns. See Verify. Cannot be set from a project file. |

Where to set them, later wins:

1. `~/.config/gearshift/config.json`
2. The plugin's options (`/plugin`, then gearshift, then configure): mode, ceiling, announce, advise
3. Environment: `GEARSHIFT_MODE`, `GEARSHIFT_CEILING`, and so on
4. `<project>/.gearshift.json`, **conservative only**: it can set mode to `off` or `shadow`, lower the ceiling, turn `announce`/`advise`/`brief` off, set `explore` to `leave`, and override the policy. It cannot select `strict` or raise the ceiling, and a policy override still cannot route above your ceiling. A repo you just cloned shouldn't be able to make your subagents cost more.

## How it interacts with Claude Code's own settings

- **`CLAUDE_CODE_EFFORT_LEVEL`** overrides per-subagent effort, so gearshift does not set effort while it is present.
- **`CLAUDE_CODE_SUBAGENT_MODEL`** is the fallback for subagents with no model. A model set on the call (which is what the hook does) comes first. With `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`, Claude Code's forced model may apply regardless; `/gearshift:report` shows which model actually ran.
- **`availableModels`** in managed settings can block a tier. Claude Code then substitutes or falls back to the inherited model. The ledger records the model that ran.
- **Tier aliases differ by provider.** On Bedrock, Vertex and Foundry, `haiku` and `sonnet` may resolve to older models. Pin them with `ANTHROPIC_DEFAULT_HAIKU_MODEL` and friends if you want specific versions.
- **Other PreToolUse hooks on Agent.** Hooks run in parallel and Claude Code does not document how several `updatedInput` results combine. If another plugin also rewrites Agent calls, run gearshift in `shadow` mode or remove one of them.

## The ledger

Events are appended to `~/.claude/gearshift/events.jsonl` (override with `GEARSHIFT_HOME`). Nothing leaves your machine. Each line is one of:

- `route`: the decision for an Agent call and what the hook did (`filled`, `kept`, `pinned`, `inherit`, `low-confidence`, `shadow`, `overrode`, `effort-only`, `skipped`). Includes the first 80 characters of the call's description.
- `done`: the model Claude Code reports as resolved, duration and tool-call count.
- `usage`: per-model token totals and list-price cost, read from the subagent's transcript, where every request is counted once (streamed lines for the same response are de-duplicated).
- `session`: session start and model switches.

`gearshift report` (or `/gearshift:report`) joins them:

```text
gearshift report - last 7 days

Agent calls seen: 42
    27  model filled in by gearshift
     8  orchestrator's own choice kept
     5  agent pinned by its definition
     2  not confident enough to act
  by task class: lookup 19, edit 9, review 6, build 5, design 3

Subagent runs measured: 40
  haiku    21 runs  1,912,400 tokens  $0.3120
  sonnet   13 runs  2,204,100 tokens  $3.1040
  opus      6 runs    911,300 tokens  $4.0210

Subagent spend at list price:            $7.4370
Same tokens at your session model rates: $19.8040
```

(Illustrative layout. Your numbers come from your own sessions.)

The last line reprices the same token counts at the session model's rates. It is there to show the weight of routing down, and it is not a measurement of savings: a bigger model would have used a different number of tokens, often fewer. On subscription plans the dollar figures are relative weight, not a bill.

Options: `--days N`, `--all`, `--session ID`, `--json`.

## Troubleshooting

**Nothing is being routed.** Run `/gearshift:doctor`. Then check, in order: mode is `fill` or `strict`; the calls are not already naming a model (kept by design); the agent type is not pinned; the classifier is confident (`gearshift route --platform claude-code --agent-type general-purpose "<the prompt>"`). `/gearshift:report` lists the action taken for every call.

**"hook error" notices.** The hooks exit 0 on every failure they can catch. A notice means the shell could not start the launcher at all. On Windows without Git Bash, hooks run in PowerShell, where `sh` does not exist: install Git for Windows.

**See what the hook saw.** Set `GEARSHIFT_DEBUG=1` and read `~/.claude/gearshift/debug.log`. Claude Code's own view is in `claude --debug`.

**Subagents ignore the effort.** Check for `CLAUDE_CODE_EFFORT_LEVEL` and your Claude Code version.

**Remove the session brief.** `GEARSHIFT_BRIEF=false`, if you would rather not spend the tokens. The skill still loads on demand.

## Without the plugin

The skill works on its own, as the original did:

```bash
git clone https://github.com/vjprsi/gearshift
mkdir -p ~/.claude/skills ~/.claude/agents
cp -r gearshift/skills/routing ~/.claude/skills/routing
cp gearshift/agents/*.md ~/.claude/agents/
```

Copied this way the agents are named `scout`, `fixer` and so on, without the `gearshift:` prefix, so edit the agent names in the skill's table to match. You lose the hook, the ledger and the commands.
