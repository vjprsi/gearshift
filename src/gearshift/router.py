"""Classification -> (tier, effort), with the reasons.

    >>> from gearshift import route
    >>> d = route("Find every caller of parse_config() and list the file paths")
    >>> d.tier, d.effort
    ('haiku', 'medium')

Treat the result as a starting rung. d.ladder says where to go next if the
output is not good enough.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .classify import Classification, classify
from .policy import EFFORTS, TIERS, Policy, default_policy, effort_rank, tier_rank

# when a rule bumps the tier (stakes, vague brief, ...) the bigger model starts
# here instead of inheriting the smaller model's effort
_ENTRY_EFFORT = {"haiku": "medium", "sonnet": "medium", "opus": "medium", "fable": "high"}

# a judge returns a class name, or {"class": ..., "flags": [...], "notes": [...]}
Judge = Callable[[str], object]


def _shift(effort: str, by: int, lo: str = "low", hi: str = "max") -> str:
    i = effort_rank(effort) + by
    i = max(effort_rank(lo), min(effort_rank(hi), i))
    return EFFORTS[i]


@dataclass
class Decision:
    """Where to run a task, and why."""

    task_class: str
    tier: str
    model: str
    effort: str
    confidence: float
    platform: str = "api"
    effort_is_default: bool = False
    strategy: Optional[str] = None
    reasons: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)
    ladder: List[Tuple[str, str]] = field(default_factory=list)
    capped_from: Optional[str] = None
    price: Optional[Dict[str, float]] = None
    policy_version: str = ""
    classification: Optional[Classification] = None

    def api_params(self) -> dict:
        """Keyword arguments for client.messages.create / .stream."""
        return {"model": self.model, "output_config": {"effort": self.effort}}

    def claude_code_params(self) -> dict:
        """Fields for a Claude Code Agent tool call or subagent frontmatter."""
        return {"model": self.tier, "effort": self.effort}

    def to_dict(self) -> dict:
        return {
            "task_class": self.task_class,
            "tier": self.tier,
            "model": self.model,
            "effort": self.effort,
            "effort_is_default": self.effort_is_default,
            "confidence": self.confidence,
            "judged": bool(self.classification and self.classification.judged),
            "strategy": self.strategy,
            "capped_from": self.capped_from,
            "reasons": list(self.reasons),
            "notes": list(self.notes),
            "flags": list(self.flags),
            "ladder": [{"tier": t, "effort": e} for t, e in self.ladder],
            "price_per_mtok": self.price,
            "platform": self.platform,
            "policy_version": self.policy_version,
            "evidence": list(self.classification.evidence) if self.classification else [],
        }

    def explain(self) -> str:
        lines = ["%s @ %s   (%s)" % (self.tier, self.effort, self.model)]
        lines.append("  class       %s   confidence %.2f%s" % (
            self.task_class, self.confidence,
            "   (decided by the judge model)" if self.classification and self.classification.judged else ""))
        if self.classification and self.classification.evidence:
            lines.append("  evidence")
            for e in self.classification.evidence:
                lines.append("    - " + e)
        lines.append("  reasoning")
        for r in self.reasons:
            lines.append("    - " + r)
        if self.strategy:
            lines.append("  strategy    " + self.strategy)
        if self.ladder:
            lines.append("  if it falls short  " + "  ->  ".join("%s@%s" % r for r in self.ladder[:4]))
        if self.price:
            lines.append("  list price  $%g in / $%g out per MTok" % (self.price["input"], self.price["output"]))
        for n in self.notes:
            lines.append("  note        " + n)
        return "\n".join(lines)


class Router:
    """Routes tasks with a a Policy.

    judge is an optional callable (task) -> class name consulted only
    when the offline classifier is not confident (see gearshift.judge).
    """

    def __init__(self, policy: Optional[Policy] = None, judge: Optional[Judge] = None,
                 judge_mode: str = "unsure"):
        """judge_mode: "unsure" asks the judge only when the keyword rules are
        not confident; "always" asks it for every task."""
        self.policy = policy or default_policy()
        self.judge = judge
        self.judge_mode = judge_mode

    # -- classification ------------------------------------------------------

    def classify(self, task: str, *, description: Optional[str] = None,
                 agent_type: Optional[str] = None, platform: str = "api") -> Classification:
        c = classify(task, policy=self.policy, description=description,
                     agent_type=agent_type, platform=platform)
        below = self.policy.thresholds.get("judge_below", 0.5)
        ask = self.judge_mode == "always" or c.task_class == "unsure" or c.confidence < below
        if self.judge is not None and ask and (task or description):
            try:
                verdict = self.judge(((description + "\n") if description else "") + task)
            except Exception:  # a judge failure must never fail routing
                verdict = None
            if isinstance(verdict, str):
                verdict = {"class": verdict}
            cls = verdict.get("class") if isinstance(verdict, dict) else None
            if cls in self.policy.classes and cls != "unsure":
                c.notes.append("keyword rules were not confident (%s, %.2f); the judge model said %s"
                               % (c.task_class, c.confidence, cls))
                c.flags.update(f for f in (verdict.get("flags") or []) if isinstance(f, str))
                # the safety rules see the judge's flags AND the keyword flags
                from .judge import apply_safety
                safe = apply_safety({"class": cls, "flags": c.flags})
                cls = safe["class"]
                c.notes.extend(safe["notes"])
                c.runner_up = c.task_class if c.task_class != cls else c.runner_up
                c.task_class, c.confidence, c.judged = cls, 0.7, True
        return c

    # -- routing -------------------------------------------------------------

    def route(
        self,
        task: str,
        *,
        description: Optional[str] = None,
        agent_type: Optional[str] = None,
        platform: str = "api",
        task_class: Optional[str] = None,
        floor: Optional[str] = None,
        ceiling: Optional[str] = None,
        available: Optional[Iterable[str]] = None,
        prefer: str = "balanced",
        context_tokens: Optional[int] = None,
    ) -> Decision:
        """Pick a model tier and effort for task.

        floor / ceiling bound the tier. available lists the tiers
        this account can use. prefer is "cost", "balanced" or
        "quality". task_class skips classification when you already
        know what kind of work it is.
        """
        p = self.policy
        if task_class is not None:
            if task_class not in p.classes:
                raise ValueError("unknown task class %r (known: %s)" % (task_class, ", ".join(p.classes)))
            cl = classify(task, policy=p, description=description, agent_type=agent_type, platform=platform)
            cl.task_class, cl.confidence = task_class, 1.0
            cl.notes.append("task class given by the caller")
        else:
            cl = self.classify(task, description=description, agent_type=agent_type, platform=platform)

        cls = cl.task_class
        spec = p.classes[cls]
        tier, effort = spec["tier"], spec["effort"]
        flags = cl.flags
        reasons = ["%s: %s" % (cls, spec["what"].rstrip(".")), spec["why"]]
        reasons.extend(cl.notes)
        notes: List[str] = []
        soft = 0   # can raise effort to high at most
        hard = 0   # can reach the tier's cap

        def raise_tier(to: str, why: str) -> None:
            nonlocal tier, effort
            if tier_rank(tier) < tier_rank(to):
                tier, effort = to, _ENTRY_EFFORT[to]
                reasons.append(why)

        judged_work = cls not in ("lookup", "extract", "chat", "unsure")
        if "stakes" in flags and (judged_work or "mutating" in flags):
            raise_tier("sonnet", "high stakes: no small-model edits where a mistake is expensive")
            soft += 1
            reasons.append("high stakes: one effort level up, and give it a way to verify its work")
        if "judgment" in flags and tier == "haiku":
            raise_tier("sonnet", "judgment call: a wrong answer would pass unnoticed, so not Haiku")
        if "tricky" in flags and cls == "review":
            raise_tier("opus", "hard-to-reproduce bug: tricky debugging goes to Opus even when read-only")
        if "computer_use" in flags:
            raise_tier("opus", "computer use is Opus-tier work")
        if "vision" in flags:
            raise_tier("sonnet", "image input: Sonnet or better for visual understanding")
        if "ambiguous" in flags and cls in ("edit", "review", "build"):
            raise_tier("opus", "vague brief: ambiguous work needs the more capable model")
        if "long_horizon" in flags:
            raise_tier("opus", "long unattended run: not on Sonnet or Haiku")
            hard += 1
            reasons.append("long unattended run: one effort level up")
        if "latency" in flags:
            hard -= 1
            reasons.append("someone is waiting: one effort level down")
        if "effort_up" in flags:
            hard += 1
            reasons.append("the brief asks for extra care: one effort level up")
        if "effort_down" in flags:
            hard -= 1
            reasons.append("the brief asks for speed: one effort level down")
        if prefer == "cost" and "stakes" not in flags:
            hard -= 1
            reasons.append("prefer=cost: one effort level down")
        elif prefer == "quality":
            soft += 1
            reasons.append("prefer=quality: one effort level up")

        if soft > 0 and effort_rank(effort) < effort_rank("high"):
            effort = _shift(effort, 1, hi="high")
        hard = max(-1, min(1, hard))
        if hard:
            effort = _shift(effort, hard, hi=p.effort_cap(tier))

        # -- constraints -------------------------------------------------
        capped_from: Optional[str] = None
        if floor and tier_rank(tier) < tier_rank(floor):
            tier, effort = floor, _ENTRY_EFFORT[floor]
            reasons.append("floor=%s" % floor)
        if ceiling and tier_rank(tier) > tier_rank(ceiling):
            capped_from, tier = tier, ceiling
        if available is not None:
            avail = [t for t in TIERS if t in set(available)]
            if avail and tier not in avail:
                lower = [t for t in avail if tier_rank(t) < tier_rank(tier)]
                if lower:
                    capped_from, tier = capped_from or tier, lower[-1]
                else:
                    tier = avail[0]
                    effort = _ENTRY_EFFORT[tier]
                    reasons.append("%s is the smallest tier available here" % tier)
        if capped_from:
            effort = _shift(effort, 1, hi="high") if effort_rank(effort) < effort_rank("high") else effort
            reasons.append(
                "wanted %s but capped at %s: running one effort level up instead. "
                "Effort buys more checking, not skill the model lacks" % (capped_from, tier))
        if effort_rank(effort) > effort_rank(p.effort_cap(tier)):
            effort = p.effort_cap(tier)

        # -- strategy and notes ------------------------------------------
        strategy = None
        if "volume" in flags:
            strategy = "cheap-first: run the batch at this effort, re-run only the failures one rung up"
            notes.append("high volume: the Batch API is half price when nobody is waiting on the answer")
        elif "checkable" in flags:
            strategy = "verify, then escalate: let it run the check; only failures climb the ladder"
        info = p.model_info(tier) or {}
        if tier == "haiku" and context_tokens and info.get("long_prompt") \
                and context_tokens > info["long_prompt"]["over_tokens"]:
            notes.append("prompt is over %d tokens: Haiku bills that request at $%g/$%g per MTok"
                         % (info["long_prompt"]["over_tokens"], info["long_prompt"]["input"],
                            info["long_prompt"]["output"]))

        default = p.default_effort(tier, platform)
        return Decision(
            task_class=cls,
            tier=tier,
            model=p.model_id(tier),
            effort=effort,
            confidence=cl.confidence,
            platform=platform,
            effort_is_default=(effort == default),
            strategy=strategy,
            reasons=reasons,
            notes=notes,
            flags=sorted(flags),
            ladder=self.ladder(tier, effort, ceiling=ceiling, available=available),
            capped_from=capped_from,
            price={"input": info.get("input"), "output": info.get("output")} if info else None,
            policy_version=p.version,
            classification=cl,
        )

    # -- escalation ladder ---------------------------------------------------

    def ladder(self, tier: str, effort: str, *, ceiling: Optional[str] = None,
               available: Optional[Iterable[str]] = None) -> List[Tuple[str, str]]:
        """Rungs above (tier, effort), cheapest first.

        Order: raise effort to high, then move up a model at a lower
        effort, and only above Opus@high go to xhigh before Fable.
        """
        rungs = self.policy.ladder
        avail = set(available) if available is not None else None

        def allowed(t: str) -> bool:
            if ceiling and tier_rank(t) > tier_rank(ceiling):
                return False
            return avail is None or t in avail

        out: List[Tuple[str, str]] = []
        e = effort
        cap = self.policy.effort_cap(tier)
        # start that isn't on the ladder (sonnet@low etc): raise effort
        # inside the tier until we land on a rung
        while (tier, e) not in rungs and effort_rank(e) < effort_rank(cap):
            e = _shift(e, 1)
            out.append((tier, e))
        if (tier, e) in rungs:
            out.extend(rungs[rungs.index((tier, e)) + 1:])
        else:
            out.extend(r for r in rungs if tier_rank(r[0]) > tier_rank(tier))
        return [r for r in out if allowed(r[0])]

    # -- main-session advice -------------------------------------------------

    def advise_session(self, task: str, *, current_tier: Optional[str] = None) -> dict:
        """Starting model and effort for a whole session doing task.

        A session model is chosen once and kept (switching mid-session drops
        the prompt cache), so this is coarser than per-task routing.
        """
        cl = self.classify(task)
        tier, effort = self.policy.session_advice.get(cl.task_class, ["opus", "medium"])
        if "long_horizon" in cl.flags and tier_rank(tier) < tier_rank("opus"):
            tier, effort = "opus", "high"
        mismatch = None
        if current_tier in TIERS:
            gap = tier_rank(tier) - tier_rank(current_tier)
            heavy = cl.task_class in ("design", "frontier") or "long_horizon" in cl.flags
            if gap > 0 and (heavy or current_tier == "haiku") and cl.confidence >= 0.6:
                mismatch = "too_small"
        return {
            "task_class": cl.task_class,
            "confidence": cl.confidence,
            "tier": tier,
            "effort": effort,
            "mismatch": mismatch,
            "note": "raise effort before switching model, and delegate bulk reading to cheaper subagents",
        }


_default_router: Optional[Router] = None


def route(task: str, **kwargs) -> Decision:
    """Route with the default policy. See Router.route."""
    global _default_router
    if _default_router is None:
        _default_router = Router()
    return _default_router.route(task, **kwargs)
