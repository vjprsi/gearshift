# How accurate is the classifier?

Short answer: reliable on phrasing it knows, and it says `unsure` instead of guessing when it doesn't. Think of it as a backstop, not an oracle.

## What it is

An offline classifier: about 750 lead verbs (English, Spanish, Portuguese) and 40 groups of phrase patterns, plus a dozen conflict rules. No model call, a few milliseconds, the same answer every time. That is why it can run inside a hook on every subagent call, and also why it is brittle to wording it has never seen.

## How it was scored

Labelled task files in `tests/`, one JSON object per line with the tier (and usually the class and effort) the policy should produce. Two numbers matter:

- **Under-routed**: sent to a smaller tier than the label. This is the harmful error, because the result is worse and nothing tells you.
- **Over-routed**: sent to a bigger tier. This wastes money and is otherwise harmless.

The development set (`cases_dev.jsonl`, 118 tasks) was used to write the rules and passes 100% by construction. It is a regression suite and proves nothing about new tasks.

Then I wrote four more sets, each one before the rules that handle it, and ran each **once, blind**. These are the first-run scores, unedited:

| Set | Tasks | Right tier | Under-routed | Over-routed | What happened next |
|---|---|---|---|---|---|
| 1 `cases_holdout.jsonl` | 66 | 89.4% | 5 | 2 | Mined for fixes. Three labels changed from Fable to Opus when the policy moved research and orchestration to Opus on the published numbers. |
| 2 `cases_holdout2.jsonl` | 45 | 75.6% | 3 | 8 | Mined for fixes. |
| 3 `cases_holdout3.jsonl` | 40 | 42.5% | 7 | 16 | Written to avoid vocabulary the classifier already knew. Led to the verb list roughly doubling to about 400, file-path masking, and repeated verbs no longer stacking. |
| 4 `cases_holdout4.jsonl` | 40 | 90.0% | 1 | 3 | **Never tuned against.** The four misses are listed below; I left them in. |

Sets 1 to 3 now score 95% or better because they were used to improve the rules. They are regression suites too. Set 4's 90.0% is the only blind English score for the classifier as shipped.

## Spanish and Portuguese (1.1)

I work in Colombia. Half the prompts around me are in Spanish, and 1.0 sent every one of them to `unsure`.

| Set | Tasks | Before | After | Notes |
|---|---|---|---|---|
| `cases_es.jsonl` | 30 | 30.0% | 100% | Written first, then used to check the new verbs. A regression suite, nothing more. |
| `cases_es_blind.jsonl` | 20 | not run | **90.0%** | Written after, run once, never tuned against. 0 under-routed. Both misses fell to `unsure`. |

The two blind misses, left in: "Dime qué servicios dependen de la librería de autenticación interna." and "Necesito que revises el contrato adjunto y señales las cláusulas riesgosas."

The Spanish and Portuguese verbs are mapped onto the English ones (`corrige` scores like `fix`), so the conflict rules are shared. The English sets did not move.

## Other languages, and the judge (1.1)

`cases_multi.jsonl` is 25 tasks in German, French, Italian, Hindi and Japanese.

| Router | Right tier | Under-routed | Over-routed |
|---|---|---|---|
| Keywords only | 32.0% | 1 | 16 |
| Keywords + a judge that answers correctly | 100% | 0 | 0 |
| Keywords + the real Haiku judge (first run, 2026-11-10) | **100%** | 0 | 0 |

The middle row is an upper bound. It shows the plumbing works and what a perfect judge would be worth. The third row is one real run, 25 tasks, my own labels. Encouraging, not proof. The other sets with the judge on are in [RESULTS.md](RESULTS.md). The one under-routed task is a proof in French that fell to `unsure`, so Opus instead of Fable.

## What the numbers mean

**Set 3 is what unfamiliar wording really looks like.** When the classifier has not seen the verbs, it mostly says `unsure` (13 of the 23 misses went to Opus that way). That is the designed failure: `unsure` is safe on the API and a no-op in the hook. But 7 tasks were under-routed, 2 of them confidently, so "it fails safe" is a tendency, not a guarantee.

**I wrote the tasks and I wrote the rules.** Even run blind, the tasks come from the same head that picked the vocabulary. Real subagent prompts written by a model, and real API traffic, will look different. Treat every number here as optimistic until you've run `gearshift eval` on your own tasks.

**Tier accuracy flatters coarse mistakes and hides fine ones.** A `review` classified as `write` is counted right if both land on Sonnet, though the effort differs.

## The safeguards that matter more than accuracy

- **Haiku decisions.** Across the five English sets as they stand, 0 of the 77 tasks routed to Haiku were labelled above Haiku. The rules that protect this (a change is never a lookup, a bug hunt is never a lookup, when torn go up) are tested directly in `tests/test_routing.py`.
- **The hook acts only when confident**, and only on calls that named no model. On set 4, the classifier was confident on 32 of 40 tasks and wrong in the harmful direction on 1 of those.
- **In Claude Code the classifier is not the router.** The orchestrating model is, guided by the skill and the pinned agents. The hook covers calls it left unrouted.
- **The judge can't override the safety rules.** Its answer goes through the same checks: a task that changes code is never `lookup`, `extract` or `chat`, and a hard-to-reproduce bug with a fix is `build`. These are tested in `tests/test_routing.py` and `tests/test_adapter.py`.
- **A judge failure is a no-op.** Timeout, bad key, garbage reply: the decision stays `unsure`.

## Known misses

I left these unfixed so set 4 stays unseen:

| Task | Wanted | Got | Why |
|---|---|---|---|
| "How many open TODO and FIXME comments are there per top-level directory?" | haiku | sonnet (edit) | "comments" reads as a code-edit cue. |
| "The footer shows the wrong year. Use the current year instead of the hard-coded 2024 in Footer.vue." | sonnet | opus (unsure) | "use" is not in the verb list. |
| "Put together an exhaustive, cited study of chargeback fraud typologies..." | opus | sonnet (write) | "exhaustive, cited study" is not recognised as deep research. |
| "At a glance, is there anything obviously wrong with this Dockerfile?" | sonnet | opus (unsure) | The question form is not recognised as a review. |

And from the earlier sets:

| Task | Wanted | Got | Why |
|---|---|---|---|
| "Find the bug." | opus (unsure) | sonnet @ high (review) | With no location or detail this is arguably too vague to route. Sonnet at high plus the ladder is defensible. |
| "Which migrations have not been applied on staging? Compare the files..." | haiku | sonnet (review) | "compare" reads as judgment; here it is a mechanical set difference. |

## Check it on your own tasks

```bash
gearshift eval my_cases.jsonl
```

with lines like:

```json
{"task": "Find where the retry policy is configured", "tier": "haiku"}
{"task": "...", "description": "short summary", "agent_type": "Explore", "platform": "claude-code", "tier": "haiku", "class": "lookup", "effort": "medium"}
```

It prints tier accuracy, under- and over-routing, how many decisions were confident, and each miss. If your tasks miss, the fix is usually a verb or a pattern in a policy override; see [POLICY.md](POLICY.md).
