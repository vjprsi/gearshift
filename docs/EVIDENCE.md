# Evidence

The numbers the policy leans on, where I got them, and how far I'd trust them.

Everything here is from Anthropic's public docs as I read them on 2026-11-10. Anthropic describes its benchmark figures as internal measurements that are directional rather than guarantees, and some comparisons use subsets chosen for the configuration that won. Prices and results drift with every release. Follow the links and check before you rely on a figure.

Sources:

- [Optimizing for cost and intelligence](https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence) (benchmarks, strategies)
- [Choosing the right model](https://platform.claude.com/docs/en/about-claude/models/choosing-a-model) (selection matrix)
- [Effort](https://platform.claude.com/docs/en/build-with-claude/effort) (levels, defaults, per-model guidance)
- [Pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- [Claude Code: prompt caching](https://code.claude.com/docs/en/prompt-caching), [subagents](https://code.claude.com/docs/en/sub-agents), [hooks](https://code.claude.com/docs/en/hooks), [model configuration](https://code.claude.com/docs/en/model-config)

## Prices (USD per million tokens, list)

| Model | Input | Output | Cache read | Default effort (API) |
|---|---|---|---|---|
| Claude Fable 5.2 | $8 | $40 | $0.20 | high |
| Claude Opus 5.5 | $4 | $20 | $0.20 | medium |
| Claude Sonnet 5.5 | $2 | $10 | $0.10 | high |
| Claude Haiku 5.5, prompt up to 100K tokens | $0.10 | $0.50 | $0.01 | medium |
| Claude Haiku 5.5, prompt over 100K tokens | $0.50 | $2.50 | $0.05 | medium |

Cache writes cost 1.25x input (5-minute) or 2x input (1-hour). The Batch API halves input and output. Haiku's long-prompt rate is decided per request and counts cache reads and writes toward prompt length. In Claude Code, Sonnet 5.5 and Fable 5.2 default to medium effort rather than high (the model configuration page: "Fable 5.2, Opus 5.5, Sonnet 5.5, and Haiku 5.5 default to `medium`"; checked 2026-11-11). Until 2.0 my price file had Fable at high in Claude Code. It only feeds the "this is the default effort" flag on a decision; no route changed.

Used in: `src/gearshift/data/models.json`, `pricing.py`.

## Cost per solved task: effort and model on one curve

SWE-bench Pro subset, cost per solved task at list price:

| Configuration | Solved | Per solved task |
|---|---|---|
| Sonnet 5.5, medium | 80.3% | $0.07 |
| Sonnet 5.5, high | 87.6% | $0.11 |
| Opus 5.5, low | 87.4% | $0.12 |
| Opus 5.5, medium | 92.8% | $0.22 |
| Opus 5.5, high | 95.3% | $0.31 |
| Opus 5.5, xhigh (463-problem subset) | 96.5% | $0.75 |
| Fable 5.2, low (463-problem subset) | 94.6% | $0.39 |
| Fable 5.2, high (463-problem subset) | 96.3% | $1.39 |

So in gearshift:

- The order of the ladder. Each rung costs more than the one before, and Sonnet at high sits right next to Opus at low, so raising effort and moving up a model are steps on the same curve.
- `review` runs Sonnet at high, not medium: seven points for four cents.
- `build` starts at Opus medium, the documented default starting point for agent work.
- Above Opus at high, `xhigh` comes before Fable: on this benchmark Opus at xhigh matched Fable at high for a little over half the cost.

## Cheap-first retry

Same benchmark, Opus 5.5, cost per task:

| Policy | Solved | Per task |
|---|---|---|
| Low, failures re-run at high | about 97% | about $0.17 |
| Medium, failures re-run at high | about 97% | about $0.24 |
| Everything at high | 95.3% | $0.29 |

Anthropic's caveats, which gearshift repeats wherever it suggests this: most of the small lift comes from having a second attempt, so treat it as a cost saving and not a quality gain; it needs a failure signal you trust; and every first-pass failure costs two runs of wall-clock time.

Used in: `run_batch`, the `cheap-first` strategy note, the skill.

## Effort is not free to lower

- On DRACO, Opus 5.5 at low scored 13.3 points below medium (while taking 84% less time).
- On the defect-search benchmark, Opus 5.5 at low scored 34 points below medium, for about a fifth of the cost.
- Haiku 5.5 at low is more likely to skip a search, stop early or skip a check in long agent prompts.

What I did with it: `lookup` runs Haiku at medium, not low. Low is reserved for single-shot `extract` work and for briefs that ask for speed. The router lowers effort by at most one level.

## Haiku: retrieval, not judgment

- GPQA Diamond: Haiku 5.5 scored 85% against 91% for Opus and Sonnet, at about a twentieth of Opus's cost per correct answer.
- Defect search (about 22M tokens of source in 201 parts): Opus with **Haiku** subagents scored F1 0.677 for $47 a run; Opus with **Sonnet** subagents scored 0.831 for $139; Opus alone at medium scored 0.827 for $200.
- Coding: adding Haiku subagents to Opus at medium cut cost per attempt by 32% and the pass rate by about 4 points, which is roughly what lowering effort would have done.

Consequence: Haiku is excellent value where a wrong answer is cheap to spot, and it gives up real quality when the reading itself is the judgment. Finding defects is judgment. Hence the split between `lookup` (Haiku) and bug hunts and reviews (Sonnet at high), and the rule that a task which changes anything is never a lookup.

## Fable: where it earns its price and where it does not

- Research (44 DeepResearch Bench II tasks, default effort): Opus 5.5 scored 2.4 points above Sonnet 5.5 for 1.5x the cost. Fable 5.2 cost 4.3x Sonnet for no measurable gain, and 2.9x Opus for a slightly lower score.
- Orchestration (defect search): Fable 5.2 with Sonnet subagents scored 0.833 for $165; Opus with Sonnet subagents scored 0.831 for $139.
- Coding (SWE-bench Pro): Opus at xhigh matched Fable 5.2 at high for about half the cost.
- Hardest single-model result (defect search): Fable 5.2 alone reached F1 0.909 at high, the best of any configuration, for $563 a run against $236 for Opus alone at high (0.850).

So: Fable buys capability at the top end, not efficiency. Research and orchestration route to Opus. Fable is a starting point only when the brief says the problem is that hard (a proof, a novel algorithm, something smaller models already failed), and otherwise it is the last rungs of the ladder. This matches Anthropic's own selection guidance: move to Fable when evals at xhigh on Opus still fall short.

A caveat in the other direction: the same research benchmark found Opus not measurably ahead of Sonnet once tasks Opus declined were counted. If research is most of your spend, benchmark Sonnet at high against Opus for it.

## When delegation pays

Anthropic found delegation to cheaper workers came out ahead in two situations: routine work with a long cost tail, and work larger than one context window. For one dependent chain, or work that fits in a single context, the orchestrator's model alone at lower effort won every case measured.

It also recommends tuning one model's effort before adding a second, and keeping a pairing only if it beats both the same model at lower effort and the next model down alone.

What that changed here: the "when not to delegate" section of the skill, and `gearshift bench`, which exists to run exactly that comparison.

## The prompt cache

In Claude Code each model has its own prompt cache, and on most models so does each effort level. Switching either mid-session means the next request reads the whole conversation with no cache hits. A cache read costs 2.5% to 10% of the input price depending on the model, so an uncached turn on a long conversation is the most expensive turn of the session.

So in gearshift: the first rule (route by delegation, never by switching), the PreModelSwitch notice, and the advice to compact or start fresh before changing model for good.

## Subagent effort inheritance

A Claude Code subagent with no effort of its own runs at the session's level. Effort can be set in a subagent's frontmatter or, from v2.1.292, per Agent call; the `CLAUDE_CODE_EFFORT_LEVEL` environment variable overrides both.

Hence: every pinned agent sets `effort`, the hook sets it on calls it routes, and `doctor` warns when the environment variable is set.
