---
name: route
description: Show which model and effort gearshift would pick for a described task, with the evidence and the escalation ladder. Use when the user asks "which model should I use for X" or runs /gearshift:route.
argument-hint: <task description>
---

Run this command with the Bash tool, passing the user's task description as one quoted argument:

```
sh "${CLAUDE_PLUGIN_ROOT}/bin/gearshift" route "<task description>"
```

Task description from the user: $ARGUMENTS

Show the command's output as-is in a code block. Then add at most two sentences: whether the
decision fits what you know about the task from this conversation, and what would change it.
The router sees only the text of the description, so say so if context it could not see matters.
