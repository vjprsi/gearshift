---
name: doctor
description: Check the gearshift install by confirming the Python version, policy load, state directory, Claude Code version and current mode. Use when routing does not seem to be happening or the user runs /gearshift:doctor.
---

Run this command with the Bash tool and show the output as-is in a code block:

```
sh "${CLAUDE_PLUGIN_ROOT}/bin/gearshift" doctor
```

If a line is marked `[!!]`, explain that one line in a sentence. A version line marked `[!!]` means the plugin and the CLI are different versions: give the user the update command it prints. If `verify` is on, tell the user to run that command once by hand in the project folder, as the output says.

If the user wants to report a problem, run the same command with `--bundle` and show that output instead: it is made to be pasted into an issue and has no paths, prompts or commands in it. If everything is ok but routing
still is not happening, the usual causes are: mode is `off` or `shadow`, the Agent call already
named a model (kept by design in `fill` mode), the subagent type is pinned by its own definition,
or the task text gave the classifier too little to be confident.
