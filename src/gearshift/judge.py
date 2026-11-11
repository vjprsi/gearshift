"""Optional LLM fallback for tasks the keyword classifier can't place.

The offline classifier only knows English verbs and patterns. For anything
else (other languages, odd phrasing) you can plug in a judge: one Haiku call
at low effort that returns the class name. The router only asks it when
confidence is below thresholds.judge_below.

    router = Router(judge=make_judge(make_call()))

NOTE: only tested with a fake model so far.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

from .policy import Policy, default_policy

PROMPT = """Classify the task below into exactly one class. Reply with the class name only.

Classes:
{classes}

Task:
<task>
{task}
</task>

Class:"""


def make_judge(call: Callable[..., object], *, model: str = "claude-haiku-5-5", effort: str = "low",
               policy: Optional[Policy] = None, max_chars: int = 4000) -> Callable[[str], Optional[str]]:
    pol = policy or default_policy()
    names = [c for c in pol.classes if c != "unsure"]
    listing = "\n".join("- %s: %s" % (n, pol.classes[n]["what"]) for n in names)
    pattern = re.compile(r"\b(%s)\b" % "|".join(map(re.escape, names)), re.IGNORECASE)

    def judge(task: str) -> Optional[str]:
        res = call(model=model, effort=effort, feedback=None,
                   prompt=PROMPT.format(classes=listing, task=task[:max_chars]))
        text = getattr(res, "text", None)
        if text is None:
            text = res[0] if isinstance(res, tuple) else str(res)
        m = pattern.search(text or "")
        return m.group(1).lower() if m else None

    return judge
