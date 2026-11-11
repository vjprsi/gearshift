"""Score the router against a labelled JSONL file.

    {"task": ..., "tier": ..., "class": ..., "effort": ...}

description / agent_type / platform are optional, class and effort are only
checked when present.

Watch "under-routed" (sent to a smaller tier than the label). Over-routing
costs money, under-routing costs quality and nobody notices.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

from .policy import tier_rank
from .router import Router


def load_cases(path: str) -> List[dict]:
    cases = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("//"):
                cases.append(json.loads(line))
    return cases


def evaluate(path: str, router: Optional[Router] = None) -> Dict[str, object]:
    router = router or Router()
    cases = load_cases(path)
    tier_ok = class_ok = class_n = effort_ok = effort_n = under = over = 0
    acted = acted_under = acted_over = 0
    act = router.policy.thresholds.get("act_confidence", 0.55)
    misses: List[dict] = []
    for c in cases:
        d = router.route(
            c["task"],
            description=c.get("description"),
            agent_type=c.get("agent_type"),
            platform=c.get("platform", "api"),
        )
        t_ok = d.tier == c["tier"]
        tier_ok += t_ok
        confident = d.task_class != "unsure" and d.confidence >= act
        acted += confident
        if not t_ok:
            if tier_rank(d.tier) < tier_rank(c["tier"]):
                under += 1
                acted_under += confident
            else:
                over += 1
                acted_over += confident
        c_ok = True
        if "class" in c:
            class_n += 1
            c_ok = d.task_class == c["class"]
            class_ok += c_ok
        e_ok = True
        if "effort" in c:
            effort_n += 1
            e_ok = d.effort == c["effort"]
            effort_ok += e_ok
        if not (t_ok and c_ok and e_ok):
            misses.append({
                "task": c["task"][:90],
                "want": "%s/%s%s" % (c.get("class", "?"), c["tier"], ("@" + c["effort"]) if "effort" in c else ""),
                "got": "%s/%s@%s" % (d.task_class, d.tier, d.effort),
                "confidence": d.confidence,
                "tier_miss": not t_ok,
            })
    n = len(cases)
    return {
        "cases": n,
        "tier_accuracy": round(tier_ok / n, 4) if n else 0.0,
        "class_accuracy": round(class_ok / class_n, 4) if class_n else None,
        "effort_accuracy": round(effort_ok / effort_n, 4) if effort_n else None,
        "under_routed": under,
        "over_routed": over,
        # the hook only acts when confident, so count those separately
        "confident": acted,
        "confident_under_routed": acted_under,
        "confident_over_routed": acted_over,
        "misses": misses,
    }
