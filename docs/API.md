# Library and CLI reference

```bash
pip install git+https://github.com/vjprsi/gearshift            # core, no dependencies
pip install "gearshift[anthropic] @ git+https://github.com/vjprsi/gearshift"   # plus the Anthropic SDK
```

Python 3.9+. The core has no dependencies and makes no network calls.

## route

```python
from gearshift import route

d = route("Plan the database migration from MySQL to Postgres with zero downtime.")
```

| Argument | Meaning |
|---|---|
| `task` | The task text. |
| `description` | A short summary, weighted 1.5x. |
| `agent_type` | A Claude Code subagent type, used as a prior. |
| `platform` | `"api"` (default) or `"claude-code"`. Affects which effort counts as the default, and treats questions as lookups. |
| `task_class` | Skip classification: `lookup`, `extract`, `edit`, `write`, `review`, `build`, `design`, `research`, `frontier`, `chat`. |
| `floor`, `ceiling` | Tier bounds. |
| `available` | Tiers this account can use. |
| `prefer` | `"cost"`, `"balanced"` or `"quality"`: one effort level down or up. |
| `context_tokens` | Prompt size, for the Haiku long-prompt note. |

It returns a `Decision`:

| Field | |
|---|---|
| `tier`, `model`, `effort` | `'opus'`, `'claude-opus-5-5'`, `'high'` |
| `task_class`, `confidence` | `'design'`, `0.88` |
| `reasons`, `notes`, `flags` | Why, in order |
| `ladder` | `[('opus', 'xhigh'), ('fable', 'high'), ('fable', 'xhigh')]` |
| `strategy` | Set for high-volume or checkable work |
| `capped_from` | The tier it wanted, if a ceiling or availability capped it |
| `effort_is_default` | Whether `effort` equals the model's default on this platform |
| `price` | List price per million tokens |
| `api_params()` | `{'model': ..., 'output_config': {'effort': ...}}` |
| `claude_code_params()` | `{'model': 'opus', 'effort': 'high'}` |
| `explain()`, `to_dict()` | Text and JSON forms |

`api_params()` always includes effort. Sending a model's default level behaves exactly like omitting it, so being explicit costs nothing and survives a default changing.

## Router

```python
from gearshift import Router, load_policy

router = Router(load_policy({"classes": {"review": {"tier": "opus", "effort": "medium"}}}))
router.route(task)
router.classify(task)                      # scores, flags, evidence, without routing
router.ladder("sonnet", "medium")          # [('sonnet', 'high'), ('opus', 'medium'), ...]
router.advise_session(task, current_tier="haiku")
```

### A judge for what the offline classifier cannot read

```python
from gearshift import Router
from gearshift.anthropic_adapter import make_call
from gearshift.judge import make_judge

router = Router(judge=make_judge(make_call()))
```

The judge is any callable `(task) -> class name or None`. It is consulted only when the offline classifier returns `unsure` or a confidence under 0.5. `make_judge` asks Haiku at low effort for one word. If the judge raises or returns something unknown, routing carries on without it.

## run: escalate on failure

```python
from gearshift import run
from gearshift.anthropic_adapter import make_call

def verify(output):
    ok = output.strip().startswith("{")
    return ok, None if ok else "reply with JSON only"

result = run(task, make_call(), verify, max_attempts=3, budget_usd=0.50)
```

Starts at the routed tier and effort. While `verify` fails, it climbs one rung of the ladder and passes the feedback to the next attempt.

- `verify(output)` returns `bool` or `(bool, feedback)`. **Without `verify` there is exactly one attempt**: no failure signal, nothing to escalate on.
- `max_attempts` (default 3) and `budget_usd` bound the climb. The budget is checked between attempts.
- Extra keyword arguments go to `route` (`ceiling=`, `prefer=`, ...).
- Exceptions from `call` propagate. A network error is not a reason to buy a bigger model; put your own retry around it.

`RunResult`: `ok`, `output`, `attempts` (each with tier, model, effort, ok, cost, usage, seconds, feedback), `cost_usd`, `cost_known`, `stopped` (`passed`, `unverified`, `exhausted`, `budget`), `decision`.

## run_batch: cheap-first retry

```python
from gearshift import run_batch

batch = run_batch(items, call, verify,                     # verify(item, output)
                  first=("opus", "low"), retry=("opus", "high"),
                  prompt=lambda item: template % item, max_workers=8)
batch.summary()
# {'tasks': 200, 'passed': 194, 'pass_rate': 0.97, 'first_pass': 171,
#  'cost_usd': ..., 'cost_per_finished_task': ...}
```

Every item runs at `first`; only failures run again at `retry`. `first=None` routes each item on its own and retries one rung up. `retry=None` disables the second pass.

`cost_per_finished_task` is total spend, failures included, divided by items that passed.

## Your own `call`

`run` and `run_batch` take any callable:

```python
def call(model: str, effort: str, prompt: str, feedback: str | None) -> CallResult | tuple[str, usage] | str: ...
```

Return usage (an SDK `Usage` object or a dict with the API's field names) if you want costs. `make_call(client, system=..., max_tokens=..., extra={...})` builds one for the Anthropic SDK. It streams, sets `max_tokens` by effort (8k at low up to 64k at xhigh, because the limit covers thinking as well as the reply), never sets `thinking`, and drops or lowers effort for older models that do not support the level.

## Pricing

```python
from gearshift import cost_usd, resolve_model, tier_of

cost_usd(response.usage, "claude-opus-5-5")          # one request, list price
cost_usd(usage, "haiku", batch=True)
resolve_model("us.anthropic.claude-opus-4-6-v1:0")   # 'claude-opus-4-6'
tier_of("claude-mythos-5-2")                         # 'fable'
```

Prices each token kind at its own rate: input, 5-minute and 1-hour cache writes, cache reads, output. Applies Haiku 5.5's long-prompt rate per request. Returns `None`, not zero, for a model with no price on file. Fast mode and regional multipliers are not modelled.

## CLI

```text
gearshift route [--json|-q] [--platform api|claude-code] [--ceiling T] [--floor T]
                [--available a,b] [--prefer cost|balanced|quality] [--class C]
                [--description S] [--agent-type S] TASK...        (or - for stdin)
gearshift session [--current T] TASK...     starting model and effort for a whole session
gearshift ladder TIER EFFORT [--ceiling T]  where to go next
gearshift cost --model M --input N --output N [--cache-read N] [--cache-write-5m N] [--cache-write-1h N] [--batch]
gearshift policy [--json]                   the routing table in effect
gearshift eval CASES.jsonl                  score the router on labelled tasks
gearshift bench TASKS.jsonl [--configs ...] [--simulate]
gearshift report [--days N|--all] [--session ID] [--json]
gearshift doctor
```

`gearshift route -q "..."` prints `<model> <effort>` for shell scripts.
