"""What happened after the route: did the cheap model cope or not?

The router decides before the work starts. This module looks at what came
back, so the next decision (and the policy itself) can use it.

Three signals, all local, all from things Claude Code already tells the hooks:

  escalated       the subagent's answer starts a line with ESCALATE:
  failed          the Agent call came back with an error status
  retried higher  the same task was sent again in the session on a bigger model

None of them says the work was GOOD. A subagent can finish, sound confident
and be wrong. So `tune` only ever proposes going up on evidence; going down
is something you test with `gearshift bench`, not something I infer.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from . import ledger
from .policy import EFFORTS, Policy, default_policy, tier_rank

ESCALATE_RE = re.compile(r"^\s*(?:\*\*)?ESCALATE(?:\*\*)?\s*:\s*(.*)$", re.MULTILINE)

HINT = ("If this task turns out to need a stronger model than you (you are guessing, or you tried "
        "and could not get it right), stop and reply with a first line that starts with ESCALATE: "
        "followed by one sentence on why. Do not hand back work you are not confident in.")

_WORD = re.compile(r"[^\W\d_]{4,}", re.UNICODE)
MAX_TASKS = 40


# -- same task again? ----------------------------------------------------------

def fingerprint(text: str, limit: int = 80) -> List[str]:
    """Short hashes of the distinct words. Enough to say "this is the same
    task again" without keeping the prompt itself in the state file."""
    words = sorted({w.lower() for w in _WORD.findall(text or "")})
    return [hashlib.sha1(w.encode("utf-8")).hexdigest()[:8] for w in words[:limit]]


def similarity(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / float(len(sa | sb))


def find_prior(tasks: List[dict], fp: List[str], desc: str) -> Optional[dict]:
    """Most recent earlier task in this session that looks like this one."""
    desc = (desc or "").strip().lower()
    for t in reversed(tasks or []):
        sim = similarity(fp, t.get("fp") or [])
        same_desc = bool(desc) and desc == (t.get("desc") or "").strip().lower()
        if sim >= 0.6 or (same_desc and sim >= 0.35):
            return t
        # a retry is rarely word for word. Live, the main session sent the failed task again with
        # "a previous attempt may already have added..." on the end and a new description, which
        # nearly doubled the words and dropped the overlap to 0.53. So for a task that fell short:
        # if almost all of its words are in the new one, it is the same task with notes added.
        old = set(t.get("fp") or [])
        if t.get("short") and len(old) >= 8 and len(old & set(fp)) >= 0.85 * len(old):
            return t
    return None


def escalation_in(resp: Any, budget: int = 200000) -> Optional[str]:
    """Reason text if any string inside the tool response has an ESCALATE: line."""
    stack, seen = [resp], 0
    while stack and seen < budget:
        x = stack.pop()
        if isinstance(x, str):
            seen += len(x)
            m = ESCALATE_RE.search(x)
            if m:
                return m.group(1).strip()[:160] or "no reason given"
        elif isinstance(x, dict):
            stack.extend(x.values())
        elif isinstance(x, (list, tuple)):
            stack.extend(x)
    return None


def failed_status(resp: Mapping[str, Any]) -> bool:
    status = str(resp.get("status") or "").lower()
    return bool(resp.get("is_error")) or status in ("error", "failed", "failure", "timeout")


def next_rung(tier: str, effort: str, policy: Optional[Policy] = None,
              ceiling: Optional[str] = None) -> Optional[Tuple[str, str]]:
    pol = policy or default_policy()
    here = (tier_rank(tier), EFFORTS.index(effort) if effort in EFFORTS else 1)
    for t, e in pol.ladder:     # cheapest first
        if (tier_rank(t), EFFORTS.index(e)) > here:
            if ceiling and tier_rank(t) > tier_rank(ceiling):
                return None
            return t, e
    return None


def prev_rung(tier: str, effort: str, policy: Optional[Policy] = None) -> Optional[Tuple[str, str]]:
    pol = policy or default_policy()
    here = (tier_rank(tier), EFFORTS.index(effort) if effort in EFFORTS else 1)
    below = [(t, e) for t, e in pol.ladder if (tier_rank(t), EFFORTS.index(e)) < here]
    return tuple(below[-1]) if below else None


# what a probe may never touch: anything that changes things, or where a quiet
# mistake is expensive. Probing is for work that is cheap to redo.
_NO_PROBE_FLAGS = {"mutating", "stakes", "tricky", "long_horizon", "computer_use", "ambiguous", "major", "multi_file"}
_NO_PROBE_CLASSES = {"unsure", "edit", "build", "frontier"}


def probe_ok(task_class: str, flags: Iterable[str]) -> bool:
    return task_class not in _NO_PROBE_CLASSES and not (_NO_PROBE_FLAGS & set(flags))


def sampled(key: str, share: float) -> bool:
    """Deterministic coin flip, so a given call always gets the same answer."""
    if share <= 0:
        return False
    h = int(hashlib.sha1((key or "").encode("utf-8")).hexdigest()[:8], 16)
    return (h % 10000) < int(share * 10000)


# -- learning from the ledger --------------------------------------------------

def collect_runs(state: Path, days: Optional[float] = 30) -> List[Dict[str, Any]]:
    """One record per Agent call: what it was, where it ran, how it ended."""
    since = time.time() - days * 86400 if days else None
    events = ledger.read_events(state, since)
    done = {e.get("tool_use_id"): e for e in events
            if e.get("ev") == "done" and e.get("tool_use_id") and e.get("status") != "async_launched"}
    usage = {e.get("agent_id"): e for e in events if e.get("ev") == "usage" and e.get("agent_id")}
    routes = [e for e in events if e.get("ev") == "route" and e.get("tool_use_id")]
    by_id = {r["tool_use_id"]: r for r in routes}
    retried_higher = set()
    for r in routes:
        prev = by_id.get(r.get("retry_of"))
        if prev and _ran(r)[0] and _ran(prev)[0] and tier_rank(_ran(r)[0]) > tier_rank(_ran(prev)[0]):
            retried_higher.add(prev["tool_use_id"])
    runs = []
    for r in routes:
        d = done.get(r["tool_use_id"])
        if d is None:
            continue    # never came back (still running, or the session ended)
        u = usage.get(d.get("agent_id")) or {}
        tier, effort = _ran(r)
        # what actually ran beats what the route event implies. a pinned agent
        # has no model on the call, and it is NOT running on the session model.
        actual = d.get("resolved_model") or u.get("main_model")
        if actual:
            from .pricing import tier_of
            tier = tier_of(str(actual)) or tier
        if not tier:
            continue
        runs.append({
            "class": r.get("class") or "unsure", "tier": tier, "effort": effort,
            "routed_by": "gearshift" if r.get("action") in ("filled", "overrode", "effort-only", "bumped") else "claude",
            "escalated": bool(d.get("escalated")), "failed": bool(d.get("failed") or d.get("verify") == "fail"),
            "retried_higher": r["tool_use_id"] in retried_higher,
            "probe_from": r.get("probe_from"),
            "cost_usd": u.get("cost_usd"),
        })
    return runs


def _ran(route: Mapping[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    model = route.get("model") or route.get("pinned_model") or route.get("session_tier")
    tier = model if model in ("haiku", "sonnet", "opus", "fable") else None
    if tier is None and model:
        from .pricing import tier_of
        tier = tier_of(str(model))
    return tier, route.get("effort") or route.get("pinned_effort")


def build_tune(state: Path, *, days: Optional[float] = 30, min_runs: int = 20, raise_at: float = 0.15,
               policy: Optional[Policy] = None) -> Dict[str, Any]:
    pol = policy or default_policy()
    runs = collect_runs(state, days)
    groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
    probes: Dict[Tuple[str, str, str], Dict[str, int]] = {}
    for r in runs:
        if r.get("probe_from"):
            # a deliberate step down. judged on its own, not mixed into the class's record
            p = probes.setdefault((r["class"], "%s@%s" % (r["tier"], r["effort"]), r["probe_from"]),
                                  {"runs": 0, "short": 0})
            p["runs"] += 1
            p["short"] += bool(r["escalated"] or r["failed"] or r["retried_higher"])
            continue
        g = groups.setdefault((r["class"], r["tier"]), {"runs": 0, "short": 0, "escalated": 0, "failed": 0,
                                                        "retried_higher": 0, "cost": 0.0, "costed": 0})
        g["runs"] += 1
        g["escalated"] += r["escalated"]
        g["failed"] += r["failed"]
        g["retried_higher"] += r["retried_higher"]
        g["short"] += bool(r["escalated"] or r["failed"] or r["retried_higher"])
        if r["cost_usd"] is not None:
            g["cost"] += float(r["cost_usd"])
            g["costed"] += 1
    rows, overrides = [], {}
    for (cls, tier), g in sorted(groups.items(), key=lambda kv: (kv[0][0], tier_rank(kv[0][1]))):
        rate = g["short"] / float(g["runs"])
        home = pol.classes.get(cls, {})
        row = {"class": cls, "tier": tier, "runs": g["runs"], "fell_short": g["short"], "rate": round(rate, 3),
               "escalated": g["escalated"], "failed": g["failed"], "retried_higher": g["retried_higher"],
               "avg_cost_usd": round(g["cost"] / g["costed"], 4) if g["costed"] else None, "verdict": "keep"}
        if g["runs"] < min_runs:
            row["verdict"] = "not enough runs (%d of %d)" % (g["runs"], min_runs)
        elif rate >= raise_at and cls in pol.classes and cls != "unsure" and home.get("tier") == tier:
            up = next_rung(tier, home.get("effort", "medium"), pol)
            if up:
                row["verdict"] = "raise %s to %s@%s" % (cls, up[0], up[1])
                overrides[cls] = {"tier": up[0], "effort": up[1]}
            else:
                row["verdict"] = "falls short often, and there is no rung above"
        elif rate >= raise_at:
            row["verdict"] = "falls short often here; this is not where the policy sends %s" % cls
        elif rate <= 0.02 and g["runs"] >= 2 * min_runs and tier != "haiku":
            row["verdict"] = "holding up; worth testing one step down with `gearshift bench`"
        rows.append(row)
    probe_rows, lower = [], {}
    need = max(10, (min_runs * 3) // 4)
    for (cls, ran, home), p in sorted(probes.items()):
        rate = p["short"] / float(p["runs"])
        row = {"class": cls, "ran": ran, "instead_of": home, "runs": p["runs"], "fell_short": p["short"],
               "rate": round(rate, 3), "verdict": "not enough probes (%d of %d)" % (p["runs"], need)}
        if p["runs"] >= need:
            t, e = ran.split("@")
            now = pol.classes.get(cls, {})
            if rate >= raise_at:
                row["verdict"] = "the cheaper step falls short too often; keep %s on %s" % (cls, home)
            elif rate <= 0.05 and cls not in overrides and "%s@%s" % (now.get("tier"), now.get("effort")) == home:
                row["verdict"] = "held up; %s can move down to %s" % (cls, ran)
                lower[cls] = {"tier": t, "effort": e}
            else:
                row["verdict"] = "mixed; keep probing"
        probe_rows.append(row)
    overrides.update(lower)
    return {"days": days, "runs": len(runs), "min_runs": min_runs, "raise_at": raise_at, "rows": rows,
            "probes": probe_rows, "lowered": sorted(lower),
            "policy_overrides": {"classes": overrides} if overrides else None}


def format_tune(t: Dict[str, Any]) -> str:
    scope = "last %g days" % t["days"] if t.get("days") else "all time"
    lines = ["gearshift tune - %s, %d finished subagent runs" % (scope, t["runs"]), ""]
    if not t["rows"] and not t.get("probes"):
        lines += ["Nothing to learn from yet. Outcomes are recorded as Claude Code delegates work",
                  "with the plugin on (1.2 or later). Come back after a few days of real use."]
        return "\n".join(lines)
    lines.append("%-9s %-7s %5s %11s   %s" % ("class", "ran on", "runs", "fell short", "verdict"))
    for r in t["rows"]:
        lines.append("%-9s %-7s %5d %4d (%3.0f%%)   %s" % (
            r["class"], r["tier"], r["runs"], r["fell_short"], 100 * r["rate"], r["verdict"]))
    if t.get("probes"):
        lines += ["", "Probes (safe calls sent one step cheaper on purpose):",
                  "%-9s %-14s %-14s %5s %11s   %s" % ("class", "ran on", "instead of", "runs", "fell short", "verdict")]
        for r in t["probes"]:
            lines.append("%-9s %-14s %-14s %5d %4d (%3.0f%%)   %s" % (
                r["class"], r["ran"], r["instead_of"], r["runs"], r["fell_short"], 100 * r["rate"], r["verdict"]))
    if t.get("lowered"):
        lines += ["", "Before you lower anything: the ledger sees escalations and errors, not wrong answers.",
                  "Read a few of the probe results for %s yourself first." % ", ".join(t["lowered"])]
    lines += ["", "fell short = the subagent said ESCALATE, the call errored, or the same task was",
              "re-sent on a bigger model. It does not measure whether finished work was right."]
    if t.get("policy_overrides"):
        lines += ["", "To apply, merge this into ~/.config/gearshift/policy.json (nothing is changed for you):",
                  "", json.dumps(t["policy_overrides"], indent=2)]
    else:
        lines += ["", "No change proposed. I only propose a raise when a class has at least %d runs on its" % t["min_runs"],
                  "home tier and %.0f%% or more of them fell short." % (100 * t["raise_at"])]
    return "\n".join(lines)
