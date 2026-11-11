# Changing the routing table

The policy is `src/gearshift/data/policy.json`. You do not edit it in place. You override it.

## Where overrides go

Later wins; all are deep-merged onto the bundled policy.

1. `~/.config/gearshift/policy.json`
2. The file named by `GEARSHIFT_POLICY`
3. `load_policy({...})` in code
4. The `"policy"` key of `<project>/.gearshift.json` (Claude Code hook only, and it still cannot route above your ceiling)

`gearshift policy` prints the table in effect.

## Change where a class routes

```json
{ "classes": { "review": { "tier": "opus", "effort": "medium" } } }
```

Only the keys you give are replaced. Tiers are `haiku`, `sonnet`, `opus`, `fable`. Efforts are `low`, `medium`, `high`, `xhigh`, `max`.

## Teach it your vocabulary

Verbs map to class weights. A weight around 2.5 is a normal verb, 3 is unambiguous, and anything below 1.5 will not classify a task alone.

```json
{
  "lead_verbs": { "reconcile": { "review": 3 }, "provision": { "build": 2.5 } },
  "mutating_verbs": ["provision"]
}
```

`mutating_verbs` replaces the list rather than extending it, so include the bundled ones you still want, or add a signal with `"flag": "mutating"` instead.

Signals are merged by `name`: a new name is added, an existing name is replaced.

```json
{
  "signals": [
    { "name": "house rule: KYC is high stakes", "flag": "stakes", "patterns": ["kyc", "sanctions", "aml"], "adds": {} },
    { "name": "our ticket intake", "patterns": ["jira intake", "triage queue"], "adds": { "extract": 2.5 } }
  ]
}
```

Patterns are case-insensitive regular expression fragments. Word boundaries are added on both sides, so write `kyc`, not `\bkyc\b`. A pattern that fails to compile is skipped rather than taking routing down.

Signal options:

| Key | |
|---|---|
| `adds` | Class weights added when any pattern matches (more distinct matches add up to 50% more). |
| `flag` | A flag to set. The router acts on `stakes`, `judgment`, `tricky`, `computer_use`, `vision`, `ambiguous`, `long_horizon`, `latency`, `effort_up`, `effort_down`, `volume`, `checkable`. The classifier acts on `read_only`, `contained`, `mutating`, `multi_file`, `major`, `bug_hunt`. |
| `quiet` | Do not list it as evidence. |
| `raw` | Match with file paths left in (they are masked by default). |
| `no_boundary` | Do not add word boundaries; `^` and `$` then match line starts and ends. |

Remove a bundled signal:

```json
{ "disable_signals": ["vision input"] }
```

## Thresholds

```json
{ "thresholds": { "act_confidence": 0.7, "min_signal": 1.5, "judge_below": 0.5, "frontier_min": 2.0 } }
```

- `act_confidence`: the Claude Code hook acts at or above this. Raise it to make the hook pickier.
- `min_signal`: below this top score a task is `unsure`.
- `judge_below`: the judge is consulted under this confidence.
- `frontier_min`: evidence needed before the top tier is considered at all.
- `torn_margin_per_tier_gap`: how close a higher-tier runner-up must be to win, for a gap of one, two, three tiers.

## The ladder and caps

```json
{
  "ladder": [["haiku", "medium"], ["sonnet", "medium"], ["sonnet", "high"], ["opus", "medium"], ["opus", "high"]],
  "effort_caps": { "haiku": "medium", "sonnet": "high", "opus": "high", "fable": "high" }
}
```

A ladder without Fable is how you say "never escalate to Fable". Keep the rungs in rising order of cost.

## New model generation

Edit `src/gearshift/data/models.json` (or override it at `~/.config/gearshift/models.json`): add the model with its tier, prices, default effort and supported levels, and point `tiers.<tier>` at it. Nothing else changes, because the policy is written by tier.

Then re-read [EVIDENCE.md](EVIDENCE.md) against the new numbers. A release can move both the capability gap and the price ratio, and a table justified by last generation's measurements is a guess.

## Check your change

```bash
gearshift policy
gearshift route "a task that should now route differently"
gearshift eval tests/cases_dev.jsonl        # did you break anything?
gearshift eval my_cases.jsonl               # does it do what you wanted?
```
