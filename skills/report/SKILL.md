---
name: report
description: Show the gearshift ledger for recent sessions, covering how many subagent calls were routed, to which model tiers, and what they cost at list price. Use when the user asks what routing saved or runs /gearshift:report.
argument-hint: "[--days N | --all | --session ID]"
---

Run this command with the Bash tool (append any options the user gave: $ARGUMENTS):

```
sh "${CLAUDE_PLUGIN_ROOT}/bin/gearshift" report
```

Show the output as-is in a code block. Do not turn the "same tokens at session model rates"
line into a savings claim: it reprices the same token counts and is a comparison aid, not a
measurement. If the report is empty, say that the ledger fills in as subagents run.
