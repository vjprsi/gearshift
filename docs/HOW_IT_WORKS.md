# How gearshift decides

The whole pipeline, and the reason behind each rule. The numbers are in [EVIDENCE.md](EVIDENCE.md).

```text
task text ──► evidence ──► conflict rules ──► task class ──► tier + effort ──► modifiers ──► constraints ──► decision + ladder
             (verbs,                        (confidence)    (policy table)   (stakes, ...)  (ceiling, ...)
              signals)
```

Everything up to the task class is `classify.py`. Everything after is `router.py`. The table, the verbs and the patterns are data in `src/gearshift/data/policy.json`.

## 1. Evidence

**Lead verbs.** The imperative verb that opens a sentence says most about a task: "Find...", "Please refactor...", "Your job is to review...". Polite prefixes and bullets are stripped. The first verb counts in full, later ones progressively less. Verbs after "and", "then" or a comma count at 0.6, as do verbs in clauses like "after renaming". Each verb maps to one or more classes with weights: `fix` is mostly `edit` and a little `build`; `refactor` is `build`, `edit` and some `design`.

A verb counts once. "Add X. Add Y. Add Z." is one piece of evidence, not three, because repetition says nothing about difficulty.

**The summary counts 1.5x.** In Claude Code every Agent call has a short `description`. It is the orchestrator's own summary of the job, so it is weighted above the long prompt, which is full of background.

**Phrase signals.** About forty groups of patterns: "race condition", "into JSON", "entire codebase", "do not modify". Some add weight to a class. Some only set a flag (stakes, checkable, latency) used later. File paths are masked before matching so that `profile.py` is not read as "profile the code".

**Agent type.** `Explore` is a prior toward `lookup`; a type with "review" in its name leans `review`.

## 2. Conflict rules

Keyword scoring on its own gets a predictable set of cases wrong. Every rule below is there because a test case failed without it.

| Rule | Why |
|---|---|
| An explicit read-only instruction ("do not modify any files", "research only") suppresses `edit` and `build`. | Subagent prompts are full of context: "we will later refactor X; for now, list its callers". The verbs in the context are not the job. |
| "Don't touch **other** files" is a scope limit, not read-only. | It marks a contained edit. Treating it as read-only would send an edit to Haiku. |
| A task that changes anything is not a `lookup` (lookup weight is cut to a quarter). | "Run the tests and fix what fails" opens with a lookup verb. Handing a fix to Haiku is the expensive mistake; over-routing a lookup to Sonnet is the cheap one. |
| Hunting for a bug is not a `lookup`. | "Find the bug" opens with "find", but a wrong answer passes unnoticed. This is the Haiku-retrieves-but-does-not-judge finding. |
| Explicitly contained scope ("your part is small", "only this file") discounts project-level signals. | A small task inside a migration is still a small task. |
| Touching both back end and UI, naming three or more files, or listing three or more deliverables after a change verb pushes it toward `build`. | That is what "multi-file feature" looks like in a brief. |
| A question to a subagent is a lookup, not chat. A question about this codebase on the API is a lookup. | "Where is X defined?" is retrieval. |
| "Can you review this?" is a task, not chat. | Politeness is not conversation. |
| Top-tier evidence below a threshold is discarded. | One stray word ("statistical", "proof of concept") must not buy Fable. A direct statement ("prove", "three agents have already failed") does. |

## 3. Picking a class

The highest score wins, with two exceptions.

**No strong signal means `unsure`.** If nothing scores above the minimum, the router does not guess. On the API `unsure` routes to Opus at medium, which is Anthropic's documented starting point when you do not know. In the Claude Code hook `unsure` means do nothing.

**When torn, go up.** If the runner-up belongs to a higher tier and is close behind, it wins. "Close" widens with the tier gap: within 20% for one tier apart, 35% for two, 50% for three. The asymmetry is the point. An over-routed task costs some money; an under-routed one returns a worse answer that looks fine.

**Confidence** combines how strong the evidence is with how far ahead the winner is. The hook acts only at 0.55 or above.

## 4. Class to tier and effort

| Class | Route | Reason |
|---|---|---|
| `lookup` | haiku @ medium | Read-only and verbose, so the cost is input tokens and Haiku's price matters most; a wrong answer is easy to spot. Medium, because at low Haiku is more likely to skip a search or stop early in agent loops. |
| `extract` | haiku @ low | Single-shot, high-volume, checkable against a schema: the documented use for Haiku at low. |
| `edit` | sonnet @ medium | The everyday coding tier; medium is the documented start for well-specified agentic coding. |
| `write` | sonnet @ medium | Content creation is Sonnet's row in the selection matrix. Effort changes thinking, not reply length. |
| `review` | sonnet @ high | Judgment work where the failure is missing something. High is seven points better than medium on SWE-bench Pro for four cents. |
| `build` | opus @ medium | Anthropic's default starting point for agent workloads. |
| `design` | opus @ high | The matrix puts large-scale refactoring and complex systems on Opus. High, because a planning error is paid for by everything built on the plan. |
| `research` | opus @ high | Opus beat Sonnet modestly on the research benchmark; Fable cost far more for no gain there and as an orchestrator. |
| `frontier` | fable @ high | Capability over cost, for the cases that say so. Most of Fable's gain is present at high. |
| `chat` | sonnet @ low | Latency-sensitive; the documented start for chat. |
| `unsure` | opus @ medium | The documented default when you do not know. |

## 5. Modifiers

Applied in this order. Each one adds a line to the decision's reasons.

| Flag | Effect | Reason |
|---|---|---|
| High stakes (production, auth, payments, irreversible) on judged or mutating work | Floor at Sonnet; effort up one level, to `high` at most | Mistakes are expensive. Stakes alone never reach `xhigh`: that level is for long-running work with large token budgets, and being careful is not the same as needing hours. |
| Judgment call | Not Haiku | A wrong answer would pass unnoticed. |
| Hard-to-reproduce bug on a `review` | Opus | Tricky debugging is Opus work even read-only. |
| Computer use | Opus | The selection matrix. |
| Image input | Sonnet or better | Visual understanding. |
| Vague brief on `edit`, `review` or `build` | Opus | Ambiguity is where the more capable model earns its price. |
| Long unattended run | At least Opus; effort up one level | Nobody is there to catch a drift. This is the only route to `xhigh`. |
| Someone is waiting | Effort down one level | Latency. |
| "Careful", "thorough", "double-check" | Effort up one level | The brief asked. |
| "Quick", "rough", "at a glance" | Effort down one level | The brief asked. |
| `prefer="cost"` / `"quality"` | Effort down / up one level | The caller asked. Cost preference is ignored when stakes are high. |

Stakes and `prefer="quality"` add one level, up to `high`. The other shifts net out to at most one level either way. Everything is clamped to the tier's cap: `high` on Haiku and Sonnet (the docs say to go higher only where your evals show a gain), `xhigh` on Opus and Fable. **`max` is never selected.** The docs reserve it for measuring a ceiling.

When a rule raises the tier, the bigger model starts at its own entry effort rather than inheriting the smaller model's, following "move up one model, at a lower effort".

## 6. Constraints

- **Ceiling.** If the route is above the ceiling it is capped there and effort goes up one level (to `high` at most), with a note that effort buys more checking, not skill the model lacks.
- **Available tiers.** A missing tier falls to the next one down, same treatment. If only bigger tiers are available, the smallest of them.
- **Floor.** Raises the tier and uses that tier's entry effort.

## 7. The ladder

```text
haiku@low → haiku@medium → haiku@high → sonnet@medium → sonnet@high → opus@medium → opus@high → opus@xhigh → fable@high → fable@xhigh
```

Three rules produce it:

1. **Verify first.** A way to check its own work is the cheapest improvement there is. In `run()` this is your `verify` function, and its feedback is passed to the next attempt.
2. **Effort before model, up to `high`.** Effort and model are steps on one cost curve, and Anthropic's guidance is to tune effort on one model before adding a second.
3. **Then one model up, at a lower effort.** Past `high`, one more effort level can cost as much as the next model.

The exception is the top: Opus goes to `xhigh` before Fable, because on the published coding numbers that was the cheaper way to the same pass rate.

A start that is not on the ladder (Sonnet at low for chat, say) climbs effort within its tier until it joins.

## 8. What the hook adds

The Claude Code hook wraps the router in a set of refusals. It does nothing when:

- the Agent call already names a model (unless `strict`)
- the agent type is Plan, a fork, pinned by its definition, or another plugin's
- confidence is below the threshold
- the route would exceed the ceiling, in which case it stays on the session model and raises effort instead
- it does not know the session model and the route is above Sonnet

**Plan is never routed** because it inherits the session model on purpose: planning is the judgment you chose that model for.

**The default ceiling is the session model.** I would be pretty annoyed at a plugin that quietly started Fable subagents from my Sonnet session. By default the hook can only route down or sideways. You raise the ceiling when you want it to route up.

**When the route equals the session tier it leaves `model` unset** so the subagent inherits the exact session model rather than whatever the tier alias resolves to, and sets only effort.

## 9. What it doesn't do, on purpose

- **It does not switch your session model.** It will tell you, once, when the work and the model are clearly mismatched, and it shows the cost before a switch you start. The decision is yours.
- **It does not call a model on every decision.** The judge is asked only when the keyword rules aren't confident, and only if you have it on. A router that spends tokens on every decision has to earn them back.
- **It does not change its own policy.** Since 1.2 the ledger records outcomes and `gearshift tune` proposes raises from them, but you apply them. Measure with `bench`, then edit the JSON.
- **It does not claim savings.** The report prices what ran. The comparison figure is labelled as a comparison.
