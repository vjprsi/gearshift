---
name: doctor
description: Check the gearshift install by confirming the Python version, policy load, state directory, Claude Code version and current mode. Use when routing does not seem to be happening or the user runs /gearshift:doctor.
---

Run this command with the Bash tool and show the output as-is in a code block:

```
sh "${CLAUDE_PLUGIN_ROOT}/bin/gearshift" doctor
```

If a line is marked `[!!]`, explain that one line in a sentence. If everything is ok but routing
still is not happening, the usual causes are: mode is `off` or `shadow`, the Agent call already
named a model (kept by design in `fill` mode), the subagent type is pinned by its own definition,
or the task text gave the classifier too little to be confident.
