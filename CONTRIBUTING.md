# Contributing

The most useful contributions, in order:

1. **A task that routes wrong.** Open an issue with the task text (and the description and agent type, if it came from Claude Code), what you expected and the output of `gearshift route --json "..."`.
2. **Results from `gearshift bench` on real tasks**, especially ones that contradict the policy. The table should follow measurements.
3. **A report from your Claude Code sessions**: does the hook rewrite Agent calls on your version? Include `/gearshift:doctor` output.

## Changing the rules

- A fix for a misrouted task is usually a verb or a pattern in `src/gearshift/data/policy.json`, not code.
- Add the task to `tests/cases_dev.jsonl` first and watch it fail.
- **Do not tune against `tests/cases_holdout4.jsonl`.** It is the unseen set. If you add cases to it, add them before changing any rule and report the blind score in your pull request.
- Under-routing is the error that matters. A change that fixes an over-route by creating an under-route is a regression.
- Every row of the routing table has a reason. If you change a row, change the reason, and cite a measurement if there is one.

## Running the tests

```bash
python -m unittest discover -s tests -t .
claude plugin validate .        # if you have Claude Code
```

No dependencies. Python 3.9 or newer. One test checks the request shape against the real Anthropic SDK and is skipped if it is not installed.

## Style

Standard library only in `src/gearshift`. Hooks must never exit non-zero or write to stderr: a routing plugin that breaks a session is worse than no plugin.
